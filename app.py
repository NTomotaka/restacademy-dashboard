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

import ui  # noqa: E402
from analysis import fatigue_window, metrics, rari, roster  # noqa: E402
from parsers import apple_health  # noqa: E402

PHASE_LABELS = {
    "phase1": "Phase1｜ベースライン測定（1週間）",
    "phase2": "Phase2｜マイクロブレイク介入（1週間）",
    "phase3": "Phase3｜教育介入後・再測定",
    "unspecified": "指定なし（単発解析）",
}

st.set_page_config(page_title="RestAcademy 健康データダッシュボード", page_icon="🩺", layout="wide")
ui.inject_css()

NAVY = ui.NAVY
AMBER = ui.AMBER
RUST = ui.RUST
MOSS = ui.MOSS
TEAL = ui.TEAL


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
ui.hero(
    title="🩺 健康データ ダッシュボード",
    subtitle="松浦冬馬様フォローアップ分析（2026-08-10）で確立したロジックを社内ツール化したもの。"
    "RHR・HRV・VO<sub>2</sub>Max・歩数・装着ギャップをzip1つで可視化する。",
    tag="RestAcademy × Apple Health 統合分析ツール",
    badge="現在 Apple Health のみ対応 ／ MiFitnessは次フェーズ",
)

with st.sidebar:
    st.header("データ投入")
    phase_key = st.selectbox(
        "この計測がどのPhaseか",
        options=list(PHASE_LABELS.keys()),
        format_func=lambda k: PHASE_LABELS[k],
        help="小松様の運用モデル（レストアカデミー運用ドキュメント）のPhase1〜3。"
        "同じ参加者で複数回アップロードすると、下部の「Phase比較」タブに蓄積される。",
    )
    uploaded = st.file_uploader("Apple Health エクスポート（.zip / .xml）", type=["zip", "xml"])
    outer_pw = st.text_input("zipパスワード（不要なら空欄）", type="password")
    st.caption("gigafile等で二重zipになっている場合、内側のzipにも同じパスワードで自動トライする。")
    if st.session_state.get("phase_results"):
        st.markdown("---")
        st.caption("このセッションで記録済みのPhase:")
        for k, v in st.session_state["phase_results"].items():
            st.caption(f"✅ {PHASE_LABELS.get(k, k)} — {v['name']}（{v['n_days']}日分・平均RARI {v['avg_rari']:.1f}）")


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
# RARI(日次)＋疲労Window(RFW) 算出、Phase別にセッション内へ蓄積
# ---------------------------------------------------------------------------
with st.spinner("RARIスコア・疲労Windowを算出中..."):
    rari_table = rari.build_daily_table(records)
    fw_result = fatigue_window.detect_fatigue_windows(records.get("hr"), records.get("steps"))

if "phase_results" not in st.session_state:
    st.session_state["phase_results"] = {}
if len(rari_table):
    st.session_state["phase_results"][phase_key] = {
        "name": matched["name"] if matched else "（新規/未照合）",
        "n_days": int(len(rari_table)),
        "avg_rari": float(rari_table["rari"].mean()),
        "date_range": (str(rari_table["date"].min()), str(rari_table["date"].max())),
        "rari_table": rari_table,
        "fw_result": fw_result,
    }

# ---------------------------------------------------------------------------
# 参加者ヘッダー
# ---------------------------------------------------------------------------
col1, col2 = st.columns([3, 1])
with col1:
    name = matched["name"] if matched else "既存名簿に一致なし（新規参加者の可能性）"
    sex_label = {"male": "男性", "female": "女性"}.get(info.get("sex"), "不明")
    meta = f"{sex_label}・{info.get('age', '?')}歳（{info.get('dob', '不明')}）"
    if matched:
        meta += f" ／ 既存メモ：{matched.get('notes', '（なし）')}"
    ui.person_header(name, meta)
with col2:
    if matched is None:
        st.warning("DOB一致なし。名前入力ミスの可能性もあるため、本人に確認してから新規登録すること。")

st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)

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

kpi_items = []
if len(rhr):
    latest = rhr.sort_values("startDate").iloc[-1]
    kpi_items.append({"label": "RHR（最新）", "value": f"{latest['value']:.0f}", "unit": "BPM",
                       "delta": f"全期間平均 {rhr['value'].mean():.1f}"})
else:
    kpi_items.append({"label": "RHR", "value": "—"})

if len(hrv):
    kpi_items.append({"label": "HRV（全期間平均）", "value": f"{hrv['value'].mean():.1f}", "unit": "ms"})
else:
    kpi_items.append({"label": "HRV", "value": "—"})

if len(vo2max):
    latest = vo2max.sort_values("startDate").iloc[-1]
    kpi_items.append({"label": "VO2Max（最新）", "value": f"{latest['value']:.1f}", "unit": "mL/kg/min"})
