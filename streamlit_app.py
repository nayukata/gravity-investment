"""Streamlit Cloud エントリポイント。"""

import sys
from pathlib import Path

# src/ ディレクトリをインポートパスに追加
sys.path.insert(0, str(Path(__file__).parent / "src"))

from dip_catcher.app import main  # noqa: E402

main()
