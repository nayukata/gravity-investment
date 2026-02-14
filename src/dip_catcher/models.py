from __future__ import annotations

from enum import Enum

import pandas as pd
from pydantic import BaseModel, Field


class AssetCategory(str, Enum):
    """銘柄カテゴリ。データソースの自動選択に使用する。"""

    US_STOCK = "us_stock"
    JP_STOCK = "jp_stock"
    JP_FUND = "jp_fund"
    INDEX = "index"


class WatchlistItem(BaseModel):
    """監視リストの1銘柄。"""

    code: str = Field(
        description="銘柄コード (例: AAPL, 7203, 03314228)",
        min_length=1,
        max_length=30,
        pattern=r"^[A-Za-z0-9_\-\.\^]+$",
    )
    name: str = Field(description="表示名", min_length=1, max_length=100)
    category: AssetCategory


class AnalysisConfig(BaseModel):
    """分析パラメータ。"""

    period_years: int = Field(default=3, ge=1, le=30, description="分析対象期間(年)")
    ma_days: int = Field(default=75, ge=5, le=200, description="移動平均日数")
    rsi_period: int = Field(default=14, ge=5, le=30, description="RSI計算期間")
    bb_period: int = Field(default=20, ge=5, le=50, description="ボリンジャーバンド期間")
    bb_std: float = Field(default=2.0, ge=1.0, le=3.0, description="ボリンジャーバンド標準偏差倍率")


class AppConfig(BaseModel):
    """アプリ全体の設定。"""

    watchlist: list[WatchlistItem] = Field(default_factory=list)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)


class PriceHistory:
    """時系列価額データのラッパー。"""

    def __init__(self, df: pd.DataFrame) -> None:
        """df は 'date' (datetime) と 'close' (float) カラムを持つ DataFrame。"""
        required = {"date", "close"}
        if not required.issubset(df.columns):
            missing = required - set(df.columns)
            raise ValueError(f"Missing columns: {missing}")
        if df.empty:
            raise ValueError("DataFrame must not be empty")
        self.df = df.sort_values("date").reset_index(drop=True)

    @property
    def latest_date(self) -> pd.Timestamp:
        return self.df["date"].iloc[-1]

    @property
    def latest_close(self) -> float:
        return float(self.df["close"].iloc[-1])

    def __len__(self) -> int:
        return len(self.df)
