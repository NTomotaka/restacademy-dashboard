"""RestAcademy Fatigue Window（RFW）v0.1 — 疲労時間帯の検出。

小松様提案の運用モデル文書「レストアカデミー　運用」
（Drive id: 1PXqxLdy1culK6by51tBnbkTJ31rqOP2gNchMpja9VFE、2026-08-19作成、
[[project_restacademy_rari_monitoring]] に要約あり）で示された考え方を実装したもの。
文書内では「RARIとは別アルゴリズム」「まだRARI v1.0には存在しない新規アルゴリズム」と
明記されており、v1.0という確定版は無い。ここではv0.1として初実装する。

考え方（文書より）:
    疲労指数 = 心拍負荷 + HRV低下 + Stress上昇 + 不活動 + 回復不足
    z = (現在値 − 本人平均) ÷ 本人SD  ※絶対値ではなく本人の直近ベースラインからの偏差
    HR高 + 不活動 が重なった時間帯を「疲労Window」とする。

Apple Healthからは HRV/Stressの連続値が取れないため、v0.1では
「HR上昇（本人ベースラインからのz-score）× 同時刻の低活動（歩数少)」の2条件に限定する。
HRVを使う版はMiFitness/Xiaomiパーサー対応後に拡張する（[[project_restacademy_rari_monitoring]]参照）。

【データ不足への配慮】
心拍が間欠的にしか記録されていない参加者（Apple Watch非常時装着等）では、
ビン数が少なすぎて「たまたま」有意に見えるだけの疑似パターンが出やすい。
min_days_required等のゲートを設け、不足時は結果を出さず理由を明記する
（[[feedback_marketing_judgment_ownership]]の「事実確認と憶測は分けて報告する」姿勢に倣う）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _strip_tz(s: pd.Series) -> pd.Series:
    return s.dt.tz_localize(None) if s.dt.tz is not None else s


def detect_fatigue_windows(
    hr_df: pd.DataFrame | None,
    steps_df: pd.DataFrame | None,
    bin_minutes: int = 15,
    baseline_days: int = 7,
    z_threshold: float = 1.0,
    low_activity_steps: float = 30.0,
    min_days_required: int = 5,
    min_samples_per_bin: int = 3,
    exclude_hours: tuple[int, int] = (0, 6),
) -> dict:
    """RFW v0.1: 心拍上昇×低活動が重なる時間帯を、本人の直近ベースラインからの偏差で検出する。

    戻り値:
        {"insufficient_data": bool, "reason": str, "days_covered": int,
         "timeline": DataFrame(index=bin開始時刻, columns=[hr, z, low_activity, flagged]),
         "hourly_summary": DataFrame(hour_bin, flag_rate, n)}
    """
    if hr_df is None or len(hr_df) == 0:
        return {"insufficient_data": True, "reason": "心拍データがない", "days_covered": 0}

    d = hr_df.dropna(subset=["startDate", "value"]).copy()
    d["startDate"] = _strip_tz(d["startDate"])
    d = d.set_index("startDate").sort_index()

    days_covered = d.index.normalize().nunique()
    if days_covered < min_days_required:
        return {
            "insufficient_data": True,
            "reason": f"心拍記録がある日数が{days_covered}日のみ（{min_days_required}日以上推奨）。"
            "装着が間欠的だと疑似パターンが出やすいため判定を保留する。",
            "days_covered": days_covered,
        }

    hr_bin = d["value"].resample(f"{bin_minutes}min").mean().dropna()
    if len(hr_bin) < min_days_required * (24 * 60 // bin_minutes) * 0.05:
        return {
            "insufficient_data": True,
            "reason": "ビン化後のサンプル数が少なすぎる（装着率が低い可能性）。",
            "days_covered": days_covered,
        }

    window = max(10, int(baseline_days * 24 * 60 / bin_minutes))
    min_periods = max(10, window // 4)
    roll_mean = hr_bin.rolling(window, min_periods=min_periods).mean().shift(1)
    roll_std = hr_bin.rolling(window, min_periods=min_periods).std().shift(1)
    z = (hr_bin - roll_mean) / roll_std.replace(0, np.nan)

    steps_bin = pd.Series(index=hr_bin.index, dtype=float)
    if steps_df is not None and len(steps_df):
        s = steps_df.dropna(subset=["startDate", "value"]).copy()
        s["startDate"] = _strip_tz(s["startDate"])
        s = s.set_index("startDate").sort_index()
        steps_bin = s["value"].resample(f"{bin_minutes}min").sum().reindex(hr_bin.index, fill_value=0)

    # 「低活動」は絶対値（そのビンの歩数がほぼゼロ）で判定する。
    # 全期間のパーセンタイルで判定すると、深夜帯の大量のゼロ歩数ビンに引っ張られて
    # 閾値がほぼ0近くまで下がり、「運動中で歩数が多いはずのビン」まで
    # 低活動と誤判定してしまう（2026-08-21のスモークテストで発覚した実バグ。
    # 松浦様の早朝ランニング習慣[[project_restacademy_health_reports]]がノイズ源になっていた）。
    low_activity = steps_bin.fillna(0) <= low_activity_steps

    flagged = (z > z_threshold) & low_activity

    timeline = pd.DataFrame({"hr": hr_bin, "z": z, "steps": steps_bin, "low_activity": low_activity, "flagged": flagged.fillna(False)})
    timeline["hour_bin"] = timeline.index.hour + (timeline.index.minute // bin_minutes) * (bin_minutes / 60)

    # 就寝〜早朝(既定0-6時)は「疲労時間帯への先回り介入(マイクロブレイク)」の対象にならないため、
    # 睡眠中/起床直後の生理的なHR変動をここで除外する（早朝ランニング等の運動由来の
    # HR上昇と、日中の"疲れているのに動けていない"状態を混同しないため）。
    lo, hi = exclude_hours
    in_excluded = (timeline["hour_bin"] >= lo) & (timeline["hour_bin"] < hi)
    reportable = timeline[~in_excluded]

    agg = reportable.groupby("hour_bin").agg(flag_rate=("flagged", "mean"), n=("flagged", "size"))
    agg = agg[agg["n"] >= min_samples_per_bin].sort_values("flag_rate", ascending=False)

    return {
        "insufficient_data": False,
        "reason": "",
        "days_covered": int(days_covered),
        "timeline": timeline,
        "hourly_summary": agg.reset_index(),
    }


def top_fatigue_windows(hourly_summary: pd.DataFrame, min_flag_rate: float = 0.3, top_n: int = 5) -> list[dict]:
    """疲労Window候補（時間帯とフラグ率）を上位N件、リストで返す。"""
    if hourly_summary is None or len(hourly_summary) == 0:
        return []
    cand = hourly_summary[hourly_summary["flag_rate"] >= min_flag_rate].head(top_n)
    out = []
    for _, r in cand.iterrows():
        h = r["hour_bin"]
        hh = int(h)
        mm = int(round((h - hh) * 60))
        out.append({"time": f"{hh:02d}:{mm:02d}", "flag_rate": round(float(r["flag_rate"]) * 100, 0), "n": int(r["n"])})
    return out
