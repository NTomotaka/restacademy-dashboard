"""RestAcademy Recovery Index (RARI) v1.0 の日次スコア算出。

出典: Driveの元アルゴリズム設計文書
「260612_RARI_RestAcademy Recovery Index v1.0 — ウェアラブル計測レポート」
（file: 1vcB2F-_dBCxRhK1nUEuuropYzotwGH4f）。2026-06-12にカグラが設計したもので、
[[project_restacademy_rari_monitoring]] に要約あり。

RARI = 身体軸(40pt) + 脳軸(35pt) + 時間軸(25pt) 、ランク: A>=80 / B65-79 / C50-64 / D<50。

【重要】元の設計はXiaomi Smart Band(Mi Fitness)の実測値（連続SpO2・Stress Index・
睡眠段階State2-5）を前提にしている。本ダッシュボードは現状Apple Healthのみ対応のため、
Xiaomi固有の指標（脳軸のリラックス%・ストレス平均）は取得できない。
元文書が定義する既存フォールバック（HRV欠損→安静HR代替 等）はそのまま使い、
それ以外の「Apple Healthでは原理的に取得不可」な項目は本モジュール独自の
中間値フォールバックを当てて計算する（= 元v1.0を拡張した"Apple Health版"であり、
Xiaomi版と数値を直接比較しないこと。値の意味が変わる点を毎回明記すること）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --- Apple Health の睡眠カテゴリ値 -----------------------------------------
ASLEEP_VALUES = {
    "HKCategoryValueSleepAnalysisAsleep",  # 旧形式（段階区別なし）
    "HKCategoryValueSleepAnalysisAsleepUnspecified",
    "HKCategoryValueSleepAnalysisAsleepCore",
    "HKCategoryValueSleepAnalysisAsleepDeep",
    "HKCategoryValueSleepAnalysisAsleepREM",
}
DEEP_REM_VALUES = {"HKCategoryValueSleepAnalysisAsleepDeep", "HKCategoryValueSleepAnalysisAsleepREM"}
INBED_VALUE = "HKCategoryValueSleepAnalysisInBed"

# Apple Healthでは取得不可な軸への中間値フォールバック（元文書の欠損時運用に倣う）
NEUTRAL_STRESS_AVG_PT = 4.0      # /8pt
NEUTRAL_RELAX_PCT_PT = 7.5       # /15pt
NEUTRAL_SPO2_PT = 4.0            # /8pt（元文書のSpO2欠損フォールバックをそのまま使用）
NEUTRAL_BEDTIME_CONSISTENCY_PT = 4.0  # /8pt（元文書の「履歴3日未満は判定保留」に対応）
NEUTRAL_STAND_PT = 1.5           # /3pt
NEUTRAL_DEEP_REM_PT = 7.5        # /15pt（睡眠段階が全く取れていない夜)
NEUTRAL_SLEEP_HOURS_PT = 6.0     # /12pt（睡眠記録が全くない参加者）
NEUTRAL_RHR_PT = 2.5             # /5pt


def _lerp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    if x1 == x0:
        return y0
    t = (x - x0) / (x1 - x0)
    t = min(max(t, 0.0), 1.0)
    return y0 + t * (y1 - y0)


def sleep_nights(sleep_df: pd.DataFrame | None, gap_hours: float = 3.0) -> pd.DataFrame:
    """sleepのRecord群を「夜単位」の睡眠エピソードにまとめる。

    endDate〜次のstartDateのギャップが gap_hours を超えたら別の夜として区切る。
    夜のラベル(night_date)は起床時刻(waketime)の日付を使う
    （元RARIレポートの日付表記が「就寝01:40→起床07:20」を同じ日付に紐づけているのに合わせる）。
    """
    cols = ["night_date", "bedtime", "waketime", "total_sleep_min", "deep_rem_min", "deep_rem_pct", "has_stages", "stage_source"]
    if sleep_df is None or len(sleep_df) == 0:
        return pd.DataFrame(columns=cols)

    d = sleep_df.dropna(subset=["startDate", "endDate"]).sort_values("startDate").reset_index(drop=True)
    d["value"] = d["value"].astype(str)

    gap = pd.Timedelta(hours=gap_hours)
    prev_end = d["endDate"].shift(1)
    new_episode = (d["startDate"] - prev_end) > gap
    new_episode.iloc[0] = True
    d["episode"] = new_episode.cumsum()

    rows = []
    for _, g in d.groupby("episode"):
        asleep = g[g["value"].isin(ASLEEP_VALUES)]
        inbed = g[g["value"] == INBED_VALUE]
        if len(asleep):
            total_min = asleep["duration_min"].sum()
            stage_source = "asleep"
        elif len(inbed):
            total_min = inbed["duration_min"].sum()
            stage_source = "inbed_only"  # 段階記録なし・InBedのみの低確度プロキシ
        else:
            continue
        deep_rem_min = g[g["value"].isin(DEEP_REM_VALUES)]["duration_min"].sum()
        has_stages = bool(g["value"].isin(DEEP_REM_VALUES).any())
        rows.append(
            {
                "night_date": g["endDate"].max().date(),
                "bedtime": g["startDate"].min(),
                "waketime": g["endDate"].max(),
                "total_sleep_min": total_min,
                "deep_rem_min": deep_rem_min,
                "deep_rem_pct": (deep_rem_min / total_min * 100) if total_min > 0 else np.nan,
                "has_stages": has_stages,
                "stage_source": stage_source,
            }
        )
    out = pd.DataFrame(rows, columns=cols)
    return out.sort_values("night_date").reset_index(drop=True)


def _bedtime_decimal_hour(ts: pd.Timestamp) -> float:
    """就寝時刻を「23:30が最適」というガウス曲線に載せるため、深夜0-11時台は+24して
    前夜からの連続量として扱う（例: 01:40就寝 → 25.67）。"""
    h = ts.hour + ts.minute / 60.0
    return h + 24.0 if h < 12.0 else h


# --- 身体軸 (Body Score, 40pt) ---------------------------------------------

def _score_sleep_hours(hours: float | None) -> float:
    if hours is None or pd.isna(hours):
        return NEUTRAL_SLEEP_HOURS_PT
    if 7 <= hours <= 9:
        return 12.0
    if 6 <= hours < 7:
        return _lerp(hours, 6, 7.0, 7, 12.0)
    if 5 <= hours < 6:
        return _lerp(hours, 5, 1.0, 6, 7.0)
    if hours > 9:
        return _lerp(hours, 9, 12.0, 11, 8.0) if hours <= 11 else 8.0
    return 1.0  # 5h未満


def _score_deep_rem_pct(pct: float | None, has_stages: bool) -> float:
    if not has_stages or pct is None or pd.isna(pct):
        return NEUTRAL_DEEP_REM_PT
    if 35 <= pct <= 55:
        return 15.0
    if 25 <= pct < 35:
        return _lerp(pct, 25, 8.0, 35, 15.0)
    if 15 <= pct < 25:
        return _lerp(pct, 15, 2.0, 25, 8.0)
    return 2.0  # 15%未満


def _score_spo2(avg: float | None, mn: float | None) -> float:
    if avg is None or pd.isna(avg):
        return NEUTRAL_SPO2_PT
    if avg >= 97 and (mn is None or mn >= 95):
        return 8.0
    if avg >= 96 and (mn is None or mn >= 93):
        return 6.0
    if avg < 92:
        return 0.5
    return _lerp(avg, 92, 0.5, 96, 6.0)


def _score_rhr(rhr: float | None) -> float:
    if rhr is None or pd.isna(rhr):
        return NEUTRAL_RHR_PT
    if rhr < 50:
        return 5.0
    if rhr <= 55:
        return _lerp(rhr, 50, 5.0, 55, 4.5)
    if rhr <= 65:
        return _lerp(rhr, 55, 4.5, 65, 3.0)
    if rhr >= 75:
        return 0.5
    return _lerp(rhr, 65, 3.0, 75, 0.5)


# --- 脳軸 (Mind Score, 35pt) ------------------------------------------------

def _score_hrv_or_rhr(hrv: float | None, rhr_fallback: float | None) -> float:
    if hrv is not None and not pd.isna(hrv):
        if hrv >= 80:
            return 12.0
        if hrv >= 60:
            return _lerp(hrv, 60, 9.0, 80, 12.0)
        if hrv < 25:
            return 1.0
        return _lerp(hrv, 25, 1.0, 60, 9.0)
    # Apple Watch未連携（HRVなし）→ 安静HRで代替（元文書のフォールバック）
    if rhr_fallback is not None and not pd.isna(rhr_fallback):
        if rhr_fallback < 50:
            return 10.0
        if rhr_fallback <= 55:
            return _lerp(rhr_fallback, 50, 10.0, 55, 8.0)
        if rhr_fallback >= 65:
            return 1.5
        return _lerp(rhr_fallback, 55, 8.0, 65, 1.5)
    return NEUTRAL_STRESS_AVG_PT * 1.5  # HRVもRHRも欠損の最終フォールバック（中間値）


# --- 時間軸 (Time Score, 25pt) ----------------------------------------------

def _score_bedtime_timing(bedtime: pd.Timestamp | None) -> float:
    if bedtime is None or pd.isna(bedtime):
        return 5.0  # 中間値
    x = _bedtime_decimal_hour(bedtime)
    return 10.0 * float(np.exp(-((x - 23.5) ** 2) / (2 * 2.5**2)))


def _score_bedtime_consistency(std_hours: float | None, n_nights: int) -> float:
    if n_nights < 3 or std_hours is None or pd.isna(std_hours):
        return NEUTRAL_BEDTIME_CONSISTENCY_PT
    if std_hours < 0.5:
        return 8.0
    if std_hours <= 1.0:
        return _lerp(std_hours, 0.5, 8.0, 1.0, 5.0)
    if std_hours <= 1.5:
        return _lerp(std_hours, 1.0, 5.0, 1.5, 3.0)
    if std_hours <= 2.5:
        return _lerp(std_hours, 1.5, 3.0, 2.5, 1.0)
    return 0.5


def _score_steps(steps: float | None) -> float:
    if steps is None or pd.isna(steps):
        return 2.0
    if 7000 <= steps <= 12000:
        return 4.0
    if 3000 <= steps < 7000:
        return _lerp(steps, 3000, 0.5, 7000, 4.0)
    if 12000 < steps <= 16000:
        return _lerp(steps, 12000, 4.0, 16000, 0.5)
    return 0.5


def _score_stand(stand_hours: float | None) -> float:
    if stand_hours is None or pd.isna(stand_hours):
        return NEUTRAL_STAND_PT
    if stand_hours >= 8:
        return 3.0
    if stand_hours >= 4:
        return _lerp(stand_hours, 4, 1.0, 6, 2.0) if stand_hours <= 6 else _lerp(stand_hours, 6, 2.0, 8, 3.0)
    return 0.5


def rank_of(total: float) -> str:
    if total >= 80:
        return "A"
    if total >= 65:
        return "B"
    if total >= 50:
        return "C"
    return "D"


def build_daily_table(records: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """participants の Apple Health records から日次RARIテーブルを構築する。

    records: apple_health.parse_export_xml() が返す data.records と同じ形。
    stress/relax（Xiaomi専用）は取得できないため、Apple Health版フォールバックで計算する。
    """
    nights = sleep_nights(records.get("sleep"))

    def _daily(df: pd.DataFrame | None, col: str = "value") -> pd.Series:
        if df is None or len(df) == 0:
            return pd.Series(dtype=float)
        d = df.copy()
        d["date"] = d["startDate"].dt.tz_localize(None).dt.date if d["startDate"].dt.tz is not None else d["startDate"].dt.date
        return d.groupby("date")[col].mean()

    def _daily_sum(df: pd.DataFrame | None, col: str = "value") -> pd.Series:
        if df is None or len(df) == 0:
            return pd.Series(dtype=float)
        d = df.copy()
        d["date"] = d["startDate"].dt.tz_localize(None).dt.date if d["startDate"].dt.tz is not None else d["startDate"].dt.date
        return d.groupby("date")[col].sum()

    rhr_daily = _daily(records.get("rhr"))
    hrv_daily = _daily(records.get("hrv"))
    steps_daily = _daily_sum(records.get("steps"))
    spo2_avg = _daily(records.get("spo2"))
    spo2_min = _daily(records.get("spo2"), col="value") if records.get("spo2") is not None else pd.Series(dtype=float)
    if records.get("spo2") is not None and len(records["spo2"]):
        d = records["spo2"].copy()
        d["date"] = d["startDate"].dt.tz_localize(None).dt.date if d["startDate"].dt.tz is not None else d["startDate"].dt.date
        spo2_min = d.groupby("date")["value"].min()
    stand_daily_min = _daily_sum(records.get("stand_time"))  # 分単位（Apple Health仕様）

    if len(nights) == 0:
        all_dates = sorted(set(rhr_daily.index) | set(hrv_daily.index) | set(steps_daily.index))
    else:
        all_dates = sorted(set(nights["night_date"]) | set(rhr_daily.index) | set(hrv_daily.index) | set(steps_daily.index))

    nights_by_date = {r["night_date"]: r for _, r in nights.iterrows()} if len(nights) else {}

    # 就寝時刻の一貫性用: 直近7夜の std（前日以前を対象、当日は含めない=先読み防止）
    bedtime_decimals = {r["night_date"]: _bedtime_decimal_hour(r["bedtime"]) for _, r in nights.iterrows()} if len(nights) else {}
    sorted_night_dates = sorted(bedtime_decimals.keys())

    rows = []
    for date in all_dates:
        night = nights_by_date.get(date)
        sleep_h = (night["total_sleep_min"] / 60.0) if night is not None else None
        deep_rem_pct = night["deep_rem_pct"] if night is not None else None
        has_stages = bool(night["has_stages"]) if night is not None else False
        bedtime = night["bedtime"] if night is not None else None

        idx = sorted_night_dates.index(date) if date in bedtime_decimals else None
        if idx is not None and idx >= 3:
            past7 = [bedtime_decimals[d] for d in sorted_night_dates[max(0, idx - 7):idx]]
            std_hours = float(np.std(past7, ddof=0)) if len(past7) >= 3 else None
            n_nights = len(past7)
        else:
            std_hours = None
            n_nights = idx if idx is not None else 0

        rhr = rhr_daily.get(date)
        hrv = hrv_daily.get(date)
        steps = steps_daily.get(date)
        spo2a = spo2_avg.get(date)
        spo2m = spo2_min.get(date) if len(spo2_min) else None
        stand_h = (stand_daily_min.get(date) / 60.0) if len(stand_daily_min) and stand_daily_min.get(date) is not None else None

        body = (
            _score_sleep_hours(sleep_h)
            + _score_deep_rem_pct(deep_rem_pct, has_stages)
            + _score_spo2(spo2a, spo2m)
            + _score_rhr(rhr)
        )
        mind = (
            NEUTRAL_RELAX_PCT_PT  # Xiaomi専用「リラックス%」はApple Healthで取得不可
            + _score_hrv_or_rhr(hrv, rhr)
            + NEUTRAL_STRESS_AVG_PT  # Xiaomi専用「ストレス平均」も同様
        )
        time_axis = (
            _score_bedtime_timing(bedtime)
            + _score_bedtime_consistency(std_hours, n_nights)
            + _score_steps(steps)
            + _score_stand(stand_h)
        )
        total = body + mind + time_axis

        rows.append(
            {
                "date": date,
                "rari": round(total, 1),
                "rank": rank_of(total),
                "body": round(body, 1),
                "mind": round(mind, 1),
                "time": round(time_axis, 1),
                "sleep_hours": round(sleep_h, 2) if sleep_h is not None else None,
                "deep_rem_pct": round(deep_rem_pct, 1) if deep_rem_pct is not None and not pd.isna(deep_rem_pct) else None,
                "bedtime": bedtime.strftime("%H:%M") if bedtime is not None else None,
                "rhr": rhr,
                "hrv": hrv,
                "steps": steps,
                "mind_axis_confidence": "reduced（Xiaomi専用のstress/relax%は未取得。Apple Health中間値フォールバック適用）",
            }
        )

    return pd.DataFrame(rows)
