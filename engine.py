#!/usr/bin/env python3
"""Consumer Credit Stress Score (CCSS) engine.

Deterministic scoring only. No network, no fabricated values.
Percentiles are expanding-window from 2000 (inclusive of t) so historical
CCSS used for backtest does not peek at future observations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

LEVEL_W, MOM_W, ACCEL_W = 0.50, 0.30, 0.20
MIN_HISTORY = 20
MIN_KPIS_FOR_CCSS = 7
MIN_WEIGHT_FOR_CCSS = 0.70
HIGH_LEVEL_CUT = 60.0
MOM_STABLE_BAND = 2.0

STATUS_BANDS = (
    (20, "VERY HEALTHY"),
    (40, "NORMAL"),
    (60, "WATCH"),
    (75, "CREDIT STRESS"),
    (90, "RECESSION / CREDIT EVENT RISK"),
    (101, "CREDIT CRISIS"),
)

COLOR_BANDS = (
    (40, "green"),
    (60, "yellow"),
    (75, "orange"),
    (101, "red"),
)

LAYERS = {
    "borrower": "BORROWER STRESS",
    "serious": "SERIOUS STRESS",
    "loss": "LOSS REALIZATION",
    "supply": "CREDIT SUPPLY",
}


@dataclass(frozen=True)
class KpiSpec:
    key: str
    layer: str
    weight: float
    label_ko: str
    label_en: str
    higher_is_stress: bool
    frequency: str  # quarterly | monthly
    unit: str
    source: str
    source_url: str
    series_id: str
    methodology: str
    interpretation: str
    mom_lag: int  # periods for momentum window


KPI_SPECS: list[KpiSpec] = [
    KpiSpec(
        key="cc_early_flow",
        layer="borrower",
        weight=0.15,
        label_ko="신용카드 초기 연체 유입",
        label_en="Credit Card Early Delinquency (flow 30+)",
        higher_is_stress=True,
        frequency="quarterly",
        unit="%",
        source="Federal Reserve Bank of New York, Household Debt and Credit Report",
        source_url="https://www.newyorkfed.org/microeconomics/hhdc",
        series_id="HHDC Page 13 / Credit Card",
        methodology=(
            "NY Fed Consumer Credit Panel/Equifax. Flow into early delinquency: "
            "new 30+ day delinquent credit-card balances as a percent of the prior "
            "quarter's not-delinquent balance (annualized in the source table). "
            "This is a flow, not the stock 30+ delinquency rate at commercial banks."
        ),
        interpretation="초기 연체 유입이 올라가면 가계 현금흐름 압력이 연체로 번지기 시작한 신호다.",
        mom_lag=4,
    ),
    KpiSpec(
        key="auto_early_flow",
        layer="borrower",
        weight=0.10,
        label_ko="자동차 대출 초기 연체 유입",
        label_en="Auto Loan Early Delinquency (flow 30+)",
        higher_is_stress=True,
        frequency="quarterly",
        unit="%",
        source="Federal Reserve Bank of New York, Household Debt and Credit Report",
        source_url="https://www.newyorkfed.org/microeconomics/hhdc",
        series_id="HHDC Page 13 / Auto",
        methodology=(
            "NY Fed CCP/Equifax flow into 30+ day delinquency for auto loans. "
            "Not the commercial-bank auto delinquency stock (DRALACBS)."
        ),
        interpretation="자동차 대출 초기 연체는 담보가 있는 소비자신용에서도 상환 여력이 꺾이는지를 본다.",
        mom_lag=4,
    ),
    KpiSpec(
        key="hh_any_delinq",
        layer="borrower",
        weight=0.05,
        label_ko="가계 전체 연체 잔액 비중",
        label_en="Aggregate Household Delinquency (stock, any stage)",
        higher_is_stress=True,
        frequency="quarterly",
        unit="%",
        source="Federal Reserve Bank of New York, Household Debt and Credit Report",
        source_url="https://www.newyorkfed.org/microeconomics/hhdc",
        series_id="HHDC Page 11 / 100 − Current",
        methodology=(
            "Share of outstanding household debt not current: 100 minus the "
            "Current share on HHDC Page 11 (30/60/90/120+/derogatory combined). "
            "This is a stock measure of household pressure, not a 30+ flow."
        ),
        interpretation="전체 연체 잔액 비중은 이미 쌓여 있는 가계 부실의 크기(레벨)다.",
        mom_lag=4,
    ),
    KpiSpec(
        key="cc_serious_flow",
        layer="serious",
        weight=0.15,
        label_ko="신용카드 심각 연체 유입",
        label_en="Credit Card Serious Delinquency (flow 90+)",
        higher_is_stress=True,
        frequency="quarterly",
        unit="%",
        source="Federal Reserve Bank of New York, Household Debt and Credit Report",
        source_url="https://www.newyorkfed.org/microeconomics/hhdc",
        series_id="HHDC Page 14 / Credit Card",
        methodology=(
            "NY Fed CCP/Equifax flow into 90+ day (serious) delinquency for "
            "credit cards. Distinct from the 90+ stock share on Page 12."
        ),
        interpretation="심각 연체 유입이 따라 오르면 초기 연체가 손실로 전이되기 시작한 것이다.",
        mom_lag=4,
    ),
    KpiSpec(
        key="auto_serious_flow",
        layer="serious",
        weight=0.10,
        label_ko="자동차 대출 심각 연체 유입",
        label_en="Auto Loan Serious Delinquency (flow 90+)",
        higher_is_stress=True,
        frequency="quarterly",
        unit="%",
        source="Federal Reserve Bank of New York, Household Debt and Credit Report",
        source_url="https://www.newyorkfed.org/microeconomics/hhdc",
        series_id="HHDC Page 14 / Auto",
        methodology="NY Fed CCP/Equifax flow into 90+ day delinquency for auto loans.",
        interpretation="자동차 대출 90+ 유입은 초기 연체 다음 단계의 전이 속도를 본다.",
        mom_lag=4,
    ),
    KpiSpec(
        key="cc_chargeoff_all",
        layer="loss",
        weight=0.10,
        label_ko="신용카드 상각률 (전 은행)",
        label_en="Credit Card Charge-off Rate, All Commercial Banks",
        higher_is_stress=True,
        frequency="quarterly",
        unit="%",
        source="Board of Governors of the Federal Reserve System, Charge-Off and Delinquency Rates",
        source_url="https://www.federalreserve.gov/releases/chargeoff/",
        series_id="CORCCACBS / chgallsa Credit cards",
        methodology=(
            "Seasonally adjusted net charge-off rate on credit-card loans at all "
            "commercial banks. Annualized. This is realized loss, not delinquency."
        ),
        interpretation="상각률 상승은 연체가 은행 손익으로 실현된 단계다.",
        mom_lag=4,
    ),
    KpiSpec(
        key="cc_chargeoff_other",
        layer="loss",
        weight=0.10,
        label_ko="신용카드 상각률 (기타/소형 은행)",
        label_en="Credit Card Charge-off Rate, Other Banks",
        higher_is_stress=True,
        frequency="quarterly",
        unit="%",
        source="Board of Governors of the Federal Reserve System, Charge-Off and Delinquency Rates",
        source_url="https://www.federalreserve.gov/releases/chargeoff/chgothersa.htm",
        series_id="chgothersa Credit cards",
        methodology=(
            "Seasonally adjusted credit-card charge-off rate at banks other than "
            "the 100 largest (Fed CHGDEL 'Other Banks'). Used because FRED "
            "CORCCACBO/CORCCACBL are no longer published. This is the official "
            "small/other-bank breakout, not a large-bank residual we invented."
        ),
        interpretation="소형·기타 은행 상각이 먼저 올라가면 손실이 대형은행 바깥에서 먼저 실현되는 구간이다.",
        mom_lag=4,
    ),
    KpiSpec(
        key="sloos_cc_tighten",
        layer="supply",
        weight=0.15,
        label_ko="SLOOS 신용카드 여신 기준 강화",
        label_en="SLOOS Credit Card Lending Standards (tightening)",
        higher_is_stress=True,
        frequency="quarterly",
        unit="pp",
        source="Board of Governors, Senior Loan Officer Opinion Survey (via FRED)",
        source_url="https://fred.stlouisfed.org/series/DRTSCLCC",
        series_id="DRTSCLCC",
        methodology=(
            "Net percentage of domestic banks tightening standards for credit-card "
            "loans. Positive = tightening (more stress). Not DRTSCIS, which is "
            "C&I loans to small firms."
        ),
        interpretation="은행이 카드 심사를 조이면 가계 신용 공급이 줄어들 가능성이 커진다.",
        mom_lag=4,
    ),
    KpiSpec(
        key="revol_credit_yoy",
        layer="supply",
        weight=0.05,
        label_ko="회전신용(카드성) 증가율",
        label_en="Revolving Consumer Credit Growth (YoY)",
        higher_is_stress=False,
        frequency="monthly",
        unit="%",
        source="Board of Governors G.19 Consumer Credit (via FRED)",
        source_url="https://fred.stlouisfed.org/series/REVOLSL",
        series_id="REVOLSL",
        methodology=(
            "Year-over-year percent change in revolving consumer credit outstanding "
            "(REVOLSL). Lower growth / contraction = more stress (credit supply "
            "shrinking). Monthly series; CCSS history uses the last month of each quarter."
        ),
        interpretation="회전신용 증가가 둔화·감소하면 신용 수축이 소비 둔화로 이어질 수 있다.",
        mom_lag=12,
    ),
    KpiSpec(
        key="cc_limit_yoy",
        layer="supply",
        weight=0.05,
        label_ko="신용카드 한도 증가율",
        label_en="Credit Card Limit Growth (YoY)",
        higher_is_stress=False,
        frequency="quarterly",
        unit="%",
        source="Federal Reserve Bank of New York, Household Debt and Credit Report",
        source_url="https://www.newyorkfed.org/microeconomics/hhdc",
        series_id="HHDC Page 10 / Credit Card Limit",
        methodology=(
            "Year-over-year percent change in aggregate credit-card limits "
            "(NY Fed CCP). Lower limit growth = tighter credit availability. "
            "Chosen over SLOOS 'willingness to make consumer installment loans' "
            "(DRIWCIL) because the spec asks for credit limits, not a survey of "
            "installment-loan willingness."
        ),
        interpretation="한도 증가가 멈추면 신규 신용 공급이 막히기 시작한 것이다.",
        mom_lag=4,
    ),
]


KPI_BY_KEY = {spec.key: spec for spec in KPI_SPECS}
WEIGHT_SUM = round(sum(s.weight for s in KPI_SPECS), 10)
assert abs(WEIGHT_SUM - 1.0) < 1e-12, WEIGHT_SUM


@dataclass
class KpiSnapshot:
    spec: KpiSpec
    series: pd.Series  # DatetimeIndex, float values in native units
    quality: str = "GOOD"  # GOOD | PARTIAL | METHODOLOGY BREAK | STALE | UNAVAILABLE
    quality_note: str = ""
    last_updated: str = ""


def _to_series(values: Iterable, index: Iterable) -> pd.Series:
    s = pd.Series(list(values), index=pd.to_datetime(list(index)), dtype="float64")
    s = s.sort_index()
    s = s[~s.index.duplicated(keep="last")]
    return s.dropna()


def expanding_percentile(history: np.ndarray, value: float, invert: bool) -> float | None:
    hist = np.asarray(history, dtype="float64")
    hist = hist[np.isfinite(hist)]
    if hist.size < MIN_HISTORY or not np.isfinite(value):
        return None
    if float(np.nanmax(hist) - np.nanmin(hist)) < 1e-12:
        return 50.0
    rank = float(np.mean(hist <= value) * 100.0)
    if invert:
        rank = 100.0 - rank
    return float(np.clip(rank, 0.0, 100.0))


def _window_change(s: pd.Series, lag: int) -> pd.Series:
    return s - s.shift(lag)


def score_series(spec: KpiSpec, series: pd.Series) -> pd.DataFrame:
    """Return a quarterly-or-native indexed frame with level/mom/accel/score."""
    s = series.dropna().astype("float64").sort_index()
    s = s[s.index >= "2000-01-01"]
    lag = spec.mom_lag
    mom = _window_change(s, lag)
    accel = mom - mom.shift(lag)
    invert = not spec.higher_is_stress
    rows = []
    vals = s.to_numpy()
    idx = s.index
    mom_v = mom.to_numpy()
    acc_v = accel.to_numpy()
    for i in range(len(s)):
        level = expanding_percentile(vals[: i + 1], vals[i], invert)
        m = expanding_percentile(mom_v[: i + 1], mom_v[i], invert) if np.isfinite(mom_v[i]) else None
        a = expanding_percentile(acc_v[: i + 1], acc_v[i], invert) if np.isfinite(acc_v[i]) else None
        parts, weights = [], []
        if level is not None:
            parts.append(LEVEL_W * level)
            weights.append(LEVEL_W)
        if m is not None:
            parts.append(MOM_W * m)
            weights.append(MOM_W)
        if a is not None:
            parts.append(ACCEL_W * a)
            weights.append(ACCEL_W)
        score = None
        if weights and abs(sum(weights) - 1.0) < 1e-9:
            score = float(sum(parts))
        elif weights and LEVEL_W in weights:
            # allow score with level+momentum if acceleration not ready
            score = float(sum(parts) / sum(weights))
        prev = float(vals[i - 1]) if i else None
        rows.append(
            {
                "date": idx[i],
                "value": float(vals[i]),
                "previous": prev,
                "change": None if prev is None else float(vals[i] - prev),
                "level": level,
                "momentum": m,
                "acceleration": a,
                "score": score,
            }
        )
    return pd.DataFrame(rows).set_index("date")


def status_label(score: float | None) -> str:
    if score is None or not np.isfinite(score):
        return "UNAVAILABLE"
    for cut, label in STATUS_BANDS:
        if score < cut:
            return label
    return "CREDIT CRISIS"


def color_band(score: float | None) -> str:
    if score is None or not np.isfinite(score):
        return "muted"
    for cut, name in COLOR_BANDS:
        if score < cut:
            return name
    return "red"


def classify_quality(spec: KpiSpec, series: pd.Series, as_of: pd.Timestamp, now: pd.Timestamp) -> tuple[str, str]:
    if series is None or series.dropna().empty:
        return "UNAVAILABLE", "공식 시계열이 비어 있어 점수를 계산하지 않습니다."
    latest = pd.Timestamp(series.dropna().index.max())
    if spec.frequency == "quarterly":
        stale_after = pd.DateOffset(months=8)
    else:
        stale_after = pd.DateOffset(months=3)
    if latest < now - stale_after:
        return "STALE", f"최신 관측 {latest.date()} 이(가) 발표 주기 대비 오래되었습니다."
    if len(series.dropna()) < MIN_HISTORY:
        return "PARTIAL", "역사가 짧아 퍼센타일 신뢰도가 낮습니다."
    return "GOOD", ""


def combine_ccss(scored: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Aligned quarterly CCSS using expanding-window KPI scores."""
    frames = []
    for key, spec in KPI_BY_KEY.items():
        df = scored.get(key)
        if df is None or df.empty or "score" not in df:
            continue
        col = df["score"].rename(key)
        if spec.frequency == "monthly":
            col = col.resample("QS").last()
        frames.append(col)
    if not frames:
        return pd.DataFrame(columns=["ccss", "available_weight", "n_kpis", "confidence"])
    wide = pd.concat(frames, axis=1).sort_index()
    wide = wide[wide.index >= "2000-01-01"]
    out_rows = []
    for dt, row in wide.iterrows():
        present = {k: float(row[k]) for k in row.index if pd.notna(row[k])}
        weight = sum(KPI_BY_KEY[k].weight for k in present)
        layers = {KPI_BY_KEY[k].layer for k in present}
        n = len(present)
        usable = n >= MIN_KPIS_FOR_CCSS and weight >= MIN_WEIGHT_FOR_CCSS and len(layers) == 4
        ccss = None
        if usable:
            ccss = float(sum(present[k] * KPI_BY_KEY[k].weight for k in present) / weight * 100.0 / 100.0)
            # present scores already 0-100; weighted average with renormalized weights
            ccss = float(sum(present[k] * KPI_BY_KEY[k].weight for k in present) / weight)
        conf = _confidence(n, weight, usable)
        out_rows.append(
            {
                "date": dt,
                "ccss": ccss,
                "available_weight": float(weight),
                "n_kpis": n,
                "confidence": conf,
                "usable": usable,
            }
        )
    return pd.DataFrame(out_rows).set_index("date")


