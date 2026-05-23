from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch


OUT_DIR = Path(__file__).resolve().parent / "figures"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def get_font():
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for path in candidates:
        if Path(path).exists():
            return font_manager.FontProperties(fname=path)
    return font_manager.FontProperties()


FONT = get_font()
plt.rcParams["axes.unicode_minus"] = False


COLORS = {
    "bg": "#f7f9fc",
    "ink": "#1f2937",
    "muted": "#64748b",
    "blue": "#2563eb",
    "cyan": "#0891b2",
    "green": "#059669",
    "orange": "#d97706",
    "red": "#dc2626",
    "line": "#94a3b8",
    "card": "#ffffff",
}


def box(ax, xy, wh, text, color, fontsize=12):
    x, y = xy
    w, h = wh
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle="round,pad=0.018,rounding_size=0.035",
        linewidth=1.4,
        edgecolor=color,
        facecolor=COLORS["card"],
    )
    ax.add_patch(patch)
    ax.text(
        x + w / 2,
        y + h / 2,
        text,
        ha="center",
        va="center",
        fontproperties=FONT,
        fontsize=fontsize,
        color=COLORS["ink"],
        linespacing=1.3,
    )


def arrow(ax, start, end, color=None, rad=0.0):
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=14,
            linewidth=1.5,
            color=color or COLORS["line"],
            connectionstyle=f"arc3,rad={rad}",
        )
    )


def setup(title):
    fig, ax = plt.subplots(figsize=(12, 7), dpi=180)
    fig.patch.set_facecolor(COLORS["bg"])
    ax.set_facecolor(COLORS["bg"])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.text(
        0.5,
        0.94,
        title,
        ha="center",
        va="center",
        fontproperties=FONT,
        fontsize=19,
        fontweight="bold",
        color=COLORS["ink"],
    )
    return fig, ax


def save(fig, name):
    fig.savefig(OUT_DIR / name, bbox_inches="tight", facecolor=COLORS["bg"])
    plt.close(fig)


def fig_async_architecture():
    fig, ax = setup("实时监控模块的本地优先与异步智能解读架构")

    box(ax, (0.05, 0.63), (0.19, 0.15), "前端实时监控\n自动轮询 / 手动下一条", COLORS["blue"])
    box(ax, (0.31, 0.63), (0.19, 0.15), "/api/next_log\n获取一条行为日志", COLORS["cyan"])
    box(ax, (0.57, 0.70), (0.18, 0.11), "LightGBM\n异常概率", COLORS["green"])
    box(ax, (0.57, 0.55), (0.18, 0.11), "专家规则\n高危场景修正", COLORS["orange"])
    box(ax, (0.81, 0.63), (0.15, 0.15), "页面立即展示\n图表 / 风险分 / SHAP", COLORS["blue"])

    box(ax, (0.31, 0.25), (0.20, 0.13), "异步任务\n生成 request_id", COLORS["muted"])
    box(ax, (0.57, 0.25), (0.19, 0.13), "大模型解读\n摘要与研判建议", COLORS["red"])
    box(ax, (0.81, 0.25), (0.15, 0.13), "增量刷新\n不阻塞主页面", COLORS["red"])

    arrow(ax, (0.24, 0.705), (0.31, 0.705))
    arrow(ax, (0.50, 0.705), (0.57, 0.755))
    arrow(ax, (0.50, 0.705), (0.57, 0.605))
    arrow(ax, (0.75, 0.755), (0.81, 0.705))
    arrow(ax, (0.75, 0.605), (0.81, 0.705))

    arrow(ax, (0.405, 0.63), (0.405, 0.38), COLORS["red"])
    arrow(ax, (0.51, 0.315), (0.57, 0.315), COLORS["red"])
    arrow(ax, (0.76, 0.315), (0.81, 0.315), COLORS["red"])
    arrow(ax, (0.885, 0.38), (0.885, 0.63), COLORS["red"])

    ax.text(0.62, 0.46, "本地检测链路先完成，LLM 只作为解释增强层", ha="center", fontproperties=FONT, fontsize=12, color=COLORS["muted"])
    ax.text(0.62, 0.16, "效果：核心结果响应稳定；网络或大模型变慢时，不影响风险检测与图表渲染", ha="center", fontproperties=FONT, fontsize=12, color=COLORS["muted"])
    save(fig, "fig_async_llm_architecture.png")


