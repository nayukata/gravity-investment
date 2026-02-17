from __future__ import annotations

from functools import lru_cache

from dip_catcher.models import AssetCategory
from dip_catcher.sources.cache import CachedSource
from dip_catcher.sources.yahoo_jp import YahooJPSource
from dip_catcher.sources.yfinance_source import YFinanceSource

_CATEGORY_SOURCE_MAP = {
    AssetCategory.US_STOCK: YFinanceSource,
    AssetCategory.INDEX: YFinanceSource,
    AssetCategory.JP_STOCK: YFinanceSource,
    AssetCategory.JP_FUND: YahooJPSource,
}


@lru_cache(maxsize=None)
def get_source(category: AssetCategory) -> CachedSource:
    """カテゴリに応じたキャッシュ付きデータソースを返す。"""
    source_cls = _CATEGORY_SOURCE_MAP[category]
    return CachedSource(source_cls())
