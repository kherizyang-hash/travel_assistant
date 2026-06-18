"""应用数据路径配置（支持 DATA_DIR 环境变量与 Sealos 卷挂载）。"""

import os
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "."))
PREFERENCES_DB = Path(os.getenv("PREFERENCES_DB", str(DATA_DIR / "preferences.db")))
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", str(DATA_DIR / "output")))
CHECKPOINTS_DB = Path(os.getenv("CHECKPOINTS_DB", str(DATA_DIR / "checkpoints.db")))


def ensure_data_dirs() -> None:
    """确保数据目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PREFERENCES_DB.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINTS_DB.parent.mkdir(parents=True, exist_ok=True)
