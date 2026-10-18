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

## D3. Verification `curl` needs a JSON content type (PR 2, 2026-10-18)

Plan:     Verification: `curl -s -XPOST :8001/activities -d @e2e/fixtures/chargeback.json` -> 201.
Built:    The same call with `-H 'Content-Type: application/json'`. The endpoint reads only JSON
          bodies.
Why:      `curl -d` sends `application/x-www-form-urlencoded`. FastAPI then does not parse the body
          as JSON and returns 422 (checked against the running stack).
Undo:     Accept any content type as JSON in `recording.entrypoints.api`. That widens the endpoint
          to form posts from browsers, so the header is the smaller change.
