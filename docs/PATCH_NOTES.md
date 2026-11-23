# Patch notes: nearby search, admin screen, notification wakeups, safety fixes

## 1. Nearby courts (was a full scan on every request)

Before: `/api/courts/nearby` loaded every active court and venue, computed distance in Python,
had no index on venue coordinates, no result limit, and no bound on the work done.

Now (`app/services/location.py`):

- A latitude/longitude **bounding box** narrows candidates. Two boxes handle the antimeridian,
  one full-longitude box handles the poles. An index-served predicate, not a scan.
- Haversine distance is computed **in PostgreSQL**, only for box candidates, and the radius is
  applied in the same statement.
- Results are ordered by distance and capped at 50.
- Results are not cached per user. They depend on a precise location, and caching them would
  retain that location outside the user's row.

Migration `0005_location_search_indexes`: partial index on `venues(latitude, longitude)` and
`courts(venue_id) WHERE active`. On a large existing table, create these with `CONCURRENTLY`.

## 2. Admin screen

API additions (admin only): `GET /api/admin/venues` (coordinates and courts),
`PATCH /api/admin/courts/{id}` (activate/deactivate), `DELETE /api/admin/venues/{id}/coordinates`.
Shared-cache invalidation is in one helper. Admin changes reach members immediately.

UI (`frontend/admin.js`, replacing the inline admin code in `app.js`): per-venue location status,
validated coordinate forms (checked in the browser and on the server), court toggles with
`aria-pressed`, add-court form, labelled controls with unique IDs per venue. It renders with
`textContent` only and does not auto-refresh, so it cannot overwrite what an admin is typing.

Payment and refund requests (review panel): each request is now a decision card showing the kind,
amount, court, member and their message. Approve/confirm asks for confirmation naming the
amount and member; decline asks for an optional note to the member. Expired payment holds can
only be closed. Previously each request was a text line with generic "Approve" / "Reject" buttons.

Known gap, not changed here: an admin-approved refund marks the payment refunded without calling
the payment provider (the member-side simulate path does call it). Demo only, but a real provider
needs that call added to `approvals.decide`.

## 3. Notification worker: Kafka wakeups instead of a 2-second scan

Design choice: Kafka carries a **signal**, not the job. Postgres remains the source of truth.

- An outbox row is written in the same transaction as the business change (unchanged).
- After that transaction **commits**, the API publishes a small `outbox_ready` message
  (`app/events.py`). A rolled-back transaction publishes nothing. A commit with no outbox work
  publishes nothing.
- The worker blocks on Kafka. On a message it drains the outbox (`FOR UPDATE SKIP LOCKED`
  claims, unchanged) until empty, then waits again.
- Publishing is best effort. A broker failure is logged and never fails a request.
- A **sweep** runs every `WORKER_SWEEP_SECONDS` (default 60 with Kafka) as a safety net for
  lost signals. Hold expiry runs on that timer, not on every loop.
- Without `KAFKA_BOOTSTRAP_SERVERS`, the worker falls back to polling every 2 seconds, as before.

Why not make Kafka the queue? A Kafka publish inside the request is a dual write: it can
announce work that was rolled back, or lose work if the broker is down. Keeping the outbox in
Postgres keeps the approval and notification guarantees the project documents.

Config: `KAFKA_BOOTSTRAP_SERVERS`, `KAFKA_NOTIFICATION_TOPIC`, `KAFKA_CONSUMER_GROUP`,
`WORKER_SWEEP_SECONDS`. Compose adds an Apache Kafka 3.9 broker (KRaft, single node, demo only).
Terraform/Ansible are not yet updated for a Kafka host; that is a deployment follow-up.

Delivery semantics: a wakeup can be duplicated or lost; both are harmless because the worker
always re-reads the database, and the sweep covers loss.

## 4. Safety and correctness fixes found along the way

- `deploy/destroy.sh`: the uncommitted working-tree change had deleted the `DESTROY`
  confirmation, so any input other than `DESTROY` still applied the destroy plan. Restored.
- Demo admin email `admin@court.local` is rejected by the login validator (reserved `.local`
  domain), so the seeded admin could not sign in. Default changed to `admin@courtly.example`
  in `app/seed.py`, `.env.example` and README. **Existing deployments:** update
  `BOOTSTRAP_ADMIN_EMAIL` in `.env` and re-run the seed, which creates the new account.

## Verification

- `pytest`: 120 passed (includes 19 location/admin tests and 8 wakeup tests).
- `ruff check .`, `mypy app`, `compileall`, Alembic upgrade/downgrade/upgrade: clean.
- The worker process was run against a seeded database: it delivered a queued message and
  exited cleanly on SIGTERM.
- The admin UI was driven in a DOM harness against the live API: 12 checks passed.

Not verified here: a live Kafka broker (none is available in this environment, so the Kafka
waker is tested with a fake consumer), and production-scale load.
