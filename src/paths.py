# =============================================================================
# 项目路径配置：脚本放在项目根目录，数据与模型产物分目录存放。
# 使用 Path(__file__) 定位根目录，不依赖当前工作目录，从任意位置运行脚本均可。
# =============================================================================

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MODELS_DIR = PROJECT_ROOT / "models"
# 前端静态站点根目录（index.html、static/ 等，由 Flask 托管）
WEB_DIR = PROJECT_ROOT / "web"


def ensure_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR


def ensure_models_dir() -> Path:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return MODELS_DIR
