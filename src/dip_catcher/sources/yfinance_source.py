from __future__ import annotations

from datetime import date

import pandas as pd
import yfinance as yf


class YFinanceSource:
    """米国株・ETF・主要指数のデータ取得 (yfinance)。"""

    def fetch(self, code: str, start: date, end: date) -> pd.DataFrame:
        try:
            ticker = yf.Ticker(code)
            hist = ticker.history(start=start.isoformat(), end=end.isoformat())
        except Exception as e:
            raise ValueError(f"Failed to fetch yfinance data for {code}: {e}") from e
        if hist.empty:
            raise ValueError(f"No data returned from yfinance for {code}")
        df = hist.reset_index()[["Date", "Close"]].rename(
            columns={"Date": "date", "Close": "close"}
        )
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        return df
