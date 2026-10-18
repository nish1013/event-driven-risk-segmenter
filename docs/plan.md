# Plan: Risk Segmenter, event-driven merchant risk bands

Three Python services keep a live risk band for every merchant: Recording, Segmenter, Notifier. They
share one Postgres database, with a schema per service, and talk only through Pub/Sub. v1 runs on
Docker Compose with the Pub/Sub emulator (#16).

Status: locked, 2026-10-18. Revised 2026-10-18: delivery order is best-effort (#43)

## Context

**The brief.** [brief.md](brief.md). Recording must never wait on scoring or notification. A failed
Notifier must never undo a band. Each service owns its data.

**Owner's decisions:** [decisions.md](decisions.md) #1-#42. Stories and invariants:
[domain.md](domain.md). This plan restates them; it decides nothing new.

**Facts checked read-only (2026-10-18):**

- Python 3.14.7, uv 0.12.11, Docker 29.2.1, Compose v5.0.2 installed. `gh` signed in as `nish1013`.
- No `gcloud` installed. The emulator runs from the `google-cloud-cli:emulators` image instead.
- Unconfirmed: whether the Pub/Sub emulator honours dead-letter policies. PR 1 proves it before
  anything depends on it. The emulator does not hold an ordering key after a nack; delivery order is
  best-effort and nothing depends on it (#43).

## Stack

| Concern | Choice | One-line reason |
| --- | --- | --- |
| Language | Python 3.14 | The installed toolchain. One language for all three services (#15) |
| Packages | uv, one `pyproject.toml` and lockfile per service | Services share no code, so each locks its own dependencies |
| HTTP | FastAPI | Request models give the 422s of #30 without hand-written checks |
| Validation | Pydantic v2, `extra="forbid"`, a union keyed on `type` | One strict schema per activity type (#30) |
| Database access | SQLAlchemy 2 Core, psycopg 3 | The guards are SQL (`ON CONFLICT`, `UPDATE … WHERE version_number = :v`). Core writes them as written |
| Migrations | Alembic, one history per service, version table in its own schema | No service migrates another's schema |
| Database | Postgres 17, a role per service (#36) | Partial unique indexes (I7) and schema-level grants |
| Messaging | `google-cloud-pubsub` client, emulator locally | The same client reaches real Pub/Sub by configuration only (#16) |
| Tests | pytest, against the Compose stack for integration and e2e | Tests run on the same Postgres and emulator as development |
| Lint and types | ruff, mypy `--strict` | Fails CI on unchecked code |
| CI | GitHub Actions, `docker compose up --wait`, then all three test layers | Every PR runs the full stack |

## Data flow

```
client --POST /activities--> recording-api
   tx: activities + outbox                       (I1, I3)
recording-relay --activity-recorded, key merchantId--> segmenter-worker
   tx: processed_events + facts + segment + checks + outbox, version check   (I5-I11)
segmenter-relay --segmenter-events, key merchantId--> notifier-worker
   tx: processed_events + alerts                 (I10, I13-I16)
officer --POST /risk-checks/{id}/resolution--> segmenter-api   (same tx shape)
officer --GET segment--> segmenter-api      officer --GET alerts--> notifier-api
```

Containers: `postgres`, `pubsub`, `pubsub-init`, `recording-api`, `recording-relay`,
`segmenter-worker`, `segmenter-api`, `segmenter-relay`, `notifier-worker`, `notifier-api`,
`dead-letter-monitor`.

## Domain contract

The rules live in [policy-v1.md](policy-v1.md), sections P1-P6. Code and tests cite those ids. Boundary
cases tested for every P2 rule: count one below and at the threshold. A fact at exactly
`anchor - window`, and one 1s older.

## Aggregates and units of work

[aggregates.md](aggregates.md), sections A1-A6 (#42). Code and tests cite those ids.

## Validation and responses

| Endpoint | Status | When |
| --- | --- | --- |
| `POST /activities` | 201 | New activity stored |
| | 200 | Same `activityId`, same body. Original returned (#8) |
| | 409 | Same `activityId`, different body (#26) |
| | 422 | Unknown type, payload fails its schema (#30), missing field, `occurredAt` > now + 5 min (#31) |
| `GET /merchants/{merchantId}/segment` | 200 | Band, rule, `policyVersion`, `updatedAt`, open check if any |
| | 404 | Merchant never seen |
| `POST /risk-checks/{checkId}/resolution` | 200 | Check resolved |
| | 404 | No such check |
| | 409 | Check not open |
| | 422 | Missing `decision`, `officerId` or `reason`. `decision` not `approve_lower` or `keep_current` |
| `GET /merchants/{merchantId}/alerts?status=open` | 200 | Newest first. Empty list for an unknown merchant. `status` optional |
| `GET /healthz` on every API | 200 | Process up and database reachable |

"Same body" compares `merchantId`, type, `occurredAt` and payload after parsing, so key order and
whitespace do not matter.

## Architecture

```
docker-compose.yml           all containers, healthchecks
infra/
  postgres/init.sql          schemas, roles, grants (#36)
  pubsub/init.py             topics, subscriptions, dead-letter topics (#10, #29)
services/
  recording/
    pyproject.toml, uv.lock, Dockerfile, alembic/
    src/recording/
      domain/                Activity, payload schemas, same-body rule
      service_layer/         record_activity handler, unit of work
      adapters/              activity repository, outbox, Pub/Sub publisher
      entrypoints/           api.py, relay.py
    tests/unit, integration, e2e
  segmenter/
    src/segmenter/
      domain/                Segment root, Check, policy v1 rules, band decision
      service_layer/         on_activity_recorded, resolve_risk_check, unit of work
      adapters/              segment repository, outbox, processed events, publisher, subscriber
      entrypoints/           api.py, worker.py, relay.py, replay.py
  notifier/
    src/notifier/
      domain/                Alert, open/close rules
      service_layer/         on_segmenter_event, unit of work
      adapters/              alert repository, processed events, alert log port (#12), subscriber
      entrypoints/           api.py, worker.py, replay.py
e2e/                         cross-service tests over HTTP and Pub/Sub
```

**Tables.** Columns in `snake_case`. All times `timestamptz`.

| Schema.table | Columns | Keys and constraints |
| --- | --- | --- |
| `recording.activities` | `activity_id`, `merchant_id`, `type`, `occurred_at`, `payload jsonb`, `recorded_at` | PK `activity_id`. `CHECK type IN (…)` |
| `recording.outbox` | `id bigserial`, `event_id uuid`, `activity_id`, `topic`, `ordering_key`, `body jsonb`, `created_at`, `sent_at` | Unique `event_id`, unique `activity_id`. Index on `id` where `sent_at IS NULL` |
| `segmenter.segments` | `merchant_id`, `band`, `rule`, `policy_version`, `version_number`, `updated_at` | PK `merchant_id`. `CHECK band IN (…)` |
| `segmenter.facts` | `activity_id`, `merchant_id`, `type`, `occurred_at` | PK `activity_id`. Index `(merchant_id, type, occurred_at)` |
| `segmenter.checks` | `check_id uuid`, `merchant_id`, `proposed_band`, `status`, `decision`, `officer_id`, `reason`, `opened_at`, `resolved_at` | PK `check_id`. Unique `(merchant_id)` where `status = 'open'` |
| `segmenter.outbox` | as `recording.outbox`, without `activity_id` | Unique `event_id` |
| `segmenter.processed_events` | `event_id uuid`, `processed_at` | PK `event_id` |
| `notifier.alerts` | `alert_id uuid`, `merchant_id`, `kind`, `check_id`, `status`, `decision`, `details jsonb`, `created_at`, `closed_at` | PK `alert_id`. Unique `check_id`. `CHECK`: `check_id` and `status` set exactly when kind is `risk_check_required`. Index `(merchant_id, created_at DESC)` |
| `notifier.processed_events` | `event_id uuid`, `processed_at` | PK `event_id` |

**Segmenter transaction on `ActivityRecorded`:**

```
BEGIN
  INSERT processed_events (event_id) ON CONFLICT DO NOTHING   -- 0 rows: COMMIT, ack, stop
  INSERT segments (merchant_id, 'low', version 0) ON CONFLICT DO NOTHING
  INSERT facts ON CONFLICT DO NOTHING
  load segment and open check; count facts per rule window, one query (#39)
  decide (domain, policy-v1.md P3-P4), prune this merchant's facts per type (P6, #38)
  UPDATE segments SET …, version_number = v + 1 WHERE merchant_id = :m AND version_number = :v
                                                     -- 0 rows: ROLLBACK, retry (A4)
  write check changes, outbox rows in raise order (A3)
COMMIT, then ack
```

The version number goes up on every event, even when the band stays the same: a new fact changed the
aggregate.

**Notifier.** A `segment_changed` alert has no status; only check alerts are `open` or `closed`, so
`?status=open` lists what waits on an officer. `ManualRiskCheckRequired`: insert alert `open` `ON CONFLICT (check_id) DO NOTHING`.
`RiskCheckResolved`: upsert the alert as `closed`. Close only rows that are `open` (I14). A late
"opened" therefore finds a closed row and does nothing.

**Cleanup** (#40). Each relay deletes outbox rows sent more than 7 days ago, and each worker deletes
`processed_events` older than 14 days, every hour.

**Relay** (#3). One per service. Loop: select up to 100 unsent rows by `id`, publish each with its
ordering key, wait for every ack, then set `sent_at`. On a publish error it stops the batch and retries
from the first unsent row, so a merchant's order never skips.

**Dead letters** (#10, #20, #35). Each subscription: 5 attempts, then its dead-letter topic.
`dead-letter-monitor` (#37) checks every dead-letter subscription every 60 s and logs at `ERROR` when depth
is above 0. `replay.py --subscription <name>` republishes as in #35.

## The hard part: I6

**The failure.** `m-100` is `medium` with chargebacks on 10-01 and 10-05. At 10-20 a chargeback and a
`failed_login` arrive. Two workers take one each.

```
worker A: reads 2 chargebacks, adds the third  -> rule band high   -> writes high, commits
worker B: reads 2 chargebacks, adds failed_login -> rule band medium -> writes medium, commits last
result:   band medium, 3 chargebacks stored. I6 broken.
```

**The guard.** Both read `version_number = 7`. A commits `8`. B's update matches 0 rows, rolls back,
retries (A4) and recomputes with all facts.

**Why simpler options fail.**

| Option | Failure |
| --- | --- |
| Ordering key only | Pub/Sub can redeliver while the first delivery still runs. Ordering does not stop overlap (#9) |
| `SELECT … FOR UPDATE` on the segment | Works, but holds a row lock across the whole recompute. #9 chose the version number |
| Lock the `facts` table | Serialises every merchant |

## Tests

Each test names the story or invariant it proves.

**Unit** (no I/O):

- Policy: P3 over every P2 boundary. A test fails if "at least" becomes "more than".
- Decide: all five rows of P4, and both rows of P5.
- The `policy-v1.md` example run, event by event, as one test.
- Payload schemas: each type valid, each field missing, one unknown field, a bad enum value.
- Same-body rule: key order and whitespace ignored, a changed amount detected.

**Integration** (real Postgres and emulator):

| Test | Proves | Fails when |
| --- | --- | --- |
| 20 concurrent posts of `a-1` give one row and one outbox row | I1, I3 | the PK or `ON CONFLICT` is removed |
| Two concurrent `ActivityRecorded` for `m-100` on two sessions give the rule band of both facts | **I6** | the `version_number` check is removed |
| Two concurrent lower recomputes give one open check | I7 | the partial unique index is removed and the version check is bypassed |
| Two concurrent resolves of `c-1`: one 200, one 409 | I9 | the `status = 'open'` condition is removed |
| One event delivered twice gives one change | I10 | the processed-events insert is removed |
| A worker meets one forced version conflict: the event is applied on the rerun, with no nack | A4 | the conflict nacks at once |
| Approving a check writes `RiskCheckResolved` before `RiskSegmentChanged` in the outbox | A3 | the unit of work writes events out of raise order |
| Relay killed after publish, before `sent_at`: consumer state changes once | #3, I10 | dedup is removed |
| The count query returns P2's counts at each window boundary | #39 | the bound becomes exclusive |
| With 10,000 failed logins for one merchant, `EXPLAIN` of the count query shows an index scan on `(merchant_id, type, occurred_at)` | #38, #39 | the index is dropped or the query loads rows |
| Relay publishes 50 rows across 5 merchants: each merchant's order matches `id` order | #3 | the relay reads by `created_at` or in parallel |
| "Closed" then a replayed "opened" leaves the alert closed | I14 | the close-only-open rule is removed |
| Segmenter's role reads `recording.activities` and is refused | #36 | grants are widened |
| Recording's role updates an activity and is refused | I2 | update is granted |

**End to end** (Compose, over HTTP and Pub/Sub):

| Test | Proves |
| --- | --- |
| Chargeback → band `medium` → `segment_changed` alert | Brief success path |
| Notifier stopped, three events, Notifier started: three alerts, bands never changed | Notifier failure never undoes a band |
| Segmenter stopped: `POST` still 201 in under 200 ms; scored after restart | Recording never waits |
| Pub/Sub stopped: `POST` still 201; published after restart | #2 |
| A fourth subscription added on `activity-recorded` receives events, no producer change | New consumers need no producer change |
| A poison event dead-letters after 5 attempts, later events for that merchant flow, replay applies it once | #10, #20, #35 |
| Drop held → check → approve → band lowered → alert closed | US3, US4, US8 |

## README outline

1. What it does, one diagram. Link to `docs/policy-v1.md`.
2. Run it: `docker compose up --wait`, three `curl` calls.
3. Services, topics, schemas: one table.
4. Design: link to `docs/domain.md` and `docs/decisions.md`.
5. Tests: how to run each layer.

| Decision | Why | Trade-off | Next step |
| --- | --- | --- | --- |
| Transactional outbox | No lost events on crash | Two relay containers | CDC (v2) |
| Version number on segment | I6 under concurrent delivery | A conflict costs one redelivery | none |
| Drops wait for an officer | A band never falls on its own | Merchants stay strict until reviewed | `kyc_renewed`, decay (v2) |
| One topic for Segmenter events | Order across event types | Consumers filter on type | none |

## PR series

1. **`chore/project-setup`**: Compose stack, `infra/` init, three empty services with `/healthz`,
   Alembic per service, roles and grants, CI, an emulator check.

   **Accept when:** `docker compose up --wait` is healthy. CI runs ruff, mypy and pytest on every
   service. The emulator check nacks one message until it lands on the dead-letter topic after 5
   attempts. It asserts no per-key delivery order (#43). If the emulator fails the check, building
   stops and the owner decides the fallback.

2. **`feat/record-activity`**: US1, I1-I4. Recording API, payload schemas, outbox, relay.

   **Accept when:** every US1 row is a passing test. The I1/I3 concurrency test fails with the PK
   removed. The relay order test fails if the relay reads in parallel.

3. **`feat/segment-rules`**: US2, US5, I5, I6, I10-I12. Segmenter worker, facts, policy v1, rises,
   segment read API, Segmenter relay.

   **Accept when:** every policy boundary has a test below, at and above it. The I6 test fails with the
   version check removed. Pruning never deletes `kyc_expired`.

4. **`feat/risk-checks`**: US3, US4, I7-I9. Held drops, checks, resolve endpoint, `superseded`.

   **Accept when:** every US3 and US4 row is a passing test. The I7 and I9 concurrency tests each fail
   with their guard removed.

5. **`feat/alerts`**: US6-US9, I13-I16. Notifier worker, alert log port, alerts read API.

   **Accept when:** every US6-US9 row is a passing test. The I14 replay test fails with the
   close-only-open rule removed.

6. **`feat/dead-letter-replay`**: US10, #10, #20, #35. Dead-letter monitor, replay command, all e2e
   tests.

   **Accept when:** the poison-event e2e passes, and fails if replay assigns a new `eventId`.

7. **`docs/readme`**: README as outlined.

   **Accept when:** a fresh clone reaches a `medium` band by following the README alone.

## Execution steps

1. `git fetch origin && git switch -c <branch> origin/main` for each PR.
2. `docker compose up --wait`.
3. `uv run --directory services/<svc> alembic upgrade head` (also run by each container on start).
4. Build the PR's tests first, red, then the code.
5. Run the verification below. Open the PR.

## Verification

```
docker compose up --wait                                  all containers healthy
uv run --directory services/<svc> ruff check . && mypy --strict src   clean
uv run --directory services/<svc> pytest tests/unit       green
uv run --directory services/<svc> pytest tests/integration   green, stack up
uv run pytest e2e                                         green, stack up
curl -s -XPOST :8001/activities -d @e2e/fixtures/chargeback.json   201
curl -s :8002/merchants/m-100/segment                     {"band":"medium",…}
curl -s ':8003/merchants/m-100/alerts?status=open'        []
```

## Deviations

None yet. Each deviation from this plan is recorded here, dated, with its reason.
