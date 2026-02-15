"""市場概況 - 主要指数・為替・コモディティのリアルタイム価格グリッド。"""

from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np
import plotly.graph_objects as go
import streamlit as st
import yfinance as yf

logger = logging.getLogger(__name__)


class MarketTicker(NamedTuple):
    symbol: str
    name: str
    category: str


MARKET_TICKERS = (
    # 日本
    MarketTicker("^N225", "日経平均", "japan"),
    MarketTicker("1306.T", "TOPIX ETF", "japan"),
    # 米国
    MarketTicker("^DJI", "ダウ", "us"),
    MarketTicker("^GSPC", "S&P 500", "us"),
    MarketTicker("^IXIC", "ナスダック", "us"),
    MarketTicker("^SOX", "半導体SOX", "us"),
    MarketTicker("^VIX", "恐怖指数", "us"),
    # 債券
    MarketTicker("^TNX", "米国10年債", "bond"),
    # 為替
    MarketTicker("JPY=X", "ドル円", "fx"),
    MarketTicker("EURJPY=X", "ユーロ円", "fx"),
    # コモディティ
    MarketTicker("GC=F", "ゴールド", "commodity"),
    MarketTicker("CL=F", "原油", "commodity"),
    MarketTicker("BTC-USD", "ビットコイン", "commodity"),
    # アジア
    MarketTicker("000001.SS", "上海", "asia"),
    MarketTicker("^KS11", "KOSPI", "asia"),
    MarketTicker("^HSI", "ハンセン", "asia"),
    MarketTicker("^TWII", "台湾", "asia"),
)

_CATEGORY_LABELS: dict[str, str] = {
    "japan": "🇯🇵 日本",
    "us": "🇺🇸 米国",
    "bond": "🏦 債券",
    "fx": "💱 為替",
    "commodity": "🛢️ コモディティ",
    "asia": "🌏 アジア",
}

_INVERSE_DELTA_SYMBOLS = frozenset({"^VIX"})

_COLS_PER_ROW = 4


class _TickerData(NamedTuple):
    price: float
    change: float
    change_pct: float
    sparkline: np.ndarray


@st.cache_data(ttl=300, show_spinner="市場データを取得中…")
def _fetch_market_data() -> dict[str, _TickerData]:
    """全銘柄を一括取得し、各銘柄の現在価格・前日比・スパークラインを返す。"""
    symbols = [t.symbol for t in MARKET_TICKERS]
    try:
        raw = yf.download(
            symbols, period="5d", interval="1h", group_by="ticker", progress=False,
        )
    except Exception:
        logger.exception("yf.download failed")
        return {}

    if raw.empty:
        return {}

    result: dict[str, _TickerData] = {}
    for ticker in MARKET_TICKERS:
        try:
            if len(symbols) == 1:
                ticker_df = raw
            else:
                ticker_df = raw[ticker.symbol]

            # yfinance バージョンによりカラム名の大文字・小文字が異なる
            ticker_df.columns = [c.lower() if isinstance(c, str) else c for c in ticker_df.columns]
            closes = ticker_df["close"].dropna()
            if len(closes) < 2:
                continue

            current = float(closes.iloc[-1])

            # 前日終値: 日付境界で直前の取引日の最終値を使用
            unique_dates = closes.index.normalize().unique()
            if len(unique_dates) < 2:
                prev_close = float(closes.iloc[0])
            else:
                prev_date = unique_dates[-2]
                prev_mask = closes.index.normalize() == prev_date
                prev_close = float(closes[prev_mask].iloc[-1])

            change = current - prev_close
            change_pct = (change / prev_close) * 100 if prev_close != 0 else 0.0

            result[ticker.symbol] = _TickerData(
                price=current,
                change=change,
                change_pct=change_pct,
                sparkline=closes.values.copy(),
            )
        except (KeyError, IndexError, TypeError):
            logger.warning("Skipping ticker %s: data extraction failed", ticker.symbol, exc_info=True)
            continue

    return result


def _sparkline_fig(values: np.ndarray, is_positive: bool) -> go.Figure:
    """スパークライン用の小さな折れ線チャートを生成する。"""
    color = "#16a34a" if is_positive else "#dc2626"
    fig = go.Figure(
        go.Scatter(
            y=values,
            mode="lines",
            line=dict(color=color, width=1.5),
            hoverinfo="skip",
        )
    )
    fig.update_layout(
        height=50,
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def _format_price(price: float, symbol: str) -> str:
    """銘柄に応じた価格フォーマット。"""
    if symbol in ("JPY=X", "EURJPY=X"):
        return f"¥{price:,.2f}"
    if symbol == "BTC-USD":
        return f"${price:,.0f}"
    if symbol == "^TNX":
        return f"{price:.3f}%"
    if symbol.endswith(".T") or symbol == "^N225":
        return f"¥{price:,.0f}"
    return f"{price:,.2f}"


def render_market_overview() -> None:
    """市場概況ページを描画する。"""
    st.markdown(
        "#### 🌐 市場概況 "
        "<small style='color:#888;font-weight:normal;'>主要指数・為替・コモディティ</small>",
        unsafe_allow_html=True,
    )

    data = _fetch_market_data()

    if not data:
        st.warning("市場データを取得できませんでした。しばらくしてから再度お試しください。")
        return

    # カテゴリ順にグルーピング
    categories: dict[str, list[MarketTicker]] = {}
    for ticker in MARKET_TICKERS:
        categories.setdefault(ticker.category, []).append(ticker)

    for cat_key, tickers in categories.items():
        label = _CATEGORY_LABELS.get(cat_key, cat_key)
        st.markdown(f"**{label}**")

        available = [t for t in tickers if t.symbol in data]
        if not available:
            continue

        for row_start in range(0, len(available), _COLS_PER_ROW):
            row_items = available[row_start:row_start + _COLS_PER_ROW]
            cols = st.columns(_COLS_PER_ROW)
            for i, ticker in enumerate(row_items):
                td = data[ticker.symbol]

                is_inverse = ticker.symbol in _INVERSE_DELTA_SYMBOLS
                delta_color = "inverse" if is_inverse else "normal"
                is_positive = td.change >= 0
                if is_inverse:
                    is_positive = not is_positive

                with cols[i]:
                    st.metric(
                        label=ticker.name,
                        value=_format_price(td.price, ticker.symbol),
                        delta=f"{td.change_pct:+.2f}%",
                        delta_color=delta_color,
                    )
                    st.plotly_chart(
                        _sparkline_fig(td.sparkline, is_positive),
                        use_container_width=True,
                        config={"displayModeBar": False},
                    )
