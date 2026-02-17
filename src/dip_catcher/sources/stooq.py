from __future__ import annotations

import io
from datetime import date
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd

_BASE_URL = "https://stooq.com/q/d/l/"
_TIMEOUT = 15


class StooqSource:
    """日本株・一部指数のデータ取得 (stooq.com CSV)。"""

    def fetch(self, code: str, start: date, end: date) -> pd.DataFrame:
        # 数字のみのコード（日本株の証券コード）には .JP を自動付与
        stooq_code = f"{code}.JP" if code.isdigit() else code
        params = {
            "s": stooq_code,
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        url = f"{_BASE_URL}?{urlencode(params)}"
        try:
            with urlopen(url, timeout=_TIMEOUT) as resp:
                df = pd.read_csv(io.StringIO(resp.read().decode("utf-8")))
        except Exception as e:
            raise ValueError(f"Failed to fetch stooq data for {code}: {e}") from e
        if df.empty or "Close" not in df.columns:
            raise ValueError(f"No data returned from stooq for {code}")
        df = df[["Date", "Close"]].rename(
            columns={"Date": "date", "Close": "close"}
        )
        df["date"] = pd.to_datetime(df["date"])
        return df.sort_values("date").reset_index(drop=True)
