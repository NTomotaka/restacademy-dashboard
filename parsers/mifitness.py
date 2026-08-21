"""MiFitness（Xiaomi Mi Fitness / Zepp系）エクスポートzipのパーサー。

2026-08-21、田中良美様の`Xiaomi田中良美.zip`を実際のパスワードで復号して
スキーマを確認した上で実装（[[project_restacademy_rari_monitoring]]参照。
パスワードは中山さんが過去チャットで入力した記録から復元した）。

エクスポート形式は `<日付>_<Uid>_MiFitness_hlth_center_fitness_data.csv` 等の
ロング形式CSV（列: Uid, Sid, Key, Time, Value(JSON文字列), UpdateTime）。
Time列はUnixエポック秒。

Apple Healthには存在しない指標（連続Stress・睡眠段階Xiaomi State2-5）が
ここから取れるため、RARI v1.0が本来前提にしていたXiaomi実測値ベースの
脳軸（ストレス平均・リラックス%）を計算できるようになる。
[[project_restacademy_health_reports]]の教訓通り、Uidは参加者ごとに不変なキーなので
名前より優先して照合すること（roster.match_by_mifitness_uid）。
"""
from __future__ import annotations

import csv
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# Xiaomi睡眠段階 → Apple Health風カテゴリ文字列（analysis/rari.pyのsleep_nights()を
# デバイス間で共通利用するための変換。出典はRARI v1.0設計文書の対応表）。
SLEEP_STATE_TO_CATEGORY = {
    1: "HKCategoryValueSleepAnalysisAsleepUnspecified",
    2: "HKCategoryValueSleepAnalysisAsleepDeep",
    3: "HKCategoryValueSleepAnalysisAsleepCore",
    4: "HKCategoryValueSleepAnalysisAsleepREM",
    5: "HKCategoryValueSleepAnalysisAwake",
}


@dataclass
class MiFitnessData:
    me: dict = field(default_factory=dict)
    records: dict[str, pd.DataFrame] = field(default_factory=dict)
    workouts: pd.DataFrame = field(default_factory=pd.DataFrame)


def _to_jst(epoch_sec) -> pd.Timestamp | None:
    if epoch_sec is None:
        return None
    try:
        return pd.Timestamp(int(epoch_sec), unit="s", tz="UTC").tz_convert("Asia/Tokyo")
    except (ValueError, OverflowError):
        return None


def _find(names: list[str], suffix: str) -> str | None:
    return next((n for n in names if n.endswith(suffix)), None)


def _read_csv_rows(zf: zipfile.ZipFile, name: str, password: bytes | None) -> list[dict]:
    with zf.open(name, pwd=password) as fh:
        text = fh.read().decode("utf-8-sig")
    return list(csv.DictReader(text.splitlines()))


def parse_export_zip(path: str | Path, password: str | None = None) -> MiFitnessData:
    """MiFitnessエクスポートzip（gigafile等の二重zipは呼び出し側で展開済みを渡す）を解析する。

    hlth_center_fitness_data.csv（イベント粒度: heart_rate/steps/stress/sleep/valid_stand等）と
    user_member_profile.csv（Uid/性別/生年月日）を読む。集計済みの
    hlth_center_aggregated_fitness_data.csv（日次stress等）は今回は使わない
    （粒度の細かい生データの方をRARI/RFW計算の入力として優先する）。
    """
    pwd = password.encode() if password else None
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        fitness_name = _find(names, "hlth_center_fitness_data.csv")
        profile_name = _find(names, "user_member_profile.csv")
        if fitness_name is None:
            raise ValueError(
                "hlth_center_fitness_data.csv が見つからない。"
                "MiFitness標準エクスポート（gigafile等のzip直下に *_hlth_center_*.csv 一式）か確認すること。"
            )
        fitness_rows = _read_csv_rows(zf, fitness_name, pwd)
        me: dict = {}
        if profile_name:
            prof_rows = _read_csv_rows(zf, profile_name, pwd)
            if prof_rows:
                p = prof_rows[0]
                me = {"dob": p.get("Birth"), "sex": p.get("Sex"), "uid": p.get("Uid")}

    buckets: dict[str, list[dict]] = {
        "hr": [], "rhr": [], "steps": [], "sleep": [], "stress": [], "stand_time": [],
    }
    n_skipped = 0
    for row in fitness_rows:
        key = row.get("Key")
        try:
            val = json.loads(row["Value"])
        except (json.JSONDecodeError, TypeError, KeyError):
            n_skipped += 1
            continue

        if key == "heart_rate" and val.get("time") is not None and val.get("bpm") is not None:
            buckets["hr"].append({"startDate": _to_jst(val["time"]), "value": val["bpm"]})
        elif key == "resting_heart_rate" and val.get("date_time") is not None:
            buckets["rhr"].append({"startDate": _to_jst(val["date_time"]), "value": val.get("bpm")})
        elif key == "steps" and val.get("time") is not None:
            buckets["steps"].append({"startDate": _to_jst(val["time"]), "value": val.get("steps", 0)})
        elif key == "stress" and val.get("time") is not None and val.get("stress") is not None:
            buckets["stress"].append({"startDate": _to_jst(val["time"]), "value": val["stress"]})
        elif key == "valid_stand" and val.get("start_time") is not None and val.get("end_time") is not None:
            st, en = val["start_time"], val["end_time"]
            buckets["stand_time"].append(
                {"startDate": _to_jst(st), "endDate": _to_jst(en), "value": (en - st) / 60.0}
            )
        elif key == "sleep":
            for item in val.get("items", []):
                cat = SLEEP_STATE_TO_CATEGORY.get(item.get("state"))
                if cat is None or item.get("start_time") is None or item.get("end_time") is None:
                    continue
                buckets["sleep"].append(
                    {"startDate": _to_jst(item["start_time"]), "endDate": _to_jst(item["end_time"]), "value": cat}
                )

    records: dict[str, pd.DataFrame] = {}
    for k, rows in buckets.items():
        df = pd.DataFrame(rows)
        if len(df):
            df = df.dropna(subset=["startDate"])
            if "endDate" not in df.columns:
                df["endDate"] = df["startDate"]
            else:
                df["endDate"] = df["endDate"].fillna(df["startDate"])
            df["duration_min"] = (df["endDate"] - df["startDate"]).dt.total_seconds() / 60.0
            if k != "sleep":
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
        records[k] = df

    # rari.py/fatigue_window.pyはspo2・vo2max・hrvを参照するが、MiFitness Mi Bandには
    # 該当データがない（実測不可）。空DataFrameとして明示しておき、Apple Health版と同じ
    # フォールバック経路（欠損時中間値）を自然に通す。
    for missing_key in ("spo2", "vo2max", "hrv"):
        records.setdefault(missing_key, pd.DataFrame())

    return MiFitnessData(me=me, records=records, workouts=pd.DataFrame())


def me_summary(me: dict) -> dict:
    """user_member_profile.csvの属性から人間が読める基本情報を作る（apple_health.me_summaryと同形)。"""
    dob = me.get("dob")
    sex = me.get("sex") if me.get("sex") in ("male", "female") else "unknown"
    age = None
    if dob:
        try:
            born = pd.to_datetime(dob)
            today = pd.Timestamp.now()
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except (ValueError, TypeError):
            pass
    return {"dob": dob, "sex": sex, "age": age}
