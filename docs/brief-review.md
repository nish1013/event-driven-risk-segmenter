# Brief review

Findings from reviewing [brief.md](brief.md) against its own requirements and known messaging failure modes. Each finding links to the decision in [decisions.md](decisions.md) that resolves it.

| Severity | Meaning |
|---|---|
| High | Data loss or a wrong segment |
| Medium | Wrong alert history, a stuck merchant, or an undefined behaviour the code must choose |
| Low | Unspecified detail with a safe default |

## 2026-10-17 - brief v1 (commit `1a8d85c`)

| ID | Finding | Severity | Status | Resolved by |
|---|---|---|---|---|
| F1 | Commit-then-publish loses the event if the process stops between the two steps. The activity is stored but never scored. | High | Resolved | #2, #3 |
| F2 | The policy needs past activity, but Segmenter cannot read the `recording` schema, and one event holds only the present. | High | Resolved | #5, #6 |
| F3 | A client retry over HTTP records the same activity twice. `eventId` dedup covers consumers only. | High | Resolved | #8 |
| F4 | Events for one merchant can be delivered out of order or processed in parallel. Alert history goes wrong, and two workers can compute a stale band. | High | Resolved | #9 |
| F5 | How a band goes down is not defined: no decay, no lowering activity type, no review step. | Medium | Resolved | #7, #18, #22 |
| F6 | With ordering keys, a message that always fails blocks every later event for that merchant. | Medium | Resolved | #10, #20 |
| F7 | Consumer idempotency on `eventId` is stated, but the mechanism and its restart safety are not. | Medium | Resolved | #4 |
| F8 | Several relay instances can publish one merchant's rows out of order. Pub/Sub preserves arrival order only. | Medium | Resolved | #3 |
| F9 | Rule thresholds per activity type are not specified. | Medium | Resolved | #17 |
| F10 | When several rules fire, which band wins is not defined. | Low | Resolved | #19 |
| F11 | "Ways to read current segment and alerts" names no owning service. | Low | Resolved | #11 |
| F12 | "Alert delivery or log" leaves the v1 channel open. | Low | Resolved | #12 |
| F13 | The effect of a new policy version on existing segments is not defined. | Low | Resolved | #13 |
| F14 | Activity for an unknown merchant is not handled. | Low | Resolved | #14 |
| F15 | Retention of recorded activity is not defined. | Low | Resolved | #21 |
| F16 | Implementation stack is not stated. | Low | Resolved | #15 |
| F17 | Deployment, infrastructure as code, API auth and tracing are not covered. | Medium | Deferred | #1, #16, v2 |
| F18 | `officerId` on `ResolveRiskCheck` is accepted unverified until auth exists. It is an audit field. | Medium | Deferred | #22, v2 |