def fig_detection_loop():
    fig, ax = setup("用户行为风险检测、解释与处置闭环")
    nodes = [
        ((0.08, 0.55), "行为日志采集\n登录 / 访问 / 下载\n失败次数", COLORS["blue"]),
        ((0.32, 0.55), "特征工程\n时间 / 角色 / 敏感级别\n滑窗统计", COLORS["cyan"]),
        ((0.56, 0.55), "融合检测\n模型概率 + 专家规则", COLORS["green"]),
        ((0.80, 0.55), "可解释输出\nSHAP贡献\n风险原因", COLORS["orange"]),
        ((0.56, 0.24), "安全研判\n告警分级 / 处置建议\n审计记录", COLORS["red"]),
        ((0.32, 0.24), "策略反馈\n规则阈值\n特征与样本迭代", COLORS["muted"]),
    ]
    for xy, text, color in nodes:
        box(ax, xy, (0.16, 0.14), text, color, fontsize=11)
    arrow(ax, (0.24, 0.62), (0.32, 0.62))
    arrow(ax, (0.48, 0.62), (0.56, 0.62))
    arrow(ax, (0.72, 0.62), (0.80, 0.62))
    arrow(ax, (0.88, 0.55), (0.64, 0.38), COLORS["red"], rad=-0.1)
    arrow(ax, (0.56, 0.31), (0.48, 0.31), COLORS["muted"])
    arrow(ax, (0.32, 0.31), (0.16, 0.55), COLORS["muted"], rad=-0.15)
    ax.text(0.5, 0.15, "闭环思路强调“发现异常、说明原因、辅助处置、持续优化”，避免系统只停留在单次分类结果。", ha="center", fontproperties=FONT, fontsize=12, color=COLORS["muted"])
    save(fig, "fig_detection_explanation_loop.png")


def fig_profile_aggregation():
    fig, ax = setup("用户风险画像聚合流程")
    box(ax, (0.06, 0.63), (0.18, 0.14), "用户历史行为\n多条日志与访问记录", COLORS["blue"])
    box(ax, (0.30, 0.63), (0.18, 0.14), "用户级统计\n异常次数 / 平均风险\n峰值风险", COLORS["cyan"])
    box(ax, (0.54, 0.63), (0.18, 0.14), "画像标签\n高频操作 / 异常模式\n敏感访问", COLORS["green"])
    box(ax, (0.78, 0.63), (0.16, 0.14), "用户详情页\n风险趋势\n处置依据", COLORS["blue"])
    box(ax, (0.30, 0.30), (0.18, 0.13), "模型输出\n风险概率", COLORS["orange"])
    box(ax, (0.54, 0.30), (0.18, 0.13), "规则输出\n触发原因", COLORS["orange"])
    arrow(ax, (0.24, 0.70), (0.30, 0.70))
    arrow(ax, (0.48, 0.70), (0.54, 0.70))
    arrow(ax, (0.72, 0.70), (0.78, 0.70))
    arrow(ax, (0.39, 0.43), (0.39, 0.63), COLORS["orange"])
    arrow(ax, (0.63, 0.43), (0.63, 0.63), COLORS["orange"])
    ax.text(0.5, 0.19, "画像不是替代单条告警，而是把分散日志沉淀为用户维度的长期风险证据。", ha="center", fontproperties=FONT, fontsize=12, color=COLORS["muted"])
    save(fig, "fig_user_profile_aggregation.png")


def fig_security_metrics():
    fig, ax = setup("面向安全业务的模型评价指标框架")
    box(ax, (0.08, 0.64), (0.18, 0.14), "通用分类指标\nAccuracy / Precision\nRecall / F1", COLORS["blue"])
    box(ax, (0.32, 0.64), (0.18, 0.14), "不平衡检测指标\nAUC / PR-AUC", COLORS["cyan"])
    box(ax, (0.56, 0.64), (0.18, 0.14), "高危场景指标\nHighRisk-Recall", COLORS["orange"])
    box(ax, (0.80, 0.64), (0.14, 0.14), "运行指标\nLatency", COLORS["green"])
    box(ax, (0.33, 0.31), (0.34, 0.14), "安全代价函数\n5×高危漏报 + 3×普通漏报 + 1×误报", COLORS["red"])
    box(ax, (0.36, 0.12), (0.28, 0.11), "综合选择\n检测效果 + 业务风险 + 实时性", COLORS["blue"])
    for start in [(0.17, 0.64), (0.41, 0.64), (0.65, 0.64), (0.87, 0.64)]:
        arrow(ax, start, (0.50, 0.45), COLORS["line"])
    arrow(ax, (0.50, 0.31), (0.50, 0.23), COLORS["red"])
    ax.text(0.5, 0.53, "异常检测不能只看准确率，需要把漏报、误报和关键场景风险纳入同一评价口径。", ha="center", fontproperties=FONT, fontsize=12, color=COLORS["muted"])
    save(fig, "fig_security_metric_framework.png")


def main():
    fig_async_architecture()
    fig_detection_loop()
    fig_profile_aggregation()
    fig_security_metrics()
    print(f"Generated figures in {OUT_DIR}")


if __name__ == "__main__":
    main()
