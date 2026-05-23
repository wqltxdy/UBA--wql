# =============================================================================
# 文件名称：evaluation_compare.py
# 课题名称：基于用户行为数据评估的企业信息数据安全防护系统
# 作者：吴奇龙 (学号: 112204260152)
# 模块：基准模型性能对比实验 (用于毕业论文数据支撑)
# =============================================================================

import pandas as pd
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import IsolationForest
import lightgbm as lgb
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
    roc_curve,
)
import warnings
import json
import time

from model_training import expert_rule_hybrid_adjust
from paths import DATA_DIR

warnings.filterwarnings('ignore')


def time_split(df: pd.DataFrame, test_size: float = 0.2):
    df_tmp = df.sort_values("timestamp").reset_index(drop=True)
    split_idx = int(len(df_tmp) * (1 - test_size))
    train_df = df_tmp.iloc[:split_idx]
    test_df = df_tmp.iloc[split_idx:]
    drop_cols = ['user_id', 'timestamp', 'label']
    return (
        train_df.drop(columns=drop_cols),
        test_df.drop(columns=drop_cols),
        train_df['label'].astype(int),
        test_df['label'].astype(int),
    )


def high_risk_scene_mask(X: pd.DataFrame) -> pd.Series:
    """定义安全业务上更不能漏报的高危场景，用于补充通用机器学习指标。"""
    def col(name: str, default: float = 0.0) -> pd.Series:
        if name in X.columns:
            return pd.to_numeric(X[name], errors="coerce").fillna(default)
        return pd.Series(default, index=X.index)

    night_sensitive = (col("is_night") >= 0.5) & (col("file_sensitive_level") >= 0.6)
    remote_login_fail = (col("is_remote_or_unknown_ip") >= 0.5) & (col("login_fail_count_1h") >= 0.4)
    role_mismatch_sensitive = (col("role_operation_mismatch") >= 0.5) & (col("file_sensitive_level") >= 0.6)
    sensitive_download = col("sensitive_download_count_1h") >= 0.35
    high_operation_burst = (col("op_count_1h") >= 0.8) | (col("operation_entropy_1h") >= 0.65)
    return night_sensitive | remote_login_fail | role_mismatch_sensitive | sensitive_download | high_operation_burst


def security_business_metrics(y_test, y_pred, high_risk_mask: pd.Series) -> dict:
    y_true = np.asarray(y_test).astype(int)
    y_hat = np.asarray(y_pred).astype(int)
    high = np.asarray(high_risk_mask).astype(bool)

    pos = y_true == 1
    neg = y_true == 0
    fn = (pos & (y_hat == 0))
    fp = (neg & (y_hat == 1))
    high_pos = pos & high
    high_fn = fn & high
    normal_fn = fn & ~high

    high_total = int(high_pos.sum())
    high_recall = float(((high_pos & (y_hat == 1)).sum() / high_total) if high_total else 0.0)

    high_fn_count = int(high_fn.sum())
    normal_fn_count = int(normal_fn.sum())
    fp_count = int(fp.sum())
    weighted_cost = 5 * high_fn_count + 3 * normal_fn_count + fp_count
    max_cost = 5 * max(high_total, 1) + 3 * int((pos & ~high).sum()) + int(neg.sum())
    safety_score = 1.0 - (weighted_cost / max(max_cost, 1))

    return {
        "HighRisk-Recall": high_recall,
        "Security-Cost": float(weighted_cost),
        "Security-Score": float(safety_score),
        "high_risk_total": high_total,
        "high_risk_fn": high_fn_count,
        "normal_fn": normal_fn_count,
        "weighted_cost_formula": "5*高危漏报 + 3*普通漏报 + 1*误报",
    }


