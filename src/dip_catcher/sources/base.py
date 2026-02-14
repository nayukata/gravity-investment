from __future__ import annotations

from datetime import date
from typing import Protocol

import pandas as pd


class DataSource(Protocol):
    """データソースの共通インターフェース。

    fetch() は 'date' (datetime) と 'close' (float) カラムを持つ DataFrame を返す。
    """

    def fetch(self, code: str, start: date, end: date) -> pd.DataFrame: ...
