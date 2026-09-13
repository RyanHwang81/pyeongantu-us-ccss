#!/usr/bin/env python3
"""Fetch official series, compute CCSS, render the public dashboard."""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from engine import (
    KPI_BY_KEY,
    KPI_SPECS,
    KpiSnapshot,
    classify_quality,
    color_band,
    combine_ccss,
    current_confidence,
    interpretation,
    layer_score,
    latest_row,
    momentum_regime,
    recession_signal,
    score_series,
    status_label,
)

ROOT = Path(__file__).resolve().parent
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
NYFED_XLSX = "https://www.newyorkfed.org/medialibrary/interactives/householdcredit/data/xls/hhd_c_report_{year}q{q}.xlsx"
CHG_ALL = "https://www.federalreserve.gov/releases/chargeoff/chgallsa.htm"
CHG_OTHER = "https://www.federalreserve.gov/releases/chargeoff/chgothersa.htm"
UA = {"User-Agent": "Mozilla/5.0 (compatible; pyeongantoo-ccss-builder/1.0)"}
CACHE = os.environ.get("CCSS_CACHE", "")

try:
    import certifi

    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # pragma: no cover
    CTX = ssl.create_default_context()


def log(*a):
    print("[data]", *a, flush=True)


def fetch(url: str, tries: int = 4) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, context=CTX, timeout=45) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"다운로드 실패: {url} ({last})")


