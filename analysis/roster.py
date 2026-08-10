"""既存参加者名簿（data/participants.yaml）との照合。

[[project_restacademy_health_reports]]の教訓：名前は聞き取り・入力ミスが起きやすいので、
DOB（生年月日）を一意キーとして機械照合する。名前だけを信じない。
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROSTER_PATH = Path(__file__).resolve().parent.parent / "data" / "participants.yaml"


def load_roster() -> dict:
    """participants.yamlは実在の参加者名・DOB・健康メモを含むため.gitignore対象。
    公開リポジトリ経由のデプロイ（Streamlit Cloud等）では存在しないので、
    その場合は空名簿として扱い、アプリはクラッシュせず「新規参加者」表示にフォールバックする。
    ローカルで名寄せ機能を使う場合は data/participants.yaml.example を参考に自分で置く。
    """
    if not ROSTER_PATH.exists():
        return {"participants": []}
    with open(ROSTER_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {"participants": []}


def match_by_dob(dob: str | None) -> dict | None:
    """<Me>のDOBから既存参加者を検索する。一致なしはNone（=新規参加者の可能性）。"""
    if not dob:
        return None
    roster = load_roster()
    for p in roster.get("participants", []):
        if p.get("dob") == dob:
            return p
    return None


def match_by_mifitness_uid(uid: str | None) -> dict | None:
    if not uid:
        return None
    roster = load_roster()
    for p in roster.get("participants", []):
        if str(p.get("mifitness_uid")) == str(uid):
            return p
    return None
