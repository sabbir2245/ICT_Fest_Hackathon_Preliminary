# Verified Bugs ()


---

## 1. ✅ Token revocation checks `sub` instead of `jti` — `app/auth.py:97`

`revoke_access_token()` (line 86) stores `payload["jti"]` (unique per-token UUID hex) into `_revoked_tokens`. But `get_token_payload()` (line 97) checks `payload.get("sub")` (the user ID string, e.g. `"1"`). Since a user ID is never in `_revoked_tokens`, no token ever appears revoked — logout is ineffective.

**Fix:** Changed `payload.get("sub")` → `payload.get("jti")` at line 97.

**Status: FIXED** — `app/auth.py:97`

---

## 2. ✅ Refresh tokens are never invalidated — `app/routers/auth.py:81-93`

`POST /auth/refresh` returns a new token pair but does not track or invalidate the presented refresh token. The spec says: *"Refresh tokens are single-use: refreshing returns a new access and refresh token and invalidates the presented refresh token (reuse → 401)."* Reusing a refresh token currently succeeds (observed in test: expected 401, got 200).

**Fix:** Added `_used_refresh_tokens: set[str]` at line 21. In the `/refresh` handler, the old token's `jti` is checked against this set before issuing new tokens; after a successful refresh the `jti` is added so reuse returns 401.

**Status: FIXED** — `app/routers/auth.py:21,87-90`

---

## 3. ✅ Back-to-back bookings incorrectly conflict — `app/routers/bookings.py:50`

Overlap check uses non-strict `<=`:
```python
if b.start_time <= end and start <= b.end_time:
```
The spec says: *"Two confirmed bookings for the same room overlap iff existing.start < new.end AND new.start < existing.end"* — strict `<` for both sides. Back-to-back bookings (end of first == start of second) must be allowed.

**Fix:** `b.start_time < end and start < b.end_time`

**Status: FIXED** — `app/routers/bookings.py:50-51`

---

## 4. ✅ Cancel refund logic is wrong in multiple ways — `app/routers/bookings.py:199-206`, `app/services/refunds.py:15-17`

**4a.** The `else` branch (< 24h) sets `refund_percent = 50` instead of `0`. The spec says: *"notice < 24 hours → 0% refund"*.

**4b.** The 48h boundary uses `notice_hours > 48` (floored hours) instead of `notice >= timedelta(hours=48)`. With 48.5 hours notice: `notice_hours = int(48.5 // 1) = 48`, `48 > 48` is False → falls to 50% tier. Spec says ≥ 48h → 100%.

**4c.** `refund_amount_cents` at line 208 uses `round()` which does banker's rounding (half-to-even). Spec says: *"half-cents rounding up"*. So `round(2.5)` = 2 but spec requires 3.

**4d.** The refund amount stored in `RefundLog` (`app/services/refunds.py:17`) uses `int(...)` (truncation), while the cancel response uses `round(...)` (line 208). These can differ for fractional cents. Spec says: *"the amount returned by the cancel response must equal the amount stored in the RefundLog"*.

**Fix:** Use `Decimal` with `ROUND_HALF_UP` consistently in both places. `bookings.py:211-222` uses `Decimal` with `ROUND_HALF_UP` directly; `refunds.py:14-17` now uses the same computation.

**Status: FIXED** — `app/routers/bookings.py:211-222`, `app/services/refunds.py:14-17`

---

## 5. ✅ GET /bookings/{id} doesn't enforce member-only-own visibility — `app/routers/bookings.py:156-161`

The query filters by `Room.org_id == user.org_id` only. A member can see any booking in their org. Spec rule 10: *"Members may read and cancel only their own bookings (another member's booking id → 404 BOOKING NOT FOUND)."*

**Fix:** For non-admin users, add `Booking.user_id == user.id` filter. The cancel endpoint (line 192) already has this check — apply the same pattern.

**Status: FIXED** — `app/routers/bookings.py:172-173`

---

## 6. ✅ GET /bookings/{id} returns `created_at` as `start_time` — `app/routers/bookings.py:166`

Line 166 overwrites the correct `start_time` from `serialize_booking()` with `iso_utc(booking.created_at)`:
```python
response["start_time"] = iso_utc(booking.created_at)  # BUG
```
The response field shows the creation timestamp instead of the actual booking start time.

