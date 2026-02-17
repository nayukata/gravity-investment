"""Dip Catcher - 投資タイミング判断ダッシュボード。"""

from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from pydantic import ValidationError

from dip_catcher.config import PRESET_ITEMS, load_config, save_config
from dip_catcher.market import render_market_overview
from dip_catcher.logic import (
    AnalysisResult,
    analyze,
    calc_bollinger_bands,
    calc_cumulative_returns,
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


_playwright_checked = False


def _ensure_playwright_browser() -> None:
    """Playwright で使える Chromium を確保する。

    1. システム Chromium（apt 等でインストール済み）があればそれを使う
    2. なければ Playwright バンドル版を試す
    3. それもなければ playwright install を実行する
    """
    global _playwright_checked
    if _playwright_checked:
        return

    # システム Chromium があれば OK（Streamlit Cloud では packages.txt 経由）
    for name in ("chromium", "chromium-browser", "google-chrome"):
        if shutil.which(name):
            logger.info("System Chromium found: %s", name)
            _playwright_checked = True
            return

    # Playwright バンドル版を試す
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
        _playwright_checked = True
        return
    except Exception:
        pass

    # バンドル版がなければインストールを試みる
    logger.info("Installing Playwright Chromium browser...")
    try:
        subprocess.run(
            ["playwright", "install", "chromium"],
            check=True,
            capture_output=True,
            timeout=300,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
        logger.error("Failed to install Playwright Chromium: %s", e)
    _playwright_checked = True

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
    _ensure_playwright_browser()
    st.set_page_config(page_title="Dip Catcher", page_icon="📉", layout="wide")
    st.markdown(
        "<style>"
        "header[data-testid='stHeader'] {display: none;}"
        ".block-container {padding-top: 1rem;}"
        "</style>",
        unsafe_allow_html=True,
    )
    view = st.sidebar.radio(
        "表示", ["分析", "市場概況"], horizontal=True, label_visibility="collapsed",
        key="radio_view",
    )

    config = load_config()

    if view == "市場概況":
        render_market_overview(config)
        return

    st.markdown("#### 📉 Dip Catcher <small style='color:#888;font-weight:normal;'>統計的確率に基づく押し目買いシグナル</small>", unsafe_allow_html=True)

    config, selected = _render_sidebar(config)

    if not config.watchlist:
        st.info("サイドバーの「銘柄を追加」→「プリセット」から人気の銘柄を追加できます。")
        return

    if selected is None:
        return

    history, last_modified, is_fallback = _load_and_display(selected, config.analysis)
    if history is None:
        return

    result = analyze(history, config.analysis)
    closes = history.df["close"].reset_index(drop=True)
    dates = history.df["date"].reset_index(drop=True)

    _render_update_status(last_modified, is_fallback)
    _render_summary(selected, history, result)
    _render_main_chart(dates, closes, config.analysis)
    _render_analysis_panel(dates, closes, result, config.analysis, config.watchlist)


# ---------------------------------------------------------------------------
# サイドバー
# ---------------------------------------------------------------------------


def _init_selection(count: int) -> None:
    """session_state の選択インデックスを初期化・正規化する。"""
    if count == 0:
        st.session_state.pop("radio_watchlist", None)
        st.session_state.pop("_pending_idx", None)
        return
    if "_pending_idx" in st.session_state:
        st.session_state["radio_watchlist"] = st.session_state.pop("_pending_idx")
    idx = st.session_state.get("radio_watchlist", 0)
    if not isinstance(idx, int) or idx < 0 or idx >= count:
        st.session_state["radio_watchlist"] = 0


def _render_sidebar(config: AppConfig) -> tuple[AppConfig, WatchlistItem | None]:
    """サイドバーを描画し、更新された設定と選択中の銘柄を返す。"""
    with st.sidebar:
        st.header("監視リスト")
        selected = _render_watchlist(config)

        expand_add = not config.watchlist or st.session_state.get("_expand_add", False)
        with st.expander("銘柄を追加", expanded=expand_add):
            tab_preset, tab_custom = st.tabs(["プリセット", "カスタム"])
            with tab_preset:
                _render_preset_picker(config)
            with tab_custom:
                _render_add_form(config)

        st.divider()
        st.header("分析設定")
        config = _render_analysis_settings(config)

    return config, selected


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
            st.error("銘柄コードまたは表示名が不正です（英数字・記号のみ、30文字以内）。")
            return
        existing_codes = {w.code for w in config.watchlist}
        if item.code in existing_codes:
            st.warning(f"{item.code} は既に登録されています。")
        else:
            config.watchlist.append(item)
            save_config(config)
            st.session_state["_pending_idx"] = len(config.watchlist) - 1
            st.session_state["_expand_add"] = True
            st.rerun()


def _render_preset_picker(config: AppConfig) -> None:
    existing_codes = {w.code for w in config.watchlist}
    for category, items in PRESET_ITEMS.items():
        st.caption(_CATEGORY_LABELS[category])
        for item in items:
            col_name, col_btn = st.columns([4, 1])
            with col_name:
                st.markdown(f"<small>{item.name}</small>", unsafe_allow_html=True)
            with col_btn:
                if item.code in existing_codes:
                    st.button("✓", key=f"preset_{item.code}", disabled=True)
                elif st.button("＋", key=f"preset_{item.code}"):
                    config.watchlist.append(item)
                    save_config(config)
                    st.session_state["_pending_idx"] = len(config.watchlist) - 1
                    st.session_state["_expand_add"] = True
                    st.rerun()


def _render_watchlist(config: AppConfig) -> WatchlistItem | None:
    if not config.watchlist:
        st.caption("銘柄が登録されていません。")
        return None

    items = config.watchlist
    _init_selection(len(items))
    selected_idx = st.session_state.get("radio_watchlist", 0)

    for i, item in enumerate(items):
        col_btn, col_del = st.columns([5, 1])
        with col_btn:
            btn_type = "primary" if i == selected_idx else "secondary"
            if st.button(
                f"{item.name} ({item.code})",
                key=f"wl_select_{i}",
                type=btn_type,
                use_container_width=True,
            ):
                st.session_state["_pending_idx"] = i
                st.session_state.pop("_expand_add", None)
                st.rerun()
        with col_del:
            if st.button("🗑", key=f"wl_del_{i}"):
                config.watchlist.pop(i)
                save_config(config)
                new_count = len(config.watchlist)
                if new_count > 0:
                    st.session_state["_pending_idx"] = min(selected_idx, new_count - 1)
                else:
                    st.session_state.pop("radio_watchlist", None)
                st.rerun()

    return items[selected_idx]


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


def _load_and_display(
    item: WatchlistItem, analysis: AnalysisConfig,
) -> tuple[PriceHistory | None, datetime | None, bool]:
    """キャッシュ優先でデータを取得する。

    1. ディスクキャッシュがあれば即座に返す（ネットワーク不要）
    2. キャッシュが古ければバックグラウンドで更新する
    3. キャッシュがなければ同期的に取得する

    Returns:
        (PriceHistory | None, last_modified | None, is_fallback)
    """
    source = get_source(item.category)
    end = date.today()
    start = end - timedelta(days=365 * analysis.period_years)

    # Step 1: ディスクキャッシュを即座に読み込む
    cached = source.load_cache(item.code, start, end)

    if cached is not None:
        # Step 2: 更新が必要かチェック → 必要ならバックグラウンドで更新
        if source.needs_refresh(item.code):
            _background_refresh(item.code, item.category.value, start, end)
        return PriceHistory(cached.df), cached.last_modified, cached.is_fallback

    # Step 3: キャッシュなし → 初回は同期取得（避けられない）
    with st.spinner("初回データ取得中…"):
        try:
            result = source.fetch(item.code, start, end)
            return PriceHistory(result.df), result.last_modified, result.is_fallback
        except (ValueError, ConnectionError, OSError, TimeoutError) as e:
            logger.warning("Failed to fetch %s: %s", item.code, e)
            st.error(f"{item.name} のデータを取得できませんでした: {e}")
            return None, None, False


@st.cache_data(ttl=10800, show_spinner=False)
def _background_refresh(
    code: str, category: str, start: date, end: date,
) -> bool:
    """キャッシュをバックグラウンドで更新する。

    st.cache_data (TTL=3時間) で同一引数の再実行を抑止する。
    """
    cat = AssetCategory(category)
    source = get_source(cat)
    try:
        source.fetch(code, start, end)
        return True
    except (ValueError, ConnectionError, OSError, TimeoutError) as e:
        logger.warning("Background refresh failed for %s: %s", code, e)
        return False


def _render_update_status(last_modified: datetime | None, is_fallback: bool) -> None:
    """最終更新日時と更新ステータスを表示する。"""
    if last_modified is None:
        return
    ts = last_modified.strftime("%Y-%m-%d %H:%M")
    if is_fallback:
        st.warning(f"データソースに接続できませんでした。キャッシュを表示中です（最終更新: {ts}）")
    else:
        st.markdown(
            f"<div style='text-align:right;color:#888;font-size:0.8rem;'>最終更新: {ts}</div>",
            unsafe_allow_html=True,
        )


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

    closes = history.df["close"]
    recent_window = min(252, len(closes))
    recent_high = float(closes.iloc[-recent_window:].max())
    recent_dd = (history.latest_close - recent_high) / recent_high if recent_high > 0 else 0.0

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    with col1:
        st.metric("基準価額", f"{sym}{history.latest_close:,.0f}")
    with col2:
        daily_ret_pct = result.current_daily_return * 100
        st.metric("前日比", f"{daily_ret_pct:+.2f}%")
    with col3:
        recent_dd_pct = recent_dd * 100
        st.metric("52週高値からの下落率", f"{recent_dd_pct:+.1f}%")
    with col4:
        dd_pct = result.current_drawdown * 100
        st.metric("最高値からの下落率", f"{dd_pct:+.1f}%")
    with col5:
        st.metric("総合スコア", f"{result.total_score:.0f} / 100")
    with col6:
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
        subplot_titles=("", "ドローダウン (%)"),
    )

    # 価格ライン
    fig.add_trace(
        go.Scatter(
            x=dates, y=closes, name="価格",
            line=dict(color="#2563eb", width=1.5),
            hovertemplate="%{x|%Y年%m月%d日}<br>価格: %{y:,.0f}<extra></extra>",
        ),
        row=1, col=1,
    )

    # 移動平均
    ma = closes.rolling(window=config.ma_days, min_periods=config.ma_days).mean()
    fig.add_trace(
        go.Scatter(
            x=dates, y=ma, name=f"移動平均 ({config.ma_days}日)",
            line=dict(color="#f59e0b", width=1, dash="dash"),
            hovertemplate="%{y:,.0f}<extra></extra>",
        ),
        row=1, col=1,
    )

    # ボリンジャーバンド
    bb = calc_bollinger_bands(closes, config.bb_period, config.bb_std)
    fig.add_trace(
        go.Scatter(
            x=dates, y=bb.upper, name=f"ボリンジャー上限",
            line=dict(color="#94a3b8", width=0.5), showlegend=False,
            hoverinfo="skip",
        ),
        row=1, col=1,
    )
    fig.add_trace(
        go.Scatter(
            x=dates, y=bb.lower, name=f"ボリンジャー下限",
            line=dict(color="#94a3b8", width=0.5),
            fill="tonexty", fillcolor="rgba(148,163,184,0.1)", showlegend=False,
            hoverinfo="skip",
        ),
        row=1, col=1,
    )

    # ドローダウン（面グラフ）
    dd = calc_drawdown(closes) * 100
    fig.add_trace(
        go.Scatter(
            x=dates, y=dd, name="ドローダウン", fill="tozeroy",
            line=dict(color="#dc2626", width=1), fillcolor="rgba(220,38,38,0.2)",
            hovertemplate="%{x|%Y年%m月%d日}<br>下落率: %{y:.1f}%<extra></extra>",
        ),
        row=2, col=1,
    )

    _dtick = dict(tickformat="%Y/%m", dtick="M3")
    fig.update_layout(
        height=500, margin=dict(l=0, r=0, t=30, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="x",
    )
    fig.update_xaxes(**_dtick, row=1, col=1)
    fig.update_xaxes(**_dtick, row=2, col=1)
    fig.update_yaxes(title_text="価格", row=1, col=1)
    fig.update_yaxes(title_text="下落率 (%)", row=2, col=1)

    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# 分析パネル
# ---------------------------------------------------------------------------


def _render_analysis_panel(
    dates: pd.Series,
    closes: pd.Series,
    result: AnalysisResult,
    config: AnalysisConfig,
    watchlist: list[WatchlistItem],
) -> None:
    st.subheader("分析パネル")

    tab_scores, tab_histogram, tab_rsi, tab_events, tab_monthly = st.tabs(
        ["スコア内訳", "騰落率分布", "RSI", "過去ドローダウン", "月次騰落率"]
    )

    with tab_scores:
        _render_score_breakdown(result)

    with tab_histogram:
        _render_return_histogram(closes, result)

    with tab_rsi:
        _render_rsi_chart(dates, closes, config)

    with tab_events:
        _render_dd_events(result)

    with tab_monthly:
        _render_monthly_table(watchlist, config)


def _render_score_breakdown(result: AnalysisResult) -> None:
    scores = result.scores
    items = [
        ("下落の深さ", scores.drawdown, 30, f"高値比 {result.current_drawdown*100:+.1f}%"),
        ("統計的な珍しさ", scores.rarity, 25, f"{result.rarity_window}日間 下位 {result.return_percentile:.1f}%"),
        ("売られすぎ度", scores.rsi, 20, f"RSI {result.current_rsi:.1f}"),
        ("移動平均との乖離", scores.ma_deviation, 15, f"乖離 {result.current_ma_deviation*100:+.1f}%"),
        ("バンドからの逸脱", scores.bollinger, 10, f"位置 {result.current_bb_percent_b:.2f}"),
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
    w = result.rarity_window
    if w > 1:
        returns = calc_cumulative_returns(closes, w) * 100
    else:
        returns = calc_daily_returns(closes) * 100
    window_label = f"{w}日間" if w > 1 else "日次"

    fig = go.Figure()
    fig.add_trace(
        go.Histogram(
            x=returns, nbinsx=50, name="騰落率",
            marker_color="#2563eb", opacity=0.7,
            hovertemplate="騰落率: %{x:.2f}%<br>回数: %{y}<extra></extra>",
        )
    )

    current_ret = result.rarity_return * 100
    fig.add_vline(
        x=current_ret, line_dash="dash", line_color="#dc2626", line_width=2,
        annotation_text=f"直近{window_label}騰落率 {current_ret:.2f}%",
        annotation_position="top right",
    )

    fig.update_layout(
        xaxis_title=f"{window_label}騰落率 (%)", yaxis_title="発生回数",
        height=350, margin=dict(l=0, r=0, t=30, b=0),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        f"直近{window_label}の騰落率は過去の分布の中で **下位 {result.return_percentile:.1f}%** の位置にあります。"
        f"1日/3日/5日の中で最もレアなウィンドウを自動選択しています。"
    )


def _render_rsi_chart(dates: pd.Series, closes: pd.Series, config: AnalysisConfig) -> None:
    rsi = calc_rsi(closes, config.rsi_period)

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates, y=rsi, name=f"RSI（{config.rsi_period}日）",
            line=dict(color="#7c3aed", width=1.5),
            hovertemplate="%{x|%Y年%m月%d日}<br>RSI: %{y:.1f}<extra></extra>",
        )
    )
    fig.add_hline(y=70, line_dash="dot", line_color="#dc2626", annotation_text="買われすぎ（70）")
    fig.add_hline(y=30, line_dash="dot", line_color="#16a34a", annotation_text="売られすぎ（30）")
    fig.add_hrect(y0=30, y1=70, fillcolor="rgba(0,0,0,0.03)", line_width=0)

    fig.update_layout(
        yaxis=dict(range=[0, 100], title="相対力指数（RSI）"),
        height=300, margin=dict(l=0, r=0, t=10, b=0),
        showlegend=False,
    )
    fig.update_xaxes(tickformat="%Y/%m", dtick="M3")
    st.plotly_chart(fig, use_container_width=True)
    st.caption("RSI（相対力指数）は、直近の値動きが上昇・下落どちらに傾いているかを示します。30以下は「売られすぎ」で反発の可能性を示唆します。")


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


def _render_monthly_table(watchlist: list[WatchlistItem], config: AnalysisConfig) -> None:
    end = date.today()
    start = end - timedelta(days=365 * config.period_years)

    series: dict[str, pd.Series] = {}
    for item in watchlist:
        source = get_source(item.category)
        cached = source.load_cache(item.code, start, end)
        if cached is None:
            continue
        df = cached.df.copy()
        df["month"] = df["date"].dt.to_period("M")
        monthly_close = df.groupby("month")["close"].last()
        series[item.name] = monthly_close.pct_change()

    if not series:
        st.info("キャッシュ済みのデータがありません。各銘柄を一度表示してください。")
        return

    combined = pd.DataFrame(series)
    combined.index = combined.index.astype(str)
    combined = combined.iloc[::-1]

    def _color_cell(v: float) -> str:
        if pd.isna(v):
            return ""
        if v > 0:
            return "color: #16a34a"
        if v < 0:
            return "color: #dc2626"
        return ""

    styled = combined.style.map(_color_cell).format(lambda v: f"{v:+.2%}" if pd.notna(v) else "-")
    st.dataframe(styled, use_container_width=True)


if __name__ == "__main__":
    main()
