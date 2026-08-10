"""Apple Health export.xml パーサー。

`export.xml`（数百MB規模）を ET.iterparse でストリーム解析し、
レストアカデミー分析で使う指標だけをDataFrameとして取り出す。
一度に全部DOM展開すると数百MB〜数GBのメモリを食うため iterparse 必須。
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import pandas as pd

# レストアカデミー分析で使う指標のみ抽出する（増やす場合はここに追記）
TARGET_TYPES = {
    "HKQuantityTypeIdentifierRestingHeartRate": "rhr",
    "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv",
    "HKQuantityTypeIdentifierVO2Max": "vo2max",
    "HKQuantityTypeIdentifierStepCount": "steps",
    "HKQuantityTypeIdentifierHeartRate": "hr",
    "HKQuantityTypeIdentifierWalkingHeartRateAverage": "walking_hr",
    "HKCategoryTypeIdentifierSleepAnalysis": "sleep",
    "HKQuantityTypeIdentifierAppleExerciseTime": "exercise_time",
    "HKQuantityTypeIdentifierAppleWalkingSteadiness": "walking_steadiness",
    "HKQuantityTypeIdentifierSixMinuteWalkTestDistance": "six_min_walk",
}


@dataclass
class AppleHealthData:
    me: dict = field(default_factory=dict)
    records: dict[str, pd.DataFrame] = field(default_factory=dict)
    workouts: pd.DataFrame = field(default_factory=pd.DataFrame)


def parse_export_xml(path: str, progress_cb=None) -> AppleHealthData:
    """export.xml を1回のストリームパスで解析する。

    progress_cb(n_elements_processed) を渡すとUI側で進捗表示できる。
    """
    rows: dict[str, list] = {k: [] for k in TARGET_TYPES.values()}
    workout_rows: list[dict] = []
    me: dict = {}

    n = 0
    for _event, elem in ET.iterparse(path, events=("end",)):
        tag = elem.tag
        if tag == "Record":
            t = elem.attrib.get("type")
            key = TARGET_TYPES.get(t)
            if key:
                rows[key].append(
                    {
                        "startDate": elem.attrib.get("startDate"),
                        "endDate": elem.attrib.get("endDate"),
                        "value": elem.attrib.get("value"),
                        "unit": elem.attrib.get("unit"),
                        "source": elem.attrib.get("sourceName"),
                    }
                )
            elem.clear()
        elif tag == "Workout":
            dist = energy = None
            for child in elem:
                ctype = child.attrib.get("type", "")
                if "Distance" in ctype:
                    dist = child.attrib.get("sum")
                elif "EnergyBurned" in ctype:
                    energy = child.attrib.get("sum")
            workout_rows.append(
                {
                    "type": elem.attrib.get("workoutActivityType"),
                    "startDate": elem.attrib.get("startDate"),
                    "endDate": elem.attrib.get("endDate"),
                    "duration_min": elem.attrib.get("duration"),
                    "distance_km": dist,
                    "energy_kcal": energy,
                    "source": elem.attrib.get("sourceName"),
                }
            )
            elem.clear()
        elif tag == "Me":
            me = dict(elem.attrib)
            elem.clear()
        else:
            elem.clear()

        n += 1
        if progress_cb and n % 500_000 == 0:
            progress_cb(n)

    records = {}
    for key, data in rows.items():
        df = pd.DataFrame(data)
        if len(df):
            df["startDate"] = pd.to_datetime(df["startDate"], format="mixed")
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
        records[key] = df

    workouts = pd.DataFrame(workout_rows)
    if len(workouts):
        workouts["startDate"] = pd.to_datetime(workouts["startDate"], format="mixed")
        workouts["endDate"] = pd.to_datetime(workouts["endDate"], format="mixed")
        for c in ("duration_min", "distance_km", "energy_kcal"):
            workouts[c] = pd.to_numeric(workouts[c], errors="coerce")

    return AppleHealthData(me=me, records=records, workouts=workouts)


def me_summary(me: dict) -> dict:
    """<Me>属性から人間が読める形の基本情報を作る。"""
    dob = me.get("HKCharacteristicTypeIdentifierDateOfBirth")
    sex_raw = me.get("HKCharacteristicTypeIdentifierBiologicalSex", "")
    sex = {"HKBiologicalSexMale": "male", "HKBiologicalSexFemale": "female"}.get(sex_raw, "unknown")
    age = None
    if dob:
        try:
            born = pd.to_datetime(dob)
            today = pd.Timestamp.now()
            age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
        except Exception:
            pass
    return {"dob": dob, "sex": sex, "age": age}
