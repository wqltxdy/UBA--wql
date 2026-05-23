#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
项目：基于用户行为数据评估的企业信息数据安全防护系统
模块：高保真用户行为日志仿真器（User Behavior Log Simulator）

功能概述：
  - 按角色生成企业用户行为基线日志；
  - 系统性注入 Almohaimeed 等研究中的典型安全间隙行为样本，用于异常检测实验。

参考文献方向：企业异常用户行为 / 内部威胁相关安全间隙建模（含异地登录、
异常时段敏感操作、越权、横向移动、认证暴力尝试等模式）。

作者：吴奇龙（毕业设计）
日期：2026
================================================================================
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from paths import DATA_DIR, ensure_data_dir

# --------------------------------------------------------------------------- #
# 可复现性
# --------------------------------------------------------------------------- #
np.random.seed(42)
random.seed(42)

# --------------------------------------------------------------------------- #
# 全局参数
# --------------------------------------------------------------------------- #
NUM_SAMPLES = 100_000
# 异常比例控制在约 10% ~ 15%（此处取 12%）
ANOMALY_RATIO = 0.12
RNG = np.random.default_rng(42)

# 时间范围：2026-01-01 起约 3 个月
START_DATE = datetime(2026, 1, 1, 0, 0, 0)
END_DATE = datetime(2026, 4, 1, 0, 0, 0)  # 右开区间上限


# --------------------------------------------------------------------------- #
# 角色与业务基线配置
# --------------------------------------------------------------------------- #
ROLES = ["普通员工", "财务人员", "技术人员", "管理员"]

# 各角色在全体用户中的占比（用于生成 user 池规模，略反映真实结构）
ROLE_USER_COUNTS = {
    "普通员工": 120,
    "财务人员": 25,
    "技术人员": 40,
    "管理员": 15,
}

# 角色前缀（user_id）
ROLE_PREFIX = {
    "普通员工": "EMP",
    "财务人员": "FIN",
    "技术人员": "TEC",
    "管理员": "ADM",
}

# 各角色“日常可解释”的操作权重（归一化前）
ROLE_OPERATION_WEIGHTS = {
    "普通员工": {
        "登录": 22,
        "文件浏览": 35,
        "文件下载": 12,
        "文件上传": 10,
        "邮件收发": 15,
        "即时通讯": 6,
    },
    "财务人员": {
        "登录": 15,
        "文件浏览": 18,
        "文件下载": 20,
        "文件上传": 12,
        "财务报表导出": 15,
        "数据库查询": 12,
        "邮件收发": 8,
    },
    "技术人员": {
        "登录": 12,
        "文件浏览": 10,
        "文件下载": 14,
        "SSH连接": 22,
        "脚本执行": 12,
        "代码仓库拉取": 10,
        "容器部署": 8,
        "日志查看": 12,
    },
    "管理员": {
        "登录": 10,
        "文件浏览": 8,
        "用户管理": 18,
        "权限变更": 15,
        "系统配置": 18,
        "备份任务": 10,
        "安全审计查看": 12,
        "数据库查询": 9,
    },
}

# 各角色日常访问文件的敏感级别权重（0公开 1内部 2机密 3绝密）— 正常业务基线
ROLE_SENSITIVE_WEIGHTS = {
    "普通员工": [0.45, 0.40, 0.13, 0.02],
    "财务人员": [0.10, 0.35, 0.40, 0.15],
    "技术人员": [0.20, 0.45, 0.30, 0.05],
    "管理员": [0.05, 0.25, 0.45, 0.25],
}

# 各角色常规登录地理位置（正常办公以总部为主）
ROLE_HOME_LOCATIONS = {
    "普通员工": ["公司总部-北京", "分公司-上海", "办事处-深圳"],
    "财务人员": ["公司总部-北京", "财务中心-杭州"],
    "技术人员": ["研发中心-北京", "机房接入区-北京", "分公司-上海"],
    "管理员": ["公司总部-北京", "运维中心-北京"],
}

