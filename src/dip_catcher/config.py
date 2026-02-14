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

from dip_catcher.models import AppConfig

logger = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / ".dip_catcher"
CONFIG_PATH = CONFIG_DIR / "config.toml"


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
