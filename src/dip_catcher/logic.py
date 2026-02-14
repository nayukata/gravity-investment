"""投資判断指標の計算・総合スコアリング。"""

from __future__ import annotations

from dataclasses import dataclass, fields

import numpy as np
import pandas as pd

from dip_catcher.models import AnalysisConfig, PriceHistory


# ---------------------------------------------------------------------------
# 個別指標
# ---------------------------------------------------------------------------


def calc_drawdown(closes: pd.Series) -> pd.Series:
    """ローリング最高値からのドローダウン（下落率）を計算する。

    戻り値は 0 以下の比率 (例: -0.10 = -10%)。
    """
    rolling_max = closes.cummax()
    return (closes - rolling_max) / rolling_max.replace(0, np.nan)


def calc_daily_returns(closes: pd.Series) -> pd.Series:
    """日次騰落率を計算する。"""
    return closes.pct_change().dropna()


def calc_return_percentile(returns: pd.Series, current_return: float) -> float:
    """現在の騰落率が過去分布の何パーセンタイルに位置するかを返す。

    戻り値は 0〜100。値が小さいほど「レアな下落」。
    <= を使用し、current_return 以下の値の割合を返す。
    """
    values = returns.dropna().values
    if len(values) == 0:
        return 50.0
    count_below = np.sum(values <= current_return)
    return float(count_below / len(values) * 100)


def calc_ma_deviation(closes: pd.Series, window: int) -> pd.Series:
    """移動平均乖離率を計算する。

    戻り値は比率 (例: -0.05 = 移動平均から-5%乖離)。
    """
    ma = closes.rolling(window=window, min_periods=window).mean()
    return (closes - ma) / ma


def calc_rsi(closes: pd.Series, period: int = 14) -> pd.Series:
    """RSI（相対力指数）を計算する。

    Wilder's smoothing method を使用。戻り値は 0〜100。
    """
    delta = closes.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


@dataclass
class BollingerBands:
    """ボリンジャーバンドの計算結果。"""

    middle: pd.Series  # 移動平均
    upper: pd.Series  # +Nσ
    lower: pd.Series  # -Nσ
    percent_b: pd.Series  # %B = (価格 - 下限) / (上限 - 下限)


