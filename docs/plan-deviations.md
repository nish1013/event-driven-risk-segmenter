# Plan deviations

Where the build departs from [plan.md](plan.md), and why. A deviation becomes a decision only once it is added to [decisions.md](decisions.md).

## D1. Two Postgres roles per service (PR 1, 2026-10-18)

Plan:     Stack table, "Postgres 17, a role per service (#36)". Execution step 3 runs Alembic for
          each service, so the same role would own and migrate its schema.
Built:    Two roles per service. `<svc>_migrator` owns the schema and runs Alembic. `<svc>` is the
          runtime role: `USAGE` on its own schema, no `CREATE`, and only the table rights each
          migration grants. The service connects as `<svc>`; only `alembic upgrade head` uses the
          migrator (`infra/postgres/init.sql`, `MIGRATION_DATABASE_URL` in `docker-compose.yml`).
Why:      Postgres lets a table owner `DROP` or `TRUNCATE` its table and grant itself any right it
          revoked. A runtime role that owns `recording.activities` can therefore always update or
          delete it, so I2 and the "insert and select only" rule of #36 cannot hold in the
          database. #36 still holds as written: each service connects as its own role, with
          rights only on its own schema.
Undo:     Drop the `*_migrator` roles, create each schema with `AUTHORIZATION <svc>`, and point
          `MIGRATION_DATABASE_URL` at the runtime role. I2 then rests on code review.

## D2. US1 "Segmenter stopped" row is proven up to the topic (PR 2, 2026-10-18)

Plan:     PR series, PR 2: "every US1 row is a passing test". The row "Segmenter and Notifier
          stopped: `a-2` is recorded -> 201. `a-2` is scored once Segmenter is back".
Built:    `e2e/test_record_activity.py` proves the PR 2 half: with no Segmenter consumer running,
          `POST` returns 201 and exactly one `ActivityRecorded` reaches `activity-recorded`, where
          `segmenter-activity-recorded` holds it. The "scored once Segmenter is back" half is left
          to PR 3, with the plan's end-to-end test "Segmenter stopped: `POST` still 201 in under
          200 ms; scored after restart".
Why:      Scoring needs the Segmenter worker, which the plan builds in PR 3 (`feat/segment-rules`).
          No PR 2 test can observe a band.
Undo:     None needed once PR 3 adds the scoring assertion. Moving the whole row to PR 3 would
          take only this entry.
Closed:   PR 3, 2026-10-18. `e2e/test_segment_rules.py::test_us1_segmenter_stopped_post_fast_scored_after_restart`
          stops `segmenter-worker`, posts a chargeback (201 in under 200 ms, no segment yet),
          starts the worker and reads band `medium` from the segment API.

## D3. Verification `curl` needs a JSON content type (PR 2, 2026-10-18)

Plan:     Verification: `curl -s -XPOST :8001/activities -d @e2e/fixtures/chargeback.json` -> 201.
Built:    The same call with `-H 'Content-Type: application/json'`. The endpoint reads only JSON
          bodies.
Why:      `curl -d` sends `application/x-www-form-urlencoded`. FastAPI then does not parse the body
          as JSON and returns 422 (checked against the running stack).
Undo:     Accept any content type as JSON in `recording.entrypoints.api`. That widens the endpoint
          to form posts from browsers, so the header is the smaller change.

## D4. A rule band equal to the band closes the open check (PR 4, 2026-10-18)

Decided as #44 on 2026-10-18.

Plan:     policy-v1.md P4, row "same | either | nothing changes", restated by the plan's Domain
          contract. An open check stays open, with its lower proposal, when a recompute gives the
          current band.
