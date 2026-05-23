# =============================================================================
# 文件名称：run_pipeline.py
# 课题名称：基于用户行为数据评估的企业信息数据安全防护系统
# 模块：训练与实验流水线入口
# 功能：按顺序执行数据模拟、特征工程、模型训练和对比实验，生成前端展示所需产物
# =============================================================================

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PROJECT_ROOT / "src"


def run_step(name: str, script: str) -> None:
    print("=" * 72)
    print(f"开始执行：{name}")
    print("=" * 72)
    subprocess.run([sys.executable, str(SRC_DIR / script)], cwd=str(PROJECT_ROOT), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="UBA 数据、训练与实验完整流水线")
    parser.add_argument(
        "--skip-simulation",
        action="store_true",
        help="跳过 simulation.py，复用 data/ 下已有日志数据",
    )
    args = parser.parse_args()

    if args.skip_simulation:
        print("[提示] 已跳过数据模拟，将复用 data/ 下已有日志数据。")
    else:
        run_step("用户行为日志模拟", "simulation.py")
    run_step("增强特征工程", "feature_engineering.py")
    run_step("LightGBM + 专家规则模型训练", "model_training.py")
    run_step("多模型对比与论文实验结果生成", "evaluation_compare.py")
    print("=" * 72)
    print("全部完成：模型、特征列表、训练元数据和实验结果已生成。")
    print("下一步运行：python src/app.py，然后访问 http://localhost:5000")
    print("=" * 72)


if __name__ == "__main__":
    main()
