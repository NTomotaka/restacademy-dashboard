"""レストアカデミー 健康データ分析ダッシュボード（内部用・中山さん/小松様専用）

Usage:
    streamlit run app.py

Apple Health の export.zip（gigafile等でパスワード付きzipに包まれている場合も対応）を
アップロードすると、RHR/HRV/VO2Max/歩数/装着ギャップ/24時間プロファイルを
インタラクティブなダッシュボードで表示する。

2026-08-10 松浦冬馬様フォローアップ分析で確立した集計ロジックを土台にした社内ツールMVP。
MiFitness（森田様・石田様が使用）は次フェーズで対応（実データのパスワードが解けたら追加）。
"""
from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from analysis import metrics, roster  # noqa: E402
from parsers import apple_health  # noqa: E402

st.set_page_config(page_title="RestAcademy 健康データダッシュボード", page_icon="🩺", layout="wide")

NAVY = "#0c1f3b"
AMBER = "#b88950"
RUST = "#a44b3a"
MOSS = "#4b6855"
TEAL = "#2f6f8f"


# ---------------------------------------------------------------------------
# 簡易パスワードゲート（内部限定ツール用。厳格な認証ではなく共有シークレット方式）
# ---------------------------------------------------------------------------
def check_password() -> bool:
    try:
        secret = st.secrets.get("app_password")
    except Exception:
        secret = None  # secrets.toml未配置（ローカル検証中など）
    if not secret:
        return True  # secrets未設定時（ローカル検証中）はゲート無効
    if st.session_state.get("authed"):
        return True
    pw = st.text_input("パスワード", type="password")
    if pw == secret:
        st.session_state["authed"] = True
        st.rerun()
    elif pw:
        st.error("パスワードが違う")
    return False


if not check_password():
    st.stop()


# ---------------------------------------------------------------------------
# アップロード & 解凍
# ---------------------------------------------------------------------------
st.title("🩺 RestAcademy 健康データダッシュボード")
st.caption("松浦冬馬様フォローアップ分析（2026-08-10）で確立したロジックを社内ツール化したもの。現在 Apple Health のみ対応。")

with st.sidebar:
    st.header("データ投入")
    uploaded = st.file_uploader("Apple Health エクスポート（.zip / .xml）", type=["zip", "xml"])
    outer_pw = st.text_input("zipパスワード（不要なら空欄）", type="password")
    st.caption("gigafile等で二重zipになっている場合、内側のzipにも同じパスワードで自動トライする。")


def find_export_xml(root: Path, password: str) -> Path | None:
    """展開済みディレクトリ配下で export.xml を探す。ネストしたzipも1段階だけ再帰的に展開する。"""
    hit = list(root.rglob("export.xml"))
    if hit:
        return hit[0]
    for inner_zip in root.rglob("*.zip"):
        try:
            with zipfile.ZipFile(inner_zip) as zf:
                pwd = password.encode() if password else None
                zf.extractall(inner_zip.parent / inner_zip.stem, pwd=pwd)
        except (RuntimeError, zipfile.BadZipFile):
            continue  # このzipは別デバイス用など。パスワード不一致はスキップして続行
    hit = list(root.rglob("export.xml"))
    return hit[0] if hit else None


def detect_other_devices(root: Path) -> list[str]:
    """export.xml以外に見つかった未対応データ（MiFitness等）を検出して名前を返す。"""
    found = {p.name for p in root.rglob("*MiFitness*") if p.is_file() and p.suffix == ".zip"}
    return sorted(found)


@st.cache_data(show_spinner=False)
def load_apple_health(xml_bytes: bytes) -> tuple[dict, dict[str, pd.DataFrame], pd.DataFrame]:
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        tmp.write(xml_bytes)
        tmp_path = tmp.name
    data = apple_health.parse_export_xml(tmp_path)
    return data.me, data.records, data.workouts


if uploaded is None:
    st.info("左のサイドバーからApple Healthのエクスポートファイルをアップロードしてください。")
    st.stop()

work_dir = Path(tempfile.mkdtemp())
xml_path: Path | None = None
other_devices: list[str] = []

if uploaded.name.endswith(".xml"):
    xml_path = work_dir / "export.xml"
    xml_path.write_bytes(uploaded.getvalue())
else:
    outer_path = work_dir / uploaded.name
    outer_path.write_bytes(uploaded.getvalue())
    try:
        with zipfile.ZipFile(outer_path) as zf:
            pwd = outer_pw.encode() if outer_pw else None
            zf.extractall(work_dir, pwd=pwd)
    except RuntimeError:
        st.error("zipのパスワードが違う（または未入力）。サイドバーで入力してください。")
        st.stop()
    except zipfile.BadZipFile:
        st.error("zipファイルとして開けなかった。ファイルが壊れているか、対応形式ではない。")
        st.stop()
    with st.spinner("export.xml を探索中（ネストしたzipも展開）..."):
        xml_path = find_export_xml(work_dir, outer_pw)
    other_devices = detect_other_devices(work_dir)

