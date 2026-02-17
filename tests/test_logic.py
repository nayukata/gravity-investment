from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dip_catcher.logic import (
    AnalysisResult,
    BollingerBands,
    DrawdownEvent,
    IndicatorScores,
    analyze,
    calc_bollinger_bands,
    calc_daily_returns,
    calc_drawdown,
    calc_ma_deviation,
    calc_return_percentile,
    calc_rsi,
    find_drawdown_events,
)
from dip_catcher.models import AnalysisConfig, PriceHistory


def _make_closes(values: list[float]) -> tuple[pd.Series, pd.Series]:
    """テスト用の closes と dates を生成する。"""
    dates = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.Series(values, dtype=float), pd.Series(dates)


def _make_history(values: list[float]) -> PriceHistory:
    """テスト用の PriceHistory を生成する。"""
    dates = pd.date_range("2024-01-01", periods=len(values), freq="B")
    df = pd.DataFrame({"date": dates, "close": values})
    return PriceHistory(df)


class TestCalcDrawdown:
    def test_no_drawdown_when_monotonic_increase(self) -> None:
        closes, _ = _make_closes([100, 110, 120, 130])
        dd = calc_drawdown(closes)
        assert (dd == 0).all()

    def test_drawdown_after_peak(self) -> None:
        closes, _ = _make_closes([100, 200, 180, 160])
        dd = calc_drawdown(closes)
        # 200 → 180 = -10%, 200 → 160 = -20%
        assert dd.iloc[2] == pytest.approx(-0.10)
        assert dd.iloc[3] == pytest.approx(-0.20)

    def test_drawdown_recovery(self) -> None:
        closes, _ = _make_closes([100, 200, 150, 200])
        dd = calc_drawdown(closes)
        assert dd.iloc[2] == pytest.approx(-0.25)
        assert dd.iloc[3] == pytest.approx(0.0)


class TestCalcDailyReturns:
    def test_basic_returns(self) -> None:
        closes, _ = _make_closes([100, 110, 99])
        ret = calc_daily_returns(closes)
        assert len(ret) == 2
        assert ret.iloc[0] == pytest.approx(0.10)
        assert ret.iloc[1] == pytest.approx(-0.10, abs=0.001)


class TestCalcReturnPercentile:
    def test_median_value(self) -> None:
        returns = pd.Series(range(100), dtype=float)
        pct = calc_return_percentile(returns, 50.0)
        # 51 values out of 100 are <= 50
        assert pct == pytest.approx(51.0)

    def test_extreme_low(self) -> None:
        returns = pd.Series(range(100), dtype=float)
        pct = calc_return_percentile(returns, 0.0)
        # 1 value (0) is <= 0
        assert pct == pytest.approx(1.0)

    def test_extreme_high(self) -> None:
        returns = pd.Series(range(100), dtype=float)
        pct = calc_return_percentile(returns, 99.0)
        assert pct == pytest.approx(100.0)

    def test_empty_returns(self) -> None:
        returns = pd.Series([], dtype=float)
        pct = calc_return_percentile(returns, 0.0)
        assert pct == 50.0


class TestCalcMADeviation:
    def test_no_deviation_at_flat_price(self) -> None:
        closes, _ = _make_closes([100.0] * 20)
        dev = calc_ma_deviation(closes, window=5)
        # 定数価格ではMA=価格なので乖離率=0
        assert dev.dropna().iloc[-1] == pytest.approx(0.0)

    def test_positive_deviation(self) -> None:
        values = [100.0] * 10 + [120.0]
        closes, _ = _make_closes(values)
        dev = calc_ma_deviation(closes, window=5)
        # MA(5) = (100*4 + 120) / 5 = 104, deviation = (120-104)/104 ≈ 0.154
        last_dev = dev.iloc[-1]
        assert last_dev > 0

    def test_negative_deviation(self) -> None:
        values = [100.0] * 10 + [80.0]
        closes, _ = _make_closes(values)
        dev = calc_ma_deviation(closes, window=5)
        last_dev = dev.iloc[-1]
        assert last_dev < 0


class TestCalcRSI:
    def test_rsi_at_100_for_all_gains(self) -> None:
        closes, _ = _make_closes(list(range(100, 130)))
        rsi = calc_rsi(closes, period=14)
        # 全て上昇なのでRSIは100に近い
        assert rsi.iloc[-1] > 95

    def test_rsi_near_0_for_all_losses(self) -> None:
        closes, _ = _make_closes(list(range(130, 100, -1)))
        rsi = calc_rsi(closes, period=14)
        # 全て下落なのでRSIは0に近い
        assert rsi.iloc[-1] < 5

    def test_rsi_range(self) -> None:
        np.random.seed(42)
        values = (100 + np.cumsum(np.random.randn(100))).tolist()
        closes, _ = _make_closes(values)
        rsi = calc_rsi(closes, period=14)
        valid = rsi.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()


