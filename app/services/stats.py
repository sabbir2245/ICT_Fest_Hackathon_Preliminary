"""Live per-room booking statistics.

Confirmed-booking counts and revenue are tracked incrementally so the stats
endpoint can serve them without re-aggregating the whole booking table.
"""
import time

import threading

_stats = {}
_stats_lock = threading.Lock()


_stats: dict[int, dict] = {}


def _aggregate_pause() -> None:
    # time.sleep(0.1)  # BUG: artificial delay widens race window
    pass


def record_create(room_id: int, price_cents: int) -> None:
    with _stats_lock:# just adeed this
        
        current = _stats.get(room_id, {"count": 0, "revenue": 0})
        count, revenue = current["count"], current["revenue"]
        _aggregate_pause()
        _stats[room_id] = {"count": count + 1, "revenue": revenue + price_cents}


def record_cancel(room_id: int, price_cents: int) -> None:
    with _stats_lock: # just added this
        
        current = _stats.get(room_id, {"count": 0, "revenue": 0})
        count, revenue = current["count"], current["revenue"]
        _aggregate_pause()
        _stats[room_id] = {"count": max(0, count - 1), "revenue": revenue - price_cents}


def get(room_id: int) -> dict:
    with _stats_lock: # just added this
        
        return _stats.get(room_id, {"count": 0, "revenue": 0})
    # should I also stats lock here?