if other_devices:
    st.warning(
        "以下のファイルはMiFitness形式で、本ダッシュボードは現在未対応: "
        + ", ".join(other_devices)
        + "。実データのパスワードが解けたら次フェーズで対応する。"
    )

if xml_path is None:
    st.error("export.xml が見つからなかった。Apple Healthのエクスポートか確認してください。")
    st.stop()

with st.spinner("Apple Health データを解析中（数百MB規模だと数十秒かかる）..."):
    me, records, workouts = load_apple_health(xml_path.read_bytes())

info = apple_health.me_summary(me)
matched = roster.match_by_dob(info.get("dob"))

# ---------------------------------------------------------------------------
# 参加者ヘッダー
# ---------------------------------------------------------------------------
col1, col2 = st.columns([3, 1])
with col1:
    name = matched["name"] if matched else "（既存名簿に一致なし＝新規参加者の可能性）"
    st.subheader(f"👤 {name}")
    sex_label = {"male": "男性", "female": "女性"}.get(info.get("sex"), "不明")
    st.caption(f"{sex_label}・{info.get('age', '?')}歳（{info.get('dob', '不明')}）")
    if matched:
        st.caption(f"既存メモ：{matched.get('notes', '（なし）')}")
with col2:
    if matched is None:
        st.warning("DOB一致なし。名前入力ミスの可能性もあるため、本人に確認してから新規登録すること。")

st.divider()

# ---------------------------------------------------------------------------
# KPIカード
# ---------------------------------------------------------------------------
rhr, hrv, vo2max, steps_df, hr = (
    records.get("rhr"),
    records.get("hrv"),
    records.get("vo2max"),
    records.get("steps"),
    records.get("hr"),
)

k1, k2, k3, k4, k5 = st.columns(5)
with k1:
    if len(rhr):
        latest = rhr.sort_values("startDate").iloc[-1]
        st.metric("RHR（最新）", f"{latest['value']:.0f} BPM", f"平均 {rhr['value'].mean():.1f}")
    else:
        st.metric("RHR", "データなし")
with k2:
    if len(hrv):
        st.metric("HRV（全期間平均）", f"{hrv['value'].mean():.1f} ms")
    else:
        st.metric("HRV", "データなし")
with k3:
    if len(vo2max):
        latest = vo2max.sort_values("startDate").iloc[-1]
        st.metric("VO2Max（最新）", f"{latest['value']:.1f}", "mL/kg/min")
    else:
        st.metric("VO2Max", "データなし")
with k4:
    if len(steps_df):
        daily = metrics.daily_sum(steps_df)
        st.metric("歩数（直近30日）", f"{daily.tail(30).mean():,.0f}", f"平均 {daily.mean():,.0f}歩/日")
    else:
        st.metric("歩数", "データなし")
with k5:
    if len(hr):
        cov = metrics.device_coverage_timeline(hr)
        recent = cov.tail(2).sum()
        status = "⚠ 欠測" if recent < 50 else ("低頻度" if recent < 1000 else "正常")
        st.metric("直近2ヶ月装着", status, f"HR{int(recent)}件")
    else:
        st.metric("装着状況", "データなし")

# ---------------------------------------------------------------------------
# 装着ギャップ アラート
# ---------------------------------------------------------------------------
if len(hr):
    cov = metrics.device_coverage_timeline(hr)
    gaps = metrics.coverage_gaps(cov, zero_run_min_months=2)
    if gaps:
        with st.container(border=True):
            st.markdown("### ⚠ ウェアラブル装着ギャップを検知")
            for g in gaps:
                st.write(f"- **{g['start']} 〜 {g['end']}**（{g['months']}ヶ月間、記録ほぼゼロ）")
            st.caption("2ヶ月以上連続でHR記録が0件の期間を自動検出。「回復指標が見えていない」期間の特定に使う。")

st.divider()

# ---------------------------------------------------------------------------
# タブ構成
# ---------------------------------------------------------------------------
tab_names = ["RHR", "HRV", "歩数", "VO2Max", "装着ギャップ", "1日の流れ", "曜日×時間帯", "睡眠・ワークアウト"]
tabs = st.tabs(tab_names)