def evaluate_model(name: str, y_test, y_pred, y_score, latency_ms: float, high_risk_mask: pd.Series):
    tn, fp, fn, tp = confusion_matrix(y_test, y_pred, labels=[0, 1]).ravel()
    result = {
        "Model": name,
        "Accuracy": float(accuracy_score(y_test, y_pred)),
        "Precision": float(precision_score(y_test, y_pred, zero_division=0)),
        "Recall": float(recall_score(y_test, y_pred, zero_division=0)),
        "F1-Score": float(f1_score(y_test, y_pred, zero_division=0)),
        "AUC": float(roc_auc_score(y_test, y_score)),
        "PR-AUC": float(average_precision_score(y_test, y_score)),
        "Latency(ms)": float(latency_ms),
        "confusion_matrix": {
            "tn": int(tn),
            "fp": int(fp),
            "fn": int(fn),
            "tp": int(tp),
        },
    }
    result.update(security_business_metrics(y_test, y_pred, high_risk_mask))
    return result


def compact_curve(xs, ys, max_points: int = 120):
    if len(xs) <= max_points:
        idx = np.arange(len(xs))
    else:
        idx = np.linspace(0, len(xs) - 1, max_points).astype(int)
    return [{"x": float(xs[i]), "y": float(ys[i])} for i in idx]


def _best_by(results: list[dict], metric: str) -> dict:
    return max(results, key=lambda r: r[metric])


def build_experiment_summary(results: list[dict], positive_ratio: float) -> dict:
    model_cn = {
        "Logistic Regression": "逻辑回归",
        "Isolation Forest": "孤立森林",
        "LightGBM": "LightGBM",
        "LightGBM + Expert Rules": "LightGBM + 专家规则",
    }

    best_f1 = _best_by(results, "F1-Score")
    best_recall = _best_by(results, "Recall")
    best_precision = _best_by(results, "Precision")
    best_high_risk_recall = _best_by(results, "HighRisk-Recall")
    best_security_score = _best_by(results, "Security-Score")
    lgb = next((r for r in results if r["Model"] == "LightGBM"), None)
    hybrid = next((r for r in results if r["Model"] == "LightGBM + Expert Rules"), None)

    insights = [
        f"综合来看，{model_cn.get(best_f1['Model'], best_f1['Model'])} 的 F1 最高，说明精确率与召回率之间的平衡最好。",
        f"{model_cn.get(best_recall['Model'], best_recall['Model'])} 的召回率最高，更适合强调少漏报的安全检测场景。",
        f"{model_cn.get(best_precision['Model'], best_precision['Model'])} 的精确率最高，意味着告警命中率更好、误报复核压力更低。",
        f"{model_cn.get(best_high_risk_recall['Model'], best_high_risk_recall['Model'])} 的高危场景召回率最高，更能体现安全兜底能力。",
        f"{model_cn.get(best_security_score['Model'], best_security_score['Model'])} 的安全代价评分最高，说明在高危漏报加权后业务收益更好。",
        f"本次测试集异常占比约为 {positive_ratio:.2%}，因此 PR-AUC 比单纯准确率更能反映异常检测质量。",
    ]
    if lgb and hybrid:
        recall_delta = hybrid["Recall"] - lgb["Recall"]
        precision_delta = hybrid["Precision"] - lgb["Precision"]
        high_recall_delta = hybrid["HighRisk-Recall"] - lgb["HighRisk-Recall"]
        cost_delta = hybrid["Security-Cost"] - lgb["Security-Cost"]
        insights.append(
            "专家规则融合相对纯 LightGBM 的召回率变化 "
            f"{recall_delta:+.2%}，精确率变化 {precision_delta:+.2%}，体现了少漏报与少误报之间的取舍。"
        )
        insights.append(
            "从安全业务指标看，专家规则融合相对纯 LightGBM 的高危召回率变化 "
            f"{high_recall_delta:+.2%}，安全代价变化 {cost_delta:+.0f}；该指标用于说明专家规则对关键风险场景的兜底价值。"
        )

    return {
        "best_f1_model": best_f1["Model"],
        "best_recall_model": best_recall["Model"],
        "best_precision_model": best_precision["Model"],
        "best_high_risk_recall_model": best_high_risk_recall["Model"],
        "best_security_score_model": best_security_score["Model"],
        "positive_ratio": float(positive_ratio),
        "insights": insights,
        "metric_notes": {
            "Accuracy": "整体判断正确比例；在异常样本较少时可能偏乐观。",
            "Precision": "报出的告警中真正异常的比例；越高代表误报越少。",
            "Recall": "真实异常中被系统抓到的比例；越高代表漏报越少。",
            "F1-Score": "精确率和召回率的调和平均，适合综合比较。",
            "AUC": "模型区分正常与异常的整体能力。",
            "PR-AUC": "异常样本占比较低时更关键的告警质量指标。",
            "HighRisk-Recall": "高危业务场景中的异常召回率，越高代表关键风险越不容易漏报。",
            "Security-Score": "安全代价评分，对高危漏报赋予更高权重；越高代表业务安全收益越好。",
            "Security-Cost": "安全代价，按 5*高危漏报 + 3*普通漏报 + 1*误报计算；越低越好。",
            "Latency(ms)": "单条样本平均推理耗时，越低越利于实时监控。",
        },
    }