# 正常行为：工作日 8:00-20:00 为主；周末压低活跃度
NORMAL_WEEKDAY_START_HOUR = 8
NORMAL_WEEKDAY_END_HOUR = 20
WEEKEND_ACTIVITY_FACTOR = 0.22  # 周末相对工作日的活跃度缩放


def _normalize_weights(w: dict) -> tuple[list[str], np.ndarray]:
    keys = list(w.keys())
    vals = np.array([w[k] for k in keys], dtype=float)
    vals = vals / vals.sum()
    return keys, vals


def build_user_pools() -> dict[str, list[str]]:
    """为每个角色生成 user_id 列表。"""
    pools: dict[str, list[str]] = {}
    for role in ROLES:
        n = ROLE_USER_COUNTS[role]
        prefix = ROLE_PREFIX[role]
        pools[role] = [f"{prefix}{i:04d}" for i in range(1, n + 1)]
    return pools


def random_timestamp_normal() -> datetime:
    """生成偏正常办公时段的时间戳（含周末抑制）。"""
    total_seconds = int((END_DATE - START_DATE).total_seconds())
    # 最多尝试若干次以落在一个“工作日高概率”的时刻
    for _ in range(30):
        secs = int(RNG.integers(0, total_seconds))
        dt = START_DATE + timedelta(seconds=secs)
        wd = dt.weekday()  # 0=周一
        is_weekend = wd >= 5
        if is_weekend and RNG.random() > WEEKEND_ACTIVITY_FACTOR:
            continue
        # 小时分布：正常集中在 8-20 点
        h = dt.hour
        if is_weekend:
            # 周末若命中，则时间更集中在 10-18
            if RNG.random() < 0.75 and not (10 <= h <= 18):
                continue
        else:
            if not (NORMAL_WEEKDAY_START_HOUR <= h < NORMAL_WEEKDAY_END_HOUR):
                if RNG.random() < 0.88:
                    continue
        return dt
    # 兜底
    secs = int(RNG.integers(0, total_seconds))
    return START_DATE + timedelta(seconds=secs)


def random_timestamp_anomaly_night() -> datetime:
    """异常：深夜时段（20:00-次日08:00）。"""
    total_seconds = int((END_DATE - START_DATE).total_seconds())
    for _ in range(50):
        secs = int(RNG.integers(0, total_seconds))
        dt = START_DATE + timedelta(seconds=secs)
        h = dt.hour
        if (h >= 20) or (h < 8):
            return dt
    return START_DATE + timedelta(hours=22)


def random_timestamp_any() -> datetime:
    total_seconds = int((END_DATE - START_DATE).total_seconds())
    secs = int(RNG.integers(0, total_seconds))
    return START_DATE + timedelta(seconds=secs)


def sample_role_by_user_distribution(users_flat: list[tuple[str, str]]) -> tuple[str, str]:
    """按用户池均匀选一个 (role, user_id)。"""
    role, uid = users_flat[int(RNG.integers(0, len(users_flat)))]
    return role, uid


def sample_sensitive_level(role: str) -> int:
    probs = np.array(ROLE_SENSITIVE_WEIGHTS[role], dtype=float)
    probs = probs / probs.sum()
    return int(RNG.choice(4, p=probs))


def sample_operation(role: str) -> str:
    w = ROLE_OPERATION_WEIGHTS[role]
    keys, p = _normalize_weights(w)
    return str(RNG.choice(keys, p=p))


def sample_ip_normal(role: str) -> str:
    locs = ROLE_HOME_LOCATIONS[role]
    return str(RNG.choice(locs))


# --------------------------------------------------------------------------- #
# 五类安全间隙注入（对应需求中的典型模式）
# --------------------------------------------------------------------------- #
ANOMALY_TYPES = [
    "异地登录",
    "深夜批量下载敏感文件",
    "权限越权访问",
    "横向移动_SSH",
    "暴力破解尝试",
]