else:
    kpi_items.append({"label": "VO2Max", "value": "—"})

if len(steps_df):
    daily = metrics.daily_sum(steps_df)
    kpi_items.append({"label": "歩数（直近30日）", "value": f"{daily.tail(30).mean():,.0f}", "unit": "歩/日",
                       "delta": f"全期間平均 {daily.mean():,.0f}"})
else:
    kpi_items.append({"label": "歩数", "value": "—"})

if len(hr):
    cov = metrics.device_coverage_timeline(hr)
    recent = cov.tail(2).sum()
    is_warn = recent < 50
    status = "⚠ 欠測" if is_warn else ("低頻度" if recent < 1000 else "正常")
    kpi_items.append({"label": "直近2ヶ月の装着", "value": status, "delta": f"HR記録{int(recent)}件", "warn": is_warn})
else:
    kpi_items.append({"label": "装着状況", "value": "—"})

ui.kpi_row(kpi_items)

# ---------------------------------------------------------------------------
# 装着ギャップ アラート
# ---------------------------------------------------------------------------
if len(hr):
    cov = metrics.device_coverage_timeline(hr)
    gaps = metrics.coverage_gaps(cov, zero_run_min_months=2)
    if gaps:
        rows = "".join(f"<li><b>{g['start']} 〜 {g['end']}</b>（{g['months']}ヶ月間、記録ほぼゼロ）</li>" for g in gaps)
        ui.card(
            "⚠ ウェアラブル装着ギャップを検知",
            f"<ul>{rows}</ul><p style='margin-top:8px;color:#777;'>"
            "2ヶ月以上連続でHR記録が0件の期間を自動検出。「回復指標が見えていない」期間の特定に使う。</p>",
            tone="red",
        )

st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# タブ構成
# ---------------------------------------------------------------------------
tab_names = [
    "RHR", "HRV", "歩数", "VO2Max", "装着ギャップ", "1日の流れ", "曜日×時間帯", "睡眠・ワークアウト",
    "RARIスコア(日次)", "疲労Window(RFW)", "Phase比較",
]
tabs = st.tabs(tab_names)

with tabs[0]:
    if len(rhr):
        m = metrics.monthly_mean(rhr)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=m.index.astype(str), y=m.values, mode="lines+markers", line=dict(color=TEAL), name="RHR"))
        fig.add_hline(y=65, line_dash="dash", line_color=RUST, annotation_text="要注意ライン 65 BPM")
        fig.add_hrect(y0=50, y1=57, fillcolor=MOSS, opacity=0.1, line_width=0, annotation_text="良好ゾーン")
        fig.update_layout(height=420, yaxis_title="BPM", margin=dict(t=20))
        st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)
    else:
        st.info("RHRデータなし")

with tabs[1]:
    if len(hrv):
        m = metrics.monthly_mean(hrv)
        fig = go.Figure()
        fig.add_trace(go.Bar(x=m.index.astype(str), y=m.values, marker_color=AMBER, name="HRV"))
        fig.add_hline(y=hrv["value"].mean(), line_dash="dash", line_color=NAVY, annotation_text=f"全期間平均 {hrv['value'].mean():.1f}ms")
        fig.update_layout(height=420, yaxis_title="SDNN (ms)", margin=dict(t=20))
        st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)
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
        st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)
        st.caption("直近90日（日次）")
        last90 = daily.tail(90)
        fig2 = go.Figure()
        fig2.add_trace(go.Bar(x=last90.index, y=last90.values, marker_color=TEAL))
        fig2.add_hline(y=8000, line_dash="dash", line_color=NAVY)
        fig2.update_layout(height=320, margin=dict(t=20))
        st.plotly_chart(ui.plotly_theme(fig2), use_container_width=True)
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
        st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)
    else:
        st.info("VO2Maxデータなし")

with tabs[4]:
    if len(hr):
        cov = metrics.device_coverage_timeline(hr)
        colors = [RUST if v == 0 else (AMBER if v < 500 else TEAL) for v in cov.values]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=cov.index.astype(str), y=cov.values, marker_color=colors))
        fig.update_layout(height=420, yaxis_title="HR記録数/月", margin=dict(t=20))
        st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)
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
        st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)
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
        st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)
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

