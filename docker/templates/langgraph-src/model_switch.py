"""模型切换管理

额度耗尽时中断工作流，通知人类选择可用模型，恢复后自动重试。

状态文件 (model_state.json) 持久化模型状态：
  - active_model: 当前使用的模型字符串
  - failed_models: 已耗尽额度的模型列表
  - last_failure_at: 最近失败时间（用于判断额度是否恢复）
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

from langgraph.types import interrupt

MODEL_STATE_FILE = os.environ.get("MODEL_STATE_FILE", "/data/langgraph/model_state.json")


# ── 状态文件读写 ──────────────────────────────────────────

def _load_state() -> dict:
    if not os.path.exists(MODEL_STATE_FILE):
        return {"active_model": None, "failed_models": [], "last_failure_at": None}
    try:
        with open(MODEL_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {"active_model": None, "failed_models": [], "last_failure_at": None}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(MODEL_STATE_FILE) or ".", exist_ok=True)
    with open(MODEL_STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ── 模型解析 ──────────────────────────────────────────────

def parse_model_str(s: str) -> Optional[Dict[str, str]]:
    """把 'opencode/gpt-5.1-codex' 拆成 {providerID, id}"""
    if "/" in s:
        p, m = s.split("/", 1)
        return {"providerID": p, "id": m}
    return None


# ── 模型层级（自环境变量） ─────────────────────────────────

def _tiers() -> List[str]:
    raw = os.environ.get("MODEL_TIERS", "").strip()
    return [t.strip() for t in raw.split(",") if t.strip()]


def _tier_names() -> List[str]:
    raw = os.environ.get("MODEL_TIER_NAMES", "").strip()
    names = [n.strip() for n in raw.split(",") if n.strip()]
    tiers = _tiers()
    # 补齐：未命名的直接用模型名
    return names + tiers[len(names):] if len(names) < len(tiers) else names[:len(tiers)]


# ── 核心 API ──────────────────────────────────────────────

def resolve_active_model() -> Optional[str]:
    """返回当前可用模型字符串（如 'kimi-for-coding/k2p5'），无可用返回 None"""
    state = _load_state()
    active = state.get("active_model")
    failed = set(state.get("failed_models", []))

    # 优先级 1：active_model 且未失败
    if active and active not in failed:
        return active

    # 优先级 2：MODEL_TIERS 中第一个未失败的
    for tier in _tiers():
        if tier not in failed:
            return tier

    return None


def record_failure(model_str: str):
    """标记模型调用失败"""
    state = _load_state()
    if model_str not in state["failed_models"]:
        state["failed_models"].append(model_str)
    state["last_failure_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _save_state(state)


def get_available_models() -> List[Dict[str, Any]]:
    """返回所有模型层级的状态列表"""
    state = _load_state()
    failed = set(state.get("failed_models", []))

    result = []
    for tier, name in zip(_tiers(), _tier_names()):
        result.append({
            "model_str": tier,
            "name": name,
            "failed": tier in failed,
            "active": state.get("active_model", "") == tier,
        })
    return result


def request_model_switch(failed_model: str, available: List[Dict]) -> str:
    """中断工作流，等待人类选择新模型，返回模型字符串

    支持 resume 值：
      - "kimi-for-coding/k2p5"  → 完整模型名
      - "1"                     → 序号（对应 available 列表中的位置）
    """
    available_non_failed = [m for m in available if not m["failed"]]
    available_strs = [m["model_str"] for m in available_non_failed]

    options_lines = [
        f"  {i+1}. {m['name']} ({m['model_str']})"
        for i, m in enumerate(available_non_failed)
    ]
    question = (
        f"模型 `{failed_model}` 额度已耗尽，请选择要切换到的模型：\n"
        + "\n".join(options_lines)
    )

    choice = interrupt({
        "type": "model_switch",
        "failed_model": failed_model,
        "available": available_non_failed,
        "question": question,
    })

    if isinstance(choice, str) and choice.strip():
        chosen = choice.strip()
        # 支持序号选择
        if chosen.isdigit():
            idx = int(chosen) - 1
            if 0 <= idx < len(available_strs):
                chosen = available_strs[idx]
            else:
                raise RuntimeError(f"序号 {choice} 超出范围，可用模型: {available_strs}")
        elif chosen not in available_strs:
            raise RuntimeError(f"无效模型 '{chosen}'，可用: {available_strs}")

        state = _load_state()
        state["active_model"] = chosen
        _save_state(state)
        return chosen

    raise RuntimeError("模型切换被取消")


# ── 管理 API（供外部脚本 / 飞书 webhook 调用） ────────────

def set_active_model(model_str: str):
    """手动设置当前激活的模型"""
    state = _load_state()
    state["active_model"] = model_str
    _save_state(state)


def reset_failed(model_str: Optional[str] = None):
    """重置失败记录（额度恢复后调用）"""
    state = _load_state()
    if model_str:
        state["failed_models"] = [m for m in state["failed_models"] if m != model_str]
    else:
        state["failed_models"] = []
    _save_state(state)


def get_state() -> dict:
    """获取完整状态（供外部脚本读取）"""
    return _load_state()