**Fix:** `response["start_time"] = iso_utc(booking.start_time)` (or simply don't overwrite — `serialize_booking` already sets it correctly).

**Status: FIXED** — `app/routers/bookings.py:181-182`

---

## 7. ✅ Booking listing sorts descending instead of ascending — `app/routers/bookings.py:137`

```python
base.order_by(Booking.start_time.desc(), Booking.id.asc())
```
Spec rule 11: *"Items are the caller's own bookings sorted ascending by start time (ties by ascending id)."*

**Fix:** `Booking.start_time.asc()`

**Status: FIXED** — `app/routers/bookings.py:138-139`

---

## 8. ✅ Booking listing offset is `page * limit` instead of `(page-1) * limit` — `app/routers/bookings.py:138`

```python
.offset(page * limit)
```
With page=1, limit=10 → offset=10, skipping the first 10 items. Should be `(page - 1) * limit` so page 1 starts at offset 0.

**Status: FIXED** — `app/routers/bookings.py:140-141`

---

## 9. ✅ Booking listing hard-codes `limit=10` — `app/routers/bookings.py:139`

```python
.limit(10)
```
Ignores the user-provided `limit` parameter (default 10, max 100). Should be `.limit(limit)`.

**Status: FIXED** — `app/routers/bookings.py:142-143`

---

## 10. ✅ Access token lifetime is 15 hours instead of 900 seconds — `app/auth.py:50`

```python
lifetime = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES * 60)
```
`ACCESS_TOKEN_EXPIRE_MINUTES = 15` → `timedelta(minutes=900)` = 15 hours. Spec says *"Access tokens expire in exactly 900 seconds"* (15 minutes).

**Root cause:** `timedelta(minutes=...)` already accepts minutes directly. The extra `* 60` multiplies the 15 minutes into 900 minutes (= 15 hours), making tokens live 60× longer than intended.

**Fix:** Removed `* 60` → `timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)`.

**Status: FIXED** — `app/auth.py:50`

---

## 11. ✅ Timezone offset not converted to UTC before storage — `app/timeutils.py:11-14`

```python
dt = datetime.fromisoformat(value)
if dt.tzinfo is not None:
    dt = dt.replace(tzinfo=None)  # strips offset, does NOT convert
return dt
```
An input like `2026-07-09T10:00:00+05:00` is stored as 10:00 UTC instead of 05:00 UTC. The spec says: *"Input datetimes carrying a UTC offset must be converted to UTC before storage or comparison."*

**Fix:** `dt = dt.astimezone(timezone.utc).replace(tzinfo=None)`

**Status: FIXED** — `app/timeutils.py:13-14`

---

## 12. ✅ No concurrency protection for booking creation — `app/routers/bookings.py:42-52,100-118`

`_has_conflict` reads all confirmed bookings without `FOR UPDATE`, then `time.sleep(0.12)`, then the insert commits separately. Multiple concurrent requests can all pass the conflict check. Observed: all 10 concurrent threads for the same slot succeeded (test: expected 1, got 10).

**Fix:** Two layers of protection:
1. A `threading.Lock()` (`_create_booking_lock`) wraps the critical section (validation → conflict check → quota check → insert → commit) in `create_booking`. This works even with SQLite, which ignores `FOR UPDATE`.
2. `.with_for_update()` on the Room query at `bookings.py:106` locks the room row in PostgreSQL for production safety.

Also removed `.with_for_update()` from `_has_conflict` (was locking empty result sets) and commented out the artificial `_pricing_warmup()` sleep.

**Status: FIXED** — `app/routers/bookings.py:22,89-128`

---

## 13. ✅ Artificial `time.sleep` calls removed — multiple files

These calls appeared in `_pricing_warmup()` (0.12s), `_quota_audit()` (0.1s), `_settlement_pause()` (0.12s), `_format_pause()` (0.12s), `_aggregate_pause()` (0.1s), and `_settle_pause()` (0.1s). They deliberately slow down request processing, making race conditions easier to trigger. Not a standalone bug — they made bugs 12 and 14 easier to reproduce.

**Fix:** All `time.sleep()` calls commented out. The real concurrency fixes (locks, `with_for_update`) handle the actual bugs.

**Status: FIXED** — `app/routers/bookings.py:29,34,39`, `app/services/reference.py:14`, `app/services/stats.py:12`, `app/services/ratelimit.py:16`

---

## 14. ✅ Rate limiter has no lock protection — `app/services/ratelimit.py:19-25`

No mutex/lock on `_buckets`. Two concurrent requests from the same user can both execute `_buckets.get()`, see `len(bucket) < 20`, both append, and both pass — exceeding the 20-request limit. The `_settle_pause()` sleep made the race window predictable.

**Fix:** Added `threading.Lock()` at `ratelimit.py:13` and wrapped the read-trim-append-check sequence in `with _lock:`. Also commented out the `_settle_pause()` call inside the critical section.

**Status: FIXED** — `app/services/ratelimit.py:13,23-30`

---

## 15. ✅ Duplicate username returns 201 instead of 409 — `app/routers/auth.py:37-43`

When `existing` user is found (same org + same username), the code returns `{user_id, org_id, username, role}` with default status 201. The spec says: *"A duplicate username within the org → 409 USERNAME TAKEN."*

**Fix:** `raise AppError(409, "USERNAME_TAKEN", "Username already taken in this organization")`

**Status: FIXED** — `app/routers/auth.py:47-49`

---

## 16. ✅ Export `fetch_bookings_raw` bypasses org scoping — `app/services/export.py:22-29`

```python
def fetch_bookings_raw(db: Session, room_id: int) -> list[Booking]:
    return db.query(Booking).filter(Booking.room_id == room_id).all()
```
This has NO `Room.org_id` join/filter. An admin from org A who knows a room_id from org B can export that room's bookings — a multi-tenancy violation. Called from `generate_export` (line 50) when `include_all=True` and `room_id` is set.

**Fix:** Added `org_id` parameter and `.join(Room, ...).filter(Room.org_id == org_id)` to scope the query to the caller's org.

**Status: FIXED** — `app/services/export.py:22-29,53`

---

## 17. ✅ 5-minute grace window for past start times — `app/routers/bookings.py:86`

```python
if start <= now - timedelta(seconds=300):
```
This rejects only if start is 5+ minutes in the past. A start time 1 minute ago passes. The spec says: *"start time must be strictly in the future at request time — no grace window."*

**Fix:** Changed to `if start <= now:`. Old code commented out.

**Status: FIXED** — `app/routers/bookings.py:88-90`

---

## 18. ✅ No min-duration check — `app/routers/bookings.py:89-94`

The code checks `duration_hours > MAX_DURATION_HOURS` but never checks `duration_hours < MIN_DURATION_HOURS` (1). A booking with 0 duration (or negative) passes these checks. The spec says: *"Duration must be a whole number of hours, minimum 1, maximum 8."*

**Fix:** Added `if duration_hours < MIN_DURATION_HOURS: raise AppError(400, ...)` before the max check.

**Status: FIXED** — `app/routers/bookings.py:95-97`

---

## 19. ✅ Reference code counter is not thread-safe — `app/services/reference.py:17-20`

```python
def next_reference_code() -> str:
    current = _counter["value"]
    _format_pause()
    _counter["value"] = current + 1
    return f"CW-{current:06d}"
```
The read-increment-write sequence is not atomic. Under concurrent booking creation, two threads can read the same `current`, then both write `current + 1`, producing duplicate reference codes. `_format_pause()` made the race window trivial.

**Fix:** Added `_ref_lock = threading.Lock()` at line 9 and wrapped the read-increment-write-return in `with _ref_lock:`. Old racy code commented out.

**Status: FIXED** — `app/services/reference.py:9,18-23`

---

## 20. ✅ `stats.py` is not thread-safe — `app/services/stats.py:15-19`

```python
def record_create(room_id: int, price_cents: int) -> None:
    current = _stats.get(room_id, {"count": 0, "revenue": 0})
    count, revenue = current["count"], current["revenue"]
    _aggregate_pause()
    _stats[room_id] = {"count": count + 1, "revenue": revenue + price_cents}
```
The read-modify-write is racy. Two concurrent creates for the same room can both read `count=0`, then both write `count=1`, losing one booking. Same bug in `record_cancel`.

**Fix:** Added `_stats_lock = threading.Lock()` at line 8 and wrapped `record_create` and `record_cancel` in `with _stats_lock:`. Old racy code in `record_create` commented out.

**Status: FIXED** — `app/services/stats.py:8,22-30,33-37`

---

## 21. ✅ `stats.get()` may be permanently inconsistent after a crash — `app/services/stats.py:29-30`

`_stats` is entirely in-memory. If the server restarts, all stats reset to zero while the database still has bookings. The spec says: *"Room stats … always consistent with the bookings themselves."* A restart breaks this guarantee.

**Fix:** Changed `stats.get()` to accept an optional `db` parameter. When `db` is provided (as it is from `rooms.py:110`), it queries the database directly using `COUNT` and `SUM` on confirmed bookings, guaranteed to be consistent. Falls back to in-memory stats if no `db` is passed (for other callers). Old in-memory-only code commented out.

**Status: FIXED** — `app/services/stats.py:40-53`, `app/routers/rooms.py:112`

---

## 22. ✅ `datetime.utcnow()` is deprecated — `app/routers/bookings.py:92,225`, `app/services/refunds.py:24`, `app/models.py:33,57,69`

`datetime.utcnow()` emits `DeprecationWarning` and is scheduled for removal. All 6 source-level call sites used the deprecated function.

**Fix:** Replaced all with `datetime.now(timezone.utc).replace(tzinfo=None)` (for call sites) or `lambda: datetime.now(timezone.utc).replace(tzinfo=None)` (for SQLAlchemy `Column` defaults).

**Status: FIXED** — `app/routers/bookings.py:92,225`, `app/services/refunds.py:24`, `app/models.py:33,57,69`

---

## 23. ✅ No concurrency protection for booking cancellation — `app/routers/bookings.py:222-254`

The cancel endpoint reads `booking.status`, checks it is not `"cancelled"`, then sets `booking.status = "cancelled"` and commits — all without a lock. Under concurrent threads, N requests all see `"confirmed"`, all pass the check, and all commit, resulting in multiple successful cancels. Observed: 7 out of 10 concurrent cancel threads succeeded (test: expected 1).

**Fix:** Added `_cancel_booking_lock = threading.Lock()` and wrapped the entire booking query → visibility check → status check → refund computation → status update → commit in `with _cancel_booking_lock:`.

**Status: FIXED** — `app/routers/bookings.py:23,212-247`

---

