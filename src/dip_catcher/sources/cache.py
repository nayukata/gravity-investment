from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from dip_catcher.sources.base import DataSource

logger = logging.getLogger(__name__)

_DATA_DIR = Path.home() / ".dip_catcher" / "data"

# キャッシュの最小更新間隔（これより新しいキャッシュは再取得しない）
MIN_REFRESH_INTERVAL = timedelta(hours=6)


@dataclass
class FetchResult:
    """fetch() の結果。フォールバック状態を伝搬する。"""

    df: pd.DataFrame = field(repr=False)
    is_fallback: bool = False
    last_modified: datetime | None = None

    @property
    def last_date(self) -> date | None:
        if self.df.empty:
            return None
        return self.df["date"].max().date()


class CachedSource:
    """ローカルCSVキャッシュ付きデータソース。

    キャッシュが存在する場合は差分のみを取得し、結合する。
    データソース障害時はキャッシュデータにフォールバックする。
    """

    def __init__(self, source: DataSource, data_dir: Path | None = None) -> None:
        self._source = source
        self._data_dir = data_dir or _DATA_DIR
        self._data_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, code: str) -> Path:
        safe_code = re.sub(r"[^\w\-.]", "_", code)
        if not safe_code or safe_code in (".", ".."):
            safe_code = "_invalid_"
        return self._data_dir / f"{safe_code}.csv"

    def load_cache(self, code: str, start: date, end: date) -> FetchResult | None:
        """ディスクキャッシュからデータを読み込む（ネットワーク不要）。"""
        cache_path = self._cache_path(code)
        cached_df = self._load_cache(cache_path)
        if cached_df is None:
            return None
        last_modified = datetime.fromtimestamp(cache_path.stat().st_mtime)
        filtered = self._filter(cached_df, start, end)
        if filtered.empty:
            return None
        return FetchResult(df=filtered, last_modified=last_modified)

    def needs_refresh(self, code: str) -> bool:
        """キャッシュが古く、更新が必要かどうかを判定する。"""
        cache_path = self._cache_path(code)
        if not cache_path.exists():
            return True
        age = datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
        return age > MIN_REFRESH_INTERVAL

    def fetch(self, code: str, start: date, end: date) -> FetchResult:
        cache_path = self._cache_path(code)
        cached_df = self._load_cache(cache_path)

        if cached_df is not None:
            fetch_start = self._next_fetch_start(cached_df, start)
            if fetch_start > end:
                logger.info("Cache is up-to-date for %s", code)
                last_modified = datetime.fromtimestamp(cache_path.stat().st_mtime)
                return FetchResult(
                    self._filter(cached_df, start, end),
                    last_modified=last_modified,
                )
        else:
            fetch_start = start

        try:
            new_df = self._source.fetch(code, fetch_start, end)
        except (ValueError, ConnectionError, OSError, TimeoutError):
            if cached_df is not None:
                logger.warning(
                    "Fetch failed for %s, using cached data (last: %s)",
                    code,
                    cached_df["date"].max(),
                )
                last_modified = datetime.fromtimestamp(cache_path.stat().st_mtime)
                return FetchResult(
                    self._filter(cached_df, start, end),
                    is_fallback=True,
                    last_modified=last_modified,
                )
            raise

        merged = self._merge(cached_df, new_df)
        self._save_cache(cache_path, merged)
        last_modified = datetime.fromtimestamp(cache_path.stat().st_mtime)
        return FetchResult(
            self._filter(merged, start, end), last_modified=last_modified,
        )

    def _load_cache(self, path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        df = pd.read_csv(path, parse_dates=["date"])
        if df.empty:
            return None
        return df

    def _save_cache(self, path: Path, df: pd.DataFrame) -> None:
        df.to_csv(path, index=False)

    def _next_fetch_start(self, cached_df: pd.DataFrame, original_start: date) -> date:
        latest = cached_df["date"].max()
        next_day = (latest + pd.Timedelta(days=1)).date()
        return max(next_day, original_start)

    def _merge(
        self, cached: pd.DataFrame | None, new: pd.DataFrame
    ) -> pd.DataFrame:
        if cached is None:
            return new.sort_values("date").reset_index(drop=True)
        combined = pd.concat([cached, new], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date"]).sort_values("date")
        return combined.reset_index(drop=True)

    def _filter(self, df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
        mask = (df["date"].dt.date >= start) & (df["date"].dt.date <= end)
        return df.loc[mask].reset_index(drop=True)
