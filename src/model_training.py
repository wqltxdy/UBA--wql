# =============================================================================
# 文件名称：model_training.py
# 课题名称：基于用户行为数据评估的企业信息数据安全防护系统
# 作者：吴奇龙 (学号: 112204260152)
# 模块：混合风险评估引擎 (LightGBM + 专家规则 + SHAP)
# 功能：训练行为基线模型，输出评估指标并保存模型结构
# =============================================================================

import os
import json
import joblib
import numpy as np
import pandas as pd

import lightgbm as lgb

from sklearn.metrics import classification_report, roc_auc_score, f1_score

from paths import DATA_DIR, MODELS_DIR, PROJECT_ROOT, ensure_data_dir, ensure_models_dir


def expert_rule_hybrid_adjust(X: pd.DataFrame, base_probs: np.ndarray) -> np.ndarray:
    """
    专家规则硬性补丁 (用于混合评估机制)
    输入：
      - X: 测试集特征 (包含 is_night/is_weekend/op_count_1h/file_sensitive_level)
      - base_probs: LightGBM 基础风险概率
    输出：
      - final_probs: 规则调整后的最终风险概率
    """
    final_probs = base_probs.astype(float).copy()

    def col(name: str, default: float = 0.0) -> pd.Series:
        if name in X.columns:
            return pd.to_numeric(X[name], errors="coerce").fillna(default)
        return pd.Series(default, index=X.index)

    # 规则 1：深夜 (is_night=1) 且 高敏感操作 (file_sensitive_level 较高) 风险直接飙升
    mask_night_sensitive = (col("is_night") >= 0.5) & (col("file_sensitive_level") >= 0.6)
    final_probs[mask_night_sensitive] = np.minimum(final_probs[mask_night_sensitive] + 0.4, 1.0)

    # 规则 2：周末 (is_weekend=1) 且 1小时内操作极度频繁 (归一化后 > 0.8)
    mask_weekend_freq = (col("is_weekend") >= 0.5) & (col("op_count_1h") >= 0.8)
    final_probs[mask_weekend_freq] = np.minimum(final_probs[mask_weekend_freq] + 0.3, 1.0)

    # 规则 3：远程/未知位置叠加登录失败，重点覆盖暴力破解与账号盗用早期迹象
    mask_remote_login_fail = (col("is_remote_or_unknown_ip") >= 0.5) & (col("login_fail_count_1h") >= 0.4)
    final_probs[mask_remote_login_fail] = np.minimum(final_probs[mask_remote_login_fail] + 0.25, 1.0)

    # 规则 4：角色与操作明显不匹配叠加高敏资源，重点覆盖越权访问类风险
    mask_role_mismatch_sensitive = (col("role_operation_mismatch") >= 0.5) & (col("file_sensitive_level") >= 0.6)
    final_probs[mask_role_mismatch_sensitive] = np.minimum(final_probs[mask_role_mismatch_sensitive] + 0.25, 1.0)

    return final_probs