Built:    `Segment.apply` closes the open check as `superseded` and emits `RiskCheckResolved` when
          the rule band equals the band, exactly as a rise does (#28). With no open check, an equal
          rule band still changes nothing. `test_d4_same_band_with_check_supersedes_it`.
Why:      The kept proposal goes stale, and approving it breaks I6. Run: `m-100` `high` by
          `chargeback_3_in_30d`, check open proposing `medium`. `kyc_expired` arrives, so the rule
          band is `high`, equal to the band. Under P4 as written the check stays at `medium`.
          `approve_lower` then sets the band to `medium` below the rule band `high`. #27 rejects
          a recompute on approval, so the check has to be closed on the event.
Undo:     Drop the equal-band branch in `Segment.apply`. I6 then needs another guard on approval,
          for example 409 when the proposal is below the current rule band.

## D5. The I9 concurrency test fails only with both of its guards removed (PR 4, 2026-10-18)

Plan:     Tests, integration row "Two concurrent resolves of `c-1`: one 200, one 409 | I9 | the
          `status = 'open'` condition is removed".
Built:    `test_i9_concurrent_resolves_one_200_one_409` fails (`[200, 200]`) when the
          `status = 'open'` condition on the check update is removed and the version check on the
          segment update is bypassed. With either guard in place it passes. This matches the
          plan's own I7 row, "the partial unique index is removed and the version check is
          bypassed".
Why:      Resolving writes the segment under its version number (aggregates.md A2, domain.md I9
          "under the segment version number"). The second resolve then matches 0 segment rows,
          reruns (A4) and finds the check resolved: 409 without the status condition. Checked by
          removing each guard alone, then both.
Undo:     None in code. Making the status condition the only guard would mean resolving without
          raising the segment version, which A2 and I9 rule out.

## D6. One guarded upsert records both check events (PR 5, 2026-10-18)

Plan:     Architecture, Notifier: "`ManualRiskCheckRequired`: insert alert `open` `ON CONFLICT
          (check_id) DO NOTHING`. `RiskCheckResolved`: upsert the alert as `closed`. Close only rows
          that are `open` (I14)." Tests, integration row "'Closed' then a replayed 'opened' leaves
          the alert closed | I14 | the close-only-open rule is removed". aggregates.md A2,
          `on_segmenter_event`: load the alert by `checkId`, then call `alert.open()` or
          `alert.close(decision)`.
Built:    Both check events go through one statement in `SqlAlertRepository.record`:
          `INSERT … ON CONFLICT (check_id) DO UPDATE … WHERE alerts.status = 'open'`. The handler
          builds the alert its event implies (`Alert.of`) and records it, with no load first. A
          late "opened" finds a closed row and changes nothing, as the plan states.
          `test_us8_i14_replayed_opened_after_close_stays_closed` fails with the `WHERE` removed.
Why:      Under the split statements a late "opened" is stopped by `DO NOTHING`, never by the
          close-only-open rule, so the I14 replay test cannot fail when that rule is removed. A
          load before the write also sees no row while another delivery's insert for the same
          check is uncommitted, so the outcome has to be decided inside the statement
          (`test_i13_concurrent_opened_then_resolved_one_closed_alert` and its reverse).
Undo:     Split `record` into `DO NOTHING` for "opened" and the guarded upsert for "resolved", and
          name `DO NOTHING` as the guard of the I14 replay test.

## D7. Verification `curl` calls need a host (PR 5, 2026-10-18)

Plan:     Verification: `curl -s ':8003/merchants/m-100/alerts?status=open'` -> `[]`. The `:8001`
          and `:8002` calls use the same form.
Built:    The same calls with `http://localhost` before the port:
          `curl -s 'http://localhost:8003/merchants/m-100/alerts?status=open'` -> `[]`, 200.
Why:      curl 8.7.1 rejects a URL with no host. It exits with code 3 (URL malformed) and sends no
          request (checked against the running stack).
Undo:     None in code. Only the plan's verification text changes.

## D8. Dead-letter depth is read by pulling and releasing (PR 6, 2026-10-18)

Plan:     Architecture, Dead letters: "`dead-letter-monitor` (#37) checks every dead-letter
          subscription every 60 s and logs at `ERROR` when depth is above 0". #37: "reads each
          dead-letter subscription's depth".
Built:    `infra/pubsub/monitor.py` pulls every message on each dead-letter subscription, counts
          them, and releases them at once (ack deadline 0). Nothing is acked. Depth above 0 logs
          `<subscription> holds <n> dead-lettered message(s)` at `ERROR`.
          `e2e/test_dead_letter_monitor.py` checks twice and reads the same depth both times.
Why:      The emulator serves no backlog metric. Pub/Sub exposes depth only through Cloud
          Monitoring (`num_undelivered_messages`), which #37 leaves to v2. While a check holds the
          messages, a replay running at that moment can see fewer of them and stop early; running
          the replay again picks up the rest.
Undo:     Read `num_undelivered_messages` from Cloud Monitoring in `depth()`, which needs real GCP.

## D9. The Pub/Sub-down test pauses the emulator instead of stopping it (PR 6, 2026-10-18)

Plan:     Tests, end to end: "Pub/Sub stopped: `POST` still 201; published after restart" (#2).
Built:    `e2e/test_delivery.py::test_2_pubsub_down_post_201_published_after` runs
          `docker compose pause pubsub`, posts a chargeback (201, no segment), then unpauses and
          reads band `medium`.
Why:      The emulator holds topics and subscriptions in memory only. After
          `docker compose stop pubsub` and `start`, `GET /v1/projects/risk-segmenter-local/topics`
          returns no topics (checked against the running stack). The relay would then publish
          into nothing until `pubsub-init` ran again, and any message published between topic and
          subscription creation would reach no subscriber. Real Pub/Sub keeps its topology through
          an outage, so a pause is the closer match.
Undo:     Stop and start the container, re-run `pubsub-init` before the relay reconnects, and
          accept that the test then measures emulator recovery as well as the outbox.

## D10. The README starts the stack with `--build` (PR 7, 2026-10-18)

Plan:     README outline, "Run it: `docker compose up --wait`, three `curl` calls".
Built:    `docker compose up --build --wait`, as CI runs it.
Why:      The Compose project is named `risk-segmenter`, so every checkout shares image names.
          Without `--build`, Compose reuses any image built earlier from another checkout or an
          older commit, and the stack can run code other than the code cloned.
Undo:     Drop `--build` from the README. A machine that has never built these images gets the
          same result; one that has must run `docker compose build` first.

## D11. The README repeats the segment and alerts reads (PR 7, 2026-10-18)

Plan:     Verification: the `POST` returns 201, then the segment read returns
          `{"band":"medium",…}`, with no wait between them.
Built:    The README calls `curl` as in D3 and D7, and says scoring and alerting run after the 201,
          so the next reads can return 404 `merchant m-100 not seen` and `[]` for a second or two
          and are repeated.
Why:      Scoring is asynchronous by design (brief: recording never waits on scoring). In a fresh
          clone the segment read sent straight after the 201 returned 404; the band was `medium`
          0.2 s later (checked against the running stack).
Undo:     None in code. Only the README and the plan's verification text differ.
