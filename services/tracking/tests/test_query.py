"""Timeline query-port tests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from conftest import SHIPMENT_ID, SOURCE_PK, a1_event_id

from tracking.application.coordinator import TimelineConsumerCoordinator
from tracking.application.query import TimelineQueryService
from tracking.domain.types import Delivery
from tracking.infrastructure.memory import MemoryTrackingStore


def _query_service(store: MemoryTrackingStore, *, max_page_size: int = 2) -> TimelineQueryService:
    return TimelineQueryService(query_port=store, max_page_size=max_page_size)


def test_get_by_event_id(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
) -> None:
    coordinator.handle(make_delivery())
    service = _query_service(store, max_page_size=10)
    entry = service.get_by_event_id(a1_event_id())
    assert entry is not None
    assert entry.source_pk == SOURCE_PK
    assert entry.shipment_id == SHIPMENT_ID
    assert service.get_by_event_id(UUID("00000000-0000-4000-8000-000000000099")) is None


def test_list_by_shipment_id_orders_by_occurred_at_then_event_id(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    coordinator.handle(make_delivery())

    middle_pk = UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
    middle_event_id = a1_event_id(source_pk=middle_pk)
    coordinator.handle(
        make_delivery(
            make_envelope(
                source_pk=middle_pk,
                event_id=str(middle_event_id),
                payload=make_payload(
                    source_pk=middle_pk,
                    occurred_at="2026-08-30T12:00:00.000Z",
                    new_status="IN_TRANSIT",
                ),
            )
        )
    )

    latest_pk = UUID("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff")
    latest_event_id = a1_event_id(source_pk=latest_pk)
    coordinator.handle(
        make_delivery(
            make_envelope(
                source_pk=latest_pk,
                event_id=str(latest_event_id),
                payload=make_payload(
                    source_pk=latest_pk,
                    occurred_at="2026-08-30T12:00:00.000Z",
                    new_status="DELIVERED",
                ),
            )
        )
    )

    service = _query_service(store, max_page_size=10)
    page = service.list_by_shipment_id(shipment_id=SHIPMENT_ID, page_size=10)
    assert len(page.entries) == 3
    ordering_keys = [(entry.occurred_at, entry.event_id) for entry in page.entries]
    assert ordering_keys == sorted(ordering_keys)
    assert page.entries[0].source_pk == SOURCE_PK
    assert page.entries[1].event_id == min(middle_event_id, latest_event_id)
    assert page.entries[2].event_id == max(middle_event_id, latest_event_id)


def test_bounded_pagination_with_timeline_query_service(
    coordinator: TimelineConsumerCoordinator,
    store: MemoryTrackingStore,
    make_delivery: Callable[..., Delivery],
    make_envelope: Callable[..., dict[str, Any]],
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    coordinator.handle(make_delivery())

    for index, (source_pk, occurred_at) in enumerate(
        [
            (UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"), "2026-08-30T12:00:00.000Z"),
            (UUID("bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"), "2026-08-30T13:00:00.000Z"),
            (UUID("cccccccc-dddd-4eee-8fff-000000000001"), "2026-08-30T14:00:00.000Z"),
        ],
        start=1,
    ):
        event_id = a1_event_id(source_pk=source_pk)
        coordinator.handle(
            make_delivery(
                make_envelope(
                    source_pk=source_pk,
                    event_id=str(event_id),
                    payload=make_payload(
                        source_pk=source_pk,
                        occurred_at=occurred_at,
                        new_status=f"STATUS_{index}",
                    ),
                )
            )
        )

    service = _query_service(store, max_page_size=2)
    first_page = service.list_by_shipment_id(shipment_id=SHIPMENT_ID, page_size=100)
    assert len(first_page.entries) == 2
    assert first_page.next_cursor is not None

    second_page = service.list_by_shipment_id(
        shipment_id=SHIPMENT_ID,
        cursor=first_page.next_cursor,
        page_size=100,
    )
    assert len(second_page.entries) == 2
    assert second_page.next_cursor is None

    all_event_ids = {
        entry.event_id for page in (first_page, second_page) for entry in page.entries
    }
    assert len(all_event_ids) == 4