with tabs[8]:
    st.caption(
        "RestAcademy Recovery Index (RARI) v1.0 の日次スコア（身体軸40+脳軸35+時間軸25）。"
        "元アルゴリズムはXiaomi Mi Fitness実測（連続SpO2・Stress・睡眠段階）を前提にしているため、"
        "Apple Healthでは取得できない指標（ストレス平均・リラックス%）は中間値で補完している。"
        "Xiaomi版のRARIスコアと数値を直接比較しないこと。"
    )
    if len(rari_table) == 0:
        st.info("RARIスコア算出に必要なデータ（睡眠・RHR・HRV・歩数のいずれか）が見つからなかった。")
    else:
        recent = rari_table.tail(90)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=recent["date"], y=recent["rari"], mode="lines+markers",
                                  line=dict(color=NAVY, width=2), name="RARI"))
        fig.add_hrect(y0=80, y1=100, fillcolor=MOSS, opacity=0.08, line_width=0, annotation_text="Aランク")
        fig.add_hrect(y0=65, y1=80, fillcolor=TEAL, opacity=0.06, line_width=0, annotation_text="Bランク")
        fig.update_layout(height=380, yaxis_title="RARI /100", margin=dict(t=20))
        st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)
        st.caption(f"直近90日。平均 {recent['rari'].mean():.1f}点 ／ 最高 {recent['rari'].max():.1f}点 ／ 最低 {recent['rari'].min():.1f}点")
        st.dataframe(
            rari_table.sort_values("date", ascending=False)
            [["date", "rari", "rank", "body", "mind", "time", "sleep_hours", "bedtime", "rhr", "hrv", "steps"]]
            .head(60),
            use_container_width=True,
        )

with tabs[9]:
    st.caption(
        "RestAcademy Fatigue Window（RFW）v0.1 — 小松様提案の運用モデルドキュメントに基づく新規アルゴリズム。"
        "「本人の直近7日ベースラインよりHRが高い」×「同時刻の歩数がほぼゼロ」が重なる時間帯を検出する。"
        "就寝〜早朝（既定0-6時）は運動・起床時の生理的なHR変動と混同しやすいため除外している。"
    )
    if fw_result.get("insufficient_data"):
        st.warning(f"疲労Windowの判定を保留：{fw_result.get('reason')}")
    else:
        st.write(f"心拍記録がある日数: {fw_result['days_covered']}日")
        top = fatigue_window.top_fatigue_windows(fw_result["hourly_summary"])
        if not top:
            st.info("明確な疲労Windowの再現パターンは検出されなかった（フラグ率30%以上の時間帯なし）。")
        else:
            rows = "".join(
                f"<li><b>{t['time']}頃</b>（{t['flag_rate']:.0f}%の日で検出・n={t['n']}）</li>" for t in top
            )
            ui.card(
                "⏰ 疲労Window候補（マイクロブレイク導入の目安時刻）",
                f"<ul>{rows}</ul><p style='margin-top:8px;color:#777;'>フラグ率＝この時間帯で"
                "「HR上昇×低活動」が同時に起きた日の割合。サンプル数(n)が少ない時間帯は参考値として扱うこと。</p>",
                tone="amber",
            )
        hs = fw_result["hourly_summary"]
        if len(hs):
            fig = go.Figure()
            fig.add_trace(go.Bar(x=hs["hour_bin"], y=hs["flag_rate"] * 100, marker_color=RUST))
            fig.update_layout(height=360, xaxis_title="時刻(JST)", yaxis_title="疲労フラグ率(%)", margin=dict(t=20))
            st.plotly_chart(ui.plotly_theme(fig), use_container_width=True)

with tabs[10]:
    st.caption("同じ参加者について、このセッション内でPhase1/2/3としてアップロードした結果を並べて比較する。")
    stored = st.session_state.get("phase_results", {})
    ordered = [k for k in ["phase1", "phase2", "phase3", "unspecified"] if k in stored]
    if len(ordered) < 2:
        st.info(
            "比較にはPhaseを2つ以上アップロードする必要がある。左のサイドバーでPhaseを切り替えて"
            "次の期間のファイルをアップロードすると、ここに並んで表示される。"
        )
    else:
        cols = st.columns(len(ordered))
        for col, k in zip(cols, ordered):
            v = stored[k]
            with col:
                st.markdown(f"**{PHASE_LABELS.get(k, k)}**")
                st.metric("平均RARI", f"{v['avg_rari']:.1f}")
                st.caption(f"{v['date_range'][0]} 〜 {v['date_range'][1]}（{v['n_days']}日）")
                fw = v["fw_result"]
                if not fw.get("insufficient_data"):
                    top = fatigue_window.top_fatigue_windows(fw["hourly_summary"], top_n=3)
                    if top:
                        st.caption("疲労Window: " + " / ".join(t["time"] for t in top))
                    else:
                        st.caption("疲労Window: 明確なパターンなし")
                else:
                    st.caption(f"疲労Window: 判定保留（{fw.get('reason', '')}）")
        st.caption("※ セッション（ブラウザタブ）を閉じるとこの蓄積は消える。恒久的な比較記録が必要なら次フェーズでDrive保存を実装する。")

st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
st.markdown(
    "<div class='ra-footer'>本ツールは自己申告ではなくApple Health実測データに基づく分析。"
    "医療的診断ではなく、プログラム設計の参考資料として使用すること。</div>",
    unsafe_allow_html=True,
)
