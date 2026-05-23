# =============================================================================
# 文件名称：app.py
# 课题名称：基于用户行为数据评估的企业信息数据安全防护系统
# 作者：吴奇龙 (学号: 112204260152)
# 模块：Flask 后端 API 接口（第二版 — 新增多标签页前端数据支持）
# 功能：加载模型与SHAP，提供实时风险评估、可解释性数据流、用户聚合看板
# =============================================================================

from __future__ import annotations

import json
import os
import random
import re
import time
import urllib.error
import urllib.request
import uuid
import warnings
from typing import Any

warnings.filterwarnings("ignore", message=".*LightGBM binary classifier with TreeExplainer.*")
warnings.filterwarnings("ignore", message=".*development server.*")

import joblib
import numpy as np
import pandas as pd
import shap
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS
import lightgbm as lgb  # noqa: F401

from paths import DATA_DIR, MODELS_DIR, PROJECT_ROOT, WEB_DIR, ensure_data_dir, ensure_models_dir


def _load_dotenv_local() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return
    try:
        raw = env_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv_local()

_WEB_ROOT = str(WEB_DIR)
_STATIC = os.path.join(_WEB_ROOT, "static")
app = Flask(__name__, static_folder=_STATIC, static_url_path="/static")
CORS(app)

random.seed(42)
np.random.seed(42)
_NEXT_LOG_REQUEST_COUNT = 0
_REALTIME_LLM_TASKS: dict[str, dict[str, Any]] = {}
_REALTIME_LLM_TASK_TTL = 300


CAT_FEATURES = ["role_code", "ip_location_code", "operation_type_code"]


def explain_rule_hits(row_dict: dict[str, Any]) -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []

    if float(row_dict.get("is_night", 0.0)) >= 0.5 and float(row_dict.get("file_sensitive_level", 0.0)) >= 0.6:
        hits.append({
            "id": "R1",
            "name": "深夜高敏操作",
            "reason": "深夜时段叠加较高文件敏感度",
            "adjustment": 0.4,
        })
    if float(row_dict.get("is_weekend", 0.0)) >= 0.5 and float(row_dict.get("op_count_1h", 0.0)) >= 0.8:
        hits.append({
            "id": "R2",
            "name": "周末高频操作",
            "reason": "周末叠加近1小时操作频次异常偏高",
            "adjustment": 0.3,
        })
    if float(row_dict.get("is_remote_or_unknown_ip", 0.0)) >= 0.5 and float(row_dict.get("login_fail_count_1h", 0.0)) >= 0.4:
        hits.append({
            "id": "R3",
            "name": "远程登录失败聚集",
            "reason": "远程/未知网络位置叠加登录失败行为",
            "adjustment": 0.25,
        })
    if float(row_dict.get("role_operation_mismatch", 0.0)) >= 0.5 and float(row_dict.get("file_sensitive_level", 0.0)) >= 0.6:
        hits.append({
            "id": "R4",
            "name": "角色操作不匹配",
            "reason": "用户角色与当前操作不匹配且资源敏感度较高",
            "adjustment": 0.25,
        })

    return hits


def expert_rule_adjust(row_dict: dict[str, Any], base_prob: float) -> float:
    risk_score = float(base_prob)
    for hit in explain_rule_hits(row_dict):
        risk_score += float(hit["adjustment"])
    return float(min(risk_score, 1.0))


def risk_level(score: float) -> str:
    if score >= 0.75:
        return "高风险"
    if score >= 0.5:
        return "中风险"
    return "低风险"


def risk_suggestion(score: float, rule_hits: list[dict[str, Any]]) -> str:
    if score >= 0.75:
        return "建议触发重点审计、二次认证或临时收敛权限，并保留会话审计记录。"
    if score >= 0.5 or rule_hits:
        return "建议进入告警队列，由安全人员结合业务上下文复核。"
    return "建议维持常规监控，并纳入周期性用户行为基线比对。"


