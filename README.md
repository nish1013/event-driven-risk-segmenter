# Risk Segmenter

Keeps a live risk band (`low`, `medium`, `high`) for every merchant, from the activities a payment
platform records. Three Python services share one Postgres database, each in its own schema, and talk
only through Pub/Sub.

- Recording never waits on scoring or notification.
- A failed Notifier never undoes a band.
- A band never falls on its own: a drop waits for a risk officer.

```
client --POST /activities--> recording-api
   tx: activity + outbox row
recording-relay --activity-recorded, key merchantId--> segmenter-worker
   tx: fact + segment + check + outbox rows, version check
segmenter-relay --segmenter-events, key merchantId--> notifier-worker
   tx: alert
officer --POST /risk-checks/{checkId}/resolution--> segmenter-api
officer --GET segment--> segmenter-api      officer --GET alerts--> notifier-api
```

The scoring rules are in [docs/policy-v1.md](docs/policy-v1.md).

## Run it

Needs Docker with Compose v2 and curl. Ports 5432, 8001-8003 and 8085 must be free.

```sh
docker compose up --build --wait
```

The first run builds the images and takes a few minutes. Every container is healthy, and
`pubsub-init` has exited, when the command returns.

Record a chargeback for merchant `m-100`:

```sh
curl -s -X POST http://localhost:8001/activities \
  -H 'Content-Type: application/json' \
  -d @e2e/fixtures/chargeback.json
```

Returns 201 and the activity. Scoring and alerting run after the 201, through the outbox and
Pub/Sub. For a second or two the next calls can return 404 `merchant m-100 not seen` and `[]`.
Repeat them.

Read the merchant's band:

```sh
curl -s http://localhost:8002/merchants/m-100/segment
```

Returns `"band":"medium"` and `"rule":"chargeback_1_in_30d"`. Read the merchant's alerts:

```sh
curl -s http://localhost:8003/merchants/m-100/alerts
```

Returns one `segment_changed` alert, `low` to `medium`. Add `?status=open` to list only the risk
checks that wait on an officer.

Stop the stack and delete its data:

```sh
docker compose down -v
```

## Services, topics, schemas

| Service | Containers | Port | Schema | Publishes to | Consumes from |
| --- | --- | --- | --- | --- | --- |
| Recording | `recording-api`, `recording-relay` | 8001 | `recording` | `activity-recorded` | - |
| Segmenter | `segmenter-api`, `segmenter-worker`, `segmenter-relay` | 8002 | `segmenter` | `segmenter-events` | `segmenter-activity-recorded` |
| Notifier | `notifier-api`, `notifier-worker` | 8003 | `notifier` | - | `notifier-segmenter-events` |
| Infrastructure | `postgres`, `pubsub`, `pubsub-init`, `dead-letter-monitor` | 5432, 8085 | - | - | every `*-dead-letter` subscription |

| Endpoint | Service |
| --- | --- |
| `POST /activities` | Recording |
| `GET /merchants/{merchantId}/segment` | Segmenter |
| `POST /risk-checks/{checkId}/resolution` | Segmenter |
| `GET /merchants/{merchantId}/alerts?status=open` | Notifier |
| `GET /healthz` | every API |

Each subscription moves a message to its `<subscription>-dead-letter` topic after 5 failed attempts.
`dead-letter-monitor` logs at `ERROR` every 60 s while any dead-letter subscription holds messages.
To republish them once the cause is fixed:

```sh
docker compose exec segmenter-worker python -m segmenter.entrypoints.replay --subscription segmenter-activity-recorded
docker compose exec notifier-worker python -m notifier.entrypoints.replay --subscription notifier-segmenter-events
```

## Design

| Document | What it holds |
| --- | --- |
| [docs/domain.md](docs/domain.md) | User stories, invariants, events |
| [docs/decisions.md](docs/decisions.md) | Every decision, with its reason and the alternatives rejected |
| [docs/policy-v1.md](docs/policy-v1.md) | Rules, windows, band decision |
| [docs/aggregates.md](docs/aggregates.md) | Aggregates and units of work |
| [docs/plan.md](docs/plan.md) | Stack, tables, tests, build order |
| [docs/plan-deviations.md](docs/plan-deviations.md) | Where the build departs from the plan, and why |

## Tests

Needs [uv](https://docs.astral.sh/uv/). Each service has its own project and lockfile; the repository
root holds the end-to-end tests.

| Layer | Needs | Command |
| --- | --- | --- |
| Unit | nothing | `uv run --locked --directory services/<service> pytest tests/unit` |
| Integration | the stack up | `uv run --locked --directory services/<service> pytest tests/integration` |
| End to end | the stack up | `uv run --locked pytest e2e` |

`<service>` is `recording`, `segmenter` or `notifier`. Lint, format and type checks, per service:

```sh
uv run --locked --directory services/<service> ruff check .
uv run --locked --directory services/<service> ruff format --check .
uv run --locked --directory services/<service> mypy src
```

The end-to-end tests stop, pause and restart containers. Run them on a stack used for nothing else.