def _confidence(n: int, weight: float, usable: bool) -> str:
    if not usable:
        return "LOW"
    if n == 10 and abs(weight - 1.0) < 1e-9:
        return "HIGH"
    if n >= 8:
        return "MEDIUM"
    return "LOW"


def current_confidence(snapshots: dict[str, KpiSnapshot], scored_ok: int, weight: float) -> str:
    qualities = [s.quality for s in snapshots.values()]
    if "UNAVAILABLE" in qualities and scored_ok < MIN_KPIS_FOR_CCSS:
        return "LOW"
    if any(q in {"STALE", "UNAVAILABLE"} for q in qualities):
        if scored_ok >= 8:
            return "MEDIUM"
        return "LOW"
    if scored_ok == 10 and any(q == "PARTIAL" for q in qualities):
        return "MEDIUM"
    if scored_ok == 10:
        return "HIGH"
    if scored_ok >= 8:
        return "MEDIUM"
    return "LOW"


def momentum_regime(ccss_hist: pd.Series) -> dict[str, Any]:
    s = ccss_hist.dropna()
    if s.empty:
        return {
            "level_bucket": "UNAVAILABLE",
            "direction": "UNAVAILABLE",
            "regime": "UNAVAILABLE",
            "delta": None,
        }
    latest = float(s.iloc[-1])
    lag = min(4, len(s) - 1)
    delta = float(s.iloc[-1] - s.iloc[-1 - lag]) if lag else 0.0
    level_bucket = "HIGH" if latest >= HIGH_LEVEL_CUT else "LOW"
    if delta > MOM_STABLE_BAND:
        direction = "DETERIORATING"
    elif delta < -MOM_STABLE_BAND:
        direction = "IMPROVING"
    else:
        direction = "STABLE"
    if level_bucket == "LOW" and direction == "IMPROVING":
        regime = "Expansion / Healthy"
    elif level_bucket == "LOW" and direction == "DETERIORATING":
        regime = "Early Warning"
    elif level_bucket == "HIGH" and direction == "DETERIORATING":
        regime = "High Risk"
    elif level_bucket == "HIGH" and direction == "IMPROVING":
        regime = "Post-Stress Recovery"
    elif level_bucket == "LOW":
        regime = "Expansion / Healthy"
    else:
        regime = "Post-Stress Recovery"
    return {
        "level_bucket": level_bucket,
        "direction": direction,
        "regime": regime,
        "delta": delta,
        "lookback_periods": lag,
    }


