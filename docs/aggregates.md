# Aggregates and units of work

How each service keeps its rules true inside one transaction. Code and tests cite the ids A1-A6.
Decision: [decisions.md](decisions.md) #42. Invariants I1-I16: [domain.md](domain.md).

## The picture

```
Recording            Segmenter                          Notifier
+-----------+        +------------------------------+   +----------+
| Activity  |        | Segment m-100     version 7  |   | Alert    |
|  a-1      |        |   band: medium               |   |  c-1     |
+-----------+        |   facts: chargeback 10-01,   |   |  open    |
                     |          chargeback 10-05    |   +----------+
                     |   check: none                |
                     +------------------------------+
  - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
  owned by the unit of work, outside every box:  outbox, processed_events
```

Each box is one aggregate. A transaction changes one box, and only through its root.

## One run

```
ActivityRecorded: chargeback 10-20 for m-100
  -> unit of work opens a transaction
  -> load Segment m-100 at version 7, with its fact counts
  -> segment.apply(fact)          the root adds the fact: 3 chargebacks in 30 days
                                  the root decides: band high
                                  the root raises RiskSegmentChanged medium -> high
  -> unit of work moves the event into the outbox
  -> UPDATE segments ... WHERE version_number = 7     now version 8
  -> commit -> ack

  0 rows updated: another writer won -> roll back -> rerun from "load"
```

## Words

| Word | Meaning |
| --- | --- |
| Aggregate | A consistency boundary: a root entity, plus only the objects that must change with it in one transaction. Often the root alone |
| Root | The entity that owns the aggregate's rules. It is the only way in, and it rejects a change that breaks a rule |
| Unit of work | One use case as one atomic transaction. It succeeds or fails as a block |
| Version number | A counter on the root. Two concurrent writers both try to raise it; only one commits |

## A1. Three aggregates

**Rules:**

1. A transaction writes at most one aggregate. A use case that needs two has the wrong boundary.
2. An aggregate holds more than its root only for a rule that spans objects.
3. Repositories return roots only. Reads may span aggregates.

| Root | Service | Holds | Rules it owns | Keeps true |
| --- | --- | --- | --- | --- |
| `Segment` | Segmenter | band, facts, checks | Decide the band ([policy-v1.md](policy-v1.md)). Hold a drop as a check. Close it on a rise. Approve or keep | I5 one segment per merchant. I6 band never below the rules. I7 one open check. I8 a check proposes a lower band. I9 a resolved check never changes. I11 one event per change |
| `Activity` | Recording | itself | Same `activityId` with another body is refused (409). Never changed | I1 one activity per id. I2 never changed. I3 one `ActivityRecorded` each |
| `Alert` | Notifier | itself, both kinds | A check alert moves `open -> closed` only | I13 one alert per check. I14 a closed alert never reopens |

`Segment` holds facts and checks because I6-I9 compare the band with them. `Activity` and `Alert` have
no rule that spans objects, so each is one entity, with no base class.

## A2. One unit of work per use case

A handler only wires: load the root, call one method, commit. It decides nothing.

```
record_activity          POST /activities
  load    Activity, if the id exists
  call    activity.same_body(request)          on a repeat only
  write   activity, outbox
  then    201 new, 200 same body, 409 other body

on_activity_recorded     segmenter-worker
  load    Segment, open check, fact counts     facts are counted in SQL, never loaded
  call    segment.apply(fact, counts)
  write   processed event, segment, fact, prune of this merchant's old facts, check, outbox
  then    ack

resolve_risk_check       POST /risk-checks/{checkId}/resolution
  load    Segment that holds the check
  call    segment.resolve(checkId, decision, officerId, reason)
  write   segment, check, outbox
  then    200, 404 no check, 409 check not open

on_segmenter_event       notifier-worker
  load    Alert by checkId                     check events only
  call    alert.open() or alert.close(decision)
  write   processed event, alert
  then    log line, ack
```

## A3. Commit, events and side effects

1. Commit is explicit. Any exception or early exit rolls back.
2. A root records its events in the order it raises them. At commit, the unit of work moves them into
   the outbox in that order. On approval, `RiskCheckResolved` therefore comes before
   `RiskSegmentChanged`.
3. Ack, reply and the alert log line happen only after commit. The log line is best effort: the alert
   row is the record.

## A4. A version conflict reruns the whole use case

The update matches 0 rows: roll back, then rerun with a new unit of work. Each rerun waits longer than
the one before. A use case runs at most 3 times.

| Entry | After 3 conflicts |
| --- | --- |
| Worker | nack. Pub/Sub redelivers |
| `resolve_risk_check` | 503. If a rerun finds the check no longer open, 409 |

**Why rerun before nack:** a merchant under a failed-login burst conflicts often. Five nacks send a
valid event to the dead-letter topic and block every later event for that merchant.

## A5. Outside every aggregate

| Item | Belongs to | Why it may span aggregates |
| --- | --- | --- |
| `outbox` | the unit of work | The relay marks rows sent, and cleanup deletes them after 7 days. No rule reads either |
| `processed_events` | the unit of work | A unique key keeps each event applied once (I10). Cleanup deletes by age only |
| Read endpoints | queries | Reads never change state |

## A6. Why each pattern is here

| Without | What breaks |
| --- | --- |
| `Segment` | The band, checks and facts change in several places. One path skips the version check. I6-I9 break |
| A unit of work | Each handler commits on its own. One path drops the outbox row or the processed event. I3, I10, I11 break |
| A fake unit of work | Every handler unit test needs Postgres |

## Revisions

| Date | Change | Decision |
| --- | --- | --- |
| 2026-10-18 | First version: three aggregates, four units of work, outbox owned by the unit of work | #42 |
