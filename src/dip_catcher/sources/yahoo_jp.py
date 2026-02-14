from __future__ import annotations

import json
import logging
import re
import time
from datetime import date

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://finance.yahoo.co.jp/quote/{code}/history"
_REQUEST_INTERVAL = 1.0
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}
_STATE_PATTERN = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*({.+?});?\s*</script>", re.DOTALL)
_MAX_PAGES = 200


class YahooJPSource:
    """日本の投資信託データのスクレイピング (Yahoo!ファイナンスJP)。

    ページはJS描画のため、埋め込みJSON (window.__PRELOADED_STATE__) からデータを抽出する。
    """

    def fetch(self, code: str, start: date, end: date) -> pd.DataFrame:
        all_rows: list[dict[str, object]] = []
        page = 1
        while page <= _MAX_PAGES:
            rows = self._fetch_page(code, page)
            if not rows:
                break

            reached_start = False
            for row in rows:
                row_date = pd.Timestamp(row["date"]).date()
                if row_date < start:
                    reached_start = True
                    break
                if row_date <= end:
                    all_rows.append(row)

            if reached_start:
                break

            page += 1
            time.sleep(_REQUEST_INTERVAL)

        if not all_rows:
            raise ValueError(f"No data scraped from Yahoo JP for {code}")

        df = pd.DataFrame(all_rows)
        df["date"] = pd.to_datetime(df["date"])
        return df[["date", "close"]].sort_values("date").reset_index(drop=True)

    def _fetch_page(self, code: str, page: int) -> list[dict[str, object]]:
        url = _BASE_URL.format(code=code)
        params = {"p": page}
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()

        match = _STATE_PATTERN.search(resp.text)
        if match is None:
            return []

        try:
            state = json.loads(match.group(1))
        except json.JSONDecodeError:
            logger.warning("Failed to parse __PRELOADED_STATE__ for %s page %d", code, page)
            return []

        histories = (
            state.get("mainFundHistory", {}).get("histories")
            or state.get("mainHistory", {}).get("histories")
        )
        if not histories:
            return []

        rows: list[dict[str, object]] = []
        for entry in histories:
            try:
                date_text = entry["date"]
                price_text = str(entry.get("price", entry.get("close", ""))).replace(",", "")
                row_date = pd.to_datetime(date_text, format="%Y年%m月%d日")
                close_val = float(price_text)
                rows.append({"date": row_date, "close": close_val})
            except (ValueError, KeyError):
                continue
        return rows
