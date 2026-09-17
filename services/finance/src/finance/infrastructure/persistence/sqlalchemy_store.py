"""PostgreSQL unit of work for the Finance service.

Two things differ from the other services' stores, and both follow from this being the
ledger.

**The ledger is insert-only.** ``_LedgerRepo.append`` stages an entry and its postings
and they are INSERTed on commit. There is no update path and no delete path, because the
database refuses both (see the W27 migration's triggers) — a store that tried would fail
at runtime rather than at review.

**Balances are read, not cached.** ``entries_for_account`` goes to the postings table
each time. A cached total is a second source of truth for the same fact, and the two
drift; the index ``ix_finance_posting_account`` exists so the honest version is fast.

Everything else is staged during the transaction and flushed on commit with a
version-conditional UPDATE, so a lost update surfaces as ``StaleFinanceRecord``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session as SaSession
from sqlalchemy.orm import sessionmaker

from finance.domain.entities import (
    CodCollection,
    Deposit,
    DriverCashAccount,
    MerchantAccount,
    PayoutRequest,
    RouteReconciliation,
)
from finance.domain.errors import StaleFinanceRecord
from finance.domain.ledger import JournalEntry, Posting
from finance.domain.messaging import (
    InboxRecord,
    InboxStatus,
    OutboxRecord,
    OutboxStatus,
)
from finance.domain.money import Currency, Money
from finance.domain.value_objects import (
    AccountKind,
    AccountRef,
    CodPaymentChannel,
    DepositMethod,
    DepositStatus,
    EvidenceMediaRef,
    JournalReason,
    PayoutMethod,
    PayoutStatus,
    ReconciliationOutcome,
    ReconciliationStatus,
    Side,
)
from finance.infrastructure.persistence.models import (
    CodCollectionRow,
    DepositRow,
    DriverCashAccountRow,
    IntegrationInboxRow,
    IntegrationOutboxRow,
    JournalEntryRow,
    JournalPostingRow,
    MerchantAccountRow,
    PayoutRequestRow,
    RouteReconciliationRow,
)

_STAGES = (
    "ledger",
    "driver_accounts",
    "merchant_accounts",
    "collections",
    "deposits",
    "payouts",
    "reconciliations",
    "outbox",
    "inbox",
)


class SqlAlchemyFinanceUnitOfWork:
    def __init__(self, *, session_factory: sessionmaker[SaSession]) -> None:
        self._session_factory = session_factory
        self._session: SaSession | None = None
        self._pending: dict[str, dict] | None = None
        self._ledger = _LedgerRepo(self)
        self._driver_accounts = _DriverAccountRepo(self)
        self._merchant_accounts = _MerchantAccountRepo(self)
        self._collections = _CollectionRepo(self)
        self._deposits = _DepositRepo(self)
        self._payouts = _PayoutRepo(self)
        self._reconciliations = _ReconciliationRepo(self)
        self._outbox = _OutboxRepo(self)
        self._inbox = _InboxRepo(self)

    def begin(self) -> None:
        if self._session is not None:
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._session = self._session_factory()
        self._pending = {name: {} for name in _STAGES}

    def commit(self) -> None:
        if self._session is None or self._pending is None:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        session = self._session
        try:
            self._flush(session)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            self._session = None
            self._pending = None

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
            self._session.close()
        self._session = None
        self._pending = None

    def _flush(self, session: SaSession) -> None:
        pending = self._pending
        assert pending is not None
        for entry in pending["ledger"].values():
            session.add(JournalEntryRow(**_entry_values(entry)))
            for position, posting in enumerate(entry.postings):
                session.add(
                    JournalPostingRow(
                        **_posting_values(posting, entry, position=position)
                    )
                )
        for entity, previous in pending["driver_accounts"].values():
            _versioned(
                session,
                DriverCashAccountRow,
                DriverCashAccountRow.account_id == entity.account_id,
                previous=previous,
                values=_driver_account_values(entity),
                new_row=lambda e=entity: DriverCashAccountRow(
                    account_id=e.account_id, **_driver_account_values(e)
                ),
            )
        for entity, previous in pending["merchant_accounts"].values():
            _versioned(
                session,
                MerchantAccountRow,
                MerchantAccountRow.account_id == entity.account_id,
                previous=previous,
                values=_merchant_account_values(entity),
                new_row=lambda e=entity: MerchantAccountRow(
                    account_id=e.account_id, **_merchant_account_values(e)
                ),
            )
        for entity, previous in pending["collections"].values():
            _versioned(
                session,
                CodCollectionRow,
                CodCollectionRow.collection_id == entity.collection_id,
                previous=previous,
                values=_collection_values(entity),
                new_row=lambda e=entity: CodCollectionRow(
                    collection_id=e.collection_id, **_collection_values(e)
                ),
            )
        for entity, previous in pending["deposits"].values():
            _versioned(
                session,
                DepositRow,
                DepositRow.deposit_id == entity.deposit_id,
                previous=previous,
                values=_deposit_values(entity),
                new_row=lambda e=entity: DepositRow(
                    deposit_id=e.deposit_id, **_deposit_values(e)
                ),
            )
        for entity, previous in pending["payouts"].values():
            _versioned(
                session,
                PayoutRequestRow,
                PayoutRequestRow.payout_id == entity.payout_id,
                previous=previous,
                values=_payout_values(entity),
                new_row=lambda e=entity: PayoutRequestRow(
                    payout_id=e.payout_id, **_payout_values(e)
                ),
            )
        for entity, previous in pending["reconciliations"].values():
            _versioned(
                session,
                RouteReconciliationRow,
                RouteReconciliationRow.reconciliation_id == entity.reconciliation_id,
                previous=previous,
                values=_reconciliation_values(entity),
                new_row=lambda e=entity: RouteReconciliationRow(
                    reconciliation_id=e.reconciliation_id,
                    **_reconciliation_values(e),
                ),
            )
        for record in pending["outbox"].values():
            session.add(IntegrationOutboxRow(**_outbox_values(record)))
        for record, is_new in pending["inbox"].values():
            if is_new:
                session.add(IntegrationInboxRow(**_inbox_values(record)))
            else:
                session.execute(
                    update(IntegrationInboxRow)
                    .where(
                        IntegrationInboxRow.consumer_name == record.consumer_name,
                        IntegrationInboxRow.event_id == record.event_id,
                    )
                    .values(**_inbox_update_values(record))
                )

    def _require(self) -> SaSession:
        if self._session is None:
            msg = "no active transaction"
            raise RuntimeError(msg)
        return self._session

    @property
    def ledger(self) -> _LedgerRepo:
        return self._ledger

    @property
    def driver_accounts(self) -> _DriverAccountRepo:
        return self._driver_accounts

    @property
    def merchant_accounts(self) -> _MerchantAccountRepo:
        return self._merchant_accounts

    @property
    def collections(self) -> _CollectionRepo:
        return self._collections

    @property
    def deposits(self) -> _DepositRepo:
        return self._deposits

    @property
    def payouts(self) -> _PayoutRepo:
        return self._payouts

    @property
    def reconciliations(self) -> _ReconciliationRepo:
        return self._reconciliations

    @property
    def outbox(self) -> _OutboxRepo:
        return self._outbox

    @property
    def inbox(self) -> _InboxRepo:
        return self._inbox


def _versioned(session, row_cls, where, *, previous, values, new_row) -> None:
    if previous is None:
        session.add(new_row())
        return
    rowcount = session.execute(
        update(row_cls).where(where, row_cls.version == previous).values(**values)
    ).rowcount
    if rowcount == 0:
        raise StaleFinanceRecord(row_cls.__tablename__)


def _stage(pending: dict, key, entity, version: int) -> None:
    previous = pending[key][1] if key in pending else None
    if previous is None and version > 1:
        previous = version - 1
    pending[key] = (entity, previous)


class _Repo:
    def __init__(self, uow: SqlAlchemyFinanceUnitOfWork) -> None:
        self._uow = uow

    def _session(self) -> SaSession:
        return self._uow._require()

    def _stage_for(self, name: str) -> dict:
        assert self._uow._pending is not None
        return self._uow._pending[name]


class _LedgerRepo(_Repo):
    """Append-only, in Python as well as in PostgreSQL."""

    def append(self, entry: JournalEntry) -> None:
        stage = self._stage_for("ledger")
        if entry.entry_id in stage:
            msg = f"journal entry {entry.entry_id} is already staged"
            raise ValueError(msg)
        stage[entry.entry_id] = entry

    def get(self, entry_id: UUID) -> JournalEntry | None:
        staged = self._stage_for("ledger").get(entry_id)
        if staged is not None:
            return staged
        row = self._session().get(JournalEntryRow, entry_id)
        return self._hydrate(row) if row is not None else None

    def find_by_idempotency_key(self, key: str) -> JournalEntry | None:
        for staged in self._stage_for("ledger").values():
            if staged.idempotency_key == key:
                return staged
        row = (
            self._session()
            .execute(
                select(JournalEntryRow).where(JournalEntryRow.idempotency_key == key)
            )
            .scalars()
            .first()
        )
        return self._hydrate(row) if row is not None else None

    def entries_for_account(self, account: AccountRef) -> Iterable[JournalEntry]:
        """Every entry touching one account, staged ones included.

        A service that posts and then reads its own balance inside one transaction —
        which the deposit flow does — has to see what it just staged, or it will refuse
        a deposit for cash it is in the middle of recording.
        """
        condition = JournalPostingRow.account_kind == account.kind.value
        condition = (
            condition & (JournalPostingRow.party_id == account.party_id)
            if account.party_id is not None
            else condition & JournalPostingRow.party_id.is_(None)
        )
        entry_ids = (
            self._session()
            .execute(select(JournalPostingRow.entry_id).where(condition).distinct())
            .scalars()
            .all()
        )
        found = [self.get(entry_id) for entry_id in entry_ids]
        staged = [
            entry
            for entry in self._stage_for("ledger").values()
            if entry.touches(account) and entry.entry_id not in set(entry_ids)
        ]
        return tuple(entry for entry in found if entry is not None) + tuple(staged)

    def entries_for_subject(
        self, subject_kind: str, subject_id: UUID
    ) -> tuple[JournalEntry, ...]:
        rows = (
            self._session()
            .execute(
                select(JournalEntryRow)
                .where(
                    JournalEntryRow.subject_kind == subject_kind,
                    JournalEntryRow.subject_id == subject_id,
                )
                .order_by(JournalEntryRow.occurred_at)
            )
            .scalars()
            .all()
        )
        return tuple(self._hydrate(row) for row in rows)

    def _hydrate(self, row: JournalEntryRow) -> JournalEntry:
        postings = (
            self._session()
            .execute(
                select(JournalPostingRow)
                .where(JournalPostingRow.entry_id == row.entry_id)
                .order_by(JournalPostingRow.position)
            )
            .scalars()
            .all()
        )
        return JournalEntry(
            entry_id=row.entry_id,
            reason=JournalReason(row.reason),
            postings=tuple(_posting_entity(p) for p in postings),
            occurred_at=row.occurred_at,
            recorded_by_actor_id=row.recorded_by_actor_id,
            subject_kind=row.subject_kind,
            subject_id=row.subject_id,
            idempotency_key=row.idempotency_key,
            corrects_entry_id=row.corrects_entry_id,
            memo=row.memo,
        )


class _DriverAccountRepo(_Repo):
    def save(self, account: DriverCashAccount) -> None:
        _stage(
            self._stage_for("driver_accounts"),
            account.account_id,
            account,
            account.version,
        )

    def get(self, account_id: UUID) -> DriverCashAccount | None:
        staged = self._stage_for("driver_accounts").get(account_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(DriverCashAccountRow, account_id)
        return _driver_account_entity(row) if row is not None else None

    def find_for_driver(self, driver_principal_id: UUID) -> DriverCashAccount | None:
        for staged, _ in self._stage_for("driver_accounts").values():
            if staged.driver_principal_id == driver_principal_id:
                return staged
        row = (
            self._session()
            .execute(
                select(DriverCashAccountRow).where(
                    DriverCashAccountRow.driver_principal_id == driver_principal_id
                )
            )
            .scalars()
            .first()
        )
        return _driver_account_entity(row) if row is not None else None

    def list_all(self) -> tuple[DriverCashAccount, ...]:
        rows = (
            self._session().execute(select(DriverCashAccountRow)).scalars().all()
        )
        return tuple(_driver_account_entity(row) for row in rows)


class _MerchantAccountRepo(_Repo):
    def save(self, account: MerchantAccount) -> None:
        _stage(
            self._stage_for("merchant_accounts"),
            account.account_id,
            account,
            account.version,
        )

    def find_for_merchant(self, merchant_id: UUID) -> MerchantAccount | None:
        for staged, _ in self._stage_for("merchant_accounts").values():
            if staged.merchant_id == merchant_id:
                return staged
        row = (
            self._session()
            .execute(
                select(MerchantAccountRow).where(
                    MerchantAccountRow.merchant_id == merchant_id
                )
            )
            .scalars()
            .first()
        )
        return _merchant_account_entity(row) if row is not None else None


class _CollectionRepo(_Repo):
    def save(self, collection: CodCollection) -> None:
        _stage(
            self._stage_for("collections"),
            collection.collection_id,
            collection,
            collection.version,
        )

    def get(self, collection_id: UUID) -> CodCollection | None:
        staged = self._stage_for("collections").get(collection_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(CodCollectionRow, collection_id)
        return _collection_entity(row) if row is not None else None

    def find_by_tracking_code(self, tracking_code: str) -> CodCollection | None:
        for staged, _ in self._stage_for("collections").values():
            if staged.tracking_code == tracking_code:
                return staged
        row = (
            self._session()
            .execute(
                select(CodCollectionRow).where(
                    CodCollectionRow.tracking_code == tracking_code
                )
            )
            .scalars()
            .first()
        )
        return _collection_entity(row) if row is not None else None

    def list_unsettled_for_driver(
        self, driver_principal_id: UUID
    ) -> tuple[CodCollection, ...]:
        rows = (
            self._session()
            .execute(
                select(CodCollectionRow)
                .where(
                    CodCollectionRow.driver_principal_id == driver_principal_id,
                    CodCollectionRow.settled_at.is_(None),
                )
                .order_by(CodCollectionRow.collected_at)
            )
            .scalars()
            .all()
        )
        staged_ids = set(self._stage_for("collections"))
        found = [
            _collection_entity(row)
            for row in rows
            if row.collection_id not in staged_ids
        ]
        found.extend(
            staged
            for staged, _ in self._stage_for("collections").values()
            if staged.driver_principal_id == driver_principal_id
            and staged.settled_at is None
        )
        return tuple(sorted(found, key=lambda c: c.collected_at))


class _DepositRepo(_Repo):
    def save(self, deposit: Deposit) -> None:
        _stage(
            self._stage_for("deposits"), deposit.deposit_id, deposit, deposit.version
        )

    def get(self, deposit_id: UUID) -> Deposit | None:
        staged = self._stage_for("deposits").get(deposit_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(DepositRow, deposit_id)
        return _deposit_entity(row) if row is not None else None

    def find_by_reference(self, reference: str) -> Deposit | None:
        for staged, _ in self._stage_for("deposits").values():
            if staged.reference == reference:
                return staged
        row = (
            self._session()
            .execute(select(DepositRow).where(DepositRow.reference == reference))
            .scalars()
            .first()
        )
        return _deposit_entity(row) if row is not None else None

    def list_for_driver(self, driver_principal_id: UUID) -> tuple[Deposit, ...]:
        rows = (
            self._session()
            .execute(
                select(DepositRow)
                .where(DepositRow.driver_principal_id == driver_principal_id)
                .order_by(DepositRow.submitted_at)
            )
            .scalars()
            .all()
        )
        staged_ids = set(self._stage_for("deposits"))
        found = [
            _deposit_entity(row) for row in rows if row.deposit_id not in staged_ids
        ]
        found.extend(
            staged
            for staged, _ in self._stage_for("deposits").values()
            if staged.driver_principal_id == driver_principal_id
        )
        return tuple(found)

    def list_pending_verification(self) -> tuple[Deposit, ...]:
        rows = (
            self._session()
            .execute(
                select(DepositRow)
                .where(DepositRow.status == DepositStatus.PENDING_VERIFICATION.value)
                .order_by(DepositRow.submitted_at)
            )
            .scalars()
            .all()
        )
        return tuple(_deposit_entity(row) for row in rows)


class _PayoutRepo(_Repo):
    def save(self, payout: PayoutRequest) -> None:
        _stage(self._stage_for("payouts"), payout.payout_id, payout, payout.version)

    def get(self, payout_id: UUID) -> PayoutRequest | None:
        staged = self._stage_for("payouts").get(payout_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(PayoutRequestRow, payout_id)
        return _payout_entity(row) if row is not None else None

    def list_for_merchant(self, merchant_id: UUID) -> tuple[PayoutRequest, ...]:
        rows = (
            self._session()
            .execute(
                select(PayoutRequestRow)
                .where(PayoutRequestRow.merchant_id == merchant_id)
                .order_by(PayoutRequestRow.requested_at)
            )
            .scalars()
            .all()
        )
        staged_ids = set(self._stage_for("payouts"))
        found = [
            _payout_entity(row) for row in rows if row.payout_id not in staged_ids
        ]
        found.extend(
            staged
            for staged, _ in self._stage_for("payouts").values()
            if staged.merchant_id == merchant_id
        )
        return tuple(found)

    def list_open(self) -> tuple[PayoutRequest, ...]:
        rows = (
            self._session()
            .execute(
                select(PayoutRequestRow)
                .where(
                    PayoutRequestRow.status.in_(
                        (PayoutStatus.REQUESTED.value, PayoutStatus.APPROVED.value)
                    )
                )
                .order_by(PayoutRequestRow.requested_at)
            )
            .scalars()
            .all()
        )
        return tuple(_payout_entity(row) for row in rows)


class _ReconciliationRepo(_Repo):
    def save(self, reconciliation: RouteReconciliation) -> None:
        _stage(
            self._stage_for("reconciliations"),
            reconciliation.reconciliation_id,
            reconciliation,
            reconciliation.version,
        )

    def get(self, reconciliation_id: UUID) -> RouteReconciliation | None:
        staged = self._stage_for("reconciliations").get(reconciliation_id)
        if staged is not None:
            return staged[0]
        row = self._session().get(RouteReconciliationRow, reconciliation_id)
        return _reconciliation_entity(row) if row is not None else None

    def find_for_driver_day(
        self, driver_principal_id: UUID, route_day: date
    ) -> RouteReconciliation | None:
        row = (
            self._session()
            .execute(
                select(RouteReconciliationRow).where(
                    RouteReconciliationRow.driver_principal_id == driver_principal_id,
                    RouteReconciliationRow.route_day == route_day,
                )
            )
            .scalars()
            .first()
        )
        return _reconciliation_entity(row) if row is not None else None

    def list_unresolved(self) -> tuple[RouteReconciliation, ...]:
        rows = (
            self._session()
            .execute(
                select(RouteReconciliationRow)
                .where(
                    RouteReconciliationRow.status != ReconciliationStatus.RESOLVED.value
                )
                .order_by(RouteReconciliationRow.opened_at)
            )
            .scalars()
            .all()
        )
        return tuple(_reconciliation_entity(row) for row in rows)

    def list_unresolved_for_driver(
        self, driver_principal_id: UUID
    ) -> tuple[RouteReconciliation, ...]:
        rows = (
            self._session()
            .execute(
                select(RouteReconciliationRow)
                .where(
                    RouteReconciliationRow.driver_principal_id == driver_principal_id,
                    RouteReconciliationRow.status
                    != ReconciliationStatus.RESOLVED.value,
                )
                .order_by(RouteReconciliationRow.opened_at)
            )
            .scalars()
            .all()
        )
        return tuple(_reconciliation_entity(row) for row in rows)


class _OutboxRepo(_Repo):
    def insert(self, record: OutboxRecord) -> None:
        self._stage_for("outbox")[record.id] = record

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        for staged in self._stage_for("outbox").values():
            if staged.event_id == event_id:
                return staged
        row = (
            self._session()
            .execute(
                select(IntegrationOutboxRow).where(
                    IntegrationOutboxRow.event_id == event_id
                )
            )
            .scalars()
            .first()
        )
        return _outbox_entity(row) if row is not None else None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        rows = (
            self._session()
            .execute(
                select(IntegrationOutboxRow)
                .where(IntegrationOutboxRow.status == OutboxStatus.PENDING.value)
                .order_by(IntegrationOutboxRow.created_at)
            )
            .scalars()
            .all()
        )
        return tuple(_outbox_entity(row) for row in rows)

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        rows = (
            self._session()
            .execute(
                select(IntegrationOutboxRow)
                .where(IntegrationOutboxRow.aggregate_id == aggregate_id)
                .order_by(IntegrationOutboxRow.aggregate_version)
            )
            .scalars()
            .all()
        )
        return tuple(_outbox_entity(row) for row in rows)


class _InboxRepo(_Repo):
    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        staged = self._stage_for("inbox").get((consumer_name, event_id))
        if staged is not None:
            return staged[0]
        row = (
            self._session()
            .execute(
                select(IntegrationInboxRow).where(
                    IntegrationInboxRow.consumer_name == consumer_name,
                    IntegrationInboxRow.event_id == event_id,
                )
            )
            .scalars()
            .first()
        )
        return _inbox_entity(row) if row is not None else None

    def insert(self, record: InboxRecord) -> None:
        self._stage_for("inbox")[(record.consumer_name, record.event_id)] = (
            record,
            True,
        )

    def save(self, record: InboxRecord) -> None:
        key = (record.consumer_name, record.event_id)
        staged = self._stage_for("inbox").get(key)
        is_new = staged[1] if staged is not None else False
        self._stage_for("inbox")[key] = (record, is_new)


# ------------------------------------------------------------------ mapping


def _entry_values(entry: JournalEntry) -> dict:
    return {
        "entry_id": entry.entry_id,
        "reason": entry.reason.value,
        "occurred_at": entry.occurred_at,
        "currency": entry.currency.value,
        "total_minor_units": entry.total.minor_units,
        "recorded_by_actor_id": entry.recorded_by_actor_id,
        "subject_kind": entry.subject_kind,
        "subject_id": entry.subject_id,
        "idempotency_key": entry.idempotency_key,
        "corrects_entry_id": entry.corrects_entry_id,
        "memo": entry.memo,
        "created_at": datetime.now(tz=UTC),
    }


def _posting_values(posting: Posting, entry: JournalEntry, *, position: int) -> dict:
    return {
        "posting_id": uuid4(),
        "entry_id": entry.entry_id,
        "account_kind": posting.account.kind.value,
        "party_id": posting.account.party_id,
        "side": posting.side.value,
        "amount_minor_units": posting.amount.minor_units,
        "currency": posting.amount.currency.value,
        "occurred_at": entry.occurred_at,
        "position": position,
    }


def _posting_entity(row: JournalPostingRow) -> Posting:
    return Posting(
        account=AccountRef(
            kind=AccountKind(row.account_kind), party_id=row.party_id
        ),
        side=Side(row.side),
        amount=Money(
            minor_units=int(row.amount_minor_units), currency=Currency(row.currency)
        ),
    )


def _money(minor_units, currency) -> Money:
    return Money(minor_units=int(minor_units), currency=Currency(currency))


def _driver_account_values(entity: DriverCashAccount) -> dict:
    return {
        "driver_principal_id": entity.driver_principal_id,
        "limit_minor_units": entity.limit.minor_units,
        "limit_currency": entity.limit.currency.value,
        "cod_blocked": entity.cod_blocked,
        "cod_blocked_reason": entity.cod_blocked_reason,
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
        "version": entity.version,
    }


def _driver_account_entity(row: DriverCashAccountRow) -> DriverCashAccount:
    return DriverCashAccount(
        account_id=row.account_id,
        driver_principal_id=row.driver_principal_id,
        limit=_money(row.limit_minor_units, row.limit_currency),
        cod_blocked=row.cod_blocked,
        cod_blocked_reason=row.cod_blocked_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
        version=row.version,
    )


def _merchant_account_values(entity: MerchantAccount) -> dict:
    return {
        "merchant_id": entity.merchant_id,
        "payouts_blocked": entity.payouts_blocked,
        "payouts_blocked_reason": entity.payouts_blocked_reason,
        "created_at": entity.created_at,
        "version": entity.version,
    }


def _merchant_account_entity(row: MerchantAccountRow) -> MerchantAccount:
    return MerchantAccount(
        account_id=row.account_id,
        merchant_id=row.merchant_id,
        payouts_blocked=row.payouts_blocked,
        payouts_blocked_reason=row.payouts_blocked_reason,
        created_at=row.created_at,
        version=row.version,
    )


def _collection_values(entity: CodCollection) -> dict:
    return {
        "tracking_code": entity.tracking_code,
        "merchant_id": entity.merchant_id,
        "channel": entity.channel.value,
        "goods_minor_units": entity.goods_amount.minor_units,
        "delivery_fee_minor_units": entity.delivery_fee.minor_units,
        "currency": entity.goods_amount.currency.value,
        "collected_at": entity.collected_at,
        "driver_principal_id": entity.driver_principal_id,
        "settled_at": entity.settled_at,
        "settling_deposit_id": entity.settling_deposit_id,
        "journal_entry_id": entity.journal_entry_id,
        "version": entity.version,
    }


def _collection_entity(row: CodCollectionRow) -> CodCollection:
    return CodCollection(
        collection_id=row.collection_id,
        tracking_code=row.tracking_code,
        merchant_id=row.merchant_id,
        channel=CodPaymentChannel(row.channel),
        goods_amount=_money(row.goods_minor_units, row.currency),
        delivery_fee=_money(row.delivery_fee_minor_units, row.currency),
        collected_at=row.collected_at,
        driver_principal_id=row.driver_principal_id,
        settled_at=row.settled_at,
        settling_deposit_id=row.settling_deposit_id,
        journal_entry_id=row.journal_entry_id,
        version=row.version,
    )


def _deposit_values(entity: Deposit) -> dict:
    return {
        "driver_principal_id": entity.driver_principal_id,
        "method": entity.method.value,
        "amount_minor_units": entity.amount.minor_units,
        "currency": entity.amount.currency.value,
        "reference": entity.reference,
        "receipt_bucket": entity.receipt.bucket,
        "receipt_key": entity.receipt.key,
        "receipt_content_type": entity.receipt.content_type,
        "status": entity.status.value,
        "submitted_at": entity.submitted_at,
        "hub_id": entity.hub_id,
        "decided_at": entity.decided_at,
        "decided_by_actor_id": entity.decided_by_actor_id,
        "rejection_reason": entity.rejection_reason,
        "submitted_entry_id": entity.submitted_entry_id,
        "settled_entry_id": entity.settled_entry_id,
        "version": entity.version,
    }


def _deposit_entity(row: DepositRow) -> Deposit:
    return Deposit(
        deposit_id=row.deposit_id,
        driver_principal_id=row.driver_principal_id,
        method=DepositMethod(row.method),
        amount=_money(row.amount_minor_units, row.currency),
        reference=row.reference,
        receipt=EvidenceMediaRef(
            bucket=row.receipt_bucket,
            key=row.receipt_key,
            content_type=row.receipt_content_type,
        ),
        status=DepositStatus(row.status),
        submitted_at=row.submitted_at,
        hub_id=row.hub_id,
        decided_at=row.decided_at,
        decided_by_actor_id=row.decided_by_actor_id,
        rejection_reason=row.rejection_reason,
        submitted_entry_id=row.submitted_entry_id,
        settled_entry_id=row.settled_entry_id,
        version=row.version,
    )


def _payout_values(entity: PayoutRequest) -> dict:
    return {
        "merchant_id": entity.merchant_id,
        "method": entity.method.value,
        "amount_minor_units": entity.amount.minor_units,
        "currency": entity.amount.currency.value,
        "status": entity.status.value,
        "destination_reference": entity.destination_reference,
        "requested_at": entity.requested_at,
        "requested_by_principal_id": entity.requested_by_principal_id,
        "decided_at": entity.decided_at,
        "decided_by_actor_id": entity.decided_by_actor_id,
        "rejection_reason": entity.rejection_reason,
        "paid_at": entity.paid_at,
        "paid_entry_id": entity.paid_entry_id,
        "version": entity.version,
    }


def _payout_entity(row: PayoutRequestRow) -> PayoutRequest:
    return PayoutRequest(
        payout_id=row.payout_id,
        merchant_id=row.merchant_id,
        method=PayoutMethod(row.method),
        amount=_money(row.amount_minor_units, row.currency),
        status=PayoutStatus(row.status),
        destination_reference=row.destination_reference,
        requested_at=row.requested_at,
        requested_by_principal_id=row.requested_by_principal_id,
        decided_at=row.decided_at,
        decided_by_actor_id=row.decided_by_actor_id,
        rejection_reason=row.rejection_reason,
        paid_at=row.paid_at,
        paid_entry_id=row.paid_entry_id,
        version=row.version,
    )


def _reconciliation_values(entity: RouteReconciliation) -> dict:
    return {
        "driver_principal_id": entity.driver_principal_id,
        "route_day": entity.route_day,
        "expected_minor_units": entity.expected.minor_units,
        "counted_minor_units": entity.counted.minor_units,
        "currency": entity.expected.currency.value,
        "outcome": entity.outcome.value,
        "status": entity.status.value,
        "parcels_returned_confirmed": entity.parcels_returned_confirmed,
        "cash_settled_confirmed": entity.cash_settled_confirmed,
        "opened_at": entity.opened_at,
        "resolved_at": entity.resolved_at,
        "resolved_by_actor_id": entity.resolved_by_actor_id,
        "resolution_note": entity.resolution_note,
        "adjustment_entry_id": entity.adjustment_entry_id,
        "version": entity.version,
    }


def _reconciliation_entity(row: RouteReconciliationRow) -> RouteReconciliation:
    return RouteReconciliation(
        reconciliation_id=row.reconciliation_id,
        driver_principal_id=row.driver_principal_id,
        route_day=row.route_day,
        expected=_money(row.expected_minor_units, row.currency),
        counted=_money(row.counted_minor_units, row.currency),
        outcome=ReconciliationOutcome(row.outcome),
        status=ReconciliationStatus(row.status),
        parcels_returned_confirmed=row.parcels_returned_confirmed,
        cash_settled_confirmed=row.cash_settled_confirmed,
        opened_at=row.opened_at,
        resolved_at=row.resolved_at,
        resolved_by_actor_id=row.resolved_by_actor_id,
        resolution_note=row.resolution_note,
        adjustment_entry_id=row.adjustment_entry_id,
        version=row.version,
    )


def _outbox_values(record: OutboxRecord) -> dict:
    return {
        "id": record.id,
        "event_id": record.event_id,
        "subject": record.subject,
        "event_type": record.event_type,
        "event_version": record.event_version,
        "aggregate_id": record.aggregate_id,
        "aggregate_version": record.aggregate_version,
        "payload_json": record.payload_json,
        "status": record.status.value,
        "attempt_count": record.attempt_count,
        "max_attempts": record.max_attempts,
        "next_attempt_at": record.next_attempt_at,
        "processing_owner": record.processing_owner,
        "processing_until": record.processing_until,
        "published_at": record.published_at,
        "last_error_code": record.last_error_code,
        "last_error_message": record.last_error_message,
        "created_at": record.created_at,
    }


def _outbox_entity(row: IntegrationOutboxRow) -> OutboxRecord:
    return OutboxRecord(
        id=row.id,
        event_id=row.event_id,
        subject=row.subject,
        event_type=row.event_type,
        event_version=row.event_version,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        payload_json=row.payload_json,
        status=OutboxStatus(row.status),
        attempt_count=row.attempt_count,
        max_attempts=row.max_attempts,
        next_attempt_at=row.next_attempt_at,
        processing_owner=row.processing_owner,
        processing_until=row.processing_until,
        published_at=row.published_at,
        last_error_code=row.last_error_code,
        last_error_message=row.last_error_message,
        created_at=row.created_at,
    )


def _inbox_values(record: InboxRecord) -> dict:
    return {
        "inbox_id": record.inbox_id,
        "consumer_name": record.consumer_name,
        "event_id": record.event_id,
        "event_type": record.event_type,
        "event_version": record.event_version,
        "status": record.status.value,
        "received_at": record.received_at,
        "attempt_count": record.attempt_count,
        "processing_started_at": record.processing_started_at,
        "processing_lease_until": record.processing_lease_until,
        "processed_at": record.processed_at,
        "last_error_code": record.last_error_code,
        "payload_json": record.payload_json,
    }


def _inbox_update_values(record: InboxRecord) -> dict:
    values = _inbox_values(record)
    for immutable in ("inbox_id", "consumer_name", "event_id"):
        values.pop(immutable)
    return values


def _inbox_entity(row: IntegrationInboxRow) -> InboxRecord:
    return InboxRecord(
        inbox_id=row.inbox_id,
        consumer_name=row.consumer_name,
        event_id=row.event_id,
        event_type=row.event_type,
        event_version=row.event_version,
        status=InboxStatus(row.status),
        received_at=row.received_at,
        attempt_count=row.attempt_count,
        processing_started_at=row.processing_started_at,
        processing_lease_until=row.processing_lease_until,
        processed_at=row.processed_at,
        last_error_code=row.last_error_code,
        payload_json=row.payload_json,
    )