with tabs[0]:
    if len(rhr):
        m = metrics.monthly_mean(rhr)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=m.index.astype(str), y=m.values, mode="lines+markers", line=dict(color=TEAL), name="RHR"))
        fig.add_hline(y=65, line_dash="dash", line_color=RUST, annotation_text="要注意ライン 65 BPM")
        fig.add_hrect(y0=50, y1=57, fillcolor=MOSS, opacity=0.1, line_width=0, annotation_text="良好ゾーン")
        fig.update_layout(height=420, yaxis_title="BPM", margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("RHRデータなし")

with tabs[1]:
    if len(hrv):
        m = metrics.monthly_mean(hrv)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=m.index.astype(str), y=m.values, marker_color=AMBER, name="HRV"))
        fig.add_hline(y=hrv["value"].mean(), line_dash="dash", line_color=NAVY, annotation_text=f"全期間平均 {hrv['value'].mean():.1f}ms")
        fig.update_layout(height=420, yaxis_title="SDNN (ms)", margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("HRVデータなし")

with tabs[2]:
    if len(steps_df):
        daily = metrics.daily_sum(steps_df)
        monthly = daily.resample("MS").mean()
        fig = go.Figure()
        fig.add_trace(go.Bar(x=monthly.index, y=monthly.values, marker_color=TEAL, name="月次平均歩数"))
        fig.add_hline(y=8000, line_dash="dash", line_color=RUST, annotation_text="目標 8,000歩/日")
        fig.update_layout(height=420, yaxis_title="歩数/日", margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("直近90日（日次）")
        last90 = daily.tail(90)
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(x=last90.index, y=last90.values, marker_color=TEAL))
        fig2.add_hline(y=8000, line_dash="dash", line_color=NAVY)
        fig2.update_layout(height=320, margin=dict(t=20))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("歩数データなし")

with tabs[3]:
    if len(vo2max):
        v = vo2max.sort_values("startDate")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=v["startDate"], y=v["value"], mode="lines+markers+text",
                                  text=[f"{x:.1f}" for x in v["value"]], textposition="top center",
                                  line=dict(color=AMBER, width=3), marker=dict(size=9)))
        fig.add_hrect(y0=42, y1=43, fillcolor=MOSS, opacity=0.12, line_width=0, annotation_text="平均以上")
        fig.update_layout(height=420, yaxis_title="VO2Max (mL/kg/min)", margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("VO2Maxデータなし")

with tabs[4]:
    if len(hr):
        cov = metrics.device_coverage_timeline(hr)
        colors = [RUST if v == 0 else (AMBER if v < 500 else TEAL) for v in cov.values]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cov.index.astype(str), y=cov.values, marker_color=colors))
        fig.update_layout(height=420, yaxis_title="HR記録数/月", margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("🔵 正常装着（500件/月以上） 🟠 低頻度装着 🔴 完全欠測（0件）")
    else:
        st.info("HRデータなし（装着ギャップ判定不可）")

with tabs[5]:
    if len(hr):
        prof = metrics.hourly_profile(hr)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=prof["hour"], y=prof["mean"], mode="lines+markers",
                                  line=dict(color=RUST, width=2),
                                  hovertext=[f"n={int(c)}" for c in prof["count"]]))
        fig.update_layout(height=420, xaxis_title="時刻(JST)", yaxis_title="平均HR(bpm)", margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("ホバーでその時間帯のサンプル数(n)を確認できる。nが薄い時間帯は解釈に注意。")
    else:
        st.info("HRデータなし")

with tabs[6]:
    if len(hr):
        mat, cnt = metrics.weekday_hour_heatmap(hr)
        fig = go.Figure(data=go.Heatmap(
            z=mat, x=list(range(24)), y=["月", "火", "水", "木", "金", "土", "日"],
            colorscale="RdYlBu_r", colorbar=dict(title="bpm"),
        ))
        fig.update_layout(height=420, xaxis_title="時刻(JST)", margin=dict(t=20))
        st.plotly_chart(fig, use_container_width=True)
        st.caption("グレー（データなし表示）= サンプル不足(n<8)のセル")
    else:
        st.info("HRデータなし")

with tabs[7]:
    sleep = records.get("sleep")
    st.markdown("#### 睡眠")
    if len(sleep):
        st.write(f"記録数: {len(sleep)}件 ／ 最終記録: {sleep['startDate'].max()}")
        st.dataframe(sleep.sort_values("startDate", ascending=False).head(20), use_container_width=True)
    else:
        st.warning("睡眠ステージ記録なし（InBedのみ・またはデータなし）")
    st.markdown("#### ワークアウト")
    if len(workouts):
        st.dataframe(workouts.sort_values("startDate", ascending=False), use_container_width=True)
    else:
        st.info("ワークアウト記録なし")

st.divider()
st.caption("本ツールは自己申告ではなくApple Health実測データに基づく分析。医療的診断ではなく、プログラム設計の参考資料として使用すること。")