def inject_anomaly_record(
    anomaly_type: str,
    users_flat: list[tuple[str, str]],
) -> dict:
    """
    根据异常类型生成单条日志。
    说明：同一类型在具体字段上保持可区分模式，便于后续规则/模型学习。
    """
    role, user_id = sample_role_by_user_distribution(users_flat)

    if anomaly_type == "异地登录":
        # 1) 异地登录：时间与部分样本可为工作时段，但 IP 与角色常规驻地明显不一致
        if RNG.random() < 0.55:
            ts = random_timestamp_normal()
        else:
            ts = random_timestamp_any()
        suspicious_remote = [
            "境外-美国加利福尼亚",
            "境外-东南亚某国",
            "异地-新疆乌鲁木齐",
            "异地-黑龙江哈尔滨",
            "异地-云南昆明",
            "公共WiFi-未知运营商",
        ]
        ip = str(RNG.choice(suspicious_remote))
        op = "登录"
        # 登录动作更常见伴随公开/内部；但异地登录仍标为异常(标签在调用方固定为1)
        sens = int(RNG.choice([0, 1], p=[0.55, 0.45]))

    elif anomaly_type == "深夜批量下载敏感文件":
        # 2) 深夜批量下载敏感文件
        ts = random_timestamp_anomaly_night()
        ip = sample_ip_normal(role) if RNG.random() < 0.35 else "远程办公隧道-未知出口"
        op = "批量导出" if RNG.random() < 0.62 else "文件下载"
        sens = int(RNG.choice([2, 3], p=[0.55, 0.45]))

    elif anomaly_type == "权限越权访问":
        # 3) 越权：较低权限角色尝试管理/高敏资源
        ts = random_timestamp_normal() if RNG.random() < 0.65 else random_timestamp_anomaly_night()
        ip = sample_ip_normal(role)
        if role in ("普通员工", "财务人员"):
            op = str(RNG.choice(["系统配置", "权限变更", "用户管理", "安全审计查看", "文件下载"]))
            sens = int(RNG.choice([2, 3], p=[0.55, 0.45]))
        elif role == "技术人员":
            op = str(RNG.choice(["权限变更", "用户管理", "数据库查询", "批量导出"]))
            sens = int(RNG.choice([2, 3], p=[0.40, 0.60]))
        else:
            # 管理员也可能出现“越权”模式：访问明显不属于其职责域的高敏对象（模拟角色误用/被盗号）
            op = str(RNG.choice(["财务报表导出", "批量导出", "权限变更"]))
            sens = 3

    elif anomaly_type == "横向移动_SSH":
        # 4) 横向移动（SSH）：非技术岗位或非常见源地址发起 SSH，或目的表现为扫描式连接
        ts = random_timestamp_anomaly_night() if RNG.random() < 0.72 else random_timestamp_any()
        if role == "技术人员":
            ip = str(RNG.choice(["境外-美国加利福尼亚", "扫描节点-IDC异常", "分公司-上海"]))
        else:
            ip = str(RNG.choice(["境外-东南亚某国", "内网跳板机-可疑会话", "公共WiFi-未知运营商"]))
        op = "SSH连接"
        sens = int(RNG.choice([1, 2, 3], p=[0.25, 0.45, 0.30]))

    elif anomaly_type == "暴力破解尝试":
        # 5) 暴力破解：认证失败/暴力尝试（短窗口可在后续建模时用聚合特征捕捉）
        ts = random_timestamp_anomaly_night() if RNG.random() < 0.78 else random_timestamp_any()
        ip = str(RNG.choice(["境外-美国加利福尼亚", "境外-东南亚某国", "扫描节点-IDC异常", "公共WiFi-未知运营商"]))
        op = "登录失败_暴力尝试"
        sens = int(RNG.choice([0, 1], p=[0.75, 0.25]))

    else:
        raise ValueError(f"未知异常类型: {anomaly_type}")

    return {
        "user_id": user_id,
        "role": role,
        "timestamp": ts,
        "ip_location": ip,
        "operation_type": op,
        "file_sensitive_level": sens,
        "label": 1,
    }


