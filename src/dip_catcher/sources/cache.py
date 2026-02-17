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

# キャッシュの最小更新間隔
MIN_REFRESH_INTERVAL = timedelta(hours=3)

# 基準価額の公表時刻（この時刻以降にデータが更新される想定）
_PUBLISH_HOUR = 10


# ---------------------------------------------------------------------------
# 日本の祝日・営業日判定
# ---------------------------------------------------------------------------

# 固定祝日 (月, 日)
_FIXED_HOLIDAYS = {
    (1, 1),    # 元日
    (2, 11),   # 建国記念の日
    (2, 23),   # 天皇誕生日
    (4, 29),   # 昭和の日
    (5, 3),    # 憲法記念日
    (5, 4),    # みどりの日
    (5, 5),    # こどもの日
    (8, 11),   # 山の日
    (11, 3),   # 文化の日
    (11, 23),  # 勤労感謝の日
}


def _is_jp_holiday(d: date) -> bool:
    """日本の祝日かどうかを判定する（主要な固定祝日＋ハッピーマンデー）。"""
    month, day = d.month, d.day

    if (month, day) in _FIXED_HOLIDAYS:
        return True

    # ハッピーマンデー制度（第n月曜日が祝日）
    if d.weekday() == 0:  # 月曜日
        week_num = (day - 1) // 7 + 1
        if month == 1 and week_num == 2:    # 成人の日（1月第2月曜）
            return True
        if month == 7 and week_num == 3:    # 海の日（7月第3月曜）
            return True
        if month == 9 and week_num == 3:    # 敬老の日（9月第3月曜）
            return True
        if month == 10 and week_num == 2:   # スポーツの日（10月第2月曜）
            return True

    # 春分の日・秋分の日（概算）
    if month == 3 and day in (20, 21):
        return True
    if month == 9 and day in (22, 23):
        return True

    # 振替休日：祝日が日曜の場合、翌月曜が休み
    yesterday = d - timedelta(days=1)
    if d.weekday() == 0 and _is_jp_holiday_no_substitute(yesterday):
        return True

    return False


def _is_jp_holiday_no_substitute(d: date) -> bool:
    """振替休日を除いた祝日判定（再帰防止用）。"""
    month, day = d.month, d.day
    if (month, day) in _FIXED_HOLIDAYS:
        return True
    if d.weekday() == 0:
        week_num = (day - 1) // 7 + 1
        if month == 1 and week_num == 2:
            return True
        if month == 7 and week_num == 3:
            return True
        if month == 9 and week_num == 3:
            return True
        if month == 10 and week_num == 2:
            return True
    if month == 3 and day in (20, 21):
        return True
    if month == 9 and day in (22, 23):
        return True
    return False


def is_jp_business_day(d: date) -> bool:
    """日本の営業日かどうかを判定する。"""
    if d.weekday() >= 5:  # 土日
        return False
    return not _is_jp_holiday(d)


# ---------------------------------------------------------------------------
# FetchResult / CachedSource
# ---------------------------------------------------------------------------


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
        """キャッシュが古く、更新が必要かどうかを判定する。

        判定ロジック:
        1. キャッシュなし → 更新必要
        2. 非営業日（土日祝） → 更新不要
        3. 10:00 前 → 基準価額未公表のため更新不要
        4. 本日 10:00 以降に更新済み → 更新不要（厳密な「更新した」判定）
        5. 前回更新から 3 時間未満 → 更新不要
        6. 上記いずれでもない → 更新必要
        """
        cache_path = self._cache_path(code)
        if not cache_path.exists():
            return True

        now = datetime.now()
        today = now.date()

        # 非営業日はデータ更新なし
        if not is_jp_business_day(today):
            return False

        # 10:00 前は基準価額未公表
        if now.hour < _PUBLISH_HOUR:
            return False

        mtime = datetime.fromtimestamp(cache_path.stat().st_mtime)

        # 本日 10:00 以降に更新（fetch or touch）済みなら再取得不要
        if mtime.date() == today and mtime.hour >= _PUBLISH_HOUR:
            return False

        # 最低 3 時間の間隔を空ける
        if (now - mtime) < MIN_REFRESH_INTERVAL:
            return False

        return True

    def fetch(self, code: str, start: date, end: date) -> FetchResult:
        cache_path = self._cache_path(code)
        cached_df = self._load_cache(cache_path)

        if cached_df is not None:
            fetch_start = self._next_fetch_start(cached_df, start)
            if fetch_start > end:
                logger.info("Cache is up-to-date for %s", code)
                # mtime を更新して「確認済み」を記録する
                cache_path.touch()
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
        cache_start = cached_df["date"].min().date()
        if cache_start > original_start:
            return original_start
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
