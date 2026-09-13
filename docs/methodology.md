# US Consumer Credit Stress Score — methodology

## Purpose

Detect turning points in the U.S. consumer credit cycle early enough that an investor can see the transmission chain forming:

Household pressure → early delinquency → serious delinquency → bank loss → lending-standard tightening → credit contraction → consumption slowdown.

The dashboard is not a description of “how consumers feel today.” It is a monitor of whether that chain is assembling.

## Why these series (not the KPI nickname)

Names in the spec were not forced onto the nearest FRED ticker.

| KPI | Official series | What it actually is | Rejected lookalike |
|---|---|---|---|
| Credit card early delinquency | NY Fed HHDC Page 13, Credit Card | **Flow** into 30+ days | `DRCCLACBS` is a **stock** 30+ rate at commercial banks |
| Auto early delinquency | NY Fed HHDC Page 13, Auto | Flow into 30+ | `DRALACBS` stock |
| Aggregate household delinquency | NY Fed HHDC Page 11, `100 − Current` | Stock of balances in any delinquent stage | Page 13 Total is a flow; kept as a separate household-pressure **level** |
| Credit card serious delinquency | NY Fed HHDC Page 14, Credit Card | **Flow** into 90+ | Page 12 is 90+ **stock**; used in interpretation text only if needed |
| Auto serious delinquency | NY Fed HHDC Page 14, Auto | Flow into 90+ | |
| Credit card charge-off, all banks | Fed CHGDEL `chgallsa.htm` credit cards; cross-check FRED `CORCCACBS` | Realized loss, SA, annualized | |
| Small/other bank credit card charge-off | Fed CHGDEL `chgothersa.htm` credit cards | Official “Other Banks” (not the 100 largest) | FRED `CORCCACBO` / `CORCCACBL` return 404 |
| SLOOS consumer/card standards | FRED `DRTSCLCC` | Net % tightening **credit card** standards | `DRTSCIS` is C&I loans to **small firms** |
| Credit card / revolving growth | FRED `REVOLSL` YoY | Lower = more stress | `TOTALSL` mixes nonrevolving |
| Credit availability / limits | NY Fed HHDC Page 10 Credit Card Limit YoY | Lower = more stress | `DRIWCIL` is willingness to make **installment** loans, not card limits |

Subprime share is **not** in the 10 KPIs. NY Fed switched credit scores from Equifax Risk Score 3.0 to VantageScore 4.0 in 2026 Q1; about 33 million additional people became scorable, so the subprime population share has a structural break. It is shown only as a context note with a 2026 Q1 break marker. Outstanding subprime-share history is not invented.

## Normalization

Each KPI is mapped to 0–100 with an **expanding window from 2000 through t** (no look-ahead).

- Level: percentile of the raw series (inverted if lower values mean more stress)
- Momentum: percentile of the change over 4 quarters (quarterly) or 12 months (monthly)
- Acceleration: percentile of the change in that momentum over the same lag

`KPI score = 0.50×Level + 0.30×Momentum + 0.20×Acceleration`

If acceleration is not yet defined, the score renormalizes over the available of {level, momentum}. Constant series (zero historical range) map to 50, not 0/100.

Minimum history: 20 observations. Below that the KPI is `PARTIAL` / unscored.

## CCSS

`CCSS = Σ (KPI score × weight) / Σ available weights`

Computed only when:

- at least 7 KPIs have scores
- available weight ≥ 70%
- all four layers have at least one score

Otherwise CCSS is blank. Missing values are never filled with 0.

Aligned history is quarterly (`QS`). Monthly `REVOLSL` YoY is last-in-quarter. The hero CCSS is the last aligned quarter. KPI cards show each series’ own latest period.

## Status bands

| CCSS | Status | Color |
|---|---|---|
| 0–20 | VERY HEALTHY | green |
| 20–40 | NORMAL | green |
| 40–60 | WATCH | yellow |
| 60–75 | CREDIT STRESS | orange |
| 75–90 | RECESSION / CREDIT EVENT RISK | red |
| 90–100 | CREDIT CRISIS | red |

Display colors: green 0–39, yellow 40–59, orange 60–74, red 75–100.

## Stress momentum (separate from CCSS)

- Level bucket: HIGH if CCSS ≥ 60, else LOW
- Direction: DETERIORATING if 4-quarter CCSS change > +2, IMPROVING if < −2, else STABLE
- Regimes: Low+Improving = Expansion/Healthy; Low+Deteriorating = Early Warning; High+Deteriorating = High Risk; High+Improving = Post-Stress Recovery

## Credit Recession Signal

- A: credit-card **and** auto early-delinquency 4Q changes > 0 for two consecutive quarters
- B: same for serious (90+) flows
- C: charge-off all-banks 4Q change > 0 for two consecutive quarters
- D: `DRTSCLCC` > 0 **and** revolving credit YoY below its own historical median

3+ conditions = ON, 2 = WATCH, else OFF. Thresholds were not fitted to 2008 or 2020.

## Missing / stale / confidence

- `GOOD` — latest observation within two quarterly releases (or 3 months if monthly)
- `PARTIAL` — history shorter than 20 observations
- `METHODOLOGY BREAK` — reserved for subprime context; not used on the 10 KPIs
- `STALE` — latest observation older than the window above; last good value is kept, never replaced with 0
- `UNAVAILABLE` — series missing; KPI omitted from CCSS

Confidence: HIGH if 10/10 GOOD; MEDIUM if 8–9 or any STALE/PARTIAL; LOW otherwise.

## Update cadence

NY Fed HHDC: quarterly. Charge-offs: quarterly. SLOOS: survey (quarterly). G.19 revolving credit: monthly. Builder runs on the 1st and 16th UTC, same as the GL monitor. Failed fetches leave the previous `dist/` commit untouched.

## Backtest limitations

NY Fed public national tables begin 2003 Q1. After YoY transforms and the 20-observation minimum, CCSS first prints in 2008 Q3 — already inside the GFC. **2000–2002 cannot be scored with these series.** 2020 is a known forbearance distortion: reported delinquency/charge-off fell even as the real economy collapsed, so CCSS did not spike. That is a data feature, not a reason to retune weights.

## Interpretation layer

The CURRENT SIGNAL box is rule-based from layer scores and condition D. No LLM writes the macro story.
