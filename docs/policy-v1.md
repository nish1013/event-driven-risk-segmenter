# Segmentation policy v1

How Segmenter sets a merchant's band. Code and tests cite the section ids (`P1`-`P6`).

```
fact stored -> P1 window -> P2 rules -> P3 rule band -> P4 decide -> P6 prune
officer resolves a check -> P5
```

## P1. Window

- Facts are one merchant's stored activities: type and `occurredAt`.
- **Anchor** = the latest `occurredAt` among them (#23).
- A fact counts when `occurredAt >= anchor - window`. Inclusive (#24).

## P2. Rules

| Rule | Type | Count | Window | Band |
| --- | --- | --- | --- | --- |
| `kyc_expired` | `kyc_expired` | 1 | none (#25) | `high` |
| `chargeback_3_in_30d` | `chargeback` | 3 | 30d | `high` |
| `chargeback_1_in_30d` | `chargeback` | 1 | 30d | `medium` |
| `velocity_breach_2_in_7d` | `velocity_breach` | 2 | 7d | `medium` |
| `failed_login_10_in_1h` | `failed_login` | 10 | 1h | `medium` |

A rule fires when its count in the window is at least the number shown. **Row order matters:** P3
reads this table top-down.

## P3. Rule band

Read the P2 table top-down. **The first rule that fires wins.** It gives the band and is recorded as
the reason (#19). No rule fires: `low`.

The table is ordered from the highest band down, so the highest band always wins. **Tie-breaker:**
when two rules of the same band fire, the one higher in the table is recorded.

| Rules that fire | Band | Recorded rule | Why |
| --- | --- | --- | --- |
| `chargeback_1_in_30d`, `kyc_expired` | `high` | `kyc_expired` | higher band |
| `chargeback_1_in_30d`, `failed_login_10_in_1h` | `medium` | `chargeback_1_in_30d` | same band, higher row |
| `kyc_expired`, `chargeback_3_in_30d` | `high` | `kyc_expired` | same band, higher row |

Reordering P2 rows changes the recorded rule. It is a policy change and gets a new version.

## P4. Decide

| Rule band vs current band | Open check | Result | Events |
| --- | --- | --- | --- |
| higher | no | band = rule band | `RiskSegmentChanged` |
| higher | yes | band = rule band, check `superseded` (#28) | `RiskCheckResolved`, `RiskSegmentChanged` |
| same | no | nothing changes | none |
| same | yes | band unchanged, check `superseded` (#44) | `RiskCheckResolved` |
| lower | no | band held, check opens with proposal = rule band (#18) | `ManualRiskCheckRequired` |
| lower | yes | band held, proposal = rule band (#27) | none |

A new merchant starts at `low` (#14).

## P5. Resolve a check

| Decision | Band | Events |
| --- | --- | --- |
| `approve_lower` | = proposal | `RiskCheckResolved`, then `RiskSegmentChanged` |
| `keep_current` | unchanged | `RiskCheckResolved`. The next lower result opens a new check |

## P6. Prune

After P4, delete facts older than `anchor - window` of their type's rule. `kyc_expired` is never
deleted (#38).

## Example

`m-100`, starting unknown.

| Time | Fact | Counts in window | Rule band | Band | Check | Events |
| --- | --- | --- | --- | --- | --- | --- |
| 09-01 | chargeback | 1 cb | `medium` | `medium` | - | changed `low -> medium` |
| 09-05 | chargeback | 2 cb | `medium` | `medium` | - | none |
| 09-10 | chargeback | 3 cb | `high` | `high` | - | changed `medium -> high` |
| 10-06 | failed_login | 1 cb (09-10). Window starts 09-06 | `medium` | `high` | c-1 opens, proposes `medium` | check required |
| 10-12 | failed_login | 0 cb | `low` | `high` | c-1 proposes `low` | none |
| 10-13 | officer approves c-1 | | | `low` | c-1 resolved | resolved, changed `high -> low` |

## Changing the policy

A policy version is data: its number and its P2 table. Segmenter counts facts per rule window in one
SQL query, then P3 picks the band (#39). A new version is a new table and applies from the next event
(#13, #41). Its rules go in a new file like this one, `policy-v2.md`.
