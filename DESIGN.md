<!-- coderabbit-ai-generated:v1 -->

<!-- coderabbit-ai-generated-section:v1:ce5d5b69b205e89d01e897a5cea51d5eae66c54900a308569dcd7f5d7511f20c -->

# Demo approval and notification design

## Scope and decisions (2026-11-19)

The user requested a patch extending the existing demo without email/SMS/payment APIs. The current request authorizes implementation of admin-reviewed payment, final reservation confirmation and refunds. Notifications require no admin approval. This intentionally replaces automatic member payment confirmation in the real demo configuration. The original database no-overlap constraint, member cap and refund rules remain mandatory. The prior design-review milestone was not delivered retroactively; this document records the current change for review with its patch.

- Members create a five-minute hold, then POST /holds/{id}/confirm or report a demo payment. Both create the same durable approval request and return 202. No charge occurs. A review window lasts at most 15 minutes from the first report, capped by booking start. Retries cannot extend it. A live pending request counts toward the three-booking cap and blocks overlapping bookings only until that deadline.
- An admin approves payment and booking atomically, or rejects it and releases the slot. Expired requests cannot be revived. Approved/rejected decisions are final and retries idempotent. Cancellation remains a member action; refundable cancellations can request admin-reviewed simulated refund, using the original stored 24-hour eligibility decision.
- Recurring bookings remain disabled in the real demo pending a series review/checkout design. Existing direct-confirm/recurring domain tests run in explicit local compatibility mode; production refuses compatibility bypass. This is a known remaining original-brief gap, not a claim of full compliance.
- Without email APIs, admins can verify demo accounts. This grants demo access; it does not prove mailbox ownership. Production never returns debug tokens. SMTP remains optional and explicit. No real SMS or payment network is contacted.

## Shared durable buffer and idle clients

PostgreSQL stores approvals, in-app messages and delivery outbox records for all members, including offline/idle users. No sleeping shell process, reserved database connection or server-side idle list is allocated per member. Browser polling pauses after 60 seconds without activity and while hidden; focus/visibility/activity return triggers a fresh fetch. Active tabs refresh in 15–20 seconds with jitter, single-flight requests and cross-tab invalidation hints. Shared court metadata may be cached for 30 seconds. Server mutations always re-check current state, so stale UI cannot override booking constraints. List queries are bounded; existing per-user booking history returns latest 100 and notifications latest 50.

## Notification queue

Every business decision and its messages commit in one PostgreSQL transaction. Event-specific unique dedupe keys avoid duplicate notifications on retries. Workers claim one message with FOR UPDATE SKIP LOCKED, commit a five-minute lease, then deliver outside the transaction. A UUID claim fences stale completions. Failed/crashed attempts are capped at five, with bounded exponential backoff; dead messages require explicit admin retry. Error storage includes exception class only to avoid recording provider secrets. Console delivery ends in simulated, with truthful in-app copy. SMTP is at-least-once: an external send may be repeated after a crash. Exactly-once external delivery is not promised. Admin queue UI shows status/attempts and permits retry of dead messages only.

## Alternatives rejected

- Per-user bash processes or open connections for idle users: resource use grows with idle membership and process state is lost on restart.
- An in-memory-only message buffer: loses approvals/messages on crashes and diverges across AWS nodes.
- An additional broker/Redis-only business queue: unnecessary operational dependency for this demo; PostgreSQL already owns business transactions and durable state.
- Trusting a member's payment-success message as settlement: bypasses the requested admin review and implies real money moved.
- Indefinite admin review locks: violates the original expiring-hold contract.

## Open questions / limits

Agree a longer approval SLA if 15 minutes is insufficient; longer windows require an explicit design change. Confirm whether future recurring series use one combined approval or per-occurrence approvals. Choose providers before adding real SMS/payment integration, signed webhooks, reconciliation or external delivery claims. Current runtime declarations still target Python 3.14/PostgreSQL 16, despite the earlier brief's 3.11/15. This patch does not certify large-scale capacity, database HA or an AWS rollout. Load/capacity measurements, retention, dead-letter operations and backups remain operator concerns.

## Explicit HTTP demo exception (latest user decision)

The user authorized unencrypted HTTP using the AWS-provided ALB hostname to restore one-command demo deployment. The demo launcher passes allow_http_demo=true by default; direct Terraform and app settings still default to HTTPS. Terraform passes the exception through its output into both API and worker environment templates. This intentionally changes the earlier mandatory-HTTPS deployment contract while retaining production authentication, Redis fail-closed behavior, admin approval, and database booking invariants. Set ALLOW_HTTP_DEMO=false when launching to require HTTPS. HTTP exposes credentials/tokens in transit and browser geolocation generally requires a secure context. The ALB hostname is an AWS hostname, not a purchased custom domain. Deploy/destroy retain their saved-plan confirmation steps.


<!-- /coderabbit-ai-generated:v1 -->