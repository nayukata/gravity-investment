from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from dip_catcher.models import AssetCategory, PriceHistory
from dip_catcher.sources import get_source
from dip_catcher.sources.cache import CachedSource, FetchResult


def _make_df(start: str, periods: int) -> pd.DataFrame:
    """テスト用の価格DataFrameを生成する。"""
    dates = pd.date_range(start, periods=periods, freq="B")
    closes = [100.0 + i for i in range(periods)]
    return pd.DataFrame({"date": dates, "close": closes})


class FakeSource:
    """テスト用のダミーデータソース。"""

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df
        self.call_count = 0

    def fetch(self, code: str, start: date, end: date) -> pd.DataFrame:
        self.call_count += 1
        mask = (self._df["date"].dt.date >= start) & (self._df["date"].dt.date <= end)
        return self._df.loc[mask].reset_index(drop=True)


class FailingSource:
    """常に例外を投げるデータソース。"""

    def fetch(self, code: str, start: date, end: date) -> pd.DataFrame:
        raise ConnectionError("Network error")


class TestCachedSource:
    def test_first_fetch_creates_cache(self, tmp_path: Path) -> None:
        df = _make_df("2024-01-01", 10)
        fake = FakeSource(df)
        source = CachedSource(fake, data_dir=tmp_path)

        result = source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 31))

        assert isinstance(result, FetchResult)
        assert not result.is_fallback
        assert len(result.df) == 10
        assert (tmp_path / "TEST.csv").exists()
        assert fake.call_count == 1

    def test_second_fetch_uses_cache_delta(self, tmp_path: Path) -> None:
        df = _make_df("2024-01-01", 30)
        fake = FakeSource(df)
        source = CachedSource(fake, data_dir=tmp_path)

        source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 14))
        assert fake.call_count == 1

        result = source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 31))
        assert fake.call_count == 2
        assert len(result.df) > 10

    def test_fallback_on_failure(self, tmp_path: Path) -> None:
        df = _make_df("2024-01-01", 5)
        fake = FakeSource(df)
        source = CachedSource(fake, data_dir=tmp_path)

        source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 31))

        failing_source = CachedSource(FailingSource(), data_dir=tmp_path)
        result = failing_source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 31))

        assert len(result.df) == 5
        assert result.is_fallback

    def test_no_cache_no_fallback(self, tmp_path: Path) -> None:
        source = CachedSource(FailingSource(), data_dir=tmp_path)

        with pytest.raises(ConnectionError):
            source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 31))

    def test_cache_path_sanitizes_special_chars(self, tmp_path: Path) -> None:
        df = _make_df("2024-01-01", 3)
        fake = FakeSource(df)
        source = CachedSource(fake, data_dir=tmp_path)

        source.fetch("^NKX", date(2024, 1, 1), date(2024, 1, 31))
        assert (tmp_path / "_NKX.csv").exists()

    def test_cache_path_prevents_traversal(self, tmp_path: Path) -> None:
        df = _make_df("2024-01-01", 3)
        fake = FakeSource(df)
        source = CachedSource(fake, data_dir=tmp_path)

        source.fetch("../../etc/passwd", date(2024, 1, 1), date(2024, 1, 31))
        cached_files = list(tmp_path.glob("*.csv"))
        assert len(cached_files) == 1
        assert cached_files[0].parent == tmp_path

    def test_merge_deduplicates(self, tmp_path: Path) -> None:
        df = _make_df("2024-01-01", 10)
        fake = FakeSource(df)
        source = CachedSource(fake, data_dir=tmp_path)

        source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 10))
        result = source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 10))

        assert len(result.df) == len(result.df.drop_duplicates(subset=["date"]))

    def test_last_date_property(self, tmp_path: Path) -> None:
        df = _make_df("2024-01-01", 5)
        fake = FakeSource(df)
        source = CachedSource(fake, data_dir=tmp_path)

        result = source.fetch("TEST", date(2024, 1, 1), date(2024, 1, 31))
        assert result.last_date is not None


class TestFetchResult:
    def test_last_date_returns_none_for_empty_df(self) -> None:
        empty_df = pd.DataFrame(
            {"date": pd.Series(dtype="datetime64[ns]"), "close": pd.Series(dtype="float64")}
        )
        result = FetchResult(empty_df)
        assert result.last_date is None
        assert not result.is_fallback

    def test_is_fallback_flag(self) -> None:
        df = _make_df("2024-01-01", 3)
        result = FetchResult(df, is_fallback=True)
        assert result.is_fallback
        assert result.last_date is not None


class TestPriceHistory:
    def test_valid_df(self) -> None:
        df = _make_df("2024-01-01", 5)
        ph = PriceHistory(df)
        assert len(ph) == 5
        assert ph.latest_close == 104.0

    def test_missing_columns_raises(self) -> None:
        df = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
        with pytest.raises(ValueError, match="Missing columns"):
            PriceHistory(df)

    def test_empty_df_raises(self) -> None:
        df = pd.DataFrame({"date": pd.Series(dtype="datetime64[ns]"), "close": pd.Series(dtype="float64")})
        with pytest.raises(ValueError, match="must not be empty"):
            PriceHistory(df)


class TestSourceFactory:
    def test_get_source_returns_cached(self) -> None:
        for category in AssetCategory:
            source = get_source(category)
            assert isinstance(source, CachedSource)
