# =============================================================================
# 文件名称：feature_engineering.py
# 课题名称：基于用户行为数据评估的企业信息数据安全防护系统
# 作者：吴奇龙 (学号: 112204260152)
# 模块：特征工程管道 (Feature Engineering Pipeline)
# 功能：从原始日志提取时间维特征、滑窗统计特征，并进行类别编码与归一化
# =============================================================================

import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler, LabelEncoder
import os

from paths import DATA_DIR, ensure_data_dir


def _operation_entropy(values):
    counts = pd.Series(values).value_counts(normalize=True)
    if counts.empty:
        return 0.0
    return float(-(counts * np.log2(counts)).sum())


def main():
    print("开始执行自动化特征工程管道...")
    
    # 1. 加载数据（由 simulation.py 写入 data/）
    parquet_path = DATA_DIR / "user_behavior_logs.parquet"
    if not os.path.exists(parquet_path):
        print(f"找不到 {parquet_path}，请先运行 simulation.py（会生成 data/user_behavior_logs.parquet）！")
        return
        
    df = pd.read_parquet(parquet_path)
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # 【关键修复】按用户和时间强行排序，为后续的时间滑窗计算打好基础
    df = df.sort_values(by=['user_id', 'timestamp']).reset_index(drop=True)

    print("1/5: 正在提取时间周期特征...")
    df['hour'] = df['timestamp'].dt.hour
    df['dayofweek'] = df['timestamp'].dt.dayofweek
    df['is_weekend'] = df['dayofweek'].apply(lambda x: 1 if x >= 5 else 0)
    # 晚上20点到次日早上8点算作深夜异常高发期
    df['is_night'] = df['hour'].apply(lambda x: 1 if x >= 20 or x < 8 else 0)
    df['is_after_work'] = df['hour'].apply(lambda x: 1 if x >= 20 or x < 8 else 0)

    print("2/5: 正在构造安全语义特征...")
    remote_pattern = r"境外|异地|公共WiFi|未知|扫描|远程办公|内网跳板机"
    df['is_remote_or_unknown_ip'] = df['ip_location'].astype(str).str.contains(remote_pattern, regex=True).astype(int)
    df['is_login_failure'] = df['operation_type'].astype(str).str.contains("登录失败|暴力", regex=True).astype(int)
    df['is_sensitive_download'] = (
        (df['file_sensitive_level'] >= 2)
        & df['operation_type'].astype(str).str.contains("下载|导出|批量", regex=True)
    ).astype(int)

    allowed_ops = {
        "普通员工": {"登录", "文件浏览", "文件下载", "文件上传", "邮件收发", "即时通讯"},
        "财务人员": {"登录", "文件浏览", "文件下载", "文件上传", "财务报表导出", "数据库查询", "邮件收发"},
        "技术人员": {"登录", "文件浏览", "文件下载", "SSH连接", "脚本执行", "代码仓库拉取", "容器部署", "日志查看"},
        "管理员": {"登录", "文件浏览", "用户管理", "权限变更", "系统配置", "备份任务", "安全审计查看", "数据库查询"},
    }
    df['role_operation_mismatch'] = df.apply(
        lambda r: 0 if str(r['operation_type']) in allowed_ops.get(str(r['role']), set()) else 1,
        axis=1,
    )

    print("3/5: 正在计算用户行为滑窗基线 (Rolling Window)...")
    # 为了使用 rolling，把 timestamp 设置为索引
    df = df.set_index('timestamp')
    
    # 过去 1 小时内该用户的总操作次数
    df['op_count_1h'] = df.groupby('user_id')['operation_type'].transform(
        lambda x: x.rolling('1h').count()
    ).fillna(0)
    
    # 过去 1 小时内该用户的高敏感操作(>=2)次数
    df['is_high_sensitive'] = (df['file_sensitive_level'] >= 2).astype(int)
    df['sensitive_op_count_1h'] = df.groupby('user_id')['is_high_sensitive'].transform(
        lambda x: x.rolling('1h').sum()
    ).fillna(0)

    # 过去 1 小时内登录失败次数、敏感下载/导出次数
    df['login_fail_count_1h'] = df.groupby('user_id')['is_login_failure'].transform(
        lambda x: x.rolling('1h').sum()
    ).fillna(0)
    df['sensitive_download_count_1h'] = df.groupby('user_id')['is_sensitive_download'].transform(
        lambda x: x.rolling('1h').sum()
    ).fillna(0)
    
    df = df.reset_index()

    print("4/5: 正在对类别字段进行 Label Encoding...")
    # LightGBM 原生支持整数型类别特征，不需要 One-Hot，省内存
    cat_cols = ['role', 'ip_location', 'operation_type']
    le_dict = {}
    for col in cat_cols:
        le = LabelEncoder()
        df[f'{col}_code'] = le.fit_transform(df[col])
        le_dict[col] = le

    # 过去 1 小时内不同操作类型数量与操作熵，用于捕捉扫描式/横向探索行为
    df = df.set_index('timestamp')
    df['distinct_operation_count_1h'] = df.groupby('user_id')['operation_type_code'].transform(
        lambda x: x.rolling('1h').apply(lambda s: len(set(s)), raw=False)
    ).fillna(0)
    df['operation_entropy_1h'] = df.groupby('user_id')['operation_type_code'].transform(
        lambda x: x.rolling('1h').apply(_operation_entropy, raw=False)
    ).fillna(0)
    df = df.reset_index()

    print("5/5: 正在计算用户基线偏离并归一化...")
    # 使用历史展开均值的 shift 版本，避免当前样本泄漏到自己的基线中
    df['user_file_sensitive_mean_hist'] = df.groupby('user_id')['file_sensitive_level'].transform(
        lambda x: x.shift().expanding().mean()
    )
    df['user_op_count_mean_hist'] = df.groupby('user_id')['op_count_1h'].transform(
        lambda x: x.shift().expanding().mean()
    )
    df['user_file_sensitive_mean_hist'] = df['user_file_sensitive_mean_hist'].fillna(df.groupby('user_id')['file_sensitive_level'].transform('mean'))
    df['user_op_count_mean_hist'] = df['user_op_count_mean_hist'].fillna(df.groupby('user_id')['op_count_1h'].transform('mean'))
    df['sensitive_level_delta_user'] = (df['file_sensitive_level'] - df['user_file_sensitive_mean_hist']).clip(lower=0)
    df['op_count_delta_user'] = (df['op_count_1h'] - df['user_op_count_mean_hist']).clip(lower=0)

    num_cols = [
        'hour', 'dayofweek', 'file_sensitive_level',
        'op_count_1h', 'sensitive_op_count_1h',
        'login_fail_count_1h', 'sensitive_download_count_1h',
        'distinct_operation_count_1h', 'operation_entropy_1h',
        'sensitive_level_delta_user', 'op_count_delta_user',
    ]
    scaler = MinMaxScaler()
    df[num_cols] = scaler.fit_transform(df[num_cols])

    # 提取并整理最终丢给模型的特征列
    feature_cols = [
        'user_id', 'timestamp', # 这两列保留用于后期 Web 端溯源展示，不进模型
        'role_code', 'ip_location_code', 'operation_type_code', # 类别特征
        'hour', 'dayofweek', 'is_weekend', 'is_night',          # 时间特征
        'is_after_work',
        'file_sensitive_level', 'op_count_1h', 'sensitive_op_count_1h', # 行为特征
        'login_fail_count_1h', 'sensitive_download_count_1h',
        'distinct_operation_count_1h', 'operation_entropy_1h',
        'sensitive_level_delta_user', 'op_count_delta_user',
        'is_remote_or_unknown_ip', 'role_operation_mismatch',
        'label' # 目标值
    ]
    
    final_df = df[feature_cols].copy()
    
    # 恢复成整体按时间排列，模拟现实中日志滚滚而来的真实顺序
    final_df = final_df.sort_values('timestamp').reset_index(drop=True)

    # 保存特征文件供 LightGBM 读取
    ensure_data_dir()
    out_file = DATA_DIR / "train_features.parquet"
    final_df.to_parquet(out_file, index=False)
    
    print("-" * 50)
    print("特征工程全部完成！")
    print(f"数据量: {len(final_df)} 条")
    print("前5条特征数据预览:")
    print(final_df.head())
    print("-" * 50)

if __name__ == "__main__":
    main()