class TestCalcBollingerBands:
    def test_flat_price_bands(self) -> None:
        closes, _ = _make_closes([100.0] * 30)
        bb = calc_bollinger_bands(closes, period=20, num_std=2.0)
        # 定数価格ではstd=0, upper=lower=middle=100
        assert bb.middle.iloc[-1] == pytest.approx(100.0)
        assert bb.upper.iloc[-1] == pytest.approx(100.0)
        assert bb.lower.iloc[-1] == pytest.approx(100.0)

    def test_percent_b_at_middle(self) -> None:
        np.random.seed(42)
        values = (100 + np.cumsum(np.random.randn(50) * 0.5)).tolist()
        closes, _ = _make_closes(values)
        bb = calc_bollinger_bands(closes, period=20, num_std=2.0)
        # %Bは概ね0〜1の範囲
        valid = bb.percent_b.dropna()
        assert len(valid) > 0


class TestFindDrawdownEvents:
    def test_single_drawdown_with_recovery(self) -> None:
        # 100 → 200 → 180 → 160 → 200
        closes, dates = _make_closes([100, 200, 180, 160, 200])
        events = find_drawdown_events(dates, closes, threshold=-0.05)
        assert len(events) == 1
        assert events[0].max_drawdown == pytest.approx(-0.20)
        assert events[0].recovery_date is not None

    def test_no_drawdown_in_uptrend(self) -> None:
        closes, dates = _make_closes([100, 110, 120, 130, 140])
        events = find_drawdown_events(dates, closes, threshold=-0.05)
        assert len(events) == 0

    def test_unrecovered_drawdown(self) -> None:
        closes, dates = _make_closes([100, 200, 150, 140])
        events = find_drawdown_events(dates, closes, threshold=-0.05)
        assert len(events) == 1
        assert events[0].recovery_date is None
        assert events[0].recovery_days is None


class TestScoring:
    def test_score_deep_drawdown(self) -> None:
        from dip_catcher.logic import _score_drawdown

        assert _score_drawdown(0.0) == 0.0
        assert _score_drawdown(-0.30) == pytest.approx(100.0)
        assert _score_drawdown(-0.15) == pytest.approx(50.0)

    def test_score_rarity(self) -> None:
        from dip_catcher.logic import _score_rarity

        # window=1: パニック渦中 (×0.85)
        assert _score_rarity(50.0, window=1) == 0.0
        assert _score_rarity(0.0, window=1) == pytest.approx(85.0)
        assert _score_rarity(25.0, window=1) == pytest.approx(42.5)
        # window=3: 安定化パターン (×1.15)
        assert _score_rarity(50.0, window=3) == 0.0
        assert _score_rarity(0.0, window=3) == pytest.approx(100.0)  # capped
        assert _score_rarity(25.0, window=3) == pytest.approx(57.5)

    def test_score_rsi(self) -> None:
        from dip_catcher.logic import _score_rsi

        assert _score_rsi(70.0) == 0.0
        assert _score_rsi(20.0) == pytest.approx(100.0)
        assert _score_rsi(45.0) == pytest.approx(50.0)

    def test_score_ma_deviation(self) -> None:
        from dip_catcher.logic import _score_ma_deviation

        assert _score_ma_deviation(0.0) == 0.0
        assert _score_ma_deviation(0.05) == 0.0  # プラス方向はスコアなし
        assert _score_ma_deviation(-0.10) == pytest.approx(100.0)
        assert _score_ma_deviation(-0.05) == pytest.approx(50.0)

    def test_score_bollinger(self) -> None:
        from dip_catcher.logic import _score_bollinger

        assert _score_bollinger(1.0) == 0.0
        assert _score_bollinger(-0.5) == pytest.approx(100.0)
        # (1.0 - 0.25) / (1.0 - (-0.5)) * 100 = 0.75 / 1.5 * 100 = 50
        assert _score_bollinger(0.25) == pytest.approx(50.0)

    def test_label_from_score(self) -> None:
        from dip_catcher.logic import _label_from_score

        assert _label_from_score(90) == "強い買い場"
        assert _label_from_score(70) == "買い場検討"
        assert _label_from_score(50) == "様子見"
        assert _label_from_score(30) == "待機"


class TestAnalyze:
    def test_analyze_returns_valid_result(self) -> None:
        np.random.seed(42)
        values = (100 + np.cumsum(np.random.randn(200))).tolist()
        history = _make_history(values)
        config = AnalysisConfig()

        result = analyze(history, config)

        assert isinstance(result, AnalysisResult)
        assert 0 <= result.total_score <= 100
        assert result.label in ("強い買い場", "買い場検討", "様子見", "待機")
        assert 0 <= result.current_rsi <= 100
        assert result.return_percentile >= 0

    def test_analyze_bearish_market(self) -> None:
        values = list(range(200, 100, -1))
        history = _make_history(values)
        config = AnalysisConfig()

        result = analyze(history, config)

        assert result.current_drawdown < 0
        assert result.scores.drawdown > 0
        assert result.scores.rsi > 50

    def test_analyze_bullish_market(self) -> None:
        values = list(range(100, 200))
        history = _make_history(values)
        config = AnalysisConfig()

        result = analyze(history, config)

        assert result.current_drawdown == pytest.approx(0.0)
        assert result.scores.drawdown == 0.0
        assert result.total_score < 30