def generate_normal_record(users_flat: list[tuple[str, str]]) -> dict:
    role, user_id = sample_role_by_user_distribution(users_flat)
    ts = random_timestamp_normal()
    ip = sample_ip_normal(role)
    op = sample_operation(role)
    sens = sample_sensitive_level(role)
    return {
        "user_id": user_id,
        "role": role,
        "timestamp": ts,
        "ip_location": ip,
        "operation_type": op,
        "file_sensitive_level": sens,
        "label": 0,
    }


def main() -> None:
    user_pools = build_user_pools()
    users_flat: list[tuple[str, str]] = []
    for r in ROLES:
        for uid in user_pools[r]:
            users_flat.append((r, uid))

    n_anom = int(round(NUM_SAMPLES * ANOMALY_RATIO))
    n_norm = NUM_SAMPLES - n_anom
    # 将异常预算分配到 5 类（近似均匀，轻微扰动）
    base = n_anom // 5
    remainder = n_anom - base * 5
    counts = [base + (1 if i < remainder else 0) for i in range(5)]

    records: list[dict] = []

    # 正常样本
    for _ in range(n_norm):
        records.append(generate_normal_record(users_flat))

    # 异常样本（系统性覆盖 5 类）
    for k, atype in enumerate(ANOMALY_TYPES):
        for _ in range(counts[k]):
            records.append(inject_anomaly_record(atype, users_flat))

    # 打乱顺序，避免训练时位置偏置
    random.shuffle(records)
    df = pd.DataFrame(records)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["file_sensitive_level"] = df["file_sensitive_level"].astype(int)
    df["label"] = df["label"].astype(int)

    # 统一列顺序
    cols = [
        "user_id",
        "role",
        "timestamp",
        "ip_location",
        "operation_type",
        "file_sensitive_level",
        "label",
    ]
    df = df[cols]

    ensure_data_dir()
    out_csv = DATA_DIR / "user_behavior_logs.csv"
    out_parquet = DATA_DIR / "user_behavior_logs.parquet"
    out_sample = DATA_DIR / "user_behavior_logs_sample.csv"

    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    df.head(1000).to_csv(out_sample, index=False, encoding="utf-8-sig")
    try:
        df.to_parquet(out_parquet, index=False, engine="pyarrow")
    except Exception as e:
        print(f"[提示] Parquet 写出失败（通常需安装 pyarrow）：{e}")
        print("可执行：pip install pyarrow")

    # ------------------------------------------------------------------ #
    # 统计输出
    # ------------------------------------------------------------------ #
    n_total = len(df)
    n_pos = int(df["label"].sum())
    role_counts = df["role"].value_counts().to_dict()
    print("=" * 72)
    print("用户行为仿真日志生成完成（吴奇龙 毕业设计）")
    print("=" * 72)
    print(f"总条数: {n_total}")
    print(f"异常条数(label=1): {n_pos}  ({n_pos / max(n_total, 1):.2%})")
    print(f"正常条数(label=0): {n_total - n_pos}  ({(n_total - n_pos) / max(n_total, 1):.2%})")
    print("\n各角色记录数分布:")
    for r in ROLES:
        print(f"  - {r}: {role_counts.get(r, 0)}")
    print("\n前 5 条数据预览:")
    print(df.head(5).to_string(index=False))
    print("\n输出文件:")
    print(f"  - {out_csv}")
    print(f"  - {out_parquet} (需 pyarrow)")
    print(f"  - {out_sample} (n=1000)")
    print("=" * 72)


if __name__ == "__main__":
    main()
