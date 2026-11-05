# Limitations: how this system can still fail

Read this before the pilot. Each item says what happens and what protects against it.

## Not built yet

- **Online payment.** Holds on venues with `payments_enabled` return `501`. The payment design
  (collect-then-confirm, the payment attempt states, webhooks, reconciliation, refunds through the
  provider) is in the design document and is not implemented here.
- **Member sign-in.** `X-Member-Id` is trusted as given. Anyone who knows a member's UUID can act as
  that member. Production must not run without a real identity check in front of this header.
- **Staff sign-in.** `X-Staff-Key` is one shared secret. Roles (viewer, manager) and audit logging
  are not implemented.
- **Staff overrides, waitlists, cash bookings, holiday rates, GST.** Out of scope, as in the design.

## Correctness limits

- **Availability is a snapshot.** A slot shown as free can be taken a moment later. The hold then
  returns `409`, which is correct, but a client can briefly see a slot that is gone.
- **Utilization counts scheduled time.** It does not know whether anyone turned up. A court can
  look busy and be empty.
- **Available hours assume one opening window per day.** The venue model has one window; a venue with
  different weekend hours needs a model change, not a config change.
- **Daylight saving is handled in the rules and tested, but no venue uses it.** The database stores
  instants in UTC and local rules run in the venue's time zone. If the tz database is updated, the
  container must be rebuilt to pick up the new rules.

## Operational limits

- **Single primary database.** A failover causes a short outage. Writes in flight return `503`
  (`retry_later`) or a connection error. Keyed retries are safe; unkeyed retries of a create can
  return `409 slot_unavailable` for the client's own earlier success. Clients should send an
  `Idempotency-Key` on every hold.
- **Idempotency keys last only as long as their rows.** The design says 24 hours. This build keeps
  them until a cleanup job is added, which is not implemented, so the table grows without bound.
- **Lock timeouts shed load under contention.** A request waiting more than 2 seconds on a row
  returns `503`. Under heavy load on one slot, some legitimate requests fail fast. That is the
  intended trade-off, but it is visible to members.
- **Worker count follows CPU count.** `WEB_CONCURRENCY` overrides it. Each worker holds its own
  connection pool, so the total number of database connections is workers times the pool size.
  That total must stay below PostgreSQL's `max_connections`. PgBouncer is in the design but not in
  this compose file.
- **io_uring is not implemented here.** The approved plan (D6) is a PostgreSQL 18 pilot with a
  custom seccomp profile, measured by a load test. This build runs PostgreSQL 15.
- **Peak sizing is an assumption.** The weekend target of 300 requests per second (D7) has not been
  load-tested against this code. Until it is, treat the capacity as unknown.
- **No backups or restore are configured in compose.** The design requires point-in-time recovery
  and a quarterly restore test. Neither exists in this repository.
- **Logging is minimal.** Errors carry a `request_id` that is not written to any log yet, so
  correlating a client error with server logs is not possible.

## What the tests do and do not show

- The tests run against PostgreSQL 15 and cover the concurrency races, the constraint behaviour,
  the contract, and the pure rules. They pass on the build environment used to write this code.
- The Docker image and compose stack were written and checked for valid syntax, but could not be
  built and started in the environment used to write this code. The first CI run of
  `compose-smoke` is the first real test of the container build.
- Load behaviour at weekend peak is not measured. The concurrency tests show correctness under
  contention, not throughput.

## The failure modes that matter most

1. A client retries a create after a timeout without an idempotency key and sees `409` for its own
   booking. Mitigation: require the key in client code.
2. A member impersonates another via the unauthenticated header. Mitigation: production identity,
   before launch.
3. Lock timeouts reject requests during a hot-slot rush. Mitigation: availability caching in the
   client, staggered slot opening, load testing before the weekend pilot.