def compute_shap_top_causes(
    explainer: Any,
    X_input: pd.DataFrame,
    feature_names: list[str],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    shap_values = explainer.shap_values(X_input)
    if isinstance(shap_values, list):
        shap_vec = shap_values[1][0] if len(shap_values) > 1 else shap_values[0][0]
    else:
        shap_vec = shap_values[0]

    shap_vec = np.asarray(shap_vec)
    if shap_vec.shape[0] != len(feature_names):
        return []

    top_idx = np.argsort(-np.abs(shap_vec))[:top_k]
    out: list[dict[str, Any]] = []
    for i in top_idx:
        out.append({
            "feature": feature_names[i],
            "value": float(X_input.iloc[0][feature_names[i]]),
            "contribution": float(shap_vec[i]),
        })
    return out


FEATURE_CN: dict[str, str] = {
    "role_code": "用户角色",
    "ip_location_code": "登录网络/位置",
    "operation_type_code": "具体操作动作",
    "hour": "发生时段",
    "dayofweek": "星期",
    "is_weekend": "是否周末",
    "is_night": "是否深夜",
    "file_sensitive_level": "文件敏感度",
    "op_count_1h": "近1小时操作频次",
    "sensitive_op_count_1h": "近1小时高敏操作频次",
    "is_after_work": "是否非工作时间",
    "login_fail_count_1h": "近1小时登录失败次数",
    "sensitive_download_count_1h": "近1小时敏感下载次数",
    "distinct_operation_count_1h": "近1小时操作类型数量",
    "operation_entropy_1h": "近1小时操作复杂度",
    "sensitive_level_delta_user": "文件敏感度用户偏离",
    "op_count_delta_user": "操作频次用户偏离",
    "is_remote_or_unknown_ip": "远程/未知网络位置",
    "role_operation_mismatch": "角色操作不匹配",
}


def _json_float(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def build_ai_narrative_template(
    base_prob: float,
    final_risk: float,
    row_dict: dict[str, Any],
    top_causes: list[dict[str, Any]],
) -> dict[str, Any]:
    is_alert = final_risk >= 0.5
    night_sensitive = float(row_dict.get("is_night", 0.0)) >= 0.5 and float(row_dict.get("file_sensitive_level", 0.0)) >= 0.6
    weekend_freq = float(row_dict.get("is_weekend", 0.0)) >= 0.5 and float(row_dict.get("op_count_1h", 0.0)) >= 0.8
    bumped = final_risk > base_prob + 1e-6

    level_cn = "高" if is_alert else "低"
    parts: list[str] = [
        f"本条日志综合风险为 {final_risk:.3f}（模型基础分约 {base_prob:.3f}），系统判定为「{level_cn}危」态势。"
    ]
    bullets: list[str] = []

    if night_sensitive:
        msg = "已命中专家规则：深夜时段叠加较高文件敏感度，风险已上调。"
        parts.append(msg)
        bullets.append(msg)
    if weekend_freq:
        msg = "已命中专家规则：周末叠加近1小时操作频次异常偏高，风险已上调。"
        parts.append(msg)
        bullets.append(msg)
    if bumped and not night_sensitive and not weekend_freq:
        parts.append("综合分相对模型输出有所提升，请结合业务上下文复核。")

    if top_causes:
        shap_lines: list[str] = []
        for c in top_causes[:3]:
            name = FEATURE_CN.get(str(c.get("feature", "")), str(c.get("feature", "")))
            contrib = float(c.get("contribution", 0.0))
            direction = "推高整体风险" if contrib > 0 else "拉低/抑制风险"
            shap_lines.append(f"{name}{direction}（SHAP {contrib:+.3f}）")
        parts.append("SHAP 可解释性要点：" + "；".join(shap_lines) + "。")
        bullets.extend(shap_lines)

    if is_alert:
        parts.append("处置建议：建议触发二次认证或临时收敛权限，并留存该会话审计记录备查。")
    else:
        parts.append("处置建议：可维持常规监控，将本条纳入周期性基线比对。")

    return {
        "title": "智能解读",
        "text": "".join(parts),
        "bullets": bullets[:6],
        "source": "rule_template",
        "hint": "本段由后端根据 LightGBM、专家规则与 SHAP 自动拼装。密钥请放在环境变量或本地 `.env`（勿提交 Git）；配置 OPENAI_API_KEY 后可启用外接大模型。",
    }


def _strip_markdown_json_fence(raw: str) -> str:
    s = raw.strip()
    m = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", s, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return s


def _parse_llm_narrative_json(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    cleaned = _strip_markdown_json_fence(raw)
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict) and isinstance(obj.get("text"), str):
            bullets = obj.get("bullets")
            if bullets is None:
                bullets = []
            if isinstance(bullets, list):
                return {"text": obj["text"].strip(), "bullets": [str(b) for b in bullets if str(b).strip()][:8]}
    except json.JSONDecodeError:
        pass
    return {"text": cleaned.strip(), "bullets": []}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def llm_api_key() -> str:
    key = (
        os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("LLM_API_KEY")
        or ""
    ).strip()
    if not key or "替换" in key or key.upper().startswith(("YOUR_", "SK-XXXX")):
        return ""
    return key


def llm_base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")


def llm_model_name() -> str:
    return os.environ.get("OPENAI_MODEL", "deepseek-chat")


def call_openai_compatible_json(
    system_msg: str,
    llm_context: dict[str, Any],
    *,
    max_tokens: int = 900,
    temperature: float = 0.25,
) -> dict[str, Any] | None:
    if os.environ.get("LLM_DISABLED", "").lower() in ("1", "true", "yes"):
        return None
    api_key = llm_api_key()
    if not api_key:
        return None

    base = llm_base_url()
    url = base + "/chat/completions"
    model = llm_model_name()
    try:
        timeout = float(os.environ.get("OPENAI_TIMEOUT", "25"))
    except ValueError:
        timeout = 25.0

    user_msg = json.dumps(llm_context, ensure_ascii=False)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            detail = str(e)
        print(f"[LLM] HTTP {e.code}: {detail}")
        return None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"[LLM] 请求失败: {e}")
        return None

    try:
        data = json.loads(resp_body)
        raw = str(data["choices"][0]["message"]["content"] or "").strip()
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        print(f"[LLM] 响应解析失败: {e}")
        return None

    cleaned = _strip_markdown_json_fence(raw)
    try:
        obj = json.loads(cleaned)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        print(f"[LLM] JSON 解析失败，已回退本地模板：{cleaned[:200]}")
        return None


def call_openai_compatible_narrative(llm_context: dict[str, Any]) -> str | None:
    system_msg = (
        "你是企业信息安全与用户行为分析（UBA）助手。用户将提供一条 JSON，包含模型打分、专家规则结果与 SHAP 特征贡献。"
        "请严格基于这些数据用简体中文撰写解读，不要编造日志中不存在的字段。"
        "输出必须是单一 JSON 对象，不要 Markdown，格式："
        '{"text":"2~4句连贯总结","bullets":["要点1","要点2","最多6条"]}'
        "。说明：SHAP 解释的是 LightGBM 基础模型部分；最终风险分可能含专家规则加权。"
    )
    obj = call_openai_compatible_json(system_msg, llm_context, max_tokens=_env_int("OPENAI_MAX_TOKENS", 900))
    return json.dumps(obj, ensure_ascii=False) if obj else None


def build_ai_narrative(
    base_prob: float,
    final_risk: float,
    row_dict: dict[str, Any],
    top_causes: list[dict[str, Any]],
    *,
    user_id: str,
    timestamp: str,
    actual_label: int,
    use_llm: bool = True,
) -> dict[str, Any]:
    template = build_ai_narrative_template(base_prob, final_risk, row_dict, top_causes)

    api_key = llm_api_key()
    if not use_llm or not api_key or os.environ.get("LLM_DISABLED", "").lower() in ("1", "true", "yes"):
        return template

    row_safe = {k: _json_float(v) for k, v in row_dict.items()}
    causes_safe: list[dict[str, Any]] = []
    for c in top_causes[:8]:
        causes_safe.append({
            "feature": str(c.get("feature", "")),
            "feature_cn": FEATURE_CN.get(str(c.get("feature", "")), str(c.get("feature", ""))),
            "value": _json_float(c.get("value")),
            "contribution": _json_float(c.get("contribution")),
        })

    llm_context: dict[str, Any] = {
        "user_id": user_id,
        "timestamp": timestamp,
        "actual_label_simulation": actual_label,
        "base_model_score_lightgbm": round(base_prob, 6),
        "final_risk_after_expert_rules": round(final_risk, 6),
        "is_alert_threshold_0_5": final_risk >= 0.5,
        "top_shap_causes": causes_safe,
        "model_feature_values": row_safe,
        "rule_template_summary": template["text"],
    }

    raw = call_openai_compatible_narrative(llm_context)
    if not raw:
        out = dict(template)
        out["hint"] = template["hint"] + " 本次外接大模型不可用或超时，已显示模板解读。"
        out["source"] = "rule_template"
        return out

    parsed = _parse_llm_narrative_json(raw)
    if not parsed or not parsed.get("text"):
        out = dict(template)
        out["hint"] = "大模型返回格式异常，已改用模板解读。"
        return out

    model_name = llm_model_name()
    return {
        "title": "智能解读",
        "text": parsed["text"],
        "bullets": (parsed.get("bullets") or [])[:6],
        "source": "llm_openai_compatible",
        "model": model_name,
        "hint": f"由外接模型「{model_name}」基于结构化结果生成，仅供辅助阅读；处置请以制度与审计为准。SHAP 对应 LightGBM 部分，最终分含专家规则。",
    }


def llm_api_configured() -> bool:
    if os.environ.get("LLM_DISABLED", "").lower() in ("1", "true", "yes"):
        return False
    return bool(llm_api_key())


def should_call_realtime_llm() -> bool:
    """控制实时轮询场景的 LLM 调用频率，避免每 3 秒烧一次 token。"""
    if not llm_api_configured() or not _env_bool("LLM_REALTIME_ENABLED", True):
        return False
    every_n = max(_env_int("LLM_REALTIME_EVERY_N", 3), 1)
    return _NEXT_LOG_REQUEST_COUNT % every_n == 1


def cleanup_realtime_llm_tasks() -> None:
    """清理实时监控异步 LLM 上下文，避免长时间演示时内存积累。"""
    now = time.time()
    expired = [
        task_id for task_id, task in _REALTIME_LLM_TASKS.items()
        if now - float(task.get("created_at", now)) > _REALTIME_LLM_TASK_TTL
    ]
    for task_id in expired:
        _REALTIME_LLM_TASKS.pop(task_id, None)


def create_realtime_llm_task(
    *,
    base_prob: float,
    final_risk_score: float,
    row_dict: dict[str, Any],
    top_causes: list[dict[str, Any]],
    rule_hits: list[dict[str, Any]],
    local_triage: dict[str, Any],
    row_data: pd.Series,
) -> str:
    cleanup_realtime_llm_tasks()
    task_id = uuid.uuid4().hex
    _REALTIME_LLM_TASKS[task_id] = {
        "created_at": time.time(),
        "base_prob": float(base_prob),
        "final_risk_score": float(final_risk_score),
        "row_dict": dict(row_dict),
        "top_causes": list(top_causes),
        "rule_hits": list(rule_hits),
        "local_triage": dict(local_triage),
        "user_id": str(row_data.get("user_id", "")),
        "timestamp": str(row_data.get("timestamp", "")),
        "actual_label": int(row_data["label"]),
    }
    return task_id


# ==================== 第二版新增：用户聚合数据预计算 ====================

OP_CATEGORY_CN = {
    0: "文件浏览", 1: "文件下载", 2: "文件上传",
    3: "数据库查询", 4: "财务报表导出", 5: "文件操作",
    6: "SSH远程", 7: "脚本执行", 8: "容器部署",
    9: "用户管理", 10: "权限变更", 11: "系统配置",
}


def classify_alert_scenario(
    row_dict: dict[str, Any],
    final_risk: float,
    rule_hits: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """基于模型特征和规则命中，给告警补一层安全研判语义。"""
    hits = rule_hits or explain_rule_hits(row_dict)
    hit_ids = {str(h.get("id", "")) for h in hits}

    remote = float(row_dict.get("is_remote_or_unknown_ip", 0.0)) >= 0.5
    login_fail = float(row_dict.get("login_fail_count_1h", 0.0)) >= 0.4
    role_mismatch = float(row_dict.get("role_operation_mismatch", 0.0)) >= 0.5
    sensitive = float(row_dict.get("file_sensitive_level", 0.0)) >= 0.6
    sensitive_download = float(row_dict.get("sensitive_download_count_1h", 0.0)) >= 0.35
    high_freq = float(row_dict.get("op_count_1h", 0.0)) >= 0.65
    operation_entropy = float(row_dict.get("operation_entropy_1h", 0.0)) >= 0.55
    night = float(row_dict.get("is_night", 0.0)) >= 0.5

    scenario = "常规风险观察"
    scenario_id = "watch"
    evidence: list[str] = []
    actions: list[str] = ["持续纳入行为基线监控", "保留本条日志用于后续趋势比对"]
    confidence = 0.45 if final_risk >= 0.5 else 0.25

    if remote and login_fail:
        scenario_id = "account_takeover"
        scenario = "疑似账号盗用或暴力破解"
        evidence.extend(["远程/未知网络位置", "近 1 小时登录失败聚集"])
        actions = ["核查登录来源 IP 与设备指纹", "触发二次认证或密码重置", "检查同账号近期失败登录与成功登录链路"]
        confidence = 0.82
    elif night and sensitive and (sensitive_download or high_freq or "R1" in hit_ids):
        scenario_id = "data_exfiltration"
        scenario = "疑似深夜高敏数据访问"
        evidence.extend(["深夜或非工作时间", "访问较高敏感度资源"])
        if sensitive_download:
            evidence.append("敏感下载/导出行为聚集")
        if high_freq:
            evidence.append("短时间操作频次偏高")
        actions = ["核查文件下载/导出对象和目标路径", "临时收敛高敏资源访问权限", "保留会话审计并通知数据责任人复核"]
        confidence = 0.78
    elif role_mismatch and sensitive:
        scenario_id = "privilege_misuse"
        scenario = "疑似越权访问或权限误用"
        evidence.extend(["角色与操作不匹配", "资源敏感度较高"])
        actions = ["核查用户岗位与权限配置是否匹配", "复核近期权限变更记录", "必要时临时降权并发起审批复核"]
        confidence = 0.76
    elif operation_entropy and high_freq:
        scenario_id = "lateral_probe"
        scenario = "疑似横向探索或扫描式操作"
        evidence.extend(["近 1 小时操作类型复杂度较高", "短时间操作频次偏高"])
        actions = ["检查同会话访问的系统与资源范围", "排查是否存在脚本化批量行为", "关注后续是否出现 SSH、权限变更或批量导出"]
        confidence = 0.68
    elif remote:
        scenario_id = "remote_anomaly"
        scenario = "远程或未知位置访问异常"
        evidence.append("网络位置偏离常规办公位置")
        actions = ["核查登录地与用户实际办公位置", "结合设备、时间和历史登录轨迹复核", "必要时要求二次认证"]
        confidence = 0.58

    for h in hits:
        reason = str(h.get("reason", "")).strip()
        if reason and reason not in evidence:
            evidence.append(reason)

    if final_risk >= 0.75 and "高风险日志优先进入人工复核队列" not in actions:
        actions.insert(0, "高风险日志优先进入人工复核队列")
    elif final_risk >= 0.5 and "进入告警队列并等待安全人员复核" not in actions:
        actions.insert(0, "进入告警队列并等待安全人员复核")

    return {
        "scenario_id": scenario_id,
        "scenario": scenario,
        "confidence": round(float(min(confidence + min(len(hit_ids), 2) * 0.04, 0.95)), 2),
        "evidence": evidence[:6],
        "recommended_actions": actions[:5],
    }


def build_user_profile_summary(
    user_id: str,
    group: pd.DataFrame,
    avg_risk: float,
    max_risk: float,
    recent_logs: list[dict[str, Any]],
    top_causes: list[dict[str, Any]],
) -> dict[str, Any]:
    """为用户详情页生成画像式总结，避免只展示原始特征表。"""
    log_count = len(group)
    actual_alert_count = int(group["label"].sum()) if "label" in group.columns else 0
    recent_alert_count = sum(1 for r in recent_logs if r.get("is_alert"))
    actual_ratio = actual_alert_count / max(log_count, 1)

    top_feature_names = [
        FEATURE_CN.get(str(c.get("feature", "")), str(c.get("feature", "")))
        for c in top_causes[:3]
    ]
    key_factors = [x for x in top_feature_names if x]
    attention: list[str] = []
    if avg_risk >= 0.75 or max_risk >= 0.9:
        level = "高风险关注用户"
        attention.append("建议优先人工复核近期高风险会话")
    elif avg_risk >= 0.5 or recent_alert_count > 0:
        level = "中风险观察用户"
        attention.append("建议关注后续是否持续出现同类异常")
    else:
        level = "常规监控用户"
        attention.append("维持常规监控和周期性基线比对")

    if actual_ratio >= 0.15:
        attention.append("历史异常样本占比较高，需要核查业务原因")
    if any("角色操作不匹配" in f for f in key_factors):
        attention.append("重点复核岗位权限与实际操作是否匹配")
    if any("远程" in f or "网络" in f for f in key_factors):
        attention.append("重点核查登录位置、设备与账号归属")
    if any("敏感" in f for f in key_factors):
        attention.append("关注高敏文件访问、下载或导出行为")

    factor_text = "、".join(key_factors) if key_factors else "近期行为特征整体接近基线"
    text = (
        f"{user_id} 当前被归类为「{level}」。"
        f"该用户共有 {log_count} 条日志，历史异常样本 {actual_alert_count} 条，"
        f"平均风险 {avg_risk:.3f}，最近日志中触发告警 {recent_alert_count} 次。"
        f"主要风险因素集中在：{factor_text}。"
    )

    return {
        "level": level,
        "text": text,
        "key_factors": key_factors,
        "attention_points": list(dict.fromkeys(attention))[:5],
        "recent_alert_count": int(recent_alert_count),
        "historical_alert_ratio": round(float(actual_ratio), 4),
    }


def enhance_alert_triage_with_llm(
    triage: dict[str, Any],
    row_dict: dict[str, Any],
    final_risk: float,
    rule_hits: list[dict[str, Any]],
    top_causes: list[dict[str, Any]],
    *,
    use_llm: bool,
) -> dict[str, Any]:
    if not use_llm or not _env_bool("LLM_TRIAGE_ENABLED", True):
        return triage

    causes_safe = [
        {
            "feature": str(c.get("feature", "")),
            "feature_cn": FEATURE_CN.get(str(c.get("feature", "")), str(c.get("feature", ""))),
            "value": _json_float(c.get("value")),
            "contribution": _json_float(c.get("contribution")),
        }
        for c in top_causes[:6]
    ]
    context = {
        "local_triage": triage,
        "final_risk_score": round(float(final_risk), 6),
        "rule_hits": rule_hits,
        "top_shap_causes": causes_safe,
        "model_feature_values": {k: _json_float(v) for k, v in row_dict.items()},
    }
    system_msg = (
        "你是企业安全运营中心（SOC）的 UBA 告警研判助手。"
        "用户会提供一条日志的模型风险分、规则命中、SHAP 贡献和本地初步研判。"
        "请只基于输入数据输出 JSON，不要 Markdown，不要编造不存在的字段。"
        "格式："
        '{"scenario":"风险场景名称","confidence":0.0到1.0,'
        '"evidence":["证据1","证据2"],"recommended_actions":["处置1","处置2"]}'
        "。证据最多 5 条，处置建议最多 5 条，语言简洁适合安全看板展示。"
    )
    obj = call_openai_compatible_json(
        system_msg,
        context,
        max_tokens=_env_int("OPENAI_MAX_TOKENS_TRIAGE", 700),
        temperature=0.2,
    )
    if not obj:
        return triage

    out = dict(triage)
    if isinstance(obj.get("scenario"), str) and obj["scenario"].strip():
        out["scenario"] = obj["scenario"].strip()
    try:
        out["confidence"] = round(float(obj.get("confidence", triage.get("confidence", 0.0))), 2)
    except (TypeError, ValueError):
        pass
    if isinstance(obj.get("evidence"), list):
        out["evidence"] = [str(x) for x in obj["evidence"] if str(x).strip()][:5]
    if isinstance(obj.get("recommended_actions"), list):
        out["recommended_actions"] = [str(x) for x in obj["recommended_actions"] if str(x).strip()][:5]
    out["source"] = "llm_openai_compatible"
    out["model"] = llm_model_name()
    return out


def enhance_user_profile_with_llm(
    profile: dict[str, Any],
    user_id: str,
    user_data: dict[str, Any],
) -> dict[str, Any]:
    if not llm_api_configured() or not _env_bool("LLM_PROFILE_ENABLED", True):
        return profile

    recent_logs = user_data.get("recent_logs", [])[-8:]
    context = {
        "user_id": user_id,
        "local_profile": profile,
        "avg_risk": user_data.get("avg_risk"),
        "max_risk": user_data.get("max_risk"),
        "alert_count_recent_window": user_data.get("alert_count"),
        "log_count": user_data.get("log_count"),
        "actual_alert_count_simulation": user_data.get("actual_alert_count"),
        "top_causes": user_data.get("top_causes", [])[:5],
        "recent_logs": [
            {
                "timestamp": r.get("timestamp"),
                "risk_score": r.get("risk_score"),
                "is_alert": r.get("is_alert"),
                "scenario": (r.get("triage") or {}).get("scenario"),
            }
            for r in recent_logs
        ],
    }
    system_msg = (
        "你是企业 UBA 用户画像分析助手。用户会提供某个账号的风险统计、近期日志和本地画像。"
        "请输出简体中文 JSON，不要 Markdown，不要编造输入外事实。格式："
        '{"level":"高风险关注用户/中风险观察用户/常规监控用户",'
        '"text":"一段 2~4 句画像总结",'
        '"key_factors":["因素1","因素2"],'
        '"attention_points":["关注点1","关注点2"]}'
        "。请让内容适合展示在用户详情页。"
    )
    obj = call_openai_compatible_json(
        system_msg,
        context,
        max_tokens=_env_int("OPENAI_MAX_TOKENS_PROFILE", 900),
        temperature=0.25,
    )
    if not obj:
        return profile

    out = dict(profile)
    if isinstance(obj.get("level"), str) and obj["level"].strip():
        out["level"] = obj["level"].strip()
    if isinstance(obj.get("text"), str) and obj["text"].strip():
        out["text"] = obj["text"].strip()
    if isinstance(obj.get("key_factors"), list):
        out["key_factors"] = [str(x) for x in obj["key_factors"] if str(x).strip()][:5]
    if isinstance(obj.get("attention_points"), list):
        out["attention_points"] = [str(x) for x in obj["attention_points"] if str(x).strip()][:5]
    out["source"] = "llm_openai_compatible"
    out["model"] = llm_model_name()
    return out


def precompute_user_data(df_feat: pd.DataFrame, clf: Any, explainer: Any, feature_names: list[str]) -> dict[str, Any]:
    """对特征数据按用户聚合，预计算每个用户的综合风险评分与明细。"""
    print("[-] 正在预计算用户聚合数据（第二版新增功能）...")

    grouped = df_feat.groupby("user_id")
    user_data = {}
    user_list = []

    for user_id, group in grouped:
        group = group.reset_index(drop=True)
        log_count = len(group)
        actual_alert_count = int(group["label"].sum())

        # 用户风险由最近 N 条日志逐条推理后聚合，避免对类别编码取平均造成语义失真。
        recent = group.tail(20)
        recent_logs = []
        representative_features: dict[str, Any] = {}
        representative_top_causes: list[dict[str, Any]] = []
        representative_risk = -1.0

        for _, r in recent.iterrows():
            X_r = r[feature_names].to_frame().T
            X_r = X_r.apply(pd.to_numeric)
            for c in CAT_FEATURES:
                if c in X_r.columns:
                    X_r[c] = X_r[c].astype("category")
            bp = float(clf.predict_proba(X_r)[:, 1][0])
            rd = X_r.iloc[0].to_dict()
            fr = expert_rule_adjust(rd, bp)
            hits = explain_rule_hits(rd)
            triage = classify_alert_scenario(rd, fr, hits)
            try:
                tc = compute_shap_top_causes(explainer, X_r, feature_names, top_k=3)
            except Exception:
                tc = []

            if fr > representative_risk:
                representative_risk = float(fr)
                representative_features = rd
                representative_top_causes = tc

            recent_logs.append({
                "timestamp": str(r.get("timestamp", "")),
                "risk_score": fr,
                "is_alert": fr >= 0.5,
                "actual_label": int(r["label"]),
                "triage": triage,
                "top_causes": tc,
            })

        avg_risk = float(np.mean([r["risk_score"] for r in recent_logs])) if recent_logs else 0.0
        max_risk = float(max((r["risk_score"] for r in recent_logs), default=0.0))
        alert_count = sum(1 for r in recent_logs if r["is_alert"])
        is_alert = max_risk >= 0.5

        profile_summary = build_user_profile_summary(
            str(user_id),
            group,
            avg_risk,
            max_risk,
            recent_logs,
            representative_top_causes,
        )

        user_data[user_id] = {
            "avg_risk": round(avg_risk, 4),
            "max_risk": round(max_risk, 4),
            "alert_count": alert_count,
            "log_count": log_count,
            "actual_alert_count": actual_alert_count,
            "is_alert": is_alert,
            "features": {k: round(float(v), 4) for k, v in representative_features.items() if k in feature_names},
            "top_causes": representative_top_causes,
            "profile_summary": profile_summary,
            "recent_logs": recent_logs,
        }
        user_list.append((user_id, avg_risk))

    user_list.sort(key=lambda x: x[1], reverse=True)
    print(f"[-] 用户聚合数据预计算完成，共 {len(user_list)} 个用户")

    return user_data, [uid for uid, _ in user_list]


# ================= 全局变量与模型加载 =================

print("正在初始化系统，加载模型与数据...")
ensure_data_dir()
ensure_models_dir()

MODEL_PATH = MODELS_DIR / "lgbm_uba_model.pkl"
FEATURE_NAMES_PATH = MODELS_DIR / "feature_names.pkl"
FEATURE_PATH = DATA_DIR / "train_features.parquet"
MODEL_META_PATH = MODELS_DIR / "train_meta.json"
EXPERIMENT_RESULTS_PATH = DATA_DIR / "experiment_results.json"
LOCAL_ECHARTS_PATH = WEB_DIR / "static" / "echarts.min.js"
LOCAL_VIS_NETWORK_PATH = WEB_DIR / "static" / "vis-network.min.js"


def startup_check() -> None:
    checks = [
        ("特征数据", FEATURE_PATH, "请先运行 src/simulation.py 和 src/feature_engineering.py"),
        ("模型文件", MODEL_PATH, "请先运行 src/model_training.py"),
        ("特征列表", FEATURE_NAMES_PATH, "请先运行 src/model_training.py"),
        ("实验结果", EXPERIMENT_RESULTS_PATH, "请先运行 src/evaluation_compare.py"),
        ("本地 ECharts", LOCAL_ECHARTS_PATH, "当前会回退到 CDN；答辩离线前建议下载到 web/static/echarts.min.js"),
        ("本地 vis-network", LOCAL_VIS_NETWORK_PATH, "请确认 web/static/vis-network.min.js 存在"),
    ]
    print("=" * 60)
    print("启动检查")
    for name, path, hint in checks:
        if path.exists():
            print(f"[OK] {name}: {path}")
        else:
            print(f"[WARN] {name}缺失: {path}；{hint}")
    if llm_api_configured():
        print(f"[OK] LLM 配置: {llm_model_name()} / 每 {max(_env_int('LLM_REALTIME_EVERY_N', 3), 1)} 条实时日志调用一次")
    else:
        print("[WARN] LLM 未启用: 将使用本地模板解读")
    print("=" * 60)


startup_check()

if not MODEL_PATH.exists():
    raise FileNotFoundError(f"找不到模型文件：{MODEL_PATH}，请先运行 model_training.py")
if not FEATURE_NAMES_PATH.exists():
    raise FileNotFoundError(f"找不到特征列表文件：{FEATURE_NAMES_PATH}，请先运行 model_training.py")
if not FEATURE_PATH.exists():
    raise FileNotFoundError(f"找不到特征数据文件：{FEATURE_PATH}，请先运行 feature_engineering.py")

# 1. 加载模型和特征名
clf = joblib.load(str(MODEL_PATH))
feature_names: list[str] = joblib.load(str(FEATURE_NAMES_PATH))
print("[-] LightGBM 模型加载成功")

# 2. 初始化 SHAP 解释器
explainer = shap.TreeExplainer(clf)
print("[-] SHAP 解释器初始化成功")

# 3. 加载特征数据，用于模拟实时日志流
df_features = pd.read_parquet(str(FEATURE_PATH))
df_features = df_features.dropna().reset_index(drop=True)
print(f"[-] 模拟数据流加载成功，共 {len(df_features)} 条")

# 4. 第二版：预计算用户聚合数据
USER_DATA, USER_RANKING = precompute_user_data(df_features, clf, explainer, feature_names)


def build_feature_importance_payload() -> dict[str, Any]:
    """生成论文展示和前端图表共用的全局特征重要性数据。"""
    payload: dict[str, Any] = {
        "gain_importance": [],
        "split_importance": [],
        "shap_mean_abs": [],
    }
    try:
        booster = clf.booster_
        gain = booster.feature_importance(importance_type="gain")
        split = booster.feature_importance(importance_type="split")
        payload["gain_importance"] = [
            {
                "feature": f,
                "feature_cn": FEATURE_CN.get(f, f),
                "value": float(v),
            }
            for f, v in sorted(zip(feature_names, gain), key=lambda x: x[1], reverse=True)
        ]
        payload["split_importance"] = [
            {
                "feature": f,
                "feature_cn": FEATURE_CN.get(f, f),
                "value": float(v),
            }
            for f, v in sorted(zip(feature_names, split), key=lambda x: x[1], reverse=True)
        ]
    except Exception as e:
        print(f"[提示] LightGBM 特征重要性计算失败：{e}")

    try:
        sample = df_features[feature_names].head(min(1000, len(df_features))).copy()
        for c in CAT_FEATURES:
            if c in sample.columns:
                sample[c] = sample[c].astype("category")
        shap_values = explainer.shap_values(sample)
        if isinstance(shap_values, list):
            shap_arr = shap_values[1] if len(shap_values) > 1 else shap_values[0]
        else:
            shap_arr = shap_values
        mean_abs = np.abs(np.asarray(shap_arr)).mean(axis=0)
        payload["shap_mean_abs"] = [
            {
                "feature": f,
                "feature_cn": FEATURE_CN.get(f, f),
                "value": float(v),
            }
            for f, v in sorted(zip(feature_names, mean_abs), key=lambda x: x[1], reverse=True)
        ]
    except Exception as e:
        print(f"[提示] SHAP 全局重要性计算失败：{e}")

    return payload


FEATURE_IMPORTANCE = build_feature_importance_payload()

if llm_api_configured():
    print(
        f"[-] 智能解读：已启用外接 LLM（{llm_base_url()} · "
        f"{llm_model_name()} · 实时每 {max(_env_int('LLM_REALTIME_EVERY_N', 3), 1)} 条调用一次）"
    )
else:
    print("[-] 智能解读：使用本地模板（设置 DEEPSEEK_API_KEY/OPENAI_API_KEY 可启用 OpenAI 兼容接口）")


# ================= 路由 =================

@app.route("/", methods=["GET"])
def index_page():
    """前端大屏首页：返回 web/index.html"""
    return send_from_directory(_WEB_ROOT, "index.html")


@app.route("/api/status", methods=["GET"])
def system_status():
    """探活接口"""
    return jsonify({
        "status": "running",
        "model": "LightGBM + Expert Rules + SHAP",
        "version": "2.0",
        "llm_enabled": llm_api_configured(),
        "llm_model": llm_model_name() if llm_api_configured() else None,
        "llm_base_url": llm_base_url() if llm_api_configured() else None,
        "llm_realtime_every_n": max(_env_int("LLM_REALTIME_EVERY_N", 3), 1),
        "user_count": len(USER_DATA),
    })


@app.route("/api/next_log", methods=["GET"])
def get_next_log():
    """前端轮询此接口，获取最新的一条日志行为分析结果"""
    global _NEXT_LOG_REQUEST_COUNT
    _NEXT_LOG_REQUEST_COUNT += 1
    use_realtime_llm = should_call_realtime_llm()

    df_pos = df_features[df_features["label"] == 1]
    df_neg = df_features[df_features["label"] == 0]
    try:
        if random.random() < 0.3 and len(df_pos) > 0:
            sample_df = df_pos.sample(1, random_state=None)
        elif len(df_neg) > 0:
            sample_df = df_neg.sample(1, random_state=None)
        elif len(df_pos) > 0:
            sample_df = df_pos.sample(1, random_state=None)
        else:
            return jsonify({"error": "特征数据中没有可用样本（正负样本均为空）"}), 503
    except ValueError:
        sample_df = df_features.sample(1, random_state=None)

    row_data = sample_df.iloc[0]
    X_input = sample_df[feature_names].copy()
    for c in CAT_FEATURES:
        if c in X_input.columns:
            X_input[c] = X_input[c].astype("category")

    base_prob = float(clf.predict_proba(X_input)[:, 1][0])
    row_dict = X_input.iloc[0].to_dict()
    final_risk_score = expert_rule_adjust(row_dict, base_prob)
    rule_hits = explain_rule_hits(row_dict)
    alert_triage = classify_alert_scenario(row_dict, final_risk_score, rule_hits)

    try:
        top_5_causes = compute_shap_top_causes(explainer, X_input, feature_names, top_k=5)
    except Exception:
        top_5_causes = []

    ai_summary = build_ai_narrative(
        base_prob, final_risk_score, row_dict, top_5_causes,
        user_id=str(row_data.get("user_id", "")),
        timestamp=str(row_data.get("timestamp", "")),
        actual_label=int(row_data["label"]),
        use_llm=False,
    )
    llm_async = {
        "enabled": False,
        "status": "disabled",
        "request_id": None,
    }
    if use_realtime_llm:
        request_id = create_realtime_llm_task(
            base_prob=base_prob,
            final_risk_score=final_risk_score,
            row_dict=row_dict,
            top_causes=top_5_causes,
            rule_hits=rule_hits,
            local_triage=alert_triage,
            row_data=row_data,
        )
        llm_async = {
            "enabled": True,
            "status": "pending",
            "request_id": request_id,
        }
        ai_summary = dict(ai_summary)
        ai_summary["hint"] = ai_summary.get("hint", "") + " 外接大模型正在异步生成，不阻塞图表刷新。"

    response_data = {
        "user_id": str(row_data.get("user_id", "")),
        "timestamp": str(row_data.get("timestamp", "")),
        "actual_label": int(row_data["label"]),
        "risk_score": float(final_risk_score),
        "risk_level": risk_level(final_risk_score),
        "is_alert": bool(final_risk_score >= 0.5),
        "base_model_score": float(base_prob),
        "rule_hits": rule_hits,
        "alert_triage": alert_triage,
        "top_causes": top_5_causes,
        "raw_features": {k: float(v) if isinstance(v, (np.floating, float, int, np.integer)) else v for k, v in row_dict.items()},
        "ai_summary": ai_summary,
        "llm_async": llm_async,
    }
    return jsonify(response_data)


@app.route("/api/realtime_llm/<request_id>", methods=["GET"])
def api_realtime_llm(request_id):
    """实时监控异步 LLM 补充接口：慢请求不阻塞 /api/next_log。"""
    cleanup_realtime_llm_tasks()
    task = _REALTIME_LLM_TASKS.pop(request_id, None)
    if not task:
        return jsonify({"error": "异步 LLM 请求已过期或不存在"}), 404

    row_dict = task["row_dict"]
    top_causes = task["top_causes"]
    rule_hits = task["rule_hits"]
    final_risk_score = float(task["final_risk_score"])

    alert_triage = enhance_alert_triage_with_llm(
        task["local_triage"],
        row_dict,
        final_risk_score,
        rule_hits,
        top_causes,
        use_llm=True,
    )
    ai_summary = build_ai_narrative(
        float(task["base_prob"]),
        final_risk_score,
        row_dict,
        top_causes,
        user_id=task["user_id"],
        timestamp=task["timestamp"],
        actual_label=int(task["actual_label"]),
        use_llm=True,
    )
    return jsonify({
        "request_id": request_id,
        "status": "done",
        "ai_summary": ai_summary,
        "alert_triage": alert_triage,
    })


# ================= 第二版新增：用户聚合看板 API =================

@app.route("/api/users", methods=["GET"])
def api_users():
    """返回所有用户的聚合风险评分排名列表。"""
    results = []
    for rank, uid in enumerate(USER_RANKING, 1):
        d = USER_DATA[uid]
        results.append({
            "rank": rank,
            "user_id": uid,
            "avg_risk": d["avg_risk"],
            "max_risk": d["max_risk"],
            "alert_count": d["alert_count"],
            "log_count": d["log_count"],
            "actual_alert_count": d["actual_alert_count"],
            "is_alert": d["is_alert"],
            "top_causes": d["top_causes"][:3],
        })
    return jsonify(results)


@app.route("/api/user/<user_id>", methods=["GET"])
def api_user_detail(user_id):
    """返回指定用户的详细特征与最近日志风险记录。"""
    if user_id not in USER_DATA:
        return jsonify({"error": f"用户 {user_id} 不存在"}), 404
    d = USER_DATA[user_id]
    profile_summary = enhance_user_profile_with_llm(d.get("profile_summary", {}), user_id, d)
    return jsonify({
        "user_id": user_id,
        "avg_risk": d["avg_risk"],
        "max_risk": d["max_risk"],
        "alert_count": d["alert_count"],
        "log_count": d["log_count"],
        "actual_alert_count": d["actual_alert_count"],
        "is_alert": d["is_alert"],
        "features": d["features"],
        "top_causes": d["top_causes"],
        "profile_summary": profile_summary,
        "recent_logs": d["recent_logs"],
    })


@app.route("/api/graph", methods=["GET"])
def api_graph():
    """返回风险关系图数据（用户-操作二分图）。"""
    nodes = []
    edges = []
    node_set = set()

    # 操作类别节点
    op_codes = sorted(OP_CATEGORY_CN.keys())
    for code in op_codes:
        nid = f"op_{code}"
        nodes.append({
            "id": nid, "label": OP_CATEGORY_CN[code],
            "group": "operation", "size": 15,
        })
        node_set.add(nid)

    # 用户节点及边
    for uid in USER_RANKING:
        d = USER_DATA[uid]
        score = d["avg_risk"]
        red = d["actual_alert_count"] > 0

        # 风险级别色彩
        if red:
            color = "red"
        elif score > 0.7:
            color = "orange"
        elif score > 0.5:
            color = "yellow"
        else:
            color = "lightblue"

        size = 30 if red else (20 if score > 0.7 else 15 if score > 0.5 else 10)

        nodes.append({
            "id": uid, "label": uid,
            "group": "user", "size": size,
            "color": color, "risk_score": score,
            "is_alert": d["is_alert"],
        })
        node_set.add(uid)

        # 从该用户的特征中推断操作关联
        user_features = d["features"]
        op_code = int(user_features.get("operation_type_code", 0))
        edge_key = f"op_{op_code}"
        if edge_key in node_set:
            edges.append({
                "from": uid, "to": edge_key,
                "width": 1 + int(user_features.get("op_count_1h", 0) * 5),
            })

        # 额外连接基于 role_code 的相关操作
        role_code = int(user_features.get("role_code", 0))
        related_ops = []
        if role_code == 0:      # 普通员工
            related_ops = [0, 1, 2]
        elif role_code == 1:    # 财务
            related_ops = [3, 4, 5]
        elif role_code == 2:    # 技术
            related_ops = [6, 7, 8]
        elif role_code == 3:    # 管理员
            related_ops = [9, 10, 11]

        for ro in related_ops:
            eid = f"op_{ro}"
            if eid != edge_key and eid in node_set:
                edges.append({
                    "from": uid, "to": eid,
                    "width": 1, "dashed": True,
                })

    return jsonify({"nodes": nodes, "edges": edges})


@app.route("/api/model_compare", methods=["GET"])
def api_model_compare():
    """返回多模型性能对比结果，用于论文图表和前端实验对比页。"""
    if not EXPERIMENT_RESULTS_PATH.exists():
        return jsonify({
            "error": "尚未生成模型对比实验结果，请先运行 src/evaluation_compare.py",
            "model_compare": [],
        }), 404
    with open(EXPERIMENT_RESULTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return jsonify(data.get("model_compare", []))


@app.route("/api/feature_importance", methods=["GET"])
def api_feature_importance():
    """返回 LightGBM gain/split 与 SHAP mean(|value|) 全局特征重要性。"""
    return jsonify(FEATURE_IMPORTANCE)


@app.route("/api/user/<user_id>/explain", methods=["GET"])
def api_user_explain(user_id):
    """返回单用户风险解释：模型分、规则命中、SHAP Top 因素和处置建议。"""
    if user_id not in USER_DATA:
        return jsonify({"error": f"用户 {user_id} 不存在"}), 404

    d = USER_DATA[user_id]
    feature_row = {name: float(d["features"].get(name, 0.0)) for name in feature_names}
    X_user = pd.DataFrame([feature_row], columns=feature_names)
    for c in CAT_FEATURES:
        if c in X_user.columns:
            X_user[c] = X_user[c].astype("category")

    base_prob = float(clf.predict_proba(X_user)[:, 1][0])
    row_dict = X_user.iloc[0].to_dict()
    final_score = expert_rule_adjust(row_dict, base_prob)
    hits = explain_rule_hits(row_dict)

    try:
        top_causes = compute_shap_top_causes(explainer, X_user, feature_names, top_k=8)
    except Exception:
        top_causes = d.get("top_causes", [])

    return jsonify({
        "user_id": user_id,
        "base_model_score": base_prob,
        "final_risk_score": final_score,
        "risk_level": risk_level(final_score),
        "rule_hits": hits,
        "top_shap_causes": top_causes,
        "suggestion": risk_suggestion(final_score, hits),
    })


@app.route("/api/experiment_results", methods=["GET"])
def api_experiment_results():
    """返回论文实验展示所需的模型对比、消融说明和 ROC/PR 曲线数据。"""
    if not EXPERIMENT_RESULTS_PATH.exists():
        return jsonify({
            "error": "尚未生成实验结果，请先运行 src/evaluation_compare.py",
            "model_compare": [],
            "feature_ablation": [],
            "rule_ablation": [],
            "roc_curve": [],
            "pr_curve": [],
        }), 404
    with open(EXPERIMENT_RESULTS_PATH, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


if __name__ == "__main__":
    _port = int(os.environ.get("PORT", "5000"))
    _host = os.environ.get("HOST", "0.0.0.0")
    _debug = os.environ.get("FLASK_DEBUG", "1").lower() in ("1", "true", "yes")
    print("=" * 60)
    print(f"第二版后端 API 服务已启动！监听: http://{_host}:{_port}")
    print(f"接口测试: http://127.0.0.1:{_port}/api/next_log")
    print(f"用户列表: http://127.0.0.1:{_port}/api/users")
    print("=" * 60)
    app.run(host=_host, port=_port, debug=_debug, use_reloader=False, threaded=True)
