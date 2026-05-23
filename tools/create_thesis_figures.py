from __future__ import annotations

import json
import pickle
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MODEL_DIR = ROOT / "models"
OUT_DIR = ROOT / "论文初稿_assets"
OUT_DIR.mkdir(exist_ok=True)

FONT_PATH = Path("C:/Windows/Fonts/msyh.ttc")
if FONT_PATH.exists():
    font_manager.fontManager.addfont(str(FONT_PATH))
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 140


def save(fig, name: str) -> str:
    path = OUT_DIR / name
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def load_results() -> dict:
    with open(DATA_DIR / "experiment_results.json", "r", encoding="utf-8") as f:
        return json.load(f)


def draw_architecture() -> None:
    fig, ax = plt.subplots(figsize=(11.2, 5.7))
    ax.axis("off")
    groups = [
        ("数据层", ["模拟行为日志", "用户/角色/操作", "异常场景注入"]),
        ("算法层", ["特征工程", "LightGBM", "专家规则融合", "SHAP解释"]),
        ("服务层", ["Flask API", "风险评分接口", "用户画像接口", "实验结果接口"]),
        ("展示层", ["实时监控", "异常用户表", "风险关系图", "实验对比"]),
        ("智能增强", ["DeepSeek兼容接口", "告警研判", "自然语言解读"]),
    ]
    xs = np.linspace(0.08, 0.92, len(groups))
    for i, (title, items) in enumerate(groups):
        x = xs[i]
        rect = plt.Rectangle((x - 0.085, 0.22), 0.17, 0.58, facecolor="#EEF4FF", edgecolor="#3B6EA8", lw=1.6)
        ax.add_patch(rect)
        ax.text(x, 0.73, title, ha="center", va="center", fontsize=14, weight="bold", color="#123A5F")
        for j, item in enumerate(items):
            ax.text(x, 0.62 - j * 0.09, item, ha="center", va="center", fontsize=10.2)
        if i < len(groups) - 1:
            ax.annotate("", xy=(xs[i + 1] - 0.095, 0.51), xytext=(x + 0.095, 0.51),
                        arrowprops=dict(arrowstyle="->", lw=1.6, color="#546A7B"))
    ax.text(0.5, 0.09, "图1 系统总体架构：从日志数据到检测、解释、研判与可视化展示的闭环", ha="center", fontsize=11)
    save(fig, "fig1_system_architecture.png")


def draw_pipeline() -> None:
    fig, ax = plt.subplots(figsize=(11, 4.8))
    ax.axis("off")
    nodes = [
        "日志生成\nsimulation.py",
        "特征工程\nfeature_engineering.py",
        "模型训练\nmodel_training.py",
        "实验评估\nevaluation_compare.py",
        "Web看板\napp.py",
    ]
    xs = np.linspace(0.1, 0.9, len(nodes))
    colors = ["#E8F4EA", "#EAF2FF", "#FFF4DF", "#FCEEEE", "#EEF1F7"]
    for i, (x, text) in enumerate(zip(xs, nodes)):
        box = plt.Rectangle((x - 0.08, 0.38), 0.16, 0.24, facecolor=colors[i], edgecolor="#2F3A45", lw=1.2)
        ax.add_patch(box)
        ax.text(x, 0.50, text, ha="center", va="center", fontsize=11, weight="bold")
        if i < len(nodes) - 1:
            ax.annotate("", xy=(xs[i + 1] - 0.09, 0.50), xytext=(x + 0.09, 0.50),
                        arrowprops=dict(arrowstyle="->", lw=1.5, color="#44515F"))
    ax.text(0.5, 0.22, "图2 系统运行流水线：数据、特征、模型、实验与前端服务依次衔接", ha="center", fontsize=11)
    save(fig, "fig2_pipeline.png")


def draw_model_metrics(results: dict) -> None:
    rows = results["model_compare"]
    models = [r["Model"].replace("LightGBM + Expert Rules", "LGBM+规则").replace("Logistic Regression", "LR").replace("Isolation Forest", "IF") for r in rows]
    metrics = ["Accuracy", "Precision", "Recall", "F1-Score", "PR-AUC"]
    x = np.arange(len(models))
    width = 0.15
    fig, ax = plt.subplots(figsize=(11.5, 5.7))
    palette = ["#4C78A8", "#59A14F", "#F28E2B", "#E15759", "#76B7B2"]
    for i, m in enumerate(metrics):
        ax.bar(x + (i - 2) * width, [r[m] for r in rows], width, label=m, color=palette[i])
    ax.set_ylim(0.6, 1.02)
    ax.set_ylabel("指标值")
    ax.set_title("模型通用性能指标对比")
    ax.set_xticks(x)
    ax.set_xticklabels(models)
    ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.12))
    ax.grid(axis="y", alpha=0.25)
    save(fig, "fig3_model_metrics.png")