def cached_get(name: str, url: str) -> bytes:
    if CACHE:
        path = Path(CACHE) / name
        if path.exists():
            return path.read_bytes()
        data = fetch(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data
    return fetch(url)


def fred(series_id: str) -> pd.Series:
    raw = cached_get(f"{series_id}.csv", FRED.format(series_id)).decode("utf-8", "replace")
    d = pd.read_csv(io.StringIO(raw))
    d.columns = ["date", "value"]
    d["value"] = pd.to_numeric(d["value"], errors="coerce")
    d["date"] = pd.to_datetime(d["date"])
    return d.dropna().set_index("date")["value"].sort_index()


def parse_quarter_label(val) -> pd.Timestamp | None:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    if isinstance(val, datetime) or isinstance(val, pd.Timestamp):
        ts = pd.Timestamp(val)
        return pd.Timestamp(year=ts.year, month=((ts.month - 1) // 3) * 3 + 1, day=1)
    s = str(val).strip()
    m = re.match(r"^(\d{2}):Q([1-4])$", s)
    if m:
        yy, q = int(m.group(1)), int(m.group(2))
        year = 2000 + yy if yy < 80 else 1900 + yy
        return pd.Timestamp(year=year, month=3 * q - 2, day=1)
    m = re.match(r"^(\d{4}):([1-4])$", s)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        return pd.Timestamp(year=year, month=3 * q - 2, day=1)
    return None


def find_latest_nyfed() -> tuple[bytes, str]:
    now = datetime.now(timezone.utc)
    year, q = now.year, (now.month - 1) // 3 + 1
    tried = []
    for _ in range(8):
        tag = f"{year}q{q}"
        url = NYFED_XLSX.format(year=year, q=q)
        tried.append(url)
        try:
            data = cached_get(f"hhd_c_report_{tag}.xlsx", url)
            if data[:2] == b"PK":
                log("NY Fed HHDC", tag)
                return data, tag
        except Exception as e:
            log("NY Fed miss", tag, e)
        q -= 1
        if q == 0:
            q = 4
            year -= 1
    raise RuntimeError("NY Fed HHDC xlsx를 찾지 못했습니다: " + "; ".join(tried[:4]))


def nyfed_sheets(data: bytes) -> dict[str, pd.DataFrame]:
    xl = pd.ExcelFile(io.BytesIO(data))
    return {name: pd.read_excel(xl, sheet_name=name, header=None) for name in xl.sheet_names}


def series_from_sheet(df: pd.DataFrame, col: int, header_scan: int = 8) -> pd.Series:
    pairs = []
    for i in range(header_scan, len(df)):
        dt = parse_quarter_label(df.iloc[i, 0])
        if dt is None:
            continue
        val = pd.to_numeric(df.iloc[i, col], errors="coerce")
        if pd.isna(val):
            continue
        pairs.append((dt, float(val)))
    if not pairs:
        return pd.Series(dtype="float64")
    s = pd.Series({d: v for d, v in pairs})
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def parse_hhdc(sheets: dict[str, pd.DataFrame]) -> dict[str, pd.Series]:
    p10 = sheets["Page 10 Data"]
    p11 = sheets["Page 11 Data"]
    p13 = sheets["Page 13 Data"]
    p14 = sheets["Page 14 Data"]
    # Page 13/14: AUTO=1, CC=2, Total/ALL=7 after a header row containing those labels
    cc_early = series_from_sheet(p13, 2)
    auto_early = series_from_sheet(p13, 1)
    agg_early = series_from_sheet(p13, 7)
    cc_serious = series_from_sheet(p14, 2)
    auto_serious = series_from_sheet(p14, 1)
    current = series_from_sheet(p11, 1)
    hh_any = (100.0 - current).rename("hh_any_delinq")
    cc_limit = series_from_sheet(p10, 3)
    cc_limit_yoy = cc_limit.pct_change(4) * 100.0
    return {
        "cc_early_flow": cc_early,
        "auto_early_flow": auto_early,
        "hh_any_delinq": hh_any,
        "cc_serious_flow": cc_serious,
        "auto_serious_flow": auto_serious,
        "cc_limit": cc_limit,
        "cc_limit_yoy": cc_limit_yoy,
        "agg_early_flow": agg_early,
    }


def parse_fed_chargeoff(html: str) -> pd.Series:
    pairs = re.findall(
        r'<th id="date\d+">([^<]+)</th>.*?headers="CON CONCC date\d+">([^<]+)',
        html,
        re.S,
    )
    out = {}
    for label, raw in pairs:
        dt = parse_quarter_label(label.strip())
        val = pd.to_numeric(raw.replace("&nbsp;", "").replace(",", "").strip(), errors="coerce")
        if dt is None or pd.isna(val):
            continue
        out[dt] = float(val)
    s = pd.Series(out)
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def recession_spans(usrec: pd.Series) -> list[dict[str, str]]:
    s = usrec.fillna(0).astype(float)
    spans = []
    start = None
    prev = None
    for dt, v in s.items():
        on = v >= 1
        if on and start is None:
            start = dt
        if not on and start is not None:
            spans.append({"start": start.strftime("%Y-%m-%d"), "end": prev.strftime("%Y-%m-%d")})
            start = None
        prev = dt
    if start is not None and prev is not None:
        spans.append({"start": start.strftime("%Y-%m-%d"), "end": prev.strftime("%Y-%m-%d")})
    return spans


def ts_iso(ts) -> str | None:
    if ts is None or (isinstance(ts, float) and np.isnan(ts)):
        return None
    return pd.Timestamp(ts).strftime("%Y-%m-%d")


def qlabel(ts) -> str:
    t = pd.Timestamp(ts)
    return f"{t.year} Q{(t.month - 1) // 3 + 1}"


def fmt(v, unit=""):
    if v is None or (isinstance(v, float) and not np.isfinite(v)):
        return "—"
    if abs(v) >= 100:
        return f"{v:.1f}{unit}"
    return f"{v:.2f}{unit}"


def collect() -> dict[str, pd.Series]:
    log("FRED 수집")
    revol = fred("REVOLSL")
    sloos = fred("DRTSCLCC")
    cor_all_fred = fred("CORCCACBS")
    usrec = fred("USREC")
    log("NY Fed HHDC")
    xlsx, tag = find_latest_nyfed()
    hh = parse_hhdc(nyfed_sheets(xlsx))
    log("Fed charge-off HTML")
    chg_all = parse_fed_chargeoff(cached_get("chgallsa.htm", CHG_ALL).decode("utf-8", "replace"))
    chg_other = parse_fed_chargeoff(cached_get("chgothersa.htm", CHG_OTHER).decode("utf-8", "replace"))
    if chg_all.empty:
        chg_all = cor_all_fred
        log("charge-off all: FRED fallback CORCCACBS")
    revol_yoy = revol.pct_change(12) * 100.0
    out = {
        **hh,
        "sloos_cc_tighten": sloos,
        "revol_credit_yoy": revol_yoy,
        "revol_level": revol,
        "cc_chargeoff_all": chg_all,
        "cc_chargeoff_other": chg_other,
        "usrec": usrec,
        "_nyfed_tag": pd.Series([tag]),
        "_cor_all_fred": cor_all_fred,
    }
    return out


def build_payload(raw: dict[str, pd.Series], now: pd.Timestamp | None = None) -> dict:
    now = now or pd.Timestamp.now(tz="UTC").tz_localize(None)
    snapshots: dict[str, KpiSnapshot] = {}
    scored = {}
    kpis = []
    for spec in KPI_SPECS:
        series = raw.get(spec.key)
        if spec.key == "cc_limit_yoy":
            series = raw.get("cc_limit_yoy")
        quality, note = classify_quality(spec, series if series is not None else pd.Series(dtype=float), now, now)
        snap = KpiSnapshot(spec=spec, series=series if series is not None else pd.Series(dtype=float), quality=quality, quality_note=note)
        snapshots[spec.key] = snap
        df = None
        if quality != "UNAVAILABLE" and series is not None and not series.dropna().empty:
            df = score_series(spec, series)
            scored[spec.key] = df
        row = latest_row(df)
        latest_dt = None if series is None or series.dropna().empty else series.dropna().index.max()
        kpis.append(
            {
                "key": spec.key,
                "layer": spec.layer,
                "weight": spec.weight,
                "label_ko": spec.label_ko,
                "label_en": spec.label_en,
                "unit": spec.unit,
                "source": spec.source,
                "source_url": spec.source_url,
                "series_id": spec.series_id,
                "methodology": spec.methodology,
                "interpretation": spec.interpretation,
                "higher_is_stress": spec.higher_is_stress,
                "frequency": spec.frequency,
                "quality": quality,
                "quality_note": note,
                "latest_period": qlabel(latest_dt) if latest_dt is not None else None,
                "latest_date": ts_iso(latest_dt),
                "latest_value": None if row is None else row.get("value"),
                "previous_value": None if row is None else row.get("previous"),
                "change": None if row is None else row.get("change"),
                "level": None if row is None else row.get("level"),
                "momentum": None if row is None else row.get("momentum"),
                "acceleration": None if row is None else row.get("acceleration"),
                "score": None if row is None else row.get("score"),
                "history": []
                if series is None
                else [
                    {"d": ts_iso(i), "v": float(v)}
                    for i, v in series.dropna().items()
                    if pd.Timestamp(i) >= pd.Timestamp("2000-01-01")
                ],
            }
        )

    hist = combine_ccss(scored)
    ccss_s = hist["ccss"] if not hist.empty else pd.Series(dtype=float)
    latest_ccss = None if ccss_s.dropna().empty else float(ccss_s.dropna().iloc[-1])
    latest_ccss_dt = None if ccss_s.dropna().empty else ccss_s.dropna().index.max()
    mom = momentum_regime(ccss_s)
    signal = recession_signal(
        raw.get("cc_early_flow"),
        raw.get("auto_early_flow"),
        raw.get("cc_serious_flow"),
        raw.get("auto_serious_flow"),
        raw.get("cc_chargeoff_all"),
        raw.get("sloos_cc_tighten"),
        raw.get("revol_credit_yoy"),
    )
    layers = {name: layer_score(scored, name) for name in ("borrower", "serious", "loss", "supply")}
    n_ok = sum(1 for k in kpis if k["score"] is not None)
    w_ok = sum(k["weight"] for k in kpis if k["score"] is not None)
    conf = current_confidence(snapshots, n_ok, w_ok)
    if hist.empty or pd.isna(hist["confidence"].iloc[-1] if len(hist) else np.nan):
        pass
    elif any(k["quality"] == "STALE" for k in kpis) and conf == "HIGH":
        conf = "MEDIUM"
    status = status_label(latest_ccss)
    interp = interpretation(layers, signal, status)
    backtest = backtest_summary(hist, raw.get("usrec"))
    regime_pts = []
    if not ccss_s.dropna().empty:
        dlt = ccss_s.dropna() - ccss_s.dropna().shift(4)
        for dt, lvl in ccss_s.dropna().items():
            if pd.isna(dlt.get(dt)):
                continue
            regime_pts.append({"d": ts_iso(dt), "x": float(lvl), "y": float(dlt.loc[dt])})

    def hist_pack(s: pd.Series | None):
        if s is None:
            return []
        return [{"d": ts_iso(i), "v": float(v)} for i, v in s.dropna().items() if pd.Timestamp(i) >= pd.Timestamp("2000-01-01")]

    payload = {
        "meta": {
            "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "as_of": ts_iso(latest_ccss_dt),
            "as_of_label": qlabel(latest_ccss_dt) if latest_ccss_dt is not None else None,
            "nyfed_file": None if raw.get("_nyfed_tag") is None else str(raw["_nyfed_tag"].iloc[0]),
            "weight_sum": 1.0,
            "data_confidence": conf,
        },
        "hero": {
            "ccss": latest_ccss,
            "status": status,
            "color": color_band(latest_ccss),
            "momentum": mom,
            "signal": signal,
            "confidence": conf,
        },
        "kpis": kpis,
        "layers": {k: v for k, v in layers.items()},
        "interpretation": interp,
        "charts": {
            "ccss": [{"d": ts_iso(i), "v": float(v)} for i, v in ccss_s.dropna().items()],
            "recessions": recession_spans(raw["usrec"]) if "usrec" in raw else [],
            "cc_early": hist_pack(raw.get("cc_early_flow")),
            "auto_early": hist_pack(raw.get("auto_early_flow")),
            "cc_serious": hist_pack(raw.get("cc_serious_flow")),
            "auto_serious": hist_pack(raw.get("auto_serious_flow")),
            "chargeoff_all": hist_pack(raw.get("cc_chargeoff_all")),
            "chargeoff_other": hist_pack(raw.get("cc_chargeoff_other")),
            "sloos": hist_pack(raw.get("sloos_cc_tighten")),
            "credit_growth": hist_pack(raw.get("revol_credit_yoy")),
            "regime": regime_pts,
        },
        "context": {
            "subprime_share": None,
            "note": (
                "Subprime share는 2026 Q1 NY Fed 신용점수 방법론이 Equifax Risk Score 3.0에서 "
                "VantageScore 4.0으로 바뀌며 구조적 단절이 생겨 CCSS 10개 KPI에 넣지 않습니다. "
                "약 3,300만 명이 추가로 scoring되면서 subprime 비중의 수준 자체가 바뀌었습니다."
            ),
            "break_date": "2026-01-01",
        },
        "backtest": backtest,
    }
    return payload


def backtest_summary(hist: pd.DataFrame, usrec: pd.Series | None) -> dict:
    if hist.empty or hist["ccss"].dropna().empty:
        return {"note": "CCSS 역사가 부족해 백테스트를 수행하지 않았습니다."}
    s = hist["ccss"].dropna()
    rec = usrec.reindex(s.index, method="ffill") if usrec is not None else None
    windows = [
        ("2000-2002", "2000-01-01", "2002-12-31"),
        ("2007-2009", "2007-01-01", "2009-12-31"),
        ("2020", "2020-01-01", "2020-12-31"),
        ("2022-2023", "2022-01-01", "2023-12-31"),
        ("2025-2026", "2025-01-01", "2026-12-31"),
    ]
    out_windows = []
    for name, a, b in windows:
        sl = s[(s.index >= a) & (s.index <= b)]
        if sl.empty:
            out_windows.append({"window": name, "available": False})
            continue
        out_windows.append(
            {
                "window": name,
                "available": True,
                "start_ccss": float(sl.iloc[0]),
                "max_ccss": float(sl.max()),
                "max_date": ts_iso(sl.idxmax()),
                "end_ccss": float(sl.iloc[-1]),
                "crossed_60": bool((sl >= 60).any()),
                "crossed_75": bool((sl >= 75).any()),
            }
        )
    rec_on = []
    if rec is not None:
        rec_on = [ts_iso(i) for i, v in rec.items() if v >= 1]
    false_pos = 0
    hits = 0
    for dt, val in s.items():
        if val < 75:
            continue
        future = rec.loc[dt : dt + pd.DateOffset(months=12)] if rec is not None else pd.Series(dtype=float)
        if rec is not None and (future.fillna(0) >= 1).any():
            hits += 1
        else:
            false_pos += 1
    return {
        "windows": out_windows,
        "first_cross_60": ts_iso(s[s >= 60].index.min()) if (s >= 60).any() else None,
        "first_cross_75": ts_iso(s[s >= 75].index.min()) if (s >= 75).any() else None,
        "n_quarters_ge_75": int((s >= 75).sum()),
        "n_ge75_followed_by_recession_12m": hits,
        "n_ge75_without_recession_12m": false_pos,
        "note": (
            "임계값 60/75는 사전 구간이며 2008·2020에 맞추어 재추정하지 않았습니다. "
            "퍼센타일은 각 시점 t에서 2000~t expanding window입니다."
        ),
    }


def render_html(payload: dict, template: str) -> str:
    blob = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    if "__DATA_JSON__" not in template:
        raise RuntimeError("template missing __DATA_JSON__")
    return template.replace("__DATA_JSON__", blob)


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--out", default=str(ROOT / "dist"))
    p.add_argument("--template", default=str(ROOT / "template.html"))
    args = p.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    raw = collect()
    # cross-check all-bank charge-off vs FRED if both exist
    if not raw["cc_chargeoff_all"].empty and not raw["_cor_all_fred"].empty:
        a = raw["cc_chargeoff_all"].dropna()
        b = raw["_cor_all_fred"].dropna()
        common = a.index.intersection(b.index)
        if len(common):
            gap = float((a.loc[common] - b.loc[common]).abs().iloc[-1])
            log(f"charge-off all vs FRED last gap={gap:.3f}")
            if gap > 0.15:
                log("WARNING charge-off source mismatch; using Fed Board HTML")
    payload = build_payload(raw)
    (out / "ccss_data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    html = render_html(payload, Path(args.template).read_text(encoding="utf-8"))
    (out / "index.html").write_text(html, encoding="utf-8")
    ccss = payload["hero"]["ccss"]
    log(
        "완료 — CCSS",
        "None" if ccss is None else f"{ccss:.1f}",
        payload["hero"]["status"],
        payload["meta"]["data_confidence"],
        payload["meta"]["as_of_label"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
