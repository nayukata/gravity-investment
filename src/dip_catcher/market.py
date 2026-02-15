"""市場概況 - 主要指数・為替・コモディティのリアルタイム価格グリッド。"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import NamedTuple

import streamlit as st
import yfinance as yf

from dip_catcher.models import AppConfig, WatchlistItem
from dip_catcher.sources import get_source

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
    "watchlist": "📋 監視リスト",
    "japan": "🇯🇵 日本",
    "us": "🇺🇸 米国",
    "bond": "🏦 債券",
    "fx": "💱 為替",
    "commodity": "🛢️ コモディティ",
    "asia": "🌏 アジア",
}

_INVERSE_DELTA_SYMBOLS = frozenset({"^VIX"})

_COLS_PER_ROW = 5

_CARD_CSS = """\
<style>
.mkt-card {
    border: 1px solid #e0e0e0;
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 4px;
}
.mkt-name {
    font-size: 0.8rem;
    color: #888;
    margin: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.mkt-pct {
    font-size: 1.4rem;
    font-weight: bold;
    margin: 2px 0;
}
.mkt-price {
    font-size: 0.75rem;
    color: #888;
    margin: 0;
}
.mkt-up { color: #16a34a; }
.mkt-down { color: #dc2626; }
.mkt-flat { color: #6b7280; }
</style>
"""


class _TickerData(NamedTuple):
    name: str
    price: float
    change_pct: float
    is_inverse: bool


@st.cache_data(ttl=300, show_spinner="市場データを取得中…")
def _fetch_market_data() -> dict[str, _TickerData]:
    """全銘柄を一括取得し、各銘柄の現在価格・前日比を返す。"""
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

            ticker_df.columns = [c.lower() if isinstance(c, str) else c for c in ticker_df.columns]
            closes = ticker_df["close"].dropna()
            if len(closes) < 2:
                continue

            current = float(closes.iloc[-1])

            unique_dates = closes.index.normalize().unique()
            if len(unique_dates) < 2:
                prev_close = float(closes.iloc[0])
            else:
                prev_date = unique_dates[-2]
                prev_mask = closes.index.normalize() == prev_date
                prev_close = float(closes[prev_mask].iloc[-1])

            change_pct = ((current - prev_close) / prev_close) * 100 if prev_close != 0 else 0.0

            result[ticker.symbol] = _TickerData(
                name=ticker.name,
                price=current,
                change_pct=change_pct,
                is_inverse=ticker.symbol in _INVERSE_DELTA_SYMBOLS,
            )
        except (KeyError, IndexError, TypeError):
            logger.warning("Skipping ticker %s: data extraction failed", ticker.symbol, exc_info=True)
            continue

    return result


def _load_watchlist_data(watchlist: list[WatchlistItem]) -> list[_TickerData]:
    """登録銘柄のキャッシュからデータを取得し、前日比を算出する。"""
    end = date.today()
    start = end - timedelta(days=30)

    items: list[_TickerData] = []
    for item in watchlist:
        try:
            source = get_source(item.category)
            cached = source.load_cache(item.code, start, end)
            if cached is None or len(cached.df) < 2:
                continue
            df = cached.df.sort_values("date")
            current = float(df["close"].iloc[-1])
            prev = float(df["close"].iloc[-2])
            change_pct = ((current - prev) / prev) * 100 if prev != 0 else 0.0
            items.append(_TickerData(
                name=item.name,
                price=current,
                change_pct=change_pct,
                is_inverse=False,
            ))
        except Exception:
            logger.warning("Watchlist item %s: data load failed", item.code, exc_info=True)
            continue
    return items


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


def _card_html(name: str, price_str: str, change_pct: float, is_inverse: bool) -> str:
    """コンパクトなカードのHTMLを生成する。"""
    if change_pct > 0:
        css_class = "mkt-down" if is_inverse else "mkt-up"
    elif change_pct < 0:
        css_class = "mkt-up" if is_inverse else "mkt-down"
    else:
        css_class = "mkt-flat"

    return (
        f"<div class='mkt-card'>"
        f"<p class='mkt-name'>{name}</p>"
        f"<p class='mkt-pct {css_class}'>{change_pct:+.2f}%</p>"
        f"<p class='mkt-price'>{price_str}</p>"
        f"</div>"
    )


def _render_card_grid(
    items: list[tuple[str, str, float, bool]],
) -> None:
    """(name, price_str, change_pct, is_inverse) のリストをグリッド描画する。"""
    for row_start in range(0, len(items), _COLS_PER_ROW):
        row = items[row_start:row_start + _COLS_PER_ROW]
        cols = st.columns(_COLS_PER_ROW)
        for i, (name, price_str, pct, inv) in enumerate(row):
            with cols[i]:
                st.markdown(_card_html(name, price_str, pct, inv), unsafe_allow_html=True)


def render_market_overview(config: AppConfig) -> None:
    """市場概況ページを描画する。"""
    st.markdown(
        "#### 🌐 市場概況 "
        "<small style='color:#888;font-weight:normal;'>主要指数・為替・コモディティ</small>",
        unsafe_allow_html=True,
    )
    st.markdown(_CARD_CSS, unsafe_allow_html=True)

    # --- 監視リスト ---
    if config.watchlist:
        wl_data = _load_watchlist_data(config.watchlist)
        if wl_data:
            st.markdown(f"**{_CATEGORY_LABELS['watchlist']}**")
            _render_card_grid([
                (td.name, f"{td.price:,.0f}", td.change_pct, td.is_inverse)
                for td in wl_data
            ])

    # --- 市場データ ---
    data = _fetch_market_data()

    if not data:
        st.warning("市場データを取得できませんでした。しばらくしてから再度お試しください。")
        return

    categories: dict[str, list[MarketTicker]] = {}
    for ticker in MARKET_TICKERS:
        categories.setdefault(ticker.category, []).append(ticker)

    for cat_key, tickers in categories.items():
        available = [t for t in tickers if t.symbol in data]
        if not available:
            continue

        label = _CATEGORY_LABELS.get(cat_key, cat_key)
        st.markdown(f"**{label}**")

        _render_card_grid([
            (data[t.symbol].name, _format_price(data[t.symbol].price, t.symbol),
             data[t.symbol].change_pct, data[t.symbol].is_inverse)
            for t in available
        ])