def main() -> None:
    print("开始训练混合风险评估引擎...")

    # 1. 读取特征数据（由 feature_engineering.py 写入 data/）
    ensure_data_dir()
    feature_path = DATA_DIR / "train_features.parquet"
    if not os.path.exists(feature_path):
        print(f"找不到 {feature_path}，请先运行 feature_engineering.py！")
        return

    df = pd.read_parquet(feature_path)

    # 2. 准备训练数据 (剔除不需要入模的列)
    #    train_features.parquet 中通常包含：user_id, timestamp, role_code, ip_location_code, operation_type_code, ... , label
    drop_cols = ["user_id", "timestamp", "label"]
    for c in drop_cols:
        if c not in df.columns:
            raise KeyError(f"train_features.parquet 缺少列：{c}，当前列为：{list(df.columns)}")

    X_all = df.drop(columns=drop_cols)
    y_all = df["label"].astype(int)

    # 3. 按时间做更严谨的切分（默认）
    #    说明：你的特征里有 rolling('1H')，随机切分可能导致评估偏乐观。
    #    如果你后续论文必须“8:2 随机”，把 USE_TIME_SPLIT 改为 False 即可。
    USE_TIME_SPLIT = True
    TEST_SIZE = 0.2

    if USE_TIME_SPLIT:
        df_tmp = df.copy()
        df_tmp = df_tmp.sort_values("timestamp").reset_index(drop=True)
        split_idx = int(len(df_tmp) * (1 - TEST_SIZE))
        train_df = df_tmp.iloc[:split_idx]
        test_df = df_tmp.iloc[split_idx:]

        X_train = train_df.drop(columns=drop_cols)
        y_train = train_df["label"].astype(int)

        X_test = test_df.drop(columns=drop_cols)
        y_test = test_df["label"].astype(int)
    else:
        # 退回随机划分（保留类别比例）
        from sklearn.model_selection import train_test_split

        X_train, X_test, y_train, y_test = train_test_split(
            X_all, y_all, test_size=TEST_SIZE, random_state=42, stratify=y_all
        )

    # 4. 类别特征设置
    cat_features = ["role_code", "ip_location_code", "operation_type_code"]
    for c in cat_features:
        if c not in X_train.columns:
            raise KeyError(f"训练特征缺少类别列：{c}")

    # 给类别特征显式转 category dtype（更稳）
    for c in cat_features:
        X_train[c] = X_train[c].astype("category")
        X_test[c] = X_test[c].astype("category")

    # 5. 计算正负样本权重 (类别不平衡)
    neg_count = int((y_train == 0).sum())
    pos_count = int((y_train == 1).sum())
    if pos_count == 0:
        raise ValueError("训练集中正样本为 0，无法计算 scale_pos_weight。请检查数据或切分方式。")

    spw = neg_count / pos_count
    print(f"当前训练集正负样本比计算完毕，scale_pos_weight 设定为: {spw:.4f}")

    # 6. 配置并训练 LightGBM
    clf = lgb.LGBMClassifier(
        objective="binary",
        learning_rate=0.05,
        n_estimators=2000,     # 给 early stopping 更充分空间
        num_leaves=31,
        scale_pos_weight=spw,
        random_state=42,
        n_jobs=-1,
        verbosity=-1,
    )

    print("模型开始拟合，启用 Early Stopping (stopping_rounds=50)...")
    clf.fit(
        X_train, y_train,
        eval_set=[(X_test, y_test)],
        eval_metric="auc",
        categorical_feature=cat_features,
        callbacks=[
            lgb.early_stopping(stopping_rounds=50),
            lgb.log_evaluation(period=50),
        ],
    )

    # 7. 预测 + 专家规则混合
    print("\n正在进行混合风险评估与指标计算...")
    lgb_probs = clf.predict_proba(X_test)[:, 1]
    final_probs = expert_rule_hybrid_adjust(X_test, lgb_probs)

    y_pred_hybrid = (final_probs >= 0.5).astype(int)

    # 8. 打印评估指标
    auc_score = roc_auc_score(y_test, final_probs)
    f1 = f1_score(y_test, y_pred_hybrid)

    print("-" * 60)
    print("【测试集评估结果】")
    print(f"AUC 得分: {auc_score:.4f} (论文目标 >= 0.90)")
    print(f"F1-Score: {f1:.4f} (论文目标 >= 0.85)")
    print("\n详细分类报告:")
    print(classification_report(y_test, y_pred_hybrid, digits=4))
    print("-" * 60)

    # 9. 计算 SHAP 值（可解释性核心）
    print("正在计算 SHAP 值，请稍候（可能耗时较长）...")
    try:
        import shap
        import matplotlib.pyplot as plt

        explainer = shap.TreeExplainer(clf)

        # 取样本，避免数据过大
        sample_X = X_test.head(min(1000, len(X_test)))
        shap_values = explainer.shap_values(sample_X)

        # --- 兼容新版 SHAP 输出 ---
        if isinstance(shap_values, list):
            # 二分类模型，取正类的 SHAP 值
            shap_values_to_use = shap_values[1]
        else:
            shap_values_to_use = shap_values

        # 计算每个特征的平均绝对 SHAP
        mean_abs = np.abs(shap_values_to_use).mean(axis=0)
        feature_names_shap = list(sample_X.columns)

        # 输出 Top 10 特征
        top_idx = np.argsort(-mean_abs)[:10]
        print("SHAP 特征重要性（Top 10）:")
        for i in top_idx:
            print(f"  - {feature_names_shap[i]}: {mean_abs[i]:.6f}")

        # --- 可视化 SHAP summary plot ---
        print("绘制 SHAP summary plot ...")
        shap.summary_plot(shap_values_to_use, sample_X, feature_names=feature_names_shap)

    except Exception as e:
        print(f"[提示] SHAP 计算或绘图失败但不影响模型保存：{e}")

    # 10. 保存模型与必要文件
    ensure_models_dir()

    joblib.dump(clf, MODELS_DIR / "lgbm_uba_model.pkl")

    # 保存训练用特征列顺序（部署/推理时必须一致）
    feature_names = list(X_train.columns)
    joblib.dump(feature_names, MODELS_DIR / "feature_names.pkl")

    # 保存实验配置（便于论文复现）
    meta = {
        "feature_path": str(feature_path.relative_to(PROJECT_ROOT)),
        "use_time_split": USE_TIME_SPLIT,
        "test_size": TEST_SIZE,
        "cat_features": cat_features,
        "scale_pos_weight": spw,
        "threshold": 0.5,
        "expert_rules": {
            "night_sensitive_add": 0.4,
            "night_sensitive_conditions": {
                "is_night>=0.5": True,
                "file_sensitive_level>=0.6": True,
            },
            "weekend_freq_add": 0.3,
            "weekend_freq_conditions": {
                "is_weekend>=0.5": True,
                "op_count_1h>=0.8": True,
            },
            "remote_login_fail_add": 0.25,
            "remote_login_fail_conditions": {
                "is_remote_or_unknown_ip>=0.5": True,
                "login_fail_count_1h>=0.4": True,
            },
            "role_mismatch_sensitive_add": 0.25,
            "role_mismatch_sensitive_conditions": {
                "role_operation_mismatch>=0.5": True,
                "file_sensitive_level>=0.6": True,
            },
        },
    }
    with open(MODELS_DIR / "train_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"模型已保存至 {MODELS_DIR / 'lgbm_uba_model.pkl'}")
    print("准备工作全部就绪，下一步可以写 Flask 接口了！")


if __name__ == "__main__":
    main()