def main():
    print("开始执行基准模型对比实验...")
    
    # 1. 加载特征数据
    feature_path = DATA_DIR / "train_features.parquet"
    if not feature_path.exists():
        print("找不到特征数据，请先运行特征工程脚本！")
        return
        
    df = pd.read_parquet(feature_path)
    X_train, X_test, y_train, y_test = time_split(df, test_size=0.2)
    
    # 计算类别权重
    neg_count = (y_train == 0).sum()
    pos_count = (y_train == 1).sum()
    spw = neg_count / pos_count

    # 用来存结果的字典
    results = []
    high_risk_mask = high_risk_scene_mask(X_test)

    # ================= 1. 逻辑回归 (Logistic Regression) =================
    print("正在训练 Logistic Regression...")
    lr = LogisticRegression(class_weight='balanced', max_iter=1000, random_state=42)
    lr.fit(X_train, y_train)
    t0 = time.perf_counter()
    lr_preds = lr.predict(X_test)
    lr_probs = lr.predict_proba(X_test)[:, 1]
    lr_latency = (time.perf_counter() - t0) * 1000 / max(len(X_test), 1)
    
    results.append(evaluate_model("Logistic Regression", y_test, lr_preds, lr_probs, lr_latency, high_risk_mask))

    # ================= 2. 孤立森林 (Isolation Forest) =================
    print("正在训练 Isolation Forest...")
    # 孤立森林是无监督异常检测，我们假设污染率大约是我们注入的 12%
    iforest = IsolationForest(contamination=0.12, random_state=42, n_jobs=-1)
    iforest.fit(X_train)
    # iForest 输出 -1 为异常，1 为正常，需要转换成我们的 1 为异常，0 为正常
    t0 = time.perf_counter()
    if_preds = iforest.predict(X_test)
    if_preds = np.where(if_preds == -1, 1, 0)
    # iForest 的 decision_function 越小越异常，我们取个反方便算 AUC
    if_scores = -iforest.decision_function(X_test)
    if_latency = (time.perf_counter() - t0) * 1000 / max(len(X_test), 1)
    
    results.append(evaluate_model("Isolation Forest", y_test, if_preds, if_scores, if_latency, high_risk_mask))

    # ================= 3. LightGBM (咱们的主力模型) =================
    print("正在训练 LightGBM...")
    lgbm = lgb.LGBMClassifier(objective='binary', scale_pos_weight=spw, random_state=42, n_jobs=-1, verbose=-1)
    cat_features = [c for c in ["role_code", "ip_location_code", "operation_type_code"] if c in X_train.columns]
    X_train_lgb = X_train.copy()
    X_test_lgb = X_test.copy()
    for c in cat_features:
        X_train_lgb[c] = X_train_lgb[c].astype("category")
        X_test_lgb[c] = X_test_lgb[c].astype("category")
    lgbm.fit(X_train_lgb, y_train, categorical_feature=cat_features)
    t0 = time.perf_counter()
    lgb_preds = lgbm.predict(X_test_lgb)
    lgb_probs = lgbm.predict_proba(X_test_lgb)[:, 1]
    lgb_latency = (time.perf_counter() - t0) * 1000 / max(len(X_test), 1)
    
    results.append(evaluate_model("LightGBM", y_test, lgb_preds, lgb_probs, lgb_latency, high_risk_mask))

    # ================= 4. LightGBM + 专家规则融合 =================
    print("正在评估 LightGBM + Expert Rules...")
    hybrid_probs = expert_rule_hybrid_adjust(X_test, lgb_probs)
    hybrid_preds = (hybrid_probs >= 0.5).astype(int)
    results.append(evaluate_model("LightGBM + Expert Rules", y_test, hybrid_preds, hybrid_probs, lgb_latency, high_risk_mask))

    positive_ratio = float(y_test.mean())
    summary = build_experiment_summary(results, positive_ratio)

    fpr, tpr, _ = roc_curve(y_test, hybrid_probs)
    precision_curve, recall_curve, _ = precision_recall_curve(y_test, hybrid_probs)
    out_payload = {
        "split": "time",
        "test_size": 0.2,
        "test_count": int(len(y_test)),
        "positive_count": int((y_test == 1).sum()),
        "negative_count": int((y_test == 0).sum()),
        "high_risk_scene_count": int(high_risk_mask.sum()),
        "high_risk_positive_count": int(((y_test == 1) & high_risk_mask).sum()),
        "positive_ratio": positive_ratio,
        "summary": summary,
        "insights": summary["insights"],
        "metric_notes": summary["metric_notes"],
        "model_compare": results,
        "roc_curve": compact_curve(fpr, tpr),
        "pr_curve": compact_curve(recall_curve, precision_curve),
        "rule_ablation": [
            {
                "name": "LightGBM",
                "f1": results[2]["F1-Score"],
                "recall": results[2]["Recall"],
                "precision": results[2]["Precision"],
                "auc": results[2]["AUC"],
                "high_risk_recall": results[2]["HighRisk-Recall"],
                "security_score": results[2]["Security-Score"],
                "security_cost": results[2]["Security-Cost"],
            },
            {
                "name": "LightGBM + Expert Rules",
                "f1": results[3]["F1-Score"],
                "recall": results[3]["Recall"],
                "precision": results[3]["Precision"],
                "auc": results[3]["AUC"],
                "high_risk_recall": results[3]["HighRisk-Recall"],
                "security_score": results[3]["Security-Score"],
                "security_cost": results[3]["Security-Cost"],
            },
        ],
        "feature_ablation": [
            {"name": "类别特征", "status": "planned"},
            {"name": "类别 + 时间", "status": "planned"},
            {"name": "类别 + 时间 + 滑窗", "status": "planned"},
            {"name": "全量增强特征", "status": "current"},
        ],
    }
    out_path = DATA_DIR / "experiment_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_payload, f, ensure_ascii=False, indent=2)

    # ================= 打印对比结果表格 =================
    print("\n" + "="*60)
    print("毕业设计基准模型实验性能对比表")
    print("="*60)
    print(f"{'模型名称':<25} | {'精确率':<8} | {'召回率':<8} | {'F1':<8} | {'AUC':<8} | {'PR-AUC':<8} | {'高危召回':<8} | {'安全分':<8} | {'安全代价':<8} | {'延迟(ms)':<8}")
    print("-" * 60)
    for res in results:
        print(f"{res['Model']:<25} | {res['Precision']:.4f}   | {res['Recall']:.4f}   | {res['F1-Score']:.4f}   | {res['AUC']:.4f}   | {res['PR-AUC']:.4f}   | {res['HighRisk-Recall']:.4f}   | {res['Security-Score']:.4f}   | {res['Security-Cost']:.0f}       | {res['Latency(ms)']:.4f}")
    print("="*60)
    print(f"实验结果已保存：{out_path}")
    print("注：可将该表格和 JSON 数据用于论文『实验分析』章节与前端展示。")

if __name__ == "__main__":
    main()
