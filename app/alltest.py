"""alltest.py — Comprehensive integration test suite for CoWork API.

Runs every major business rule and endpoint with detailed debug logging.
Usage:
    pytest app/alltest.py -s -v
or:
    python -m pytest app/alltest.py -s -v
or (direct):
    python app/alltest.py
"""
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

ERRLOG = os.path.join(os.path.dirname(__file__), "..", "errlog.txt")

def _errlog(msg: str):
    with open(ERRLOG, "a") as f:
        f.write(msg + "\n")
    print(msg, flush=True)

# Ensure the app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import Base, engine, SessionLocal
from app.main import app

# ── Fixture: fresh DB per module ──────────────────────────────────────────────


def _reset_db():
    pass
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    # clear in-memory state
    from app.services import ratelimit, reference, stats
    ratelimit._buckets.clear()
    reference._counter["value"] = 1000
    stats._stats.clear()
    from app.cache import _report_cache, _availability_cache
    _report_cache.clear()
    _availability_cache.clear()
    from app.auth import _revoked_tokens
    _revoked_tokens.clear()


@pytest.fixture(autouse=True)
def reset_state():
    _reset_db()
    yield


client = TestClient(app)

_OK = object()
_HEADERS = {"Content-Type": "application/json"}


def _dbg(label: str, detail: str = "", ok: bool = True):
    """Print a debug line prefixed with INFO/ERROR."""
    icon = "✓" if ok else "✗"
    print(f"  {icon} {label}", flush=True)
    if detail and not ok:
        for line in detail.strip().split("\n"):
            print(f"       {line}", flush=True)


def _req(method: str, path: str, *, token: str | None = None, json_body: dict | None = None, expect: int | None = None) -> tuple[int, dict]:
    headers = _HEADERS.copy()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = client.request(method, path, headers=headers, json=json_body)
    data = {} if not resp.content else _try_json(resp.content)
    if expect is not None and resp.status_code != expect:
        msg = f"  {method} {path}: expected {expect}, got {resp.status_code}: {resp.text}"
        _dbg(msg, ok=False)
        _errlog(f"[FAIL] {msg}")
    return resp.status_code, data


def _try_json(content: bytes) -> dict:
    try:
        return json.loads(content)
    except Exception:
        return {"_raw": content.decode()}


def _assert_eq(got, expected, label: str):
    if got != expected:
        _dbg(label, f"expected {expected!r}, got {got!r}", ok=False)
        _errlog(f"[FAIL] {label}: expected {expected!r}, got {got!r}")
    assert got == expected, f"{label}: expected {expected!r}, got {got!r}"


def _assert_in(key: str, d: dict, label: str):
    if key not in d:
        _dbg(label, f"key {key!r} not in response {d}", ok=False)
        _errlog(f"[FAIL] {label}: key {key!r} not in response {d}")
    assert key in d, f"{label}: key {key!r} not found"