def calc_bollinger_bands(
    closes: pd.Series, period: int = 20, num_std: float = 2.0
) -> BollingerBands:
    """ボリンジャーバンドを計算する。

    percent_b が 0 以下 = 下限バンド割れ（レアな下落）。
    """
    middle = closes.rolling(window=period, min_periods=period).mean()
    std = closes.rolling(window=period, min_periods=period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    band_width = upper - lower
    percent_b = (closes - lower) / band_width.replace(0, np.nan)
    return BollingerBands(middle=middle, upper=upper, lower=lower, percent_b=percent_b)


@dataclass
class DrawdownEvent:
    """過去のドローダウンイベント。"""

    peak_date: pd.Timestamp
    trough_date: pd.Timestamp
    max_drawdown: float  # 負の比率
    recovery_date: pd.Timestamp | None  # 高値回復日 (未回復ならNone)
    recovery_days: int | None  # 回復までの日数


def find_drawdown_events(
    dates: pd.Series, closes: pd.Series, threshold: float = -0.05
) -> list[DrawdownEvent]:
    """過去のドローダウンイベントを検出する。

    threshold 以下の下落が発生した区間を一覧化し、回復情報を付与する。
    回復 = ドローダウンがゼロに戻る（＝高値を更新する）こと。
    """
    dd = calc_drawdown(closes)
    dd_values = dd.values
    close_values = closes.values
    date_values = dates.values

    events: list[DrawdownEvent] = []
    in_drawdown = False
    peak_idx = 0
    trough_idx = 0
    trough_dd = 0.0

    for i in range(len(dd_values)):
        dd_val = dd_values[i]
        if np.isnan(dd_val):
            continue
        if dd_val < threshold:
            if not in_drawdown:
                in_drawdown = True
                # ピーク = 直前のcummax値に一致する最も近い位置を逆順探索
                peak_val = closes.cummax().iloc[i]
                peak_idx = i
                for j in range(i - 1, -1, -1):
                    if close_values[j] == peak_val:
                        peak_idx = j
                        break
                trough_idx = i
                trough_dd = dd_val
            elif dd_val < trough_dd:
                trough_idx = i
                trough_dd = dd_val
        elif in_drawdown and dd_val >= 0:
            events.append(
                DrawdownEvent(
                    peak_date=pd.Timestamp(date_values[peak_idx]),
                    trough_date=pd.Timestamp(date_values[trough_idx]),
                    max_drawdown=trough_dd,
                    recovery_date=pd.Timestamp(date_values[i]),
                    recovery_days=(pd.Timestamp(date_values[i]) - pd.Timestamp(date_values[trough_idx])).days,
                )
            )
            in_drawdown = False

    if in_drawdown:
        events.append(
            DrawdownEvent(
                peak_date=pd.Timestamp(date_values[peak_idx]),
                trough_date=pd.Timestamp(date_values[trough_idx]),
                max_drawdown=trough_dd,
                recovery_date=None,
                recovery_days=None,
            )
        )

    return events


# ---------------------------------------------------------------------------
# 総合スコアリング
# ---------------------------------------------------------------------------

# スコアリング境界値の定数
_DD_MAX = 0.30  # この深度で100点
_RARITY_UPPER = 50.0  # この値以上はスコア0
_RSI_UPPER = 70.0  # この値以上はスコア0
_RSI_LOWER = 20.0  # この値以下はスコア100
_MA_DEV_MAX = 0.10  # この乖離率で100点
_BB_UPPER = 1.0  # この%B以上はスコア0
_BB_LOWER = -0.5  # この%B以下はスコア100


@dataclass
class IndicatorScores:
    """各指標の個別スコア (0〜100)。"""

    drawdown: float
    rarity: float
    rsi: float
    ma_deviation: float
    bollinger: float


@dataclass
class AnalysisResult:
    """分析結果の全体像。"""

    scores: IndicatorScores
    total_score: float  # 0〜100
    label: str  # 判定ラベル
    current_drawdown: float  # 現在のドローダウン値
    current_daily_return: float  # 直近日次リターン（前日比）
    current_rsi: float
    current_ma_deviation: float
    current_bb_percent_b: float
    return_percentile: float  # 現在騰落率のパーセンタイル
    drawdown_events: list[DrawdownEvent]


def _score_drawdown(dd: float) -> float:
    """ドローダウン深度をスコア化する。

    0% → 0点, -15% → 50点, -30%以下 → 100点（線形補間）
    """
    depth = abs(dd)
    return float(np.clip(depth / _DD_MAX * 100, 0, 100))


def _score_rarity(percentile: float) -> float:
    """統計的レアリティをスコア化する。

    パーセンタイルが低いほど高スコア。
    50%以上 → 0点, 25% → 50点, 0% → 100点（線形補間）
    """
    if percentile >= _RARITY_UPPER:
        return 0.0
    return float(np.clip((_RARITY_UPPER - percentile) / _RARITY_UPPER * 100, 0, 100))


def _score_rsi(rsi: float) -> float:
    """RSIをスコア化する。

    70以上 → 0点, 45 → 50点, 20以下 → 100点（線形補間）
    """
    if rsi >= _RSI_UPPER:
        return 0.0
    return float(np.clip((_RSI_UPPER - rsi) / (_RSI_UPPER - _RSI_LOWER) * 100, 0, 100))


def _score_ma_deviation(deviation: float) -> float:
    """移動平均乖離率をスコア化する。

    +方向 → 0点, -5% → 50点, -10%以下 → 100点（線形補間）
    """
    if deviation >= 0:
        return 0.0
    depth = abs(deviation)
    return float(np.clip(depth / _MA_DEV_MAX * 100, 0, 100))


def _score_bollinger(percent_b: float) -> float:
    """ボリンジャーバンド%Bをスコア化する。

    1.0以上 → 0点, 0.25 → 50点, -0.5以下 → 100点（線形補間）
    """
    if percent_b >= _BB_UPPER:
        return 0.0
    return float(np.clip((_BB_UPPER - percent_b) / (_BB_UPPER - _BB_LOWER) * 100, 0, 100))


_WEIGHTS = {
    "drawdown": 0.30,
    "rarity": 0.25,
    "rsi": 0.20,
    "ma_deviation": 0.15,
    "bollinger": 0.10,
}

assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, f"Weights must sum to 1.0, got {sum(_WEIGHTS.values())}"


def _total_score(scores: IndicatorScores) -> float:
    """加重平均で総合スコアを計算する。"""
    score_fields = {f.name for f in fields(scores)}
    weight_fields = set(_WEIGHTS.keys())
    if score_fields != weight_fields:
        raise ValueError(f"Weight keys {weight_fields} do not match score fields {score_fields}")
    raw = sum(getattr(scores, name) * weight for name, weight in _WEIGHTS.items())
    return float(np.clip(raw, 0, 100))


def _label_from_score(score: float) -> str:
    """スコアから判定ラベルを返す。"""
    if score >= 80:
        return "強い買い場"
    if score >= 60:
        return "買い場検討"
    if score >= 40:
        return "様子見"
    return "待機"


def analyze(history: PriceHistory, config: AnalysisConfig) -> AnalysisResult:
    """全指標を計算し、総合スコアを返す。"""
    closes = history.df["close"].reset_index(drop=True)
    dates = history.df["date"].reset_index(drop=True)

    # 個別指標の計算
    dd_series = calc_drawdown(closes)
    current_dd = float(dd_series.iloc[-1]) if not np.isnan(dd_series.iloc[-1]) else 0.0

    daily_ret = calc_daily_returns(closes)
    current_ret = float(daily_ret.iloc[-1]) if len(daily_ret) > 0 else 0.0
    percentile = calc_return_percentile(daily_ret, current_ret) if len(daily_ret) > 0 else 50.0

    ma_dev_series = calc_ma_deviation(closes, config.ma_days)
    current_ma_dev = float(ma_dev_series.iloc[-1]) if not np.isnan(ma_dev_series.iloc[-1]) else 0.0

    rsi_series = calc_rsi(closes, config.rsi_period)
    current_rsi = float(rsi_series.iloc[-1]) if not np.isnan(rsi_series.iloc[-1]) else 50.0

    bb = calc_bollinger_bands(closes, config.bb_period, config.bb_std)
    current_bb = float(bb.percent_b.iloc[-1]) if not np.isnan(bb.percent_b.iloc[-1]) else 0.5

    dd_events = find_drawdown_events(dates, closes)

    # スコアリング
    scores = IndicatorScores(
        drawdown=_score_drawdown(current_dd),
        rarity=_score_rarity(percentile),
        rsi=_score_rsi(current_rsi),
        ma_deviation=_score_ma_deviation(current_ma_dev),
        bollinger=_score_bollinger(current_bb),
    )
    total = _total_score(scores)

    return AnalysisResult(
        scores=scores,
        total_score=total,
        label=_label_from_score(total),
        current_drawdown=current_dd,
        current_daily_return=current_ret,
        current_rsi=current_rsi,
        current_ma_deviation=current_ma_dev,
        current_bb_percent_b=current_bb,
        return_percentile=percentile,
        drawdown_events=dd_events,
    )