def _rising_persistently(s: pd.Series, lag: int = 4, consecutive: int = 2) -> bool:
    d = _window_change(s.dropna(), lag).dropna()
    if len(d) < consecutive:
        return False
    return bool((d.iloc[-consecutive:] > 0).all())


def recession_signal(
    cc_early: pd.Series | None,
    auto_early: pd.Series | None,
    cc_serious: pd.Series | None,
    auto_serious: pd.Series | None,
    chargeoff: pd.Series | None,
    sloos: pd.Series | None,
    credit_yoy: pd.Series | None,
) -> dict[str, Any]:
    def last4_up(a: pd.Series | None, b: pd.Series | None) -> bool:
        flags = []
        if a is not None and not a.dropna().empty:
            flags.append(_rising_persistently(a))
        if b is not None and not b.dropna().empty:
            flags.append(_rising_persistently(b))
        return bool(flags) and all(flags)

    a = last4_up(cc_early, auto_early)
    b = last4_up(cc_serious, auto_serious)
    c = False
    if chargeoff is not None and not chargeoff.dropna().empty:
        d = _window_change(chargeoff.dropna(), 4).dropna()
        if len(d) >= 2:
            c = bool(d.iloc[-1] > 0 and d.iloc[-2] > 0)
    d_flag = False
    if sloos is not None and credit_yoy is not None and not sloos.dropna().empty and not credit_yoy.dropna().empty:
        tighten = float(sloos.dropna().iloc[-1]) > 0
        yoy = credit_yoy.dropna()
        slow = float(yoy.iloc[-1]) < float(np.nanmedian(yoy.to_numpy()))
        d_flag = bool(tighten and slow)
    conditions = {"A": a, "B": b, "C": c, "D": d_flag}
    n_on = sum(1 for v in conditions.values() if v)
    if n_on >= 3:
        flag = "ON"
    elif n_on == 2:
        flag = "WATCH"
    else:
        flag = "OFF"
    return {"conditions": conditions, "n_on": n_on, "flag": flag}


