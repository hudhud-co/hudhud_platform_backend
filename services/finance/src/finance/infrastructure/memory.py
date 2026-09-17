"""In-memory adapters for the Finance service.

The ledger double is the one worth reading: like the real table it is **append-only**,
and it refuses a second entry under the same idempotency key, because that is the
property the services rely on to make a retry safe.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from uuid import UUID

from finance.domain.entities import (
    CodCollection,
    Deposit,
    DriverCashAccount,
    MerchantAccount,
    PayoutRequest,
    RouteReconciliation,
)
from finance.domain.ledger import JournalEntry
from finance.domain.messaging import InboxRecord, OutboxRecord, OutboxStatus
from finance.domain.value_objects import AccountRef, DepositStatus, ReconciliationStatus


class InMemoryLedgerRepository:
    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []
        self._by_id: dict[UUID, JournalEntry] = {}
        self._by_key: dict[str, JournalEntry] = {}

    def append(self, entry: JournalEntry) -> None:
        if entry.entry_id in self._by_id:
            msg = f"journal entry {entry.entry_id} is already recorded"
            raise ValueError(msg)
        if entry.idempotency_key and entry.idempotency_key in self._by_key:
            msg = f"idempotency key already used: {entry.idempotency_key}"
            raise ValueError(msg)
        self._entries.append(entry)
        self._by_id[entry.entry_id] = entry
        if entry.idempotency_key:
            self._by_key[entry.idempotency_key] = entry

    def get(self, entry_id: UUID) -> JournalEntry | None:
        return self._by_id.get(entry_id)

    def find_by_idempotency_key(self, key: str) -> JournalEntry | None:
        return self._by_key.get(key)

    def entries_for_account(self, account: AccountRef) -> Iterable[JournalEntry]:
        return tuple(entry for entry in self._entries if entry.touches(account))

    def entries_for_subject(
        self, subject_kind: str, subject_id: UUID
    ) -> tuple[JournalEntry, ...]:
        return tuple(
            entry
            for entry in self._entries
            if entry.subject_kind == subject_kind and entry.subject_id == subject_id
        )

    @property
    def all_entries(self) -> tuple[JournalEntry, ...]:
        return tuple(self._entries)


class InMemoryDriverAccountRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, DriverCashAccount] = {}

    def save(self, account: DriverCashAccount) -> None:
        self._by_id[account.account_id] = account

    def get(self, account_id: UUID) -> DriverCashAccount | None:
        return self._by_id.get(account_id)

    def find_for_driver(self, driver_principal_id: UUID) -> DriverCashAccount | None:
        for account in self._by_id.values():
            if account.driver_principal_id == driver_principal_id:
                return account
        return None

    def list_all(self) -> tuple[DriverCashAccount, ...]:
        return tuple(self._by_id.values())


class InMemoryMerchantAccountRepository:
    def __init__(self) -> None:
        self._by_merchant: dict[UUID, MerchantAccount] = {}

    def save(self, account: MerchantAccount) -> None:
        self._by_merchant[account.merchant_id] = account

    def find_for_merchant(self, merchant_id: UUID) -> MerchantAccount | None:
        return self._by_merchant.get(merchant_id)


class InMemoryCollectionRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, CodCollection] = {}

    def save(self, collection: CodCollection) -> None:
        self._by_id[collection.collection_id] = collection

    def get(self, collection_id: UUID) -> CodCollection | None:
        return self._by_id.get(collection_id)

    def find_by_tracking_code(self, tracking_code: str) -> CodCollection | None:
        for collection in self._by_id.values():
            if collection.tracking_code == tracking_code:
                return collection
        return None

    def list_unsettled_for_driver(
        self, driver_principal_id: UUID
    ) -> tuple[CodCollection, ...]:
        return tuple(
            collection
            for collection in self._by_id.values()
            if collection.driver_principal_id == driver_principal_id
            and collection.settled_at is None
        )


class InMemoryDepositRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, Deposit] = {}

    def save(self, deposit: Deposit) -> None:
        self._by_id[deposit.deposit_id] = deposit

    def get(self, deposit_id: UUID) -> Deposit | None:
        return self._by_id.get(deposit_id)

    def find_by_reference(self, reference: str) -> Deposit | None:
        for deposit in self._by_id.values():
            if deposit.reference == reference:
                return deposit
        return None

    def list_for_driver(self, driver_principal_id: UUID) -> tuple[Deposit, ...]:
        return tuple(
            deposit
            for deposit in self._by_id.values()
            if deposit.driver_principal_id == driver_principal_id
        )

    def list_pending_verification(self) -> tuple[Deposit, ...]:
        return tuple(
            deposit
            for deposit in self._by_id.values()
            if deposit.status is DepositStatus.PENDING_VERIFICATION
        )


class InMemoryPayoutRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, PayoutRequest] = {}

    def save(self, payout: PayoutRequest) -> None:
        self._by_id[payout.payout_id] = payout

    def get(self, payout_id: UUID) -> PayoutRequest | None:
        return self._by_id.get(payout_id)

    def list_for_merchant(self, merchant_id: UUID) -> tuple[PayoutRequest, ...]:
        return tuple(
            payout
            for payout in self._by_id.values()
            if payout.merchant_id == merchant_id
        )

    def list_open(self) -> tuple[PayoutRequest, ...]:
        return tuple(p for p in self._by_id.values() if p.is_open)


class InMemoryReconciliationRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, RouteReconciliation] = {}

    def save(self, reconciliation: RouteReconciliation) -> None:
        self._by_id[reconciliation.reconciliation_id] = reconciliation

    def get(self, reconciliation_id: UUID) -> RouteReconciliation | None:
        return self._by_id.get(reconciliation_id)

    def find_for_driver_day(
        self, driver_principal_id: UUID, route_day: date
    ) -> RouteReconciliation | None:
        for reconciliation in self._by_id.values():
            if (
                reconciliation.driver_principal_id == driver_principal_id
                and reconciliation.route_day == route_day
            ):
                return reconciliation
        return None

    def list_unresolved(self) -> tuple[RouteReconciliation, ...]:
        return tuple(
            r
            for r in self._by_id.values()
            if r.status is not ReconciliationStatus.RESOLVED
        )

    def list_unresolved_for_driver(
        self, driver_principal_id: UUID
    ) -> tuple[RouteReconciliation, ...]:
        return tuple(
            r
            for r in self._by_id.values()
            if r.driver_principal_id == driver_principal_id
            and r.status is not ReconciliationStatus.RESOLVED
        )


class InMemoryOutboxRepository:
    def __init__(self) -> None:
        self._records: list[OutboxRecord] = []

    def insert(self, record: OutboxRecord) -> None:
        if any(r.event_id == record.event_id for r in self._records):
            msg = f"duplicate outbox event_id: {record.event_id}"
            raise ValueError(msg)
        if any(
            r.aggregate_id == record.aggregate_id
            and r.aggregate_version == record.aggregate_version
            for r in self._records
        ):
            msg = (
                "duplicate outbox aggregate version: "
                f"{record.aggregate_id}@{record.aggregate_version}"
            )
            raise ValueError(msg)
        self._records.append(record)

    def get_by_event_id(self, event_id: UUID) -> OutboxRecord | None:
        for record in self._records:
            if record.event_id == event_id:
                return record
        return None

    def list_pending(self) -> tuple[OutboxRecord, ...]:
        return tuple(r for r in self._records if r.status is OutboxStatus.PENDING)

    def list_for_aggregate(self, aggregate_id: UUID) -> tuple[OutboxRecord, ...]:
        return tuple(
            sorted(
                (r for r in self._records if r.aggregate_id == aggregate_id),
                key=lambda r: r.aggregate_version,
            )
        )


class InMemoryInboxRepository:
    def __init__(self) -> None:
        self._by_key: dict[tuple[str, UUID], InboxRecord] = {}

    def find(self, consumer_name: str, event_id: UUID) -> InboxRecord | None:
        return self._by_key.get((consumer_name, event_id))

    def insert(self, record: InboxRecord) -> None:
        key = (record.consumer_name, record.event_id)
        if key in self._by_key:
            msg = f"duplicate inbox record: {key}"
            raise ValueError(msg)
        self._by_key[key] = record

    def save(self, record: InboxRecord) -> None:
        self._by_key[(record.consumer_name, record.event_id)] = record


class InMemoryDatabase:
    """The data. Shared by every request, exactly like a real database is.

    Split from the unit of work on purpose. A unit of work is *one request's*
    transaction; the rows outlive it. Keeping both in one object is what let a single
    shared instance serve every request, so two concurrent requests fought over one
    session and 39 of 40 failed with "transaction already active".
    """

    def __init__(self) -> None:
        self.ledger = InMemoryLedgerRepository()
        self.driver_accounts = InMemoryDriverAccountRepository()
        self.merchant_accounts = InMemoryMerchantAccountRepository()
        self.collections = InMemoryCollectionRepository()
        self.deposits = InMemoryDepositRepository()
        self.payouts = InMemoryPayoutRepository()
        self.reconciliations = InMemoryReconciliationRepository()
        self.outbox = InMemoryOutboxRepository()
        self.inbox = InMemoryInboxRepository()


class InMemoryUnitOfWork:
    """One request's transaction over a shared :class:`InMemoryDatabase`.

    Rollback does not undo in-memory mutations, and says so: pretending to would make
    this double disagree with SQLAlchemy in a way that hides bugs rather than exposing
    them. Tests that care about rollback run against PostgreSQL.

    What it *does* copy exactly is the transaction discipline — ``begin`` refuses to open
    a second transaction on the same instance, just as the SQLAlchemy store does. A
    double that is more permissive than the thing it stands in for is worse than no
    double: this defect reached PostgreSQL precisely because the in-memory version
    shrugged at the misuse that the real store rejects.
    """

    def __init__(self, database: InMemoryDatabase | None = None) -> None:
        self._database = database or InMemoryDatabase()
        self._active = False
        self.depth = 0
        self.commits = 0
        self.rollbacks = 0

    @property
    def database(self) -> InMemoryDatabase:
        """The shared rows, so a factory can hand the next request the same data."""
        return self._database

    def new_unit_of_work(self) -> InMemoryUnitOfWork:
        """Another transaction over the same rows — one per request."""
        return InMemoryUnitOfWork(self._database)

    # The repositories are the database's; the transaction is this object's.
    @property
    def ledger(self) -> InMemoryLedgerRepository:
        return self._database.ledger

    @property
    def driver_accounts(self) -> InMemoryDriverAccountRepository:
        return self._database.driver_accounts

    @property
    def merchant_accounts(self) -> InMemoryMerchantAccountRepository:
        return self._database.merchant_accounts

    @property
    def collections(self) -> InMemoryCollectionRepository:
        return self._database.collections

    @property
    def deposits(self) -> InMemoryDepositRepository:
        return self._database.deposits

    @property
    def payouts(self) -> InMemoryPayoutRepository:
        return self._database.payouts

    @property
    def reconciliations(self) -> InMemoryReconciliationRepository:
        return self._database.reconciliations

    @property
    def outbox(self) -> InMemoryOutboxRepository:
        return self._database.outbox

    @property
    def inbox(self) -> InMemoryInboxRepository:
        return self._database.inbox

    def begin(self) -> None:
        if self._active:
            # The same message the SQLAlchemy store raises, so a leaked transaction
            # fails identically in a unit test and in production.
            msg = "transaction already active"
            raise RuntimeError(msg)
        self._active = True
        self.depth += 1

    def commit(self) -> None:
        if not self._active:
            msg = "commit without transaction"
            raise RuntimeError(msg)
        self._active = False
        self.depth = max(0, self.depth - 1)
        self.commits += 1

    def rollback(self) -> None:
        self._active = False
        self.depth = max(0, self.depth - 1)
        self.rollbacks += 1
