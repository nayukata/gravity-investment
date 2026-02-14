"""Dip Catcher - 投資タイミング判断ダッシュボード。"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from pydantic import ValidationError

from dip_catcher.config import load_config, save_config
from dip_catcher.logic import (
    AnalysisResult,
    analyze,
    calc_bollinger_bands,
    calc_daily_returns,
    calc_drawdown,
    calc_ma_deviation,
    calc_rsi,
)
from dip_catcher.models import (
    AnalysisConfig,
    AppConfig,
    AssetCategory,
    PriceHistory,
    WatchlistItem,
)
from dip_catcher.sources import get_source

logger = logging.getLogger(__name__)

_CATEGORY_LABELS = {
    AssetCategory.US_STOCK: "米国株・ETF",
    AssetCategory.JP_STOCK: "日本株・ETF",
    AssetCategory.JP_FUND: "日本の投資信託",
    AssetCategory.INDEX: "主要指数",
}

_LABEL_COLORS = {
    "強い買い場": "#dc2626",
    "買い場検討": "#ea580c",
    "様子見": "#ca8a04",
    "待機": "#6b7280",
}


def main() -> None:
    st.set_page_config(page_title="Dip Catcher", page_icon="📉", layout="wide")
    st.title("📉 Dip Catcher")
    st.caption("統計的確率に基づく押し目買いシグナル")

    config = load_config()

    config = _render_sidebar(config)

    if not config.watchlist:
        st.info("サイドバーから銘柄を登録してください。")
        return

    selected = _select_watchlist_item(config)
    if selected is None:
        return

    history = _fetch_data(selected, config.analysis)
    if history is None:
        return

    result = analyze(history, config.analysis)
    closes = history.df["close"].reset_index(drop=True)
    dates = history.df["date"].reset_index(drop=True)

    _render_summary(selected, history, result)
    _render_main_chart(dates, closes, config.analysis)
    _render_analysis_panel(dates, closes, result, config.analysis)


# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------


def _render_sidebar(config: AppConfig) -> AppConfig:
    """サイドバーを描画し、更新された設定を返す。"""
    with st.sidebar:
        st.header("監視リスト")
        _render_add_form(config)
        _render_watchlist(config)

        st.divider()
        st.header("分析設定")
        config = _render_analysis_settings(config)

    return config


def _render_add_form(config: AppConfig) -> None:
    with st.form("add_item", clear_on_submit=True):
        col1, col2 = st.columns(2)
        with col1:
            code = st.text_input("銘柄コード", placeholder="AAPL, ^nkx, 03311187")
        with col2:
            name = st.text_input("表示名", placeholder="Apple")
        category = st.selectbox(
            "カテゴリ",
            options=list(AssetCategory),
            format_func=lambda c: _CATEGORY_LABELS[c],
        )
        submitted = st.form_submit_button("追加", use_container_width=True)

    if submitted and code and name and category:
        try:
            item = WatchlistItem(code=code.strip(), name=name.strip(), category=category)
        except ValidationError:
            st.sidebar.error("銘柄コードまたは表示名が不正です（英数字・記号のみ、30文字以内）。")
            return
        existing_codes = {w.code for w in config.watchlist}
        if item.code in existing_codes:
            st.sidebar.warning(f"{item.code} は既に登録されています。")
        else:
            config.watchlist.append(item)
            save_config(config)
            st.rerun()


def _render_watchlist(config: AppConfig) -> None:
    if not config.watchlist:
        st.caption("銘柄が登録されていません。")
        return

    for i, item in enumerate(config.watchlist):
        col1, col2 = st.columns([4, 1])
        with col1:
            st.text(f"{item.name} ({item.code})")
        with col2:
            if st.button("✕", key=f"del_{i}"):
                config.watchlist.pop(i)
                save_config(config)
                st.rerun()


def _render_analysis_settings(config: AppConfig) -> AppConfig:
    a = config.analysis

    period = st.selectbox(
        "分析期間",
        options=[1, 3, 5, 10],
        index=[1, 3, 5, 10].index(a.period_years) if a.period_years in [1, 3, 5, 10] else 1,
        format_func=lambda y: f"{y}年",
    )
    ma_days = st.slider("移動平均 (日)", 5, 200, a.ma_days)

    new_analysis = AnalysisConfig(
        period_years=period,
        ma_days=ma_days,
        rsi_period=a.rsi_period,
        bb_period=a.bb_period,
        bb_std=a.bb_std,
    )
    if new_analysis != config.analysis:
        config.analysis = new_analysis
        save_config(config)

    return config


# ---------------------------------------------------------------------------
# 銘柄選択・データ取得
# ---------------------------------------------------------------------------


def _select_watchlist_item(config: AppConfig) -> WatchlistItem | None:
    items = config.watchlist
    labels = [f"{item.name} ({item.code})" for item in items]
    idx = st.selectbox("銘柄を選択", range(len(items)), format_func=lambda i: labels[i])
    return items[idx] if idx is not None else None


@st.cache_data(ttl=300, show_spinner="データ取得中...")
def _fetch_data_cached(
    code: str, category: str, period_years: int,
) -> tuple[pd.DataFrame, bool] | None:
    """データを取得し (DataFrame, is_fallback) を返す。完全失敗時は None。"""
    cat = AssetCategory(category)
    source = get_source(cat)
    end = date.today()
    start = end - timedelta(days=365 * period_years)
    try:
        result = source.fetch(code, start, end)
        return result.df, result.is_fallback
    except (ValueError, ConnectionError, OSError, TimeoutError) as e:
        logger.warning("Failed to fetch %s: %s", code, e)
        return None


def _fetch_data(item: WatchlistItem, analysis: AnalysisConfig) -> PriceHistory | None:
    cached = _fetch_data_cached(item.code, item.category.value, analysis.period_years)
    if cached is None:
        st.error(f"{item.name} のデータを取得できませんでした。")
        return None
    df, is_fallback = cached
    if df.empty:
        st.error(f"{item.name} のデータを取得できませんでした。")
        return None
    if is_fallback:
        last_date = df["date"].max()
        st.warning(
            f"データソースに接続できなかったため、キャッシュデータを表示しています"
            f"（最終更新: {last_date:%Y-%m-%d}）"
        )
    return PriceHistory(df)


# ---------------------------------------------------------------------------
# サマリーパネル
# ---------------------------------------------------------------------------


def _currency_symbol(category: AssetCategory) -> str:
    if category == AssetCategory.US_STOCK:
        return "$"
    if category == AssetCategory.INDEX:
        return ""
    return "¥"


def _render_summary(item: WatchlistItem, history: PriceHistory, result: AnalysisResult) -> None:
    label_color = _LABEL_COLORS.get(result.label, "#6b7280")
    sym = _currency_symbol(item.category)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("基準価額", f"{sym}{history.latest_close:,.0f}")
    with col2:
        dd_pct = result.current_drawdown * 100
        st.metric("高値からの下落率", f"{dd_pct:+.1f}%")
    with col3:
        st.metric("総合スコア", f"{result.total_score:.0f} / 100")
    with col4:
        st.markdown(
            f"<div style='text-align:center;padding:0.5rem;'>"
            f"<span style='font-size:0.8rem;color:#888;'>判定</span><br>"
            f"<span style='font-size:1.5rem;font-weight:bold;color:{label_color};'>"
            f"{result.label}</span></div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# メインチャート
# ---------------------------------------------------------------------------


def _render_main_chart(dates: pd.Series, closes: pd.Series, config: AnalysisConfig) -> None:
    st.subheader("価格チャート")

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.7, 0.3],
        subplot_titles=("", "ドローダウン"),
    )

    # 価格ライン
    fig.add_trace(
        go.Scatter(x=dates, y=closes, name="価格", line=dict(color="#2563eb", width=1.5)),
        row=1, col=1,
    )

    # 移動平均
    ma = closes.rolling(window=config.ma_days, min_periods=config.ma_days).mean()
    fig.add_trace(
        go.Scatter(x=dates, y=ma, name=f"MA({config.ma_days})", line=dict(color="#f59e0b", width=1, dash="dash")),
        row=1, col=1,
    )

    # ボリンジャーバンド
    bb = calc_bollinger_bands(closes, config.bb_period, config.bb_std)
    fig.add_trace(
        go.Scatter(x=dates, y=bb.upper, name=f"BB+{config.bb_std}σ", line=dict(color="#94a3b8", width=0.5), showlegend=False),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(x=dates, y=bb.lower, name=f"BB-{config.bb_std}σ", line=dict(color="#94a3b8", width=0.5),
                   fill="tonexty", fillcolor="rgba(148,163,184,0.1)", showlegend=False),
        row=1, col=1,
    )

    # ドローダウン（面グラフ）
    dd = calc_drawdown(closes) * 100
    fig.add_trace(
        go.Scatter(x=dates, y=dd, name="ドローダウン", fill="tozeroy",
                   line=dict(color="#dc2626", width=1), fillcolor="rgba(220,38,38,0.2)"),
        row=2, col=1,
    )

    fig.update_layout(
        height=500, margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="価格", row=1, col=1)
    fig.update_yaxes(title_text="%", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# 分析パネル
# ---------------------------------------------------------------------------


def _render_analysis_panel(
    dates: pd.Series, closes: pd.Series, result: AnalysisResult, config: AnalysisConfig,
) -> None:
    st.subheader("分析パネル")

    tab_scores, tab_histogram, tab_rsi, tab_events = st.tabs(
        ["スコア内訳", "騰落率分布", "RSI", "過去ドローダウン"]
    )

    with tab_scores:
        _render_score_breakdown(result)

    with tab_histogram:
        _render_return_histogram(closes, result)

    with tab_rsi:
        _render_rsi_chart(dates, closes, config)

    with tab_events:
        _render_dd_events(result)


def _render_score_breakdown(result: AnalysisResult) -> None:
    scores = result.scores
    items = [
        ("ドローダウン", scores.drawdown, 30, f"{result.current_drawdown*100:+.1f}%"),
        ("統計的レアリティ", scores.rarity, 25, f"パーセンタイル {result.return_percentile:.1f}%"),
        ("RSI", scores.rsi, 20, f"{result.current_rsi:.1f}"),
        ("移動平均乖離率", scores.ma_deviation, 15, f"{result.current_ma_deviation*100:+.1f}%"),
        ("ボリンジャーバンド", scores.bollinger, 10, f"%B = {result.current_bb_percent_b:.2f}"),
    ]

    for name, score, weight, value in items:
        col1, col2, col3 = st.columns([3, 5, 2])
        with col1:
            st.text(f"{name} (×{weight}%)")
        with col2:
            st.progress(min(score / 100, 1.0))
        with col3:
            st.text(f"{score:.0f}点  {value}")

    st.divider()
    label_color = _LABEL_COLORS.get(result.label, "#6b7280")
    st.markdown(
        f"**総合スコア: {result.total_score:.0f} / 100** → "
        f"<span style='color:{label_color};font-weight:bold;'>{result.label}</span>",
        unsafe_allow_html=True,
    )


def _render_return_histogram(closes: pd.Series, result: AnalysisResult) -> None:
    returns = calc_daily_returns(closes) * 100

    fig = go.Figure()
    fig.add_trace(
        go.Histogram(x=returns, nbinsx=50, name="騰落率", marker_color="#2563eb", opacity=0.7)
    )

    current_ret = returns.iloc[-1] if len(returns) > 0 else 0
    fig.add_vline(
        x=current_ret, line_dash="dash", line_color="#dc2626", line_width=2,
        annotation_text=f"直近 {current_ret:.2f}%",
        annotation_position="top right",
    )

    fig.update_layout(
        xaxis_title="日次騰落率 (%)", yaxis_title="頻度",
        height=350, margin=dict(l=0, r=0, t=30, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(f"現在の下落は過去分布の **{result.return_percentile:.1f}パーセンタイル** に位置しています。")


def _render_rsi_chart(dates: pd.Series, closes: pd.Series, config: AnalysisConfig) -> None:
    rsi = calc_rsi(closes, config.rsi_period)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=dates, y=rsi, name=f"RSI({config.rsi_period})", line=dict(color="#7c3aed", width=1.5))
    )
    fig.add_hline(y=70, line_dash="dot", line_color="#dc2626", annotation_text="買われすぎ (70)")
    fig.add_hline(y=30, line_dash="dot", line_color="#16a34a", annotation_text="売られすぎ (30)")
    fig.add_hrect(y0=30, y1=70, fillcolor="rgba(0,0,0,0.03)", line_width=0)

    fig.update_layout(
        yaxis=dict(range=[0, 100], title="RSI"),
        height=300, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_dd_events(result: AnalysisResult) -> None:
    events = result.drawdown_events
    if not events:
        st.info("分析期間中に有意なドローダウンイベントはありません。")
        return

    rows = []
    for e in events:
        rows.append({
            "ピーク日": e.peak_date.strftime("%Y-%m-%d"),
            "底値日": e.trough_date.strftime("%Y-%m-%d"),
            "最大下落率": f"{e.max_drawdown*100:.1f}%",
            "回復日": e.recovery_date.strftime("%Y-%m-%d") if e.recovery_date else "未回復",
            "回復日数": f"{e.recovery_days}日" if e.recovery_days is not None else "-",
        })

    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
