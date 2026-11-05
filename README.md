# Court booking service

Backend for a community club's sports courts: members browse availability, place a short hold,
confirm it, cancel with the 24-hour refund rule, book weekly recurring slots, and staff read
weekly utilization. A court can never be double booked, however many requests arrive at once.

Stack: Python 3.11, FastAPI, SQLAlchemy 2 (psycopg 3), Alembic, PostgreSQL 15, pytest, Ruff, mypy (strict).

The design rationale is in the design document (Claude Doc / `Court_Booking_Service_Design.pdf`).

## Run it

```bash
docker compose up --build
curl http://localhost:8000/readyz            # {"status":"ready"}
```

This starts PostgreSQL 15, applies the migrations, loads one demo venue ("Riverside Courts",
two courts, peak 17:00 to 21:00 every day), and serves the API on port 8000. The demo member ID
is printed by the `migrate` service logs (`docker compose logs migrate`).

Run the test suite in the container, against its own database:

```bash
docker compose --profile test run --rm tests
```

Run it locally (Python 3.11 and a PostgreSQL 15 server):

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
createdb -h localhost -U postgres booking_test       # tests refuse any name not ending in _test
export TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/booking_test
ruff check . && ruff format --check . && mypy && pytest -q
```

## The API

Members identify themselves with `X-Member-Id` (a development stub; see LIMITATIONS.md).
Staff use `X-Staff-Key`. Errors share one shape: `{"code", "detail", "request_id"}`.

| Method and path | What it does |
| --- | --- |
| `POST /holds` | Place a hold. `201` on create, `200` on an idempotent replay. Optional `Idempotency-Key`. |
| `POST /holds/{id}/confirm` | Confirm a live hold. Retrying returns the same booking. `410` if the hold expired. |
| `POST /bookings/{id}/cancel` | Cancel. Full refund at 24 hours' notice or more, otherwise none. Retrying returns the stored decision. |
| `GET /courts/{id}/availability?from=&to=` | Hourly slots and prices for up to 7 days. Snapshot, no locks. |
| `POST /recurring-bookings` | Book 1 to 3 weekly occurrences, all or nothing. |
| `GET /staff/utilization/weekly?venue_id=&week_start=` | Scheduled utilization per court for one Monday-to-Monday week. Staff only. |
| `GET /healthz`, `GET /readyz` | Liveness, and readiness including a database check. |

Example:

```bash
curl -X POST localhost:8000/holds \
  -H "X-Member-Id: $(uuidgen)" -H "Content-Type: application/json" \
  -d '{"court_id":"<court>","starts_at":"2026-11-08T18:00:00+05:30","ends_at":"2026-11-08T19:00:00+05:30"}'
```

## How the guarantees are enforced

| Guarantee | Where it lives | Proven by |
| --- | --- | --- |
| A court is never double booked | `reservations_no_overlap`, a PostgreSQL exclusion constraint (migration `0001`) | `test_fifty_members_race_for_one_slot...`, `test_database_rejects_two_live_bookings_on_one_court` |
| A retried confirm never makes two bookings | Confirm locks one row and is state based | `test_retried_confirms_produce_one_booking` |
| Duplicate creates with one key share one booking | Idempotency key claimed in the same transaction (`ON CONFLICT`) | `test_concurrent_duplicates_with_one_idempotency_key...`, `test_idempotent_hold_replay...` |
| No partial state after a failed multi-step operation | One transaction per use case; any `ApiError` rolls it back | `test_recurring_series_is_all_or_nothing` |
| Three active bookings per member | Member row locked before the count | `test_one_member_cannot_exceed_three_bookings_under_concurrency` |
| Expired holds never block a slot | Expiry is by database time; stale holds are expired before an insert | `test_expired_hold_returns_410_and_frees_the_slot` |

## Layout

```
app/domain/rules.py     pure rules: intervals, pricing, refund, live-hold predicate (no I/O)
app/services/           use cases, one transaction each: holds, recurring, queries, common
app/api.py              FastAPI routes and error mapping
app/db.py               engine, lock/statement timeouts, transaction runner with deadlock retry
app/models.py           SQLAlchemy 2 models
migrations/             Alembic; 0001 holds the schema and the exclusion constraint
tests/                  domain, API contract, constraints, and concurrency tests
docker/initdb/          creates the test database on first start
```

## Not in this release

Online payment. The design's payment slice starts after the payment provider is chosen (D2) and
the late-payment rule is confirmed (D3). Venues with `payments_enabled` are refused with `501`
until then. See LIMITATIONS.md for everything else that is out of scope.
# Court_service