def draw_security_metrics(results: dict) -> None:
    rows = [r for r in results["model_compare"] if r["Model"] in ("LightGBM", "LightGBM + Expert Rules")]
    labels = ["LightGBM", "LGBM+专家规则"]
    fig, axes = plt.subplots(1, 2, figsize=(11.3, 4.8))
    axes[0].bar(labels, [r["HighRisk-Recall"] for r in rows], color=["#4C78A8", "#E15759"])
    axes[0].set_ylim(0.99, 1.0)
    axes[0].set_title("高危场景召回率")
    axes[0].set_ylabel("HighRisk-Recall")
    for i, r in enumerate(rows):
        axes[0].text(i, r["HighRisk-Recall"] + 0.00015, f"{r['HighRisk-Recall']:.4f}", ha="center", fontsize=10)

    axes[1].bar(labels, [r["Security-Cost"] for r in rows], color=["#4C78A8", "#E15759"])
    axes[1].set_title("加权安全代价")
    axes[1].set_ylabel("Security-Cost（越低越好）")
    for i, r in enumerate(rows):
        axes[1].text(i, r["Security-Cost"] + 3, f"{r['Security-Cost']:.0f}", ha="center", fontsize=10)
    save(fig, "fig4_security_metrics.png")


def draw_confusion(results: dict) -> None:
    row = next(r for r in results["model_compare"] if r["Model"] == "LightGBM + Expert Rules")
    cm = row["confusion_matrix"]
    data = np.array([[cm["tn"], cm["fp"]], [cm["fn"], cm["tp"]]])
    fig, ax = plt.subplots(figsize=(6.4, 5.5))
    im = ax.imshow(data, cmap="Blues")
    ax.set_xticks([0, 1])
    ax.set_yticks([0, 1])
    ax.set_xticklabels(["预测正常", "预测异常"])
    ax.set_yticklabels(["实际正常", "实际异常"])
    ax.set_title("LightGBM + 专家规则混淆矩阵")
    for i in range(2):
        for j in range(2):
            ax.text(j, i, str(data[i, j]), ha="center", va="center", fontsize=15, weight="bold",
                    color="white" if data[i, j] > data.max() * 0.55 else "#123A5F")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    save(fig, "fig5_confusion_matrix.png")


def draw_feature_importance() -> None:
    model = joblib.load(MODEL_DIR / "lgbm_uba_model.pkl")
    with open(MODEL_DIR / "feature_names.pkl", "rb") as f:
        names = pickle.load(f)
    gain = model.booster_.feature_importance(importance_type="gain")
    pairs = sorted(zip(names, gain), key=lambda x: x[1], reverse=True)[:10]
    cn = {
        "role_operation_mismatch": "角色操作不匹配",
        "operation_type_code": "操作类型",
        "is_remote_or_unknown_ip": "远程/未知位置",
        "file_sensitive_level": "文件敏感级别",
        "sensitive_level_delta_user": "敏感度偏离",
        "login_fail_count_1h": "登录失败次数",
        "sensitive_download_count_1h": "敏感下载次数",
        "op_count_1h": "操作频次",
        "operation_entropy_1h": "操作复杂度",
        "is_night": "深夜时段",
        "role_code": "用户角色",
        "ip_location_code": "IP位置",
    }
    labels = [cn.get(k, k) for k, _ in pairs][::-1]
    values = [v for _, v in pairs][::-1]
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    ax.barh(labels, values, color="#4C78A8")
    ax.set_title("LightGBM 特征重要性 Top 10（Gain）")
    ax.set_xlabel("Gain")
    ax.grid(axis="x", alpha=0.25)
    save(fig, "fig6_feature_importance.png")


def write_support(results: dict) -> None:
    df = pd.read_parquet(DATA_DIR / "train_features.parquet")
    support = {
        "feature_rows": int(len(df)),
        "feature_count": int(len([c for c in df.columns if c not in ("user_id", "timestamp", "label")])),
        "user_count": int(df["user_id"].nunique()) if "user_id" in df.columns else None,
        "positive_count": int(df["label"].sum()),
        "negative_count": int((df["label"] == 0).sum()),
        "positive_ratio": float(df["label"].mean()),
        "experiment": {
            "test_count": results.get("test_count"),
            "positive_count": results.get("positive_count"),
            "negative_count": results.get("negative_count"),
            "positive_ratio": results.get("positive_ratio"),
            "model_compare": results.get("model_compare", []),
        },
    }
    with open(OUT_DIR / "support.json", "w", encoding="utf-8") as f:
        json.dump(support, f, ensure_ascii=False, indent=2)


def main() -> None:
    results = load_results()
    draw_architecture()
    draw_pipeline()
    draw_model_metrics(results)
    draw_security_metrics(results)
    draw_confusion(results)
    draw_feature_importance()
    write_support(results)
    print(f"figures written to {OUT_DIR}")


if __name__ == "__main__":
    main()
