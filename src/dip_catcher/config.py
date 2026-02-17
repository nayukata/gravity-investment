from __future__ import annotations

import logging
import sys
from pathlib import Path

import tomli_w

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from pydantic import ValidationError

from dip_catcher.models import AppConfig, AssetCategory, WatchlistItem

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".dip_catcher"
CONFIG_PATH = CONFIG_DIR / "config.toml"


PRESET_ITEMS: dict[AssetCategory, list[WatchlistItem]] = {
    AssetCategory.JP_FUND: [
        WatchlistItem(code="0331418A", name="eMAXIS Slim 全世界株式", category=AssetCategory.JP_FUND),
        WatchlistItem(code="03311187", name="eMAXIS Slim 米国株式(S&P500)", category=AssetCategory.JP_FUND),
        WatchlistItem(code="02311251", name="Tracers NASDAQ100ゴールドプラス", category=AssetCategory.JP_FUND),
        WatchlistItem(code="02315228", name="Tracers S&P500ゴールドプラス", category=AssetCategory.JP_FUND),
        WatchlistItem(code="9I312179", name="iFreeNEXT FANG+", category=AssetCategory.JP_FUND),
        WatchlistItem(code="89311199", name="SBI・V・S&P500", category=AssetCategory.JP_FUND),
    ],
    AssetCategory.US_STOCK: [
        WatchlistItem(code="VOO", name="Vanguard S&P 500 ETF", category=AssetCategory.US_STOCK),
        WatchlistItem(code="QQQ", name="Invesco QQQ Trust", category=AssetCategory.US_STOCK),
        WatchlistItem(code="VTI", name="Vanguard Total Stock Market", category=AssetCategory.US_STOCK),
        WatchlistItem(code="VT", name="Vanguard Total World Stock", category=AssetCategory.US_STOCK),
        WatchlistItem(code="AAPL", name="Apple", category=AssetCategory.US_STOCK),
        WatchlistItem(code="NVDA", name="NVIDIA", category=AssetCategory.US_STOCK),
    ],
    AssetCategory.JP_STOCK: [
        WatchlistItem(code="1306.T", name="TOPIX連動型ETF", category=AssetCategory.JP_STOCK),
        WatchlistItem(code="1321.T", name="日経225連動型ETF", category=AssetCategory.JP_STOCK),
        WatchlistItem(code="1655.T", name="iシェアーズ S&P500 ETF", category=AssetCategory.JP_STOCK),
    ],
    AssetCategory.INDEX: [
        WatchlistItem(code="^GSPC", name="S&P 500", category=AssetCategory.INDEX),
        WatchlistItem(code="^DJI", name="ダウ工業株30種", category=AssetCategory.INDEX),
        WatchlistItem(code="^IXIC", name="NASDAQ総合", category=AssetCategory.INDEX),
        WatchlistItem(code="^N225", name="日経平均", category=AssetCategory.INDEX),
    ],
}


def load_config(path: Path | None = None) -> AppConfig:
    """設定ファイルを読み込む。存在しなければデフォルト設定を返す。

    不正な設定ファイルの場合はデフォルト設定にフォールバックする。
    """
    config_path = path or CONFIG_PATH
    if not config_path.exists():
        return AppConfig()
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
        return AppConfig.model_validate(raw)
    except (tomllib.TOMLDecodeError, ValidationError) as e:
        logger.warning("Invalid config file %s: %s. Using defaults.", config_path, e)
        return AppConfig()


def save_config(config: AppConfig, path: Path | None = None) -> None:
    """設定をTOMLファイルに保存する。"""
    config_path = path or CONFIG_PATH
    config_dir = config_path.parent
    config_dir.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="json")
    config_path.write_bytes(tomli_w.dumps(data).encode("utf-8"))
