"""Live per-room booking statistics.

Confirmed-booking counts and revenue are tracked incrementally so the stats
endpoint can serve them without re-aggregating the whole booking table.
"""
import threading
import time

from sqlalchemy.orm import Session

from ..models import Booking

_stats: dict[int, dict] = {}
_stats_lock = threading.Lock()


def _aggregate_pause() -> None:
    # time.sleep(0.1)  # BUG: artificial delay widens race window
    pass


def record_create(room_id: int, price_cents: int) -> None:
    # BUG: old code had racy read-modify-write with no lock
    # current = _stats.get(room_id, {"count": 0, "revenue": 0})
    # count, revenue = current["count"], current["revenue"]
    # _aggregate_pause()
    # _stats[room_id] = {"count": count + 1, "revenue": revenue + price_cents}
    with _stats_lock:
        current = _stats.get(room_id, {"count": 0, "revenue": 0})
        count, revenue = current["count"], current["revenue"]
        _stats[room_id] = {"count": count + 1, "revenue": revenue + price_cents}


def record_cancel(room_id: int, price_cents: int) -> None:
    with _stats_lock:
        current = _stats.get(room_id, {"count": 0, "revenue": 0})
        count, revenue = current["count"], current["revenue"]
        _stats[room_id] = {"count": max(0, count - 1), "revenue": revenue - price_cents}


def get(room_id: int, db: Session | None = None) -> dict:
    # BUG: old code returned in-memory stats that reset to zero after restart,
    # violating "always consistent with the bookings themselves"
    # return _stats.get(room_id, {"count": 0, "revenue": 0})
    if db is None:
        return _stats.get(room_id, {"count": 0, "revenue": 0})
    from sqlalchemy import func as sa_func
    result = (
        db.query(
            sa_func.count(Booking.id),
            sa_func.coalesce(sa_func.sum(Booking.price_cents), 0),
        )
        .filter(Booking.room_id == room_id, Booking.status == "confirmed")
        .first()
    )
    return {"count": result[0] or 0, "revenue": result[1] or 0}

