# Domain: Risk Segmenter

Order and checks: stories are confirmed before invariants; invariants are confirmed before the rest is
derived. Settled input: [brief.md](brief.md), [brief-review.md](brief-review.md),
[decisions.md](decisions.md) #1-#22.

## Brief

> A payment platform must maintain a live **risk segment** for every merchant: `low` | `medium` | `high`.
>
> Notification latency or failure must not block or undo a segment decision. Recording must succeed
> even if scoring is temporarily unavailable.

Source: [brief.md](brief.md). Scope is its functional requirements, as refined by the decision log.
Out of scope for v1: decay (#7), email or Slack delivery (#12), cloud deployment, IaC, API auth and
tracing (#16).

## User stories

Values used throughout: merchant `m-100`, policy version `1`, rules from #17. Times are UTC.

### US1: Record an activity (Recording)

As a payment platform service, I want to record a risk-relevant activity for a merchant, so that it
is kept as a durable fact and scored without my request waiting.

| Given | When | Then |
| --- | --- | --- |
| No activity `a-1` | `a-1` is recorded: `m-100`, `chargeback`, `occurredAt` 2026-10-01T10:00, payload `{"transactionId": "t-9", "amount": "120.00", "currency": "GBP", "reasonCode": "4837"}` | 201 with the activity. One activity stored. One `ActivityRecorded` with a new `eventId` and the same fields is published |
| `a-1` stored | `a-1` is recorded again with the same body | 200 with the original activity. No new row. No new event |
| `a-1` stored as `chargeback` | `a-1` is recorded with type `failed_login` | 409. Nothing changes |
| Segmenter and Notifier stopped | `a-2` is recorded | 201. `a-2` is scored once Segmenter is back |
| Pub/Sub down | `a-3` is recorded | 201. `ActivityRecorded` for `a-3` is published once Pub/Sub is back |
| - | An activity of type `refund` is recorded | 422. Nothing stored |
| - | An activity with no `activityId` is recorded | 422. Nothing stored |
| - | A `chargeback` with no `currency` in its payload is recorded | 422. Nothing stored |
| - | A `failed_login` with an extra payload field `ip` is recorded | 422. Nothing stored |
| Now is 2026-10-01T10:00 | An activity with `occurredAt` 2026-10-01T10:06 is recorded | 422. Nothing stored |

Payload per type (#30). Every field is required. An unknown field is rejected.

| Type | Fields |
| --- | --- |
| `chargeback` | `transactionId`, `amount` (decimal string), `currency` (ISO 4217), `reasonCode` |
| `velocity_breach` | `metric` (`txn_count` \| `txn_amount`), `window` (ISO 8601 duration), `observed`, `limit` (decimal strings) |
| `kyc_expired` | `kycCaseId`, `documentType` (`identity` \| `business_registration` \| `proof_of_address`) |
| `failed_login` | `userId`, `method` (`password` \| `otp`) |

Decided: a repeated `activityId` with a different body returns 409 (#26). An `occurredAt` more than
5 minutes ahead returns 422 (#31). An unknown `merchantId` is accepted (#14).

### US2: Raise a merchant's band (Segmenter)

As a risk officer, I want a merchant's band raised as soon as its activity crosses a rule, so that
controls tighten without delay.

| Given | When | Then |
| --- | --- | --- |
| `m-100` unknown | `ActivityRecorded`: `chargeback` at 10-01 | Band `medium`. Rule `chargeback_1_in_30d`, policy `1`. `RiskSegmentChanged` `low -> medium` |
| `m-100` `medium`, chargebacks at 10-01 and 10-05 | `chargeback` at 10-20 | Band `high`, rule `chargeback_3_in_30d`. `RiskSegmentChanged` `medium -> high` |
| `m-100` `medium`, one chargeback at 10-01 | `chargeback` at 10-05 | Band stays `medium`. No event |
| `m-200` unknown | `failed_login` at 10-01T09:00 | `m-200` stored at `low`. No event |
| `m-200` `low`, 9 failed logins from 09:00 to 09:40 | `failed_login` at 09:50 | Band `medium`, rule `failed_login_10_in_1h` |
| `m-100` `medium` by `chargeback_1_in_30d` | `kyc_expired` at 10-06 | Band `high`, rule `kyc_expired` (the highest band wins, #19) |
| `m-100` `low`, no facts | The same `chargeback` event is delivered twice | It is counted once. Band `medium`. One `RiskSegmentChanged` |
| `m-100` `medium`, chargebacks at 09-01T10:00 and 10-01T10:00 | `chargeback` at 10-01T10:00 + 1s, anchor 10-01T10:00:01 | Band `medium`: 09-01T10:00 is outside the window by 1s |

Decided: the window is anchored at the latest `occurredAt` among the merchant's facts (#23). Bounds
are inclusive, `occurredAt >= anchor - 30d` (#24). `kyc_expired` has no window and is never pruned
(#25).

### US3: Hold a lower band for review (Segmenter)

As a risk officer, I want a drop in band to wait for my confirmation, so that a band never falls only
because facts aged out of a window.

| Given | When | Then |
| --- | --- | --- |
| `m-100` `high`, chargebacks at 09-01, 09-05 and 09-10. No open check | `failed_login` at 10-20. Recompute gives `low` | Band stays `high`. Check `c-1` opens, proposing `low`. `ManualRiskCheckRequired` |
| `m-300` `high`, check `c-1` open, proposing `medium` | Another recompute gives `low` | Band stays `high`. `c-1` now proposes `low`. No event |
| `m-400` `medium`, check `c-2` open, proposing `low` | `kyc_expired`. Recompute gives `high` | Band `high`. `c-2` closed as `superseded`. `RiskSegmentChanged` `medium -> high`. `RiskCheckResolved` |
| `m-100` `high`, check `c-1` open, proposing `medium` | `kyc_expired`. Recompute gives `high` | Band stays `high`. `c-1` closed as `superseded`. `RiskCheckResolved` (#44) |

Decided: rules run on every event. A lower result while a check is open updates its proposed band
and emits nothing (#27). A rise closes the check as `superseded` and emits `RiskCheckResolved` (#28). So does a
recompute that gives the current band (#44).

### US4: Resolve a risk check (Segmenter)

As a risk officer, I want to approve or reject a proposed drop, so that a band is lowered only by a
human decision.

| Given | When | Then |
| --- | --- | --- |
| `c-1` open on `m-100`: band `high`, proposing `low` | Officer `o-7` resolves `c-1`: `approve_lower`, reason "chargebacks settled" | 200. Band `low`. `c-1` resolved. `RiskCheckResolved`, then `RiskSegmentChanged` `high -> low` |
| `c-1` open | `o-7` resolves `c-1`: `keep_current`, reason "pattern still active" | 200. Band stays `high`. `RiskCheckResolved` only |
| `c-1` resolved with `keep_current` | Next recompute gives `low` | Check `c-3` opens. `ManualRiskCheckRequired` (#22) |
| `c-1` already resolved | `o-7` resolves `c-1` again | 409. Nothing changes |
| No check `c-9` | `o-7` resolves `c-9` | 404 |
| `c-1` open | Resolved with no `reason` | 422. Nothing changes |

Assumption: `officerId` is accepted as given until auth lands (#22, F18).

### US5: Read a merchant's segment (Segmenter)

As a risk officer, I want to read a merchant's current band and why it was set, so that I can explain
the decision.

| Given | When | Then |
| --- | --- | --- |
| `m-100` `high` by `chargeback_3_in_30d`, policy `1`, check `c-1` open | `m-100` is read | Band `high`, rule `chargeback_3_in_30d`, policy `1`, `updatedAt`, open check `c-1` proposing `low` |
| No `m-999` | `m-999` is read | 404 |

### US6: Alert on a band change (Notifier)

As a risk officer, I want an alert when a merchant's band changes, so that I can act on it.

| Given | When | Then |
| --- | --- | --- |
| - | `RiskSegmentChanged` `m-100` `low -> medium` | One alert stored: kind `segment_changed`, `low -> medium`, rule, policy. One structured log line |
| Alert stored for that `eventId` | The same event is delivered again | Still one alert |
| Notifier stopped, then the event is published | Notifier restarts | The alert is stored. Segmenter's band was never affected |

### US7: Alert on a pending risk check (Notifier)

As a risk officer, I want an alert when a drop waits for my review, so that a check is not left open
unseen.

| Given | When | Then |
| --- | --- | --- |
| - | `ManualRiskCheckRequired` `c-1` on `m-100`: current `high`, proposed `low` | One alert stored: kind `risk_check_required`, status `open`, check `c-1` |
| Alert stored for that `eventId` | The same event is delivered again | Still one alert |

### US8: Close a risk check alert (Notifier)

As a risk officer, I want the check alert closed once the check is resolved, so that open alerts are
only the ones waiting on someone.

| Given | When | Then |
| --- | --- | --- |
| `c-1` alert `open` | `RiskCheckResolved` `c-1`: `keep_current` by `o-7` | `c-1` alert `closed`, decision `keep_current` |
| `c-1` alert `open` | `RiskCheckResolved` `c-1`: `superseded` | `c-1` alert `closed`, decision `superseded` |
| `ManualRiskCheckRequired` `c-1` dead-lettered. `RiskCheckResolved` `c-1` handled | `ManualRiskCheckRequired` `c-1` is replayed | `c-1` alert stays `closed` |

Decided: all Segmenter events go to one topic, `segmenter-events`, ordering key `merchantId` (#29).
A replay can still deliver "opened" after "closed", so `closed` is final for a check alert.

### US9: Read alerts (Notifier)

As a risk officer, I want to list a merchant's alerts, so that I can see what changed and what waits on me.

| Given | When | Then |
| --- | --- | --- |
| `m-100` has a `segment_changed` alert at 10-01 and an open `c-1` alert at 10-20 | Alerts for `m-100` are read | Both, newest first, each with kind and status |
| Same | Open alerts for `m-100` are read | Only the `c-1` alert |
| No alerts for `m-999` | Alerts for `m-999` are read | 200, empty list |

### US10: Replay dead-lettered messages (operator)

As an operator, I want to republish dead-lettered messages after a fix, so that a merchant's parked
events are processed.

| Given | When | Then |
| --- | --- | --- |
| One `ActivityRecorded` for `m-100` dead-lettered from `segmenter-activity-recorded`. Fix deployed | The replay command runs for that subscription | It is republished and processed once. The dead-letter queue is empty |
| The replayed `eventId` was already processed | It arrives again | Acked. Nothing changes (#4) |
| Later events for `m-100` were processed while it was parked | It is replayed | The rules see all facts, anchored at the latest `occurredAt` (#23) |

## Not stories

These are requirements on the system, not actions. They become tests in the plan.

- A new consumer is added without changing a producer.
- Each service reads and writes only its own schema.

## Invariants

An invariant is a condition on stored state that is always true: a query can check it with no request
in hand. Preconditions, validation and response behaviour are acceptance criteria on the stories.

"Rule band" means the band the policy gives over a merchant's stored facts, anchored per #23.

| # | Invariant | Kind | Constraint that keeps it true | Can two requests break it together? |
| --- | --- | --- | --- | --- |
| I1 | An `activityId` names at most one activity | Immediate | Primary key on the activity id | Yes: two retries of `a-1` at once |
| I2 | A recorded activity is never changed or removed (#21) | Immediate | No update or delete path. The service's DB role has insert and select only | No |
| I3 | Every recorded activity has exactly one `ActivityRecorded` event, written in the same transaction | Immediate | Activity and outbox row in one transaction (#2). Unique activity id on the outbox. A sent row is deleted after 7 days (#40), so the row proves the event only until then | No, given I1 |
| I4 | Every `ActivityRecorded` event is published | Eventual | Relay retries until ack (#3) | No |
| I5 | A merchant has exactly one segment | Immediate | Primary key on the merchant id | Yes: two first events for a new merchant |
| **I6** | **A merchant's band is never lower than its rule band** | Immediate | Fact insert and recompute in one transaction. Version number on the segment (#9) | **Yes, the hard one.** Two events for `m-100` on two instances. Each misses the other's fact, and the last write sets a band below the rule band |
| I7 | A merchant has at most one open check (#18) | Immediate | Partial unique index on open checks per merchant. Segment version number | Yes: two lower recomputes at once |
| I8 | An open check proposes a band lower than the merchant's band | Immediate | Checked by the segment on every save | No, given I6's guard |
| I9 | A resolved check never changes | Immediate | Resolve only an open check, under the segment version number | Yes: two officers resolve `c-1` at once |
| I10 | A consumer applies each `eventId` at most once (#4) | Immediate | Primary key on processed event id, written with the state change | Yes: a redelivery while the first is still running |
| I11 | Every band change, check opened and check resolved has exactly one Segmenter event, written in the same transaction | Immediate | State change and outbox row in one transaction (#2). Cleanup as in I3 (#40) | No |
| I12 | Segmenter holds every recorded activity a rule can still read: all `kyc_expired` facts, and every other fact inside its type's window | Eventual | Repaired by `ActivityRecorded`. Pruning removes only facts older than their type's window, never `kyc_expired` (#6, #25, #38) | No |
| I13 | A check has at most one alert | Immediate | Unique check id on alerts | Yes: a replayed "opened" while "closed" is applied |
| I14 | A closed check alert never reopens (#29) | Immediate | Status moves only `open -> closed` | No |
| I15 | A check alert is closed exactly when its check is resolved | Eventual | Repaired by `RiskCheckResolved` on `segmenter-events` (#29) | No |
| I16 | Every band change has exactly one `segment_changed` alert | Eventual | Repaired by `RiskSegmentChanged`, deduplicated by I10 | No |

Moved out by the sort test:

| Looked like | Goes to |
| --- | --- |
| A repeated `activityId` with a different body is refused | AC on US1 (#26) |
| Type, payload and `occurredAt` are valid | AC on US1 (#30, #31) |
| Only an open check can be resolved; a reason is required | AC on US4 |
| No IP address or email is stored | Decision #30 |

## Words used

| Word | Context | Meaning |
| --- | --- | --- |
| Merchant | all | Account under risk monitoring, known by `merchantId` |
| Activity | Recording | A risk-relevant fact sent by a client: `activityId`, `merchantId`, type, `occurredAt`, payload |
| Fact | Segmenter | Segmenter's record of one activity: its type and `occurredAt` |
| Band | Segmenter | `low`, `medium` or `high` |
| Segment | Segmenter | A merchant's band, the rule that set it, and the policy version |
| Rule | Segmenter | A threshold on one type inside a window (#17) |
| Rule band | Segmenter | The band the rules give over a merchant's facts |
| Window, anchor | Segmenter | The period a rule counts; it ends at the anchor, the latest `occurredAt` (#23) |
| Check | Segmenter | A held drop, waiting for an officer. Holds the proposed band |
| Decision | Segmenter | How a check was resolved: `approve_lower`, `keep_current` or `superseded` |
| Officer | Segmenter | The person who resolves a check, known by `officerId` |
| Alert | Notifier | A stored notice for officers. Kind `segment_changed` or `risk_check_required`. A check alert has status `open` or `closed` |
| Event | all | A published fact, known by `eventId` |
| Outbox | Recording, Segmenter | Events waiting to be published, written with the change that caused them (#2) |

## Contexts

One context per service. No service asks another; every arrow is a copy carried by an event (#33).

| Context | Owns (invariants, words) | Asks | Copies, and why |
| --- | --- | --- | --- |
| Recording | I1-I4. Activity | nothing | nothing |
| Segmenter | I5-I12. Fact, band, segment, rule, check, decision | nothing | Activity type and `occurredAt`, as facts. Rules read history on every event, and scoring must go on while Recording is down (#6) |
| Notifier | I13-I16. Alert | nothing | A band change and a check's status. Shown to officers and allowed to lag. A band change is a snapshot of one moment (#12, #29) |

```
client --HTTP--> Recording --activity-recorded--> Segmenter --segmenter-events--> Notifier
officer --HTTP--> Segmenter (resolve, read)        officer --HTTP--> Notifier (read)
```

## Aggregates and data model

| Aggregate | Root | Context | Identity | Guards invariants | Tables | Guard |
| --- | --- | --- | --- | --- | --- | --- |
| Activity | `Activity` | Recording | `activityId` | I1, I2, I3 | `activities` | PK on `activities.activity_id`. Insert-only DB role |
| Merchant risk | `Segment` | Segmenter | `merchantId` | I5, I6, I7, I8, I9, I11 | `segments`, `facts`, `checks` | PK on `segments.merchant_id`. `segments.version_number` checked on every save. Partial unique index on `checks(merchant_id)` where open |
| Alert | `Alert` | Notifier | `alertId`. A check alert is also found by `checkId` | I13, I14 | `alerts` | Unique `alerts.check_id`. Close only where `open` |

Owned by the unit of work, not by an aggregate ([aggregates.md](aggregates.md) A5, #42):

| Table | Why it is outside every aggregate |
| --- | --- |
| `outbox`, one per producer | It holds the events of many roots. The relay and cleanup (#40) write across them, and no invariant reads `sent_at` |
| `processed_events`, one per consumer | I10 spans the whole set, so a unique key guards it. It is written in the same transaction as the aggregate it applies to |

**Why facts sit inside `Segment`:** I6 compares the band with the facts. Both must change in one
transaction, under one version number. Facts are counted in SQL, not loaded (#39). The rules are in
[policy-v1.md](policy-v1.md).

**Why one activity is one aggregate:** no invariant spans two activities, so recording never locks.

**Places each fact lives:**

| Fact | Lives in | Second place, and why |
| --- | --- | --- |
| Activity, with payload | `recording.activities` | none |
| Activity type and `occurredAt` | `recording.activities` | `segmenter.facts`: rules read history, Recording may be down (#6) |
| Band, rule, policy version | `segmenter.segments` | Alert details hold `from`, `to` and rule: a snapshot of the change, never updated |
| Check status, proposed band, decision | `segmenter.checks` | `notifier.alerts.status` (`open`, `closed`): shown to officers, may lag (I15) |
| Which events a consumer applied | that consumer's `processed_events` | none. Each consumer records only its own work |

## Events

All events carry `eventId`, `merchantId` and `occurredAt` (when the change happened). Ordering key is
`merchantId` on every topic.

| Event | Published by | Topic | When | Also carries | Handled by |
| --- | --- | --- | --- | --- | --- |
| `ActivityRecorded` | Recording | `activity-recorded` | An activity is stored | `activityId`, type, activity `occurredAt`, payload | Segmenter |
| `RiskSegmentChanged` | Segmenter | `segmenter-events` | The band rises, or a check is approved | `from`, `to`, rule, `policyVersion` | Notifier |
| `ManualRiskCheckRequired` | Segmenter | `segmenter-events` | A check opens | `checkId`, current band, proposed band, `policyVersion` | Notifier |
| `RiskCheckResolved` | Segmenter | `segmenter-events` | A check is approved, kept or superseded | `checkId`, decision, `officerId` and reason (empty when `superseded`) | Notifier |

On approval, `RiskCheckResolved` is written to the outbox before `RiskSegmentChanged`, so it is
published first.

## Checks

| Check | Result |
| --- | --- |
| Each invariant is a business rule, stated without a table | Pass. I1-I16 name no table |
| The invariant two requests can break together has a database guard and a failing test | Pass. I6: version number on `segments`. The test is in the plan |
| Every word comes from the stories or invariants | Pass. "Outbox" and "version number" come from the guards and #2, #9 |
| Each fact lives in one place, or has a written reason | Pass. Three second places, each with a reason in the table above |
| No invariant exists only to keep two copies in agreement | Pass with a reason. I12 and I15 keep copies in agreement. Both copies are deliberate: removing them means one service asking another, which the brief forbids |
| Every arrow is ask by default; every copy names its reason | Pass. No asks. Two copies, both with reasons |
| No transaction changes two aggregates | Pass. Resolving a check changes the check and the band, both inside `Segment`. Pruning deletes only that merchant's facts. The relay and cleanup write only unit-of-work tables |
| Every requested guard fits the model | Pass. The unique keys guard inserted rows. Status and band changes go through the version number or a conditional update |
