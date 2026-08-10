"""レストアカデミーのビジュアルアイデンティティ（LP・PDFレポートと共通の配色）を
Streamlitダッシュボードに適用するための小さなUIキット。

配色の出典: [[project_restacademy_lp]] のブランドパレット
（深い藍 #0c1f3b・温かい琥珀 #b88950・生成り #f7f5ef・朱 #a44b3a・苔 #4b6855）。
2026-08-10のフォローアップPDFレポートで組んだカード/アラートのビジュアル言語をそのまま移植する。
"""
from __future__ import annotations

import html
import re

import streamlit as st


def _flat(s: str) -> str:
    """複数行/インデント付きHTML文字列を1行に潰す。

    Streamlitのst.markdownはCommonMark準拠のため、HTML断片を結合したときに
    空行や4スペース以上のインデントを挟むと「インデントコードブロック」と誤認され、
    HTMLがエスケープされずそのままテキスト表示されてしまう罠がある
    （kpi_row等、複数カードをループで結合するケースで発生した実バグ）。
    st.markdown(unsafe_allow_html=True)に渡す直前に必ず通すこと。
    """
    return re.sub(r"\s*\n\s*", "", s).strip()

NAVY = "#0c1f3b"
NAVY_2 = "#14304f"
AMBER = "#b88950"
PAPER = "#f7f5ef"
RUST = "#a44b3a"
MOSS = "#4b6855"
TEAL = "#2f6f8f"
INK = "#1a1f2b"

