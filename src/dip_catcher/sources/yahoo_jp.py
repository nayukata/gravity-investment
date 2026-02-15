"""日本の投資信託データの取得 (Yahoo!ファイナンスJP)。

Playwright でページを描画し、ページネーション操作で BFF API レスポンスを
キャプチャして基準価額を取得する。
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import date
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from playwright.sync_api import Page, Response

logger = logging.getLogger(__name__)

_BASE_URL = "https://finance.yahoo.co.jp/quote/{code}/history"
_MAX_PAGES = 100
_NAV_WAIT_MS = 2000

# システムにインストールされた Chromium を検出する
_SYSTEM_CHROMIUM_NAMES = ("chromium", "chromium-browser", "google-chrome")


def _find_system_chromium() -> str | None:
    """システムにインストールされた Chromium のパスを返す。"""
    for name in _SYSTEM_CHROMIUM_NAMES:
        path = shutil.which(name)
        if path:
            return path
    return None


class YahooJPSource:
    """日本の投資信託データのスクレイピング (Yahoo!ファイナンスJP)。"""

    def fetch(self, code: str, start: date, end: date) -> pd.DataFrame:
        from playwright.sync_api import sync_playwright

        all_rows: list[dict[str, object]] = []

        with sync_playwright() as pw:
            launch_kwargs: dict[str, object] = {"headless": True}
            sys_chromium = _find_system_chromium()
            if sys_chromium:
                launch_kwargs["executable_path"] = sys_chromium
                logger.info("Using system Chromium: %s", sys_chromium)
            browser = pw.chromium.launch(**launch_kwargs)
            try:
                page = browser.new_page()

                # BFF API レスポンスをキャプチャ
                captured: list[dict] = []

                def _on_response(response: Response) -> None:
                    url = response.url
                    if "/bff/" in url and "/history" in url:
                        try:
                            captured.append(response.json())
                        except Exception:
                            logger.debug("Failed to parse BFF response from %s", url)

                page.on("response", _on_response)

                page.goto(_BASE_URL.format(code=code))
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(_NAV_WAIT_MS)

                # ページ1: HTMLテーブルから抽出（BFF APIは初回読込では呼ばれない）
                rows = self._extract_table(page)
                all_rows.extend(rows)

                # ページ2以降: 「次へ」をクリックしてBFF APIレスポンスをキャプチャ
                for _ in range(2, _MAX_PAGES + 1):
                    # セレクタ: Yahoo Finance JP の履歴ページのページネーション要素
                    next_btn = page.locator("p").filter(has_text="次へ").first
                    if not next_btn.is_visible():
                        break
                    classes = next_btn.get_attribute("class") or ""
                    if "disabled" in classes:
                        break

                    captured.clear()
                    next_btn.click()
                    page.wait_for_timeout(_NAV_WAIT_MS)
                    page.wait_for_load_state("networkidle")
                    page.wait_for_timeout(500)

                    # BFF API レスポンスからデータ抽出
                    if captured:
                        new_rows = self._parse_bff_response(captured[-1])
                        if not new_rows:
                            break
                        all_rows.extend(new_rows)

                        # start 日より前のデータが出たら終了
                        earliest = min(r["date"] for r in new_rows)
                        if pd.Timestamp(earliest).date() <= start:
                            break

                        paging = captured[-1].get("paging", {})
                        if not paging.get("hasNext", False):
                            break
                    else:
                        # BFF API が呼ばれなかった場合はテーブルから再抽出
                        rows = self._extract_table(page)
                        if not rows:
                            break
                        all_rows.extend(rows)
            finally:
                browser.close()

        if not all_rows:
            raise ValueError(f"No data scraped from Yahoo JP for {code}")

        df = pd.DataFrame(all_rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset=["date"]).sort_values("date").reset_index(drop=True)

        # 期間でフィルタ
        mask = (df["date"].dt.date >= start) & (df["date"].dt.date <= end)
        result = df.loc[mask, ["date", "close"]].reset_index(drop=True)
        if result.empty:
            raise ValueError(f"No data in range for {code}")
        return result

    def _extract_table(self, page: Page) -> list[dict[str, object]]:
        """HTMLテーブルから日付と基準価額を抽出する。"""
        rows = page.locator("table tbody tr").all()
        data: list[dict[str, object]] = []
        for row in rows:
            cells = row.locator("td").all()
            if len(cells) < 2:
                continue
            date_text = cells[0].text_content().strip()
            price_text = cells[1].text_content().strip()
            m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_text)
            if m:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                try:
                    close = float(price_text.replace(",", ""))
                except ValueError:
                    continue
                data.append({"date": pd.Timestamp(d), "close": close})
        return data

    def _parse_bff_response(self, body: dict) -> list[dict[str, object]]:
        """BFF API のレスポンスから基準価額データを抽出する。"""
        histories = body.get("histories", [])
        data: list[dict[str, object]] = []
        for entry in histories:
            try:
                date_str = entry.get("date", "")
                m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", date_str)
                if not m:
                    continue
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                price = entry.get("price", entry.get("close"))
                if price is None:
                    continue
                close = float(str(price).replace(",", ""))
                data.append({"date": pd.Timestamp(d), "close": close})
            except (ValueError, TypeError):
                continue
        return data
