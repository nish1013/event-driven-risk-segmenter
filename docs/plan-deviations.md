# Plan deviations

Open for the owner's review. Not decisions.

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
