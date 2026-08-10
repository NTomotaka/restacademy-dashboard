"""レストアカデミー健康データ分析の共通ロジック。

2026-08-10 松浦冬馬様フォローアップレポートで組んだ集計を汎用化したもの。
参加者やデバイスが変わっても同じ関数で「1日の流れ」「装着ギャップ」等が出せるようにする。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _strip_tz(s: pd.Series) -> pd.Series:
    return s.dt.tz_localize(None) if s.dt.tz is not None else s


def monthly_mean(df: pd.DataFrame, value_col: str = "value") -> pd.Series:
    """月次平均のSeries（PeriodIndex）を返す。空データはそのまま空Seriesで返す。"""
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    d = df.copy()
    d["ym"] = _strip_tz(d["startDate"]).dt.to_period("M")
    return d.groupby("ym")[value_col].mean().sort_index()


def daily_sum(df: pd.DataFrame, value_col: str = "value") -> pd.Series:
    """日次合計（歩数など加算指標向け）。DatetimeIndex。"""
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    d = df.copy()
    d["date"] = _strip_tz(d["startDate"]).dt.date
    s = d.groupby("date")[value_col].sum()
    s.index = pd.to_datetime(s.index)
    return s.sort_index()


def hourly_profile(df: pd.DataFrame, value_col: str = "value", min_n: int = 5) -> pd.DataFrame:
    """時間帯別「1日の流れ」。n<min_nの時間帯はサンプル不足として除外しない
    （nを列で返すのでUI側で薄い時間帯を明記できる。[[feedback_restacademy_analysis_standard]]準拠）。
    """
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["hour", "mean", "count"])
    d = df.copy()
    d["hour"] = d["startDate"].dt.hour
    g = d.groupby("hour")[value_col].agg(["mean", "count"]).reindex(range(24))
    g.index.name = "hour"
    return g.reset_index()


def weekday_hour_heatmap(df: pd.DataFrame, value_col: str = "value", min_n: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """曜日(0=月)×時間帯の平均値マトリクスと、サンプル数マトリクスを返す。
    サンプル不足セルはNaNでマスクする（UI側でグレー表示するため）。
    """
    if df is None or len(df) == 0:
        empty = np.full((7, 24), np.nan)
        return empty, empty
    d = df.copy()
    d["hour"] = d["startDate"].dt.hour
    d["weekday"] = d["startDate"].dt.dayofweek
    mean_p = d.pivot_table(index="weekday", columns="hour", values=value_col, aggfunc="mean")
    cnt_p = d.pivot_table(index="weekday", columns="hour", values=value_col, aggfunc="count")
    mean_p = mean_p.reindex(index=range(7), columns=range(24))
    cnt_p = cnt_p.reindex(index=range(7), columns=range(24)).fillna(0)
    masked = mean_p.where(cnt_p >= min_n)
    return masked.values, cnt_p.values


def device_coverage_timeline(df: pd.DataFrame, end_period: pd.Period | None = None) -> pd.Series:
    """ウェアラブル装着状況ギャップ検知（汎用版）。

    HRなど高頻度で取れるはずの指標の「月次レコード数」を全期間で埋めて返す。
    0件の月＝完全欠測、少数の月＝低頻度装着、として呼び出し側で色分けする。
    松浦様フォローアップの「Apple Watch装着状況ギャップ評価」セクションの一般化版。
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype=int)
    d = df.copy()
    d["ym"] = _strip_tz(d["startDate"]).dt.to_period("M")
    counts = d.groupby("ym").size()
    if end_period is None:
        end_period = pd.Timestamp.now().to_period("M")
    full_range = pd.period_range(counts.index.min(), end_period, freq="M")
    return counts.reindex(full_range, fill_value=0)


def coverage_gaps(coverage: pd.Series, zero_run_min_months: int = 2) -> list[dict]:
    """coverage(月次件数)の中から「N ヶ月以上連続でゼロ」の欠測区間を検出する。"""
    if coverage is None or len(coverage) == 0:
        return []
    is_zero = coverage == 0
    gaps = []
    run_start = None
    for i, (period, z) in enumerate(is_zero.items()):
        if z and run_start is None:
            run_start = period
        if (not z or i == len(is_zero) - 1) and run_start is not None:
            run_end_idx = i - 1 if not z else i
            run_end = is_zero.index[run_end_idx]
            length = (run_end - run_start).n + 1
            if length >= zero_run_min_months:
                gaps.append({"start": str(run_start), "end": str(run_end), "months": length})
            run_start = None
    return gaps


def integrity_check(current: pd.DataFrame, known_points: list[dict]) -> list[dict]:
    """既存レポートの既知の値（date, value許容差）と、新データを突き合わせて一致率を返す。
    known_points = [{"date": "2025-08-27", "value": 67, "tolerance": 1}, ...]
    [[feedback_restacademy_analysis_standard]] の「既存レポートとの整合チェック」を自動化する土台。
    """
    if current is None or len(current) == 0:
        return [{"date": p["date"], "match": None, "note": "現データなし"} for p in known_points]
    d = current.copy()
    d["date"] = d["startDate"].dt.tz_localize(None).dt.date if d["startDate"].dt.tz is not None else d["startDate"].dt.date
    results = []
    for p in known_points:
        target_date = pd.to_datetime(p["date"]).date()
        tol = p.get("tolerance", 1)
        matches = d[d["date"] == target_date]
        if len(matches) == 0:
            results.append({"date": p["date"], "match": None, "note": "該当日のデータなし"})
            continue
        ok = (abs(matches["value"] - p["value"]) <= tol).any()
        results.append({"date": p["date"], "match": bool(ok), "actual": matches["value"].tolist()})
    return results