def interpretation(layer_scores: dict[str, float | None], signal: dict[str, Any], status: str) -> dict[str, str]:
    def band(v: float | None, high: str, mid: str, low: str) -> str:
        if v is None:
            return "데이터 없음"
        if v >= 60:
            return high
        if v >= 40:
            return mid
        return low

    borrower = layer_scores.get("borrower")
    serious = layer_scores.get("serious")
    loss = layer_scores.get("loss")
    supply = layer_scores.get("supply")
    return {
        "consumer_stress": band(borrower, "Elevated", "Watch", "Contained"),
        "delinquency": band(serious if serious is not None else borrower, "Deteriorating", "Mixed", "Stable"),
        "bank_loss": band(loss, "Rising", "Watch", "Stable"),
        "credit_standards": band(supply, "Tight", "Mixed", "Not Tight"),
        "credit_contraction": "Yes" if signal.get("conditions", {}).get("D") else "Not Yet",
        "overall": status,
    }


def layer_score(scored: dict[str, pd.DataFrame], layer: str) -> float | None:
    parts, wts = [], []
    for spec in KPI_SPECS:
        if spec.layer != layer:
            continue
        df = scored.get(spec.key)
        if df is None or df.empty or pd.isna(df["score"].iloc[-1]):
            continue
        parts.append(float(df["score"].iloc[-1]) * spec.weight)
        wts.append(spec.weight)
    if not wts:
        return None
    return float(sum(parts) / sum(wts))


def latest_row(df: pd.DataFrame | None) -> dict[str, Any] | None:
    if df is None or df.empty:
        return None
    r = df.iloc[-1]
    return {k: (None if pd.isna(v) else v) for k, v in r.items()}