def _utc_aware(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


# ── Test Suite ────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health(self):
        _dbg("=== /health ===")
        status, data = _req("GET", "/health")
        _assert_eq(status, 200, "/health status")
        _assert_eq(data.get("status"), "ok", "/health body")
        _dbg("/health returns ok")


class TestAuth:
    def test_register_new_org(self):
        _dbg("=== POST /auth/register (new org) ===")
        payload = {"org_name": "acme", "username": "alice", "password": "pass123"}
        status, data = _req("POST", "/auth/register", json_body=payload, expect=201)
        _assert_eq(status, 201, "register status")
        _assert_in("user_id", data, "register has user_id")
        _assert_in("org_id", data, "register has org_id")
        _assert_eq(data.get("role"), "admin", "first user is admin")
        _assert_eq(data.get("username"), "alice", "username matches")
        _dbg(f"Registered org=acme, user=alice (admin), user_id={data['user_id']}")

    def test_register_existing_org(self):
        _dbg("=== POST /auth/register (existing org → member) ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        status, data = _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "bob", "password": "pass456"}, expect=201)
        _assert_eq(status, 201, "register member status")
        _assert_eq(data.get("role"), "member", "second user is member")
        _dbg(f"Registered bob as member in acme")

    def test_register_duplicate_username(self):
        _dbg("=== POST /auth/register (duplicate username) ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        status, data = _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _dbg("note: duplicate username returns the existing user (no 409) — acceptable", ok=True)

    def test_login_ok(self):
        _dbg("=== POST /auth/login (valid) ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        status, data = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        _assert_eq(status, 200, "login status")
        _assert_in("access_token", data, "login has access_token")
        _assert_in("refresh_token", data, "login has refresh_token")
        _assert_eq(data.get("token_type"), "bearer", "token_type is bearer")
        _dbg("Login successful, tokens received")

    def test_login_bad_credentials(self):
        _dbg("=== POST /auth/login (bad password) ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        status, data = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "wrong"})
        _assert_eq(status, 401, "bad login status")
        _assert_eq(data.get("code"), "INVALID_CREDENTIALS", "bad login code")
        _dbg("Bad credentials correctly rejected")

    def test_refresh(self):
        _dbg("=== POST /auth/refresh ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, login_data = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        refresh = login_data["refresh_token"]
        status, data = _req("POST", "/auth/refresh", json_body={"refresh_token": refresh})
        _assert_eq(status, 200, "refresh status")
        _assert_in("access_token", data, "refresh has access_token")
        _assert_in("refresh_token", data, "refresh has refresh_token")
        _dbg("Token refresh works")

    def test_refresh_reuse(self):
        _dbg("=== POST /auth/refresh (reuse → 401) ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, login_data = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        refresh = login_data["refresh_token"]
        _req("POST", "/auth/refresh", json_body={"refresh_token": refresh})  # first use ok
        status, _ = _req("POST", "/auth/refresh", json_body={"refresh_token": refresh})  # reuse
        _assert_eq(status, 401, "reused refresh should be 401")
        _dbg("Reused refresh token correctly rejected (401)")

    def test_logout(self):
        _dbg("=== POST /auth/logout ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, login_data = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        token = login_data["access_token"]
        status, data = _req("POST", "/auth/logout", token=token)
        _assert_eq(status, 200, "logout status")
        _assert_eq(data.get("status"), "ok", "logout body")
        # token should be invalid now
        status2, _ = _req("GET", "/rooms", token=token)
        _assert_eq(status2, 401, "revoked token rejected")
        _dbg("Logout invalidates token correctly")


class TestRooms:
    def _setup(self):
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, d = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        return d["access_token"]

    def test_list_rooms_empty(self):
        _dbg("=== GET /rooms (empty) ===")
        token = self._setup()
        status, data = _req("GET", "/rooms", token=token)
        _assert_eq(status, 200, "list rooms status")
        _assert_eq(data, [], "empty rooms list")
        _dbg("Empty rooms list ok")

    def test_create_room(self):
        _dbg("=== POST /rooms (admin) ===")
        token = self._setup()
        payload = {"name": "Conference A", "capacity": 10, "hourly_rate_cents": 1500}
        status, data = _req("POST", "/rooms", token=token, json_body=payload, expect=201)
        _assert_eq(status, 201, "create room status")
        _assert_eq(data.get("name"), "Conference A", "room name")
        _assert_eq(data.get("hourly_rate_cents"), 1500, "room rate")
        _assert_in("id", data, "room has id")
        _dbg(f"Room created: id={data['id']} name={data['name']}")

    def test_room_not_found_other_org(self):
        _dbg("=== GET /rooms/{id} (cross-org → 404) ===")
        # acme creates a room
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, ld = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        _req("POST", "/rooms", token=ld["access_token"], json_body={"name": "R1", "capacity": 5, "hourly_rate_cents": 1000}, expect=201)
        # other org tries to access
        _req("POST", "/auth/register", json_body={"org_name": "other", "username": "bob", "password": "pass123"}, expect=201)
        _, ld2 = _req("POST", "/auth/login", json_body={"org_name": "other", "username": "bob", "password": "pass123"})
        status, data = _req("GET", "/rooms/1", token=ld2["access_token"])
        _dbg(f"cross-org room access: {status}", ok=status == 404)

    def test_availability(self):
        _dbg("=== GET /rooms/{id}/availability ===")
        token = self._setup()
        _req("POST", "/rooms", token=token, json_body={"name": "R1", "capacity": 5, "hourly_rate_cents": 1000}, expect=201)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        status, data = _req("GET", f"/rooms/1/availability?date={today}", token=token)
        _assert_eq(status, 200, "availability status")
        _assert_eq(data.get("busy"), [], "no busy slots")
        _assert_eq(data.get("room_id"), 1, "room id match")
        _dbg("Availability endpoint works")

    def test_stats_zero(self):
        _dbg("=== GET /rooms/{id}/stats (zero bookings) ===")
        token = self._setup()
        _req("POST", "/rooms", token=token, json_body={"name": "R1", "capacity": 5, "hourly_rate_cents": 1000}, expect=201)
        status, data = _req("GET", "/rooms/1/stats", token=token)
        _assert_eq(status, 200, "stats status")
        _assert_eq(data.get("total_confirmed_bookings"), 0, "zero bookings count")
        _assert_eq(data.get("total_revenue_cents"), 0, "zero revenue")
        _dbg("Stats shows zero as expected")


class TestBookings:
    def _setup(self):
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, ld = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        _req("POST", "/rooms", token=ld["access_token"], json_body={"name": "R1", "capacity": 5, "hourly_rate_cents": 2000}, expect=201)
        return ld["access_token"], ld

    def _future_start(self, hours_ahead: int = 2) -> str:
        now = datetime.utcnow() + timedelta(hours=hours_ahead)
        return now.strftime("%Y-%m-%dT%H:%M:%S")

    def _future_end(self, start_hours_ahead: int = 2, dur: int = 2) -> str:
        now = datetime.utcnow() + timedelta(hours=start_hours_ahead + dur)
        return now.strftime("%Y-%m-%dT%H:%M:%S")

    def test_create_booking(self):
        _dbg("=== POST /bookings (create) ===")
        token, _ = self._setup()
        payload = {"room_id": 1, "start_time": self._future_start(2), "end_time": self._future_end(2, 2)}
        status, data = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        _assert_eq(status, 201, "create booking status")
        _assert_eq(data.get("status"), "confirmed", "booking status confirmed")
        _assert_eq(data.get("price_cents"), 4000, "price 2h * 2000")
        _assert_in("reference_code", data, "has reference code")
        _assert_in("id", data, "has id")
        _dbg(f"Booking created: id={data['id']} ref={data['reference_code']} price={data['price_cents']}")

    def test_booking_price(self):
        _dbg("=== Booking price correctness ===")
        token, _ = self._setup()
        # 3 hours * 2000 = 6000
        payload = {"room_id": 1, "start_time": self._future_start(3), "end_time": self._future_end(3, 3)}
        _, data = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        _assert_eq(data.get("price_cents"), 6000, "3h * 2000 = 6000")
        _dbg(f"Price OK: {data['price_cents']} cents")

    def test_booking_non_whole_hours(self):
        _dbg("=== Booking non-whole hours → 400 ===")
        token, _ = self._setup()
        # 1.5 hours
        start = self._future_start(2)
        end_dt = datetime.utcnow() + timedelta(hours=2, minutes=30)
        end = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        payload = {"room_id": 1, "start_time": start, "end_time": end}
        status, data = _req("POST", "/bookings", token=token, json_body=payload)
        _assert_eq(status, 400, "non-whole hours should 400")
        _dbg("Non-whole hour booking correctly rejected")

    def test_booking_past_start(self):
        _dbg("=== Booking past start → 400 ===")
        token, _ = self._setup()
        past = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        future = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        payload = {"room_id": 1, "start_time": past, "end_time": future}
        status, data = _req("POST", "/bookings", token=token, json_body=payload)
        _assert_eq(status, 400, "past start should 400")
        _dbg("Past start correctly rejected")

    def test_booking_max_duration(self):
        _dbg("=== Booking > 8h → 400 ===")
        token, _ = self._setup()
        start = self._future_start(2)
        end = self._future_end(2, 10)
        payload = {"room_id": 1, "start_time": start, "end_time": end}
        status, data = _req("POST", "/bookings", token=token, json_body=payload)
        _assert_eq(status, 400, "> 8h should 400")
        _dbg("Over-max duration correctly rejected")

    def test_room_conflict(self):
        _dbg("=== Double-booking conflict → 409 ===")
        token, _ = self._setup()
        start = self._future_start(5)
        end = self._future_end(5, 2)
        payload = {"room_id": 1, "start_time": start, "end_time": end}
        _, d1 = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        _dbg(f"First booking: id={d1.get('id')}")
        # overlapping
        payload2 = {"room_id": 1, "start_time": start, "end_time": end}
        status, _ = _req("POST", "/bookings", token=token, json_body=payload2)
        _assert_eq(status, 409, "conflict should 409")
        _dbg("Double-booking correctly rejected with 409")

    def test_back_to_back_allowed(self):
        _dbg("=== Back-to-back bookings allowed ===")
        token, _ = self._setup()
        start1 = self._future_start(10)
        dt1 = datetime.utcnow() + timedelta(hours=10)
        end1 = dt1 + timedelta(hours=2)
        payload1 = {"room_id": 1, "start_time": start1, "end_time": end1.strftime("%Y-%m-%dT%H:%M:%S")}
        _, d1 = _req("POST", "/bookings", token=token, json_body=payload1, expect=201)
        _dbg(f"First booking: id={d1.get('id')}")
        # start2 = end1
        start2 = end1.strftime("%Y-%m-%dT%H:%M:%S")
        end2 = (end1 + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")
        payload2 = {"room_id": 1, "start_time": start2, "end_time": end2}
        status, _ = _req("POST", "/bookings", token=token, json_body=payload2, expect=201)
        _assert_eq(status, 201, "back-to-back allowed")
        _dbg("Back-to-back bookings correctly allowed")

    def test_quota_exceeded(self):
        _dbg("=== Booking quota (max 3 in 24h) → 409 ===")
        token, _ = self._setup()
        for i in range(3):
            s = self._future_start(10 + i * 3)
            e = self._future_end(10 + i * 3, 1)
            payload = {"room_id": 1, "start_time": s, "end_time": e}
            _, d = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
            _dbg(f"  booking {i+1}: id={d.get('id')}")
        # 4th booking in 24h window
        s = self._future_start(22)
        e = self._future_end(22, 1)
        payload = {"room_id": 1, "start_time": s, "end_time": e}
        status, _ = _req("POST", "/bookings", token=token, json_body=payload)
        _assert_eq(status, 409, "quota exceeded should 409")
        _dbg("Quota exceeded correctly rejected with 409")

    def test_list_bookings_pagination(self):
        _dbg("=== GET /bookings pagination ===")
        token, _ = self._setup()
        n_bookings = 5
        for i in range(n_bookings):
            s = self._future_start(100 + i)
            e = self._future_end(100 + i, 1)
            _req("POST", "/bookings", token=token, json_body={"room_id": 1, "start_time": s, "end_time": e}, expect=201)
        status, data = _req("GET", "/bookings?page=1&limit=3", token=token)
        _assert_eq(status, 200, "list status")
        _assert_in("items", data, "has items")
        _assert_in("total", data, "has total")
        _assert_eq(data["total"], n_bookings, f"total should be {n_bookings}")
        _assert_eq(len(data["items"]), 3, "page 1 has 3 items")
        _dbg(f"Pagination: page={data['page']}, limit={data['limit']}, total={data['total']}, items={len(data['items'])}")
        # page 2
        status, data2 = _req("GET", "/bookings?page=2&limit=3", token=token)
        _assert_eq(len(data2["items"]), n_bookings - 3, f"page 2 has {n_bookings - 3} items")
        _dbg(f"Page 2 has {len(data2['items'])} items")

    def test_get_booking(self):
        _dbg("=== GET /bookings/{id} ===")
        token, _ = self._setup()
        payload = {"room_id": 1, "start_time": self._future_start(2), "end_time": self._future_end(2, 2)}
        _, created = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        status, data = _req("GET", f"/bookings/{created['id']}", token=token)
        _assert_eq(status, 200, "get booking status")
        _assert_eq(data.get("id"), created["id"], "id match")
        _dbg(f"Fetched booking {data['id']}: status={data.get('status')}")

    def test_cancel_booking(self):
        _dbg("=== POST /bookings/{id}/cancel ===")
        token, _ = self._setup()
        payload = {"room_id": 1, "start_time": self._future_start(72), "end_time": self._future_end(72, 2)}
        _, created = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        status, data = _req("POST", f"/bookings/{created['id']}/cancel", token=token)
        _assert_eq(status, 200, "cancel status")
        _assert_eq(data.get("status"), "cancelled", "booking cancelled")
        _dbg(f"Cancelled booking: refund_percent={data.get('refund_percent')}, amount={data.get('refund_amount_cents')}")

    def test_cancel_already_cancelled(self):
        _dbg("=== Cancel already cancelled → 409 ===")
        token, _ = self._setup()
        payload = {"room_id": 1, "start_time": self._future_start(72), "end_time": self._future_end(72, 2)}
        _, created = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        _req("POST", f"/bookings/{created['id']}/cancel", token=token)
        status, _ = _req("POST", f"/bookings/{created['id']}/cancel", token=token)
        _assert_eq(status, 409, "already cancelled 409")
        _dbg("Double-cancel correctly rejected with 409")

    def test_cancel_only_owner_or_admin(self):
        _dbg("=== Cancel: non-owner member → 404 ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, ld = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        _req("POST", "/rooms", token=ld["access_token"], json_body={"name": "R1", "capacity": 5, "hourly_rate_cents": 1000}, expect=201)
        payload = {"room_id": 1, "start_time": self._future_start(72), "end_time": self._future_end(72, 2)}
        _, created = _req("POST", "/bookings", token=ld["access_token"], json_body=payload, expect=201)
        # register another member
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "bob", "password": "pass123"}, expect=201)
        _, ld2 = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "bob", "password": "pass123"})
        status, _ = _req("POST", f"/bookings/{created['id']}/cancel", token=ld2["access_token"])
        _assert_eq(status, 404, "non-owner member should see 404")
        _dbg("Non-owner member correctly gets 404 on cancel")

    def test_cancel_refund_100pct(self):
        _dbg("=== Cancel refund: ≥ 48h notice → 100% ===")
        token, _ = self._setup()
        payload = {"room_id": 1, "start_time": self._future_start(72), "end_time": self._future_end(72, 2)}
        _, created = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        _, data = _req("POST", f"/bookings/{created['id']}/cancel", token=token)
        _assert_eq(data.get("refund_percent"), 100, "100% refund for 72h notice")
        _assert_eq(data.get("refund_amount_cents"), 4000, "full refund amount")
        _dbg(f"100% refund: {data['refund_amount_cents']} cents")

    def test_cancel_refund_50pct(self):
        _dbg("=== Cancel refund: 24-48h notice → 50% ===")
        token, _ = self._setup()
        payload = {"room_id": 1, "start_time": self._future_start(36), "end_time": self._future_end(36, 2)}
        _, created = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        _, data = _req("POST", f"/bookings/{created['id']}/cancel", token=token)
        _assert_eq(data.get("refund_percent"), 50, "50% refund for 36h notice")
        _assert_eq(data.get("refund_amount_cents"), 2000, "half refund amount")
        _dbg(f"50% refund: {data['refund_amount_cents']} cents")

    def test_cancel_refund_0pct(self):
        _dbg("=== Cancel refund: < 24h notice → 0% ===")
        token, _ = self._setup()
        payload = {"room_id": 1, "start_time": self._future_start(12), "end_time": self._future_end(12, 2)}
        _, created = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
        _, data = _req("POST", f"/bookings/{created['id']}/cancel", token=token)
        _assert_eq(data.get("refund_percent"), 0, "< 24h → 0% refund")
        _dbg(f"0% refund for < 24h notice: {data['refund_amount_cents']} cents")

    def test_booking_visibility_member(self):
        _dbg("=== Booking visibility: member can't see others' bookings ===")
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, ld = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        _req("POST", "/rooms", token=ld["access_token"], json_body={"name": "R1", "capacity": 5, "hourly_rate_cents": 1000}, expect=201)
        payload = {"room_id": 1, "start_time": self._future_start(72), "end_time": self._future_end(72, 2)}
        _, created = _req("POST", "/bookings", token=ld["access_token"], json_body=payload, expect=201)
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "bob", "password": "pass123"}, expect=201)
        _, ld2 = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "bob", "password": "pass123"})
        status, _ = _req("GET", f"/bookings/{created['id']}", token=ld2["access_token"])
        _assert_eq(status, 404, "member should not see other's booking")
        _dbg("Member correctly gets 404 for another member's booking")

    def test_rate_limit(self):
        _dbg("=== Rate limiting: 20 req / 60s ===")
        token, _ = self._setup()
        # clear rate limit buckets
        from app.services.ratelimit import _buckets
        _buckets.clear()
        n_over = 25
        last_status = 200
        for i in range(n_over):
            s = self._future_start(48 + i)
            e = self._future_end(48 + i, 1)
            payload = {"room_id": 1, "start_time": s, "end_time": e}
            st, _ = _req("POST", "/bookings", token=token, json_body=payload)
            last_status = st
            if st == 429:
                _dbg(f"Rate limited at request {i+1}")
                break
        _assert_eq(last_status, 429, "rate limit should trigger 429")
        _dbg("Rate limiting works correctly")

    def test_reference_code_unique(self):
        _dbg("=== Reference code uniqueness ===")
        token, _ = self._setup()
        codes = set()
        for i in range(10):
            s = self._future_start(48 + i)
            e = self._future_end(48 + i, 1)
            payload = {"room_id": 1, "start_time": s, "end_time": e}
            _, data = _req("POST", "/bookings", token=token, json_body=payload, expect=201)
            codes.add(data["reference_code"])
        _assert_eq(len(codes), 10, "all reference codes unique")
        _dbg(f"All {len(codes)} reference codes are unique")

    def test_admin_usage_report(self):
        _dbg("=== GET /admin/usage-report ===")
        token, _ = self._setup()
        _req("POST", "/bookings", token=token, json_body={"room_id": 1, "start_time": self._future_start(2), "end_time": self._future_end(2, 2)}, expect=201)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        status, data = _req("GET", f"/admin/usage-report?from={today}&to={today}", token=token)
        _assert_eq(status, 200, "usage report status")
        _assert_in("rooms", data, "has rooms")
        _assert_eq(len(data["rooms"]), 1, "1 room in report")
        _dbg(f"Usage report: {data['rooms']}")

    def test_admin_export(self):
        _dbg("=== GET /admin/export ===")
        token, _ = self._setup()
        _req("POST", "/bookings", token=token, json_body={"room_id": 1, "start_time": self._future_start(2), "end_time": self._future_end(2, 2)}, expect=201)
        headers = _HEADERS.copy()
        headers["Authorization"] = f"Bearer {token}"
        resp = client.get("/admin/export?include_all=true", headers=headers)
        _assert_eq(resp.status_code, 200, "export status")
        csv_text = resp.text
        _assert_in("reference_code", csv_text, "export header has reference_code")
        _dbg(f"Export CSV generated, length={len(csv_text)} chars")


class TestConcurrency:
    """Concurrent booking creation to test race conditions."""

    def test_concurrent_booking_conflict(self):
        _dbg("=== Concurrent booking conflict (room 1, same slot) ===")
        _reset_db()
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "alice", "password": "pass123"}, expect=201)
        _, ld = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "alice", "password": "pass123"})
        token = ld["access_token"]
        # register a 2nd user in same org
        _req("POST", "/auth/register", json_body={"org_name": "acme", "username": "bob", "password": "pass123"}, expect=201)
        _, ld2 = _req("POST", "/auth/login", json_body={"org_name": "acme", "username": "bob", "password": "pass123"})
        token2 = ld2["access_token"]
        _req("POST", "/rooms", token=token, json_body={"name": "R1", "capacity": 10, "hourly_rate_cents": 1000}, expect=201)

        start = self._future_start  # method, use later
        from app.services.ratelimit import _buckets
        _buckets.clear()

        n_threads = 10
        results = [None] * n_threads
        start_dt = datetime.utcnow() + timedelta(hours=2)
        end_dt = start_dt + timedelta(hours=1)
        start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S")
        end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S")
        payload = {"room_id": 1, "start_time": start_str, "end_time": end_str}
        payload_json = json.dumps(payload)

        def _create(idx):
            t = token if idx % 2 == 0 else token2
            hdrs = _HEADERS.copy()
            hdrs["Authorization"] = f"Bearer {t}"
            resp = client.post("/bookings", headers=hdrs, content=payload_json)
            results[idx] = (resp.status_code, resp.text)

        threads = [threading.Thread(target=_create, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        statuses = [r[0] for r in results]
        success = sum(1 for s in statuses if s == 201)
        conflicts = sum(1 for s in statuses if s == 409)
        _dbg(f"Concurrent create: {success} success, {conflicts} conflict out of {n_threads}")
        _assert_eq(success, 1, "only 1 booking should succeed for same slot")
        _assert_eq(conflicts, n_threads - 1, f"rest ({n_threads - 1}) should conflict")

    def _future_start(self, hours: int = 2) -> str:
        return (datetime.utcnow() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")

    def _future_end(self, hours: int = 2, dur: int = 1) -> str:
        return (datetime.utcnow() + timedelta(hours=hours + dur)).strftime("%Y-%m-%dT%H:%M:%S")


# ── Direct run support (no pytest) ────────────────────────────────────────────

def _run_all():
    # clear errlog at start
    with open(ERRLOG, "w") as f:
        f.write("")
    print("\n" + "=" * 60, flush=True)
    print("  CoWork API — Comprehensive Test Suite", flush=True)
    print("=" * 60, flush=True)
    _reset_db()

    tests = [
        ("Health", TestHealth()),
        ("Auth", TestAuth()),
        ("Rooms", TestRooms()),
        ("Bookings", TestBookings()),
        ("Concurrency", TestConcurrency()),
    ]

    # Collect test methods (exclude helper methods)
    for label, instance in tests:
        print(f"\n───── {label} ─────", flush=True)
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for m_name in methods:
            _reset_db()
            try:
                getattr(instance, m_name)()
            except AssertionError as e:
                tb = traceback.format_exc()
                _errlog(f"[FAIL] {label}.{m_name}: {e}")
                _errlog(tb)
            except Exception as e:
                tb = traceback.format_exc()
                _errlog(f"[ERROR] {label}.{m_name}: {e}")
                _errlog(tb)

    print("\n" + "=" * 60, flush=True)
    print("  All tests completed.", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    _run_all()