TONE_STYLES = {
    "blue": {"bg": "#eaf1f5", "border": TEAL, "title": TEAL},
    "red": {"bg": "#fbeceb", "border": RUST, "title": RUST},
    "green": {"bg": "#eef2ec", "border": MOSS, "title": MOSS},
    "amber": {"bg": "#fbf3e6", "border": AMBER, "title": "#8a6b2f"},
    "navy": {"bg": "#eef1f5", "border": NAVY, "title": NAVY},
}


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Noto+Sans+JP:wght@400;500;600;700&family=Cormorant+Garamond:ital@0;1&display=swap');

        html, body, [class*="css"] {{
            font-family: "Noto Sans JP", "Hiragino Sans", sans-serif;
        }}

        .stApp {{
            background: #ffffff;
        }}
        section[data-testid="stSidebar"] {{
            background: {PAPER};
            border-right: 1px solid #e7e2d6;
        }}
        section[data-testid="stSidebar"] h2 {{
            color: {NAVY};
            font-weight: 700;
        }}
        [data-testid="stMainBlockContainer"] {{
            padding-top: 1.6rem;
            max-width: 1200px;
        }}

        /* タブの下線をブランドカラーに */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 4px;
            border-bottom: 2px solid #e7e6e0;
        }}
        .stTabs [data-baseweb="tab"] {{
            font-weight: 600;
            color: #8a8f9a;
        }}
        .stTabs [aria-selected="true"] {{
            color: {NAVY} !important;
        }}
        .stTabs [data-baseweb="tab-highlight"] {{
            background-color: {AMBER} !important;
            height: 3px;
        }}

        /* プロット・データフレームを軽くカード化 */
        [data-testid="stPlotlyChart"], [data-testid="stDataFrame"] {{
            border: 1px solid #ecebe4;
            border-radius: 10px;
            padding: 6px;
            background: #ffffff;
        }}

        [data-testid="stVerticalBlockBorderWrapper"] {{
            border-radius: 10px !important;
        }}

        hr {{
            border-color: #ecebe4 !important;
        }}

        .ra-hero {{
            background: linear-gradient(135deg, {NAVY} 0%, {NAVY_2} 100%);
            color: {PAPER};
            border-radius: 14px;
            padding: 30px 34px 26px;
            margin-bottom: 22px;
        }}
        .ra-hero .tag {{
            font-size: 11px; letter-spacing: .14em; color: #cfa96b;
            font-weight: 700; margin-bottom: 10px; text-transform: uppercase;
        }}
        .ra-hero h1 {{
            font-size: 26px; margin: 0 0 8px; font-weight: 700; color: #ffffff;
            letter-spacing: .01em; line-height: 1.3;
        }}
        .ra-hero .sub {{
            font-size: 13px; color: #cdd6e3; margin-bottom: 14px; line-height: 1.6;
        }}
        .ra-hero .badge {{
            display: inline-block; background: rgba(255,255,255,.10);
            border: 1px solid rgba(255,255,255,.28); border-radius: 20px;
            padding: 5px 14px; font-size: 11.5px; color: #f0e6d2;
        }}

        .ra-kpi-row {{
            display: flex; gap: 12px; margin-bottom: 20px; flex-wrap: wrap;
        }}
        .ra-kpi {{
            flex: 1; min-width: 150px; background: {PAPER};
            border: 1px solid #e7e2d6; border-left: 4px solid {AMBER};
            border-radius: 10px; padding: 14px 16px;
        }}
        .ra-kpi.warn {{ border-left-color: {RUST}; background: #fbeceb; }}
        .ra-kpi .ra-kpi-label {{
            font-size: 11px; color: #8a7a5f; font-weight: 700;
            letter-spacing: .02em; margin-bottom: 6px;
        }}
        .ra-kpi.warn .ra-kpi-label {{ color: {RUST}; }}
        .ra-kpi .ra-kpi-value {{
            font-size: 24px; font-weight: 700; color: {NAVY}; line-height: 1.2;
        }}
        .ra-kpi .ra-kpi-value .unit {{ font-size: 12px; color: #666; font-weight: 500; margin-left: 4px; }}
        .ra-kpi .ra-kpi-delta {{
            font-size: 10.5px; color: {MOSS}; margin-top: 5px; font-weight: 600;
        }}

        .ra-card {{
            border-radius: 10px; padding: 16px 20px; margin: 10px 0;
        }}
        .ra-card .ra-card-title {{
            font-weight: 700; font-size: 13.5px; margin-bottom: 8px;
        }}
        .ra-card p, .ra-card li {{ font-size: 12.5px; line-height: 1.65; color: #3a3a34; margin: 3px 0; }}
        .ra-card ul {{ margin: 4px 0; padding-left: 18px; }}

        .ra-person {{
            display: flex; align-items: baseline; gap: 10px; margin-bottom: 2px;
        }}
        .ra-person .avatar {{ font-size: 22px; }}
        .ra-person h3 {{ margin: 0; color: {NAVY}; font-size: 19px; }}
        .ra-person .meta {{ color: #777; font-size: 12.5px; margin-top: 2px; }}

        .ra-footer {{
            text-align: center; color: #a8a8a0; font-size: 11px; margin-top: 20px;
            font-family: "Cormorant Garamond", serif; font-style: italic; letter-spacing: .02em;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def hero(title: str, subtitle: str, tag: str, badge: str) -> None:
    st.markdown(
        _flat(
            f"""
            <div class="ra-hero">
                <div class="tag">{html.escape(tag)}</div>
                <h1>{title}</h1>
                <div class="sub">{subtitle}</div>
                <span class="badge">{html.escape(badge)}</span>
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def kpi_row(items: list[dict]) -> None:
    """items: [{"label": str, "value": str, "unit": str, "delta": str, "warn": bool}, ...]"""
    cards = []
    for it in items:
        warn_cls = " warn" if it.get("warn") else ""
        unit_html = f'<span class="unit">{html.escape(it.get("unit", ""))}</span>' if it.get("unit") else ""
        delta_html = f'<div class="ra-kpi-delta">{html.escape(it.get("delta", ""))}</div>' if it.get("delta") else ""
        cards.append(
            _flat(
                f"""
                <div class="ra-kpi{warn_cls}">
                    <div class="ra-kpi-label">{html.escape(it['label'])}</div>
                    <div class="ra-kpi-value">{it['value']}{unit_html}</div>
                    {delta_html}
                </div>
                """
            )
        )
    st.markdown(f'<div class="ra-kpi-row">{"".join(cards)}</div>', unsafe_allow_html=True)


def card(title: str, body_html: str, tone: str = "blue") -> None:
    s = TONE_STYLES.get(tone, TONE_STYLES["blue"])
    st.markdown(
        _flat(
            f"""
            <div class="ra-card" style="background:{s['bg']}; border-left:4px solid {s['border']};">
                <div class="ra-card-title" style="color:{s['title']};">{html.escape(title)}</div>
                {body_html}
            </div>
            """
        ),
        unsafe_allow_html=True,
    )


def person_header(name: str, meta: str) -> None:
    st.markdown(
        _flat(
            f"""
            <div class="ra-person">
                <span class="avatar">👤</span><h3>{html.escape(name)}</h3>
            </div>
            <div class="meta">{html.escape(meta)}</div>
            """
        ),
        unsafe_allow_html=True,
    )


def plotly_theme(fig):
    """Plotlyの見た目をPDFレポート/LPと揃える共通レイアウト。"""
    fig.update_layout(
        font=dict(family="Noto Sans JP, Hiragino Sans, sans-serif", color=INK, size=12),
        plot_bgcolor="#ffffff",
        paper_bgcolor="#ffffff",
        margin=dict(t=24, l=10, r=10, b=10),
        hoverlabel=dict(font_family="Noto Sans JP, Hiragino Sans, sans-serif"),
    )
    fig.update_xaxes(gridcolor="#eeece4", zeroline=False)
    fig.update_yaxes(gridcolor="#eeece4", zeroline=False)
    return fig
