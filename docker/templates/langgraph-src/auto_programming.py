"""
多角色自动化编程工作流

预制 13 个软件开发角色：
  需求线：产品经理、业务分析师
  设计线：系统架构师、API 设计师、数据库工程师
  管理线：技术负责人、项目经理
  实现线：后端工程师、前端工程师、DevOps 工程师
  质量线：代码评审员、安全工程师、测试工程师、技术文档工程师

特性：
  1. 接口定义完成后再并行前后端开发
  2. 需求评审、架构评审、代码评审支持 Human-in-the-Loop
  3. 安全工程师、API 复核为可选节点
  4. 每个角色可独立配置模型（通过 OpenClaw 多 agent 路由）
  5. OpenCode 通过 HTTP Server API 调用（opencode serve 模式）

通过 LangGraph 编排，并真正调用 LLM 与 OpenCode 完成自动化编程需求。
"""

import base64
import hashlib
import hmac
import json
import operator
import os
import re
import time
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import requests
from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from src.model_switch import (
    get_available_models,
    parse_model_str,
    record_failure,
    request_model_switch,
    resolve_active_model,
)
# LangGraph dev/serve 模式自动管理持久化，不需要自定义 checkpointer。
# 若需生产级 PostgreSQL 持久化，设置 POSTGRES_URI 环境变量即可。

# 导入统一记忆层
from src.memory import (
    append_daily_note,
    append_to_memory_md,
    complete_task,
    create_task,
    load_global_context,
    search_memory,
    update_task_phase,
)


# ============================================================
# LLM 接入层（经 OpenCode HTTP Server → Kimi Code API）
# ============================================================
# 说明：OpenClaw Gateway 因数据库初始化问题无法在此环境中正常路由，
# 而 OpenCode 作为受支持的编码 Agent 可与 Kimi Code API 正常通信。
# 因此将 LLM 调用改为通过 OpenCode 的会话 API 转发。
# ============================================================

def _is_quota_error(err_str: str) -> bool:
    """检测配额耗尽 / 频率限制 / 余额不足 类错误"""
    err_lower = err_str.lower()
    return any(kw in err_lower for kw in [
        "quota", "rate limit", "insufficient_quota",
        "exhausted", "429", "402", "insufficient balance",
        "token limit", "limit reached",
        "额度不足", "余额不足", "频率限制",
    ])


def _extract_response_text(result: Dict[str, Any]) -> Optional[str]:
    """从 OpenCode 响应中提取文本（原有 call_llm 的提取逻辑）"""
    json_res = result.get("result", {})
    if isinstance(json_res, dict):
        parts = json_res.get("parts")
        if isinstance(parts, list):
            text_parts = []
            for p in parts:
                if isinstance(p, dict) and p.get("type") == "text":
                    t = p.get("text", "")
                    if t and isinstance(t, str):
                        text_parts.append(t)
            if text_parts:
                return "\n".join(text_parts).strip()[:8000]
        events = json_res.get("events")
        if isinstance(events, list) and events:
            for evt in reversed(events):
                if isinstance(evt, dict):
                    for field in ("text", "content", "message", "output", "response"):
                        val = evt.get(field)
                        if val and isinstance(val, str) and val.strip():
                            return val.strip()
        for field in ("text", "content", "message", "output", "response"):
            val = json_res.get(field)
            if val and isinstance(val, str) and val.strip():
                return val.strip()
    raw = result.get("raw_result", "")
    if raw and raw.strip():
        return raw.strip()[:8000]
    return None


def _notify_model_switch_needed(failed_model: str, available: list, state: dict):
    """发送模型切换通知到飞书"""
    task_title = state.get("task_title", "")
    task_id = state.get("task_id", "")

    options_lines = [
        f"  {i+1}. {m['name']} ({m['model_str']})"
        for i, m in enumerate(available) if not m["failed"]
    ]
    message = (
        f"🤖 **模型额度耗尽，需要切换**\n\n"
        f"**任务**: {task_title}\n"
        f"**task_id**: `{task_id}`\n\n"
        f"**失败模型**: `{failed_model}`\n\n"
        f"**可用模型**:\n" + "\n".join(options_lines) + "\n\n"
        f"请回复模型序号或完整模型名来切换。"
    )

    chat_id = state.get("chat_id", "") or ""
    if chat_id:
        if _notify_feishu_via_api(chat_id, message):
            return
    feishu_webhook = os.environ.get("FEISHU_NOTIFY_WEBHOOK", "").strip()
    if feishu_webhook:
        try:
            timestamp = str(int(time.time()))
            payload = {"msg_type": "text", "content": {"text": message}}
            secret = os.environ.get("FEISHU_NOTIFY_SECRET", "").strip()
            if secret:
                payload["timestamp"] = timestamp
                payload["sign"] = _gen_feishu_sign(timestamp, secret)
            requests.post(feishu_webhook, json=payload, timeout=5)
        except Exception:
            pass
    print(f"[MODEL SWITCH] {failed_model} 额度耗尽，等待人类选择...", flush=True)


def call_llm(
    prompt: str,
    role: str = "资深软件工程师",
    role_key: str = "",
    agent_id: Optional[str] = None,
    state: Optional[Dict[str, Any]] = None,
) -> str:
    """通过 OpenCode HTTP Server 调用 LLM，支持开销户耗尽后的模型切换。

    调用流程：
      1. resolve_active_model() 读取 model_state.json 确定当前模型
      2. call_opencode(wrapped, model_str=model_str) 传入模型让 OpenCode 路由
      3. 若配额耗尽 → record_failure() + 飞书通知 + interrupt() 暂停
      4. 人类选择模型后 resume → 循环重试
      5. 正常返回文本
    """
    state = state or {}

    model_str = resolve_active_model()
    if not model_str:
        raise RuntimeError("所有模型额度均已耗尽，无法继续。请在 model_state.json 中重置后重试。")

    while True:
        wrapped = f"""你是一位{role}。请用中文回答，输出尽量结构化。

{prompt}

只输出指定格式的内容，不要额外说明。"""

        result = call_opencode(wrapped, model_str=model_str)

        if result.get("error"):
            err = result["error"]
            if _is_quota_error(err):
                record_failure(model_str)
                available = get_available_models()
                _notify_model_switch_needed(model_str, available, state)
                model_str = request_model_switch(model_str, available)
                continue
            raise RuntimeError(f"LLM call via OpenCode failed: {str(err)[:300]}")

        text = _extract_response_text(result)
        if text:
            return text

        raise RuntimeError("LLM returned no output")


def parse_json_from_llm(text: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出中提取 JSON 对象"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _use_persistent_checkpointer() -> bool:
    return os.environ.get("LANGGRAPH_PERSISTENCE", "true").lower() in ("true", "1", "yes")


# ============================================================
# OpenCode HTTP Server 接入层
# ============================================================

_OPENCODE_PORT = int(os.environ.get("OPENCODE_PORT", "4096"))
_OPENCODE_BASE = f"http://127.0.0.1:{_OPENCODE_PORT}"
_OPENCODE_PASSWORD = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
_OPENCODE_AUTH = ("opencode", _OPENCODE_PASSWORD) if _OPENCODE_PASSWORD else None
_OPENCODE_SESSION_ENDPOINTS = ("/session", "/sessions")


def _wait_opencode_ready(max_wait: int = 60) -> bool:
    """等待 OpenCode HTTP Server 就绪（最多 60 秒）"""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            r = requests.get(f"{_OPENCODE_BASE}/global/health", timeout=3)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(3)
    return False


def call_opencode(
    instruction: str,
    cwd: Optional[str] = None,
    model_str: Optional[str] = None,
) -> Dict[str, Any]:
    """通过 OpenCode HTTP Server API 执行编程任务

    使用 opencode serve 的持久化服务，避免每次冷启动。
    每次调用创建一个独立 session，发送指令并同步等待完成（最长 10 分钟）。
    通过 model_str 参数（格式 "providerID/modelID"）让 OpenCode 路由到指定模型/提供商。
    """
    prefix = f"工作目录：{cwd}\n\n" if cwd else ""
    full_instruction = prefix + instruction

    if not _wait_opencode_ready():
        return {"error": "OpenCode server 不可用，请检查 opencode serve 进程", "returncode": 1}

    def _build_model_payload() -> Optional[Dict[str, Any]]:
        if model_str and "/" in model_str:
            provider, model_id = model_str.split("/", 1)
            return {"id": model_id, "providerID": provider}
        return None

    def _create_session() -> Dict[str, Any]:
        payload: Dict[str, Any] = {"title": instruction[:80]}
        model_payload = _build_model_payload()
        if model_payload:
            payload["model"] = model_payload
        last_error = ""
        for endpoint in _OPENCODE_SESSION_ENDPOINTS:
            try:
                resp = requests.post(
                    f"{_OPENCODE_BASE}{endpoint}",
                    json=payload,
                    auth=_OPENCODE_AUTH,
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                session_id = data.get("id") or data.get("session_id")
                if session_id:
                    return {"session_id": str(session_id), "endpoint": endpoint}
                last_error = f"{endpoint} missing session id"
            except Exception as exc:
                last_error = f"{endpoint}: {exc}"
        return {"error": f"创建 session 失败: {last_error}"}

    def _extract_response_payload(resp: requests.Response) -> Dict[str, Any]:
        content_type = (resp.headers.get("content-type") or "").lower()
        text = resp.text or ""

        if "application/json" in content_type:
            try:
                return {"json": resp.json(), "raw_text": text}
            except Exception:
                pass

        sse_events: List[Dict[str, Any]] = []
        if "text/event-stream" in content_type or "\ndata:" in text:
            for line in text.splitlines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if not payload or payload == "[DONE]":
                    continue
                try:
                    sse_events.append(json.loads(payload))
                except Exception:
                    sse_events.append({"raw": payload})
            return {"json": {"events": sse_events}, "raw_text": text}

        return {"json": {"raw": text[:4000]}, "raw_text": text}

    def _send_message(session_id: str, session_endpoint: str) -> Dict[str, Any]:
        message_candidates = [
            f"{session_endpoint}/{session_id}/message",
            f"{session_endpoint}/{session_id}/messages",
        ]
        last_error = ""
        for endpoint in message_candidates:
            try:
                resp = requests.post(
                    f"{_OPENCODE_BASE}{endpoint}",
                    json={"parts": [{"type": "text", "text": full_instruction}]},
                    auth=_OPENCODE_AUTH,
                    timeout=600,
                )
                resp.raise_for_status()
                return _extract_response_payload(resp)
            except Exception as exc:
                last_error = f"{endpoint}: {exc}"
        return {"error": f"发送消息失败: {last_error}"}

    def _fetch_diff(session_id: str, session_endpoint: str) -> List[Any]:
        diff_candidates = [
            f"{session_endpoint}/{session_id}/diff",
            f"{session_endpoint}/{session_id}/diffs",
        ]
        for endpoint in diff_candidates:
            try:
                diff_resp = requests.get(
                    f"{_OPENCODE_BASE}{endpoint}",
                    auth=_OPENCODE_AUTH,
                    timeout=15,
                )
                if diff_resp.ok:
                    data = diff_resp.json()
                    if isinstance(data, list):
                        return data
                    if isinstance(data, dict):
                        return data.get("items") or data.get("diff") or [data]
            except Exception:
                continue
        return []

    try:
        created = _create_session()
        if created.get("error"):
            return {"error": created["error"], "returncode": 1}

        session_id = created["session_id"]
        session_endpoint = created["endpoint"]

        message_result = _send_message(session_id, session_endpoint)
        if message_result.get("error"):
            return {
                "error": message_result["error"],
                "session_id": session_id,
                "returncode": 1,
            }

        return {
            "session_id": session_id,
            "returncode": 0,
            "diff": _fetch_diff(session_id, session_endpoint),
            "result": message_result.get("json", {}),
            "raw_result": message_result.get("raw_text", "")[:8000],
        }
    except Exception as e:
        return {"error": str(e), "returncode": 1}


# ============================================================
# 角色定义（13 个）
# ============================================================

ROLES: Dict[str, Dict[str, Any]] = {
    "product_manager": {
        "name": "产品经理",
        "description": "负责需求澄清、用户故事拆解、验收标准制定、PRD 输出",
        "system_role": "资深产品经理",
        "agent_id": "pm",
        "prompt_template": """你是一位资深产品经理。请分析以下需求：

任务：{task_title}
描述：{task_description}

请输出 JSON，格式如下：
{{
  "core_problem": "核心问题描述",
  "target_users": ["目标用户1", "目标用户2"],
  "business_value": "业务价值描述",
  "scope": "明确范围与不在范围内",
  "user_stories": [
    "作为...我希望...以便...",
    "作为...我希望...以便..."
  ],
  "acceptance_criteria": [
    "可检查的通过条件1",
    "可检查的通过条件2"
  ],
  "open_questions": ["需要人类确认的不确定问题1"]
}}

只输出 JSON，不要额外说明。"""
    },
    "business_analyst": {
        "name": "业务分析师",
        "description": "负责业务流程梳理、领域建模、用例分析、数据流梳理",
        "system_role": "资深业务分析师",
        "agent_id": "business_analyst",
        "prompt_template": """你是一位资深业务分析师。基于产品经理输出的需求，进一步梳理业务：

任务：{task_title}
需求：{requirements}
用户故事：{user_stories}
验收标准：{acceptance_criteria}

请输出 JSON，格式如下：
{{
  "business_flow": "核心业务流程描述",
  "domain_entities": [
    {{"name": "实体名", "attributes": ["属性1"], "relationships": "关系"}}
  ],
  "use_cases": ["用例1", "用例2"],
  "data_flow": "模块/系统间数据流描述",
  "risks": ["业务风险1"]
}}

只输出 JSON，不要额外说明。"""
    },
    "system_architect": {
        "name": "系统架构师",
        "description": "负责技术选型、系统架构设计、模块划分、非功能需求设计",
        "system_role": "资深系统架构师",
        "agent_id": "architect",
        "prompt_template": """你是一位资深系统架构师。基于需求和业务分析，给出系统级设计：

任务：{task_title}
需求：{requirements}
业务分析：{business_analysis}
验收标准：{acceptance_criteria}

请输出 JSON，格式如下：
{{
  "modules": ["模块1", "模块2"],
  "module_responsibilities": {{"模块1": "职责"}},
  "tech_stack": ["技术1", "技术2"],
  "data_flow": "模块间数据流描述",
  "deployment": "部署方式",
  "non_functional": ["性能", "安全", "可扩展性"],
  "open_questions": ["需要人类确认的架构问题1"]
}}

只输出 JSON，不要额外说明。"""
    },
    "api_designer": {
        "name": "API 设计师",
        "description": "负责前后端接口契约设计，定义 OpenAPI/GraphQL 接口",
        "system_role": "资深 API 设计师",
        "agent_id": "api_designer",
        "prompt_template": """你是一位资深 API 设计师。基于架构设计，定义前后端接口契约：

任务：{task_title}
架构设计：{architecture_design}
业务分析：{business_analysis}

请输出 JSON，格式如下：
{{
  "api_contracts": [
    {{
      "path": "/api/example",
      "method": "POST",
      "summary": "接口用途",
      "request": {{"field": "type"}},
      "response": {{"field": "type"}},
      "errors": ["400", "401"]
    }}
  ],
  "data_types": [{{"name": "User", "fields": ["id", "name"]}}],
  "frontend_backend_boundary": "前后端职责边界说明",
  "open_questions": ["需要人类确认的接口问题1"]
}}

只输出 JSON，不要额外说明。"""
    },
    "database_engineer": {
        "name": "数据库工程师",
        "description": "负责数据模型、表结构、索引、迁移脚本设计",
        "system_role": "资深数据库工程师",
        "agent_id": "database_engineer",
        "prompt_template": """你是一位资深数据库工程师。基于业务实体和 API 契约，设计数据存储：

任务：{task_title}
业务实体：{domain_entities}
API 契约：{api_contracts}

请输出 JSON，格式如下：
{{
  "entities": [
    {{"table": "users", "fields": [{{"name": "id", "type": "UUID", "pk": true}}]}}
  ],
  "indexes": ["CREATE INDEX ..."],
  "migrations": ["ALTER TABLE ..."],
  "data_consistency": "一致性策略"
}}

只输出 JSON，不要额外说明。"""
    },
    "devops_engineer": {
        "name": "DevOps 工程师",
        "description": "负责 CI/CD、部署、监控、基础设施准备",
        "system_role": "资深 DevOps 工程师",
        "agent_id": "devops_engineer",
        "prompt_template": """你是一位资深 DevOps 工程师。基于架构设计，给出交付侧准备建议：

任务：{task_title}
架构设计：{architecture_design}
技术栈：{tech_stack}

请输出 JSON，格式如下：
{{
  "deployment_target": "部署目标",
  "ci_cd_steps": ["构建", "测试", "部署"],
  "infrastructure": ["容器", "数据库", "缓存"],
  "observability": ["日志", "指标", "告警"],
  "secrets_management": "密钥管理方式"
}}

只输出 JSON，不要额外说明。"""
    },
    "tech_lead": {
        "name": "技术负责人",
        "description": "负责任务拆解、技术决策拍板、代码初审、方案协调",
        "system_role": "资深技术负责人",
        "agent_id": "tech_lead",
        "prompt_template": """你是一位资深技术负责人。基于架构、API 和数据库设计，制定可执行计划：

任务：{task_title}
架构设计：{architecture_design}
API 契约：{api_contracts}
数据库设计：{database_design}

请输出 JSON，格式如下：
{{
  "implementation_plan": {{
    "frontend_tasks": ["前端任务1"],
    "backend_tasks": ["后端任务1"],
    "shared_tasks": ["前后端依赖任务1"]
  }},
  "task_dependencies": ["依赖说明"],
  "decisions": ["已拍板的技术决策"],
  "open_questions": ["需要人类确认的技术问题1"]
}}

只输出 JSON，不要额外说明。"""
    },
    "backend_engineer": {
        "name": "后端工程师",
        "description": "负责后端服务代码实现、单元测试、接口实现",
        "system_role": "资深后端工程师",
        "agent_id": "backend_engineer",
        "prompt_template": """你是一位资深后端工程师。基于 API 契约和技术负责人的计划，实现后端代码：

任务：{task_title}
后端任务：{backend_tasks}
API 契约：{api_contracts}
数据库设计：{database_design}

请输出 JSON，格式如下：
{{
  "files_to_create": ["src/api/user.py"],
  "files_to_modify": [],
  "implementation_notes": "实现要点",
  "instruction_for_opencode": "用自然语言描述希望 OpenCode 自动执行的后端编程任务，包括目录、文件、依赖"
}}

只输出 JSON，不要额外说明。"""
    },
    "frontend_engineer": {
        "name": "前端工程师",
        "description": "负责前端页面/组件实现、接口调用、UI 交互",
        "system_role": "资深前端工程师",
        "agent_id": "frontend_engineer",
        "prompt_template": """你是一位资深前端工程师。基于 API 契约和技术负责人的计划，实现前端代码：

任务：{task_title}
前端任务：{frontend_tasks}
API 契约：{api_contracts}

请输出 JSON，格式如下：
{{
  "files_to_create": ["src/pages/UserList.tsx"],
  "files_to_modify": [],
  "implementation_notes": "实现要点",
  "instruction_for_opencode": "用自然语言描述希望 OpenCode 自动执行的前端编程任务，包括目录、文件、依赖"
}}

只输出 JSON，不要额外说明。"""
    },
    "code_reviewer": {
        "name": "代码评审员",
        "description": "负责代码质量、规范、边界条件、错误处理评审",
        "system_role": "资深代码评审员",
        "agent_id": "code_reviewer",
        "prompt_template": """你是一位资深代码评审员。请评审以下代码变更：

任务：{task_title}
后端实现：{backend_result}
前端实现：{frontend_result}
验收标准：{acceptance_criteria}
OpenCode 实际变更摘要：{opencode_changes}
自动化验证结果：{validation_result}

请输出 JSON，格式如下：
{{
  "review_comments": [
    "评审意见1"
  ],
  "review_approved": true,
  "issues": ["发现的问题1"],
  "human_review_needed": false,
  "human_review_reason": ""
}}

只输出 JSON，不要额外说明。"""
    },
    "security_engineer": {
        "name": "安全工程师",
        "description": "负责安全审计、注入、越权、敏感信息检查（可选节点）",
        "system_role": "资深安全工程师",
        "agent_id": "security_engineer",
        "optional": True,
        "prompt_template": """你是一位资深安全工程师。请对以下实现做安全审计：

任务：{task_title}
后端实现：{backend_result}
前端实现：{frontend_result}
API 契约：{api_contracts}

请输出 JSON，格式如下：
{{
  "security_risks": [
    {{"risk": "风险描述", "severity": "high|medium|low", "mitigation": "修复建议"}}
  ],
  "approved": true,
  "blockers": []
}}

只输出 JSON，不要额外说明。"""
    },
    "qa_engineer": {
        "name": "测试工程师",
        "description": "负责测试计划、测试用例、测试执行、缺陷报告",
        "system_role": "资深测试工程师",
        "agent_id": "qa_engineer",
        "prompt_template": """你是一位资深测试工程师。请为以下功能制定并执行测试计划：

任务：{task_title}
验收标准：{acceptance_criteria}
后端实现：{backend_result}
前端实现：{frontend_result}
自动化验证结果：{validation_result}

请输出 JSON，格式如下：
{{
  "test_plan": {{
    "unit_tests": ["test_case_1"],
    "integration_tests": ["test_case_2"],
    "edge_cases": ["边界场景1"]
  }},
  "test_results": [
    {{"name": "test_case_1", "status": "passed"}}
  ],
  "test_passed": true,
  "instruction_for_opencode": "让 OpenCode 运行相关测试或补充缺失测试的命令描述"
}}

只输出 JSON，不要额外说明。"""
    },
    "technical_writer": {
        "name": "技术文档工程师",
        "description": "负责 API 文档、README、CHANGELOG、使用说明编写",
        "system_role": "资深技术文档工程师",
        "agent_id": "technical_writer",
        "prompt_template": """你是一位资深技术文档工程师。基于实现和测试情况，编写/更新文档：

任务：{task_title}
API 契约：{api_contracts}
实现摘要：{implementation_summary}
测试结果：{test_results}

请输出 JSON，格式如下：
{{
  "documents_to_update": [
    {{"file": "README.md", "content": "文档内容摘要"}}
  ],
  "instruction_for_opencode": "让 OpenCode 写入或更新文档的自然语言描述"
}}

只输出 JSON，不要额外说明。"""
    },
    "project_manager": {
        "name": "项目经理",
        "description": "负责任务跟踪、进度管理、风险识别、总结报告",
        "system_role": "资深项目经理",
        "agent_id": "project_manager",
        "prompt_template": """你是一位资深项目经理。请汇总当前任务状态并输出最终报告：

任务：{task_title}
当前阶段：{current_phase}
代码评审：{review_approved}
安全评审：{security_approved}
测试：{test_passed}
实现摘要：{implementation_summary}

请输出 JSON，格式如下：
{{
  "status": "completed|failed|blocked",
  "progress": 100,
  "blockers": ["阻塞项1"],
  "summary": "任务总结",
  "next_actions": ["下一步行动1"]
}}

只输出 JSON，不要额外说明。"""
    },
}


# ============================================================
# 状态定义
# ============================================================

def _last_value(old: str, new: str) -> str:
    return new if new else old

def _max_progress(old: float, new: float) -> float:
    return max(old, new)


class TaskState(TypedDict):
    """任务全局状态"""

    task_id: str
    task_title: str
    task_description: str
    project_dir: Optional[str]
    chat_id: Optional[str]
    created_at: str

    # 控制流
    # current_phase / progress 使用 reducer 允许并行节点同时写入
    current_phase: Annotated[str, _last_value]
    status: str
    progress: Annotated[float, _max_progress]
    retry_count: int
    max_retries: int
    error: Optional[str]

    # Human-in-the-Loop
    human_review_phase: Optional[str]
    human_review_question: Optional[str]
    human_feedback: Optional[str]
    needs_human_review: bool

    # 需求线
    requirements: Optional[Dict[str, Any]]
    business_analysis: Optional[Dict[str, Any]]

    # 设计线
    architecture_design: Optional[Dict[str, Any]]
    api_contracts: Optional[Dict[str, Any]]
    database_design: Optional[Dict[str, Any]]
    devops_plan: Optional[Dict[str, Any]]

    # 管理线
    implementation_plan: Optional[Dict[str, Any]]
    tech_lead_decisions: Optional[Dict[str, Any]]

    # 实现线
    backend_result: Optional[Dict[str, Any]]
    frontend_result: Optional[Dict[str, Any]]
    code_changes: List[Dict[str, Any]]
    files_modified: List[str]
    # opencode_results 使用 operator.add reducer：
    # 并行节点（backend_engineer / frontend_engineer）各返回 delta=[new_item]，
    # LangGraph 自动合并，避免最后写入覆盖问题。
    opencode_results: Annotated[List[Dict[str, Any]], operator.add]

    # 质量线
    review_result: Optional[Dict[str, Any]]
    review_comments: List[str]
    review_approved: bool
    security_result: Optional[Dict[str, Any]]
    security_approved: bool
    api_review_result: Optional[Dict[str, Any]]
    test_plan: Optional[Dict[str, Any]]
    test_results: List[Dict[str, Any]]
    test_passed: bool
    validation_result: Optional[Dict[str, Any]]
    documents: Optional[Dict[str, Any]]

    # 最终输出
    summary: str
    blockers: List[str]
    next_actions: List[str]


# ============================================================
# 通用辅助函数
# ============================================================

# 使用 LangGraph reducer 管理的字段——节点返回时只放 delta，不做整体传播
_REDUCER_FIELDS: frozenset = frozenset({"opencode_results"})


def _state_update(state: TaskState, **updates: Any) -> dict:
    """构建安全的状态更新字典。

    只返回显式提供的 updates，不自动透传其他状态字段，
    避免并行节点同时写入相同字段导致冲突。
    LangGraph 在步骤间自动保留未显式返回的状态字段。
    reducer 字段（如 opencode_results）应由对应节点的 delta 触发，
    不在本函数中自动从 base state 带出，避免 reducer 重复累加。
    """
    return dict(updates)


def _safe_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if value else "{}"


def _extract_opencode_instruction(result: Dict[str, Any]) -> str:
    return result.get("instruction_for_opencode", "")


def _maybe_call_opencode(
    result: Dict[str, Any],
    cwd: Optional[str] = None,
    label: str = "",
) -> Optional[Dict[str, Any]]:
    """如果 LLM 结果包含 opencode instruction，则通过 HTTP API 调用 OpenCode（使用当前模型）"""
    instruction = _extract_opencode_instruction(result)
    if not instruction:
        return None
    model_str = resolve_active_model()
    opencode_result = call_opencode(instruction, cwd=cwd, model_str=model_str)
    if label:
        append_daily_note(f"调用 OpenCode [{label}]: {instruction[:80]}...", label)
    return opencode_result


def _collect_text_fragments(value: Any, fragments: List[str], depth: int = 0, max_items: int = 60) -> None:
    if depth > 6 or len(fragments) >= max_items:
        return
    if isinstance(value, str):
        text = value.strip()
        if text:
            fragments.append(text)
        return
    if isinstance(value, dict):
        for item in value.values():
            _collect_text_fragments(item, fragments, depth + 1, max_items)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text_fragments(item, fragments, depth + 1, max_items)


def _extract_opencode_text(opencode_result: Dict[str, Any]) -> str:
    fragments: List[str] = []
    _collect_text_fragments(opencode_result.get("result"), fragments)
    if opencode_result.get("error"):
        fragments.append(str(opencode_result.get("error")))

    unique_lines: List[str] = []
    seen = set()
    for fragment in fragments:
        line = fragment.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        unique_lines.append(line)
        if len(unique_lines) >= 20:
            break
    return "\n".join(unique_lines)


def _extract_diff_paths(diff_items: Any) -> List[str]:
    paths: List[str] = []
    if not isinstance(diff_items, list):
        return paths
    for item in diff_items:
        if isinstance(item, str):
            paths.append(item)
            continue
        if not isinstance(item, dict):
            continue
        for key in ("path", "file", "filename", "newPath", "oldPath"):
            value = item.get(key)
            if isinstance(value, str) and value:
                paths.append(value)
                break
    return list(dict.fromkeys(paths))


def _summarize_opencode_changes(opencode_results: List[Dict[str, Any]], max_items: int = 6) -> str:
    if not opencode_results:
        return "[]"

    summary: List[Dict[str, Any]] = []
    for entry in opencode_results[-max_items:]:
        role = entry.get("role", "unknown")
        result = entry.get("result", {})
        diff_paths = _extract_diff_paths(result.get("diff", [])) if isinstance(result, dict) else []
        text_excerpt = _extract_opencode_text(result) if isinstance(result, dict) else ""
        summary.append({
            "role": role,
            "returncode": result.get("returncode") if isinstance(result, dict) else None,
            "diff_files": diff_paths[:8],
            "diff_file_count": len(diff_paths),
            "diff_excerpt": json.dumps(result.get("diff", []), ensure_ascii=False)[:2500] if isinstance(result, dict) else "",
            "error": result.get("error") if isinstance(result, dict) else "invalid_result",
            "output_excerpt": text_excerpt[:1000],
        })
    return json.dumps(summary, ensure_ascii=False)


def _build_validation_instruction(task_title: str, acceptance_criteria: List[Any]) -> str:
    criteria_text = json.dumps(acceptance_criteria or [], ensure_ascii=False)
    return f"""你是资深测试与构建工程师，请在当前项目目录执行自动化验证。

任务：{task_title}
验收标准：{criteria_text}

要求：
1. 自动识别项目语言与包管理方式（如 Node.js/Python/Go/Rust 等）。
2. 尽量执行可用的命令：lint、类型检查、构建、测试。
3. 不要修改业务代码；仅执行验证并收集结果。
4. 如果某类命令不可用（缺脚本/缺依赖），记录为 skipped。

最后只输出 JSON，格式如下：
{{
  "detected_stack": ["nodejs", "python"],
  "commands": [
    {{"command": "npm run test", "status": "passed|failed|skipped", "summary": "..."}}
  ],
  "results": [
    {{"name": "test_suite", "status": "passed|failed|skipped", "details": "..."}}
  ],
  "passed": true,
  "failures": ["失败原因"],
  "summary": "验证总结"
}}"""


def _parse_validation_report(opencode_result: Dict[str, Any]) -> Dict[str, Any]:
    output_text = _extract_opencode_text(opencode_result)
    parsed = parse_json_from_llm(output_text) or {}

    if parsed:
        parsed.setdefault("commands", [])
        parsed.setdefault("results", [])
        parsed.setdefault("failures", [])
        if "passed" not in parsed:
            parsed["passed"] = opencode_result.get("returncode", 1) == 0 and not parsed.get("failures")
        parsed.setdefault("summary", "自动化验证已执行")
        return parsed

    error = opencode_result.get("error")
    return {
        "detected_stack": [],
        "commands": [],
        "results": [],
        "passed": bool(opencode_result.get("returncode", 1) == 0 and not error),
        "failures": [str(error)] if error else ["未能解析自动化验证输出"],
        "summary": "自动化验证输出非结构化，已按回退策略处理",
        "raw_excerpt": output_text[:1200],
    }


def _gen_feishu_sign(timestamp: str, secret: str) -> str:
    """生成飞书自定义机器人签名字符串。

    算法：签名字符串 = timestamp + "\\n" + secret
          用 HmacSHA256 计算，再 Base64 编码。

    参考：https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot
    """
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
    ).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _notify_feishu_via_api(chat_id: str, text: str) -> bool:
    """通过飞书 Bot API 主动发送消息到指定群。

    用 FEISHU_APP_ID + FEISHU_APP_SECRET 换取 tenant_access_token，
    再调用 im/v1/messages 接口。不需要 webhook。
    """
    app_id = os.environ.get("FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return False
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=5,
        )
        data = resp.json()
        token = data.get("tenant_access_token", "")
        if not token:
            print(f"[FEISHU API] 获取 tenant_access_token 失败: {data}", flush=True)
            return False
        content = json.dumps({"text": text}, ensure_ascii=False)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id",
            headers=headers,
            json={"receive_id": chat_id, "msg_type": "text", "content": content},
            timeout=5,
        )
        if not resp.ok:
            print(f"[FEISHU API] 发送失败: {resp.status_code} {resp.text[:300]}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"[FEISHU API] 异常: {e}", flush=True)
        return False


def _notify_phase_progress(state: TaskState) -> None:
    """向飞书推送阶段进度通知。优先使用 Bot API + chat_id，fallback 到 webhook。"""
    chat_id = state.get("chat_id", "") or ""
    task_title = (state.get("task_title", "") or "").strip()
    task_id = (state.get("task_id", "") or "").strip()
    phase = state.get("current_phase", "")
    progress = state.get("progress", 0)
    model_str = resolve_active_model()
    model_tag = f" [{model_str}]" if model_str else ""
    task_info = task_title if task_title else "未知任务"
    if task_id:
        task_info += f" ({task_id})"
    text = (
        f"多 Agent 编程进度更新{model_tag}\n"
        f"任务: {task_info}\n"
        f"阶段: {phase}\n"
        f"进度: {int(progress)}%"
    )
    print(f"[PROGRESS] {text}", flush=True)
    if chat_id:
        if _notify_feishu_via_api(chat_id, text):
            return
    feishu_webhook = os.environ.get("FEISHU_NOTIFY_WEBHOOK", "").strip()
    if feishu_webhook:
        try:
            timestamp = str(int(time.time()))
            payload = {"msg_type": "text", "content": {"text": text}}
            secret = os.environ.get("FEISHU_NOTIFY_SECRET", "").strip()
            if secret:
                payload["timestamp"] = timestamp
                payload["sign"] = _gen_feishu_sign(timestamp, secret)
            requests.post(feishu_webhook, json=payload, timeout=5)
        except Exception:
            pass


def _notify_human_review_needed(state: TaskState, phase: str, question: str) -> None:
    """通知人类审批需求。

    通知渠道（按优先级）：
    1. 飞书 Webhook（FEISHU_NOTIFY_WEBHOOK）
    2. Telegram Bot（TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID）
    3. 持久化到文件（保底，确保 OpenClaw 可查询）
    4. stdout 打印（日志可见）
    """
    task_title = state.get("task_title", "")
    task_id = state.get("task_id", "")
    thread_id = state.get("task_id", "")
    created_at = state.get("created_at", "")
    current_phase = state.get("current_phase", "")
    progress = state.get("progress", 0)

    # 构建结构化消息
    resume_cmd = f"/auto-programming resume:{task_id} 通过"
    resume_cmd_reject = f"/auto-programming resume:{task_id} 驳回，请修改"

    message = (
        f"🤖 **多 Agent 编程工作流需要人工审批**\n\n"
        f"**任务**: {task_title}\n"
        f"**阶段**: {phase}\n"
        f"**当前进度**: {progress}%\n"
        f"**task_id**: `{task_id}`\n\n"
        f"**审批内容**: {question}\n\n"
        f"**恢复命令**:\n"
        f"- 通过: `{resume_cmd}`\n"
        f"- 驳回: `{resume_cmd_reject}`\n\n"
        f"也可直接在飞书/Telegram 回复 \"通过\" 或 \"驳回\"，我帮你转发。"
    )

    # 简单文本版本（用于 webhook）
    simple_message = (
        f"[HITL] 阶段={phase} 任务={task_title} task_id={task_id} "
        f"问题={question} 恢复命令: {resume_cmd}"
    )

    # 1. 持久化到文件（保底机制，无论 webhook 是否配置）
    _persist_human_review_request(state, phase, question)

    # 2. stdout 打印（日志可见）
    print(f"[HITL NOTIFY] {simple_message}", flush=True)

    # 3. 飞书 Bot API（优先，可指定 chat_id）
    chat_id = state.get("chat_id", "") or ""
    if chat_id:
        if _notify_feishu_via_api(chat_id, message):
            return
        print(f"[HITL NOTIFY] Bot API 发送失败，回退 webhook", flush=True)

    # 4. 飞书 Webhook（fallback）
    feishu_webhook = os.environ.get("FEISHU_NOTIFY_WEBHOOK", "").strip()
    if feishu_webhook:
        try:
            feishu_secret = os.environ.get("FEISHU_NOTIFY_SECRET", "").strip()
            timestamp = str(int(time.time()))
            payload = {
                "msg_type": "text",
                "content": {"text": message},
            }
            if feishu_secret:
                payload["timestamp"] = timestamp
                payload["sign"] = _gen_feishu_sign(timestamp, feishu_secret)
            resp = requests.post(feishu_webhook, json=payload, timeout=5)
            if not resp.ok:
                print(f"[HITL NOTIFY] 飞书 webhook 返回错误: {resp.status_code} {resp.text[:200]}", flush=True)
        except Exception as exc:
            print(f"[HITL NOTIFY] 飞书 webhook 发送失败: {exc}", flush=True)
    else:
        print(f"[HITL NOTIFY] 飞书 webhook 未配置 (FEISHU_NOTIFY_WEBHOOK)，跳过", flush=True)

    # 4. Telegram
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if telegram_token and telegram_chat_id:
        try:
            requests.post(
                f"https://api.telegram.org/bot{telegram_token}/sendMessage",
                json={"chat_id": telegram_chat_id, "text": message},
                timeout=5,
            )
        except Exception as exc:
            print(f"[HITL NOTIFY] Telegram 发送失败: {exc}", flush=True)
    else:
        print(f"[HITL NOTIFY] Telegram 未配置，跳过", flush=True)


def _persist_human_review_request(state: TaskState, phase: str, question: str) -> None:
    """将审批请求持久化到文件，供 OpenClaw 查询。"""
    task_id = state.get("task_id", "")
    pending_file = "/data/langgraph/pending_human_reviews.json"

    try:
        # 读取现有记录
        existing = []
        if os.path.exists(pending_file):
            try:
                with open(pending_file, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                    if not isinstance(existing, list):
                        existing = []
            except Exception:
                existing = []

        # 查找并更新或新增
        record = {
            "task_id": task_id,
            "task_title": state.get("task_title", ""),
            "phase": phase,
            "question": question,
            "current_phase": state.get("current_phase", ""),
            "progress": state.get("progress", 0),
            "created_at": state.get("created_at", ""),
            "notified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "pending",
        }

        # 去重：更新已有记录
        updated = False
        for i, item in enumerate(existing):
            if isinstance(item, dict) and item.get("task_id") == task_id:
                existing[i] = record
                updated = True
                break

        if not updated:
            existing.append(record)

        # 写入文件
        with open(pending_file, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[HITL NOTIFY] 持久化审批请求失败: {exc}", flush=True)


def _resolve_cwd(state: TaskState) -> str:
    """根据 state 中的 project_dir 确定 OpenCode 工作目录。"""
    project_dir = state.get("project_dir", "") or ""
    if project_dir:
        return f"/data/code/{project_dir}"
    return os.environ.get("CODE_DIR", "/data/code")


def _role_node(
    state: TaskState,
    role_key: str,
    prompt_inputs: Dict[str, Any],
    state_updates: Dict[str, Any],
    call_opencode_label: str = "",
    cwd: Optional[str] = None,
) -> dict:
    """通用角色节点：调用 LLM + 解析 JSON + 可选调用 OpenCode + 更新状态

    对于 opencode_results（reducer 字段），只返回新增的 delta=[item]，
    由 LangGraph 的 operator.add reducer 负责累加到全局状态中。
    """
    role = ROLES[role_key]
    prompt = role["prompt_template"].format(**prompt_inputs)

    try:
        result_text = call_llm(
            prompt,
            role=role["system_role"],
            role_key=role_key,
            agent_id=role.get("agent_id"),
            state=state,
        )
        result = parse_json_from_llm(result_text) or {}
    except Exception as e:
        result = {"error": str(e)}

    # 使用 _state_update 剔除 reducer 字段后再展开
    update = _state_update(state, **state_updates)
    update[f"{role_key}_result"] = result

    if call_opencode_label:
        opencode_result = _maybe_call_opencode(result, cwd=cwd, label=call_opencode_label)
        if opencode_result is not None:
            # 只返回 delta；LangGraph reducer 负责追加到现有列表
            update["opencode_results"] = [{"role": role_key, "result": opencode_result}]

    # 推送阶段进度通知
    phase = state_updates.get("current_phase", "")
    if phase:
        dummy_state = dict(state)
        dummy_state["current_phase"] = phase
        dummy_state["progress"] = state_updates.get("progress", state.get("progress", 0))
        _notify_phase_progress(dummy_state)

    return update


# ============================================================
# Human-in-the-Loop 节点
# ============================================================

def human_review_node(state: TaskState) -> dict:
    """人类评审节点：向人类展示当前产出并等待反馈"""
    phase = state.get("human_review_phase", "unknown")
    question = state.get("human_review_question", "请确认是否继续？")
    _notify_human_review_needed(state, phase, question)

    feedback = interrupt({
        "phase": phase,
        "question": question,
        "task_id": state.get("task_id"),
        "task_title": state.get("task_title"),
        "current_phase": state.get("current_phase"),
        "summary": _build_review_summary(state, phase),
    })

    return _state_update(
        state,
        human_feedback=feedback,
        needs_human_review=False,
    )


def _build_review_summary(state: TaskState, phase: str) -> str:
    if phase == "requirements_review":
        return _safe_json(state.get("business_analysis"))
    if phase == "architecture_review":
        return _safe_json(state.get("architecture_design"))
    if phase == "code_review":
        return json.dumps({
            "review_comments": state.get("review_comments", []),
            "review_approved": state.get("review_approved", False),
        }, ensure_ascii=False)
    return ""


def _is_approved(feedback: str) -> bool:
    """宽松匹配：检查 feedback 是否包含任一批准关键词"""
    feedback_lower = feedback.strip().lower()
    for kw in ("yes", "y", "ok", "通过", "approve", "approved", "确认", "同意"):
        if kw in feedback_lower:
            return True
    return False


def route_after_human_review(state: TaskState) -> str:
    feedback = (state.get("human_feedback") or "").strip()
    phase = state.get("human_review_phase", "")

    if _is_approved(feedback):
        if phase == "requirements_review":
            return "system_architect"
        if phase == "architecture_review":
            return "api_designer"
        if phase == "code_review":
            return "post_code_review_router"
        return "project_manager"

    if phase == "requirements_review":
        return "product_manager"
    if phase == "architecture_review":
        return "system_architect"
    if phase == "code_review":
        return "tech_lead"

    return "project_manager"


# ============================================================
# 各角色节点
# ============================================================

def product_manager_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    task_title = state.get("task_title", "")
    task_description = state.get("task_description", "")

    create_task(task_id, task_title, task_description)

    # 加载全局上下文（用户偏好、技术栈、历史经验等）并作为 preamble 注入 prompt
    global_context = load_global_context()
    preamble = ""
    if global_context:
        lines = ["【项目上下文参考（请结合以下背景信息进行分析）】"]
        for filename, content in global_context.items():
            lines.append(f"\n### {filename}\n{content}")
        preamble = "\n".join(lines) + "\n\n---\n\n"

    role = ROLES["product_manager"]
    prompt = preamble + role["prompt_template"].format(
        task_title=task_title,
        task_description=task_description,
    )

    try:
        result_text = call_llm(
            prompt,
            role=role["system_role"],
            role_key="product_manager",
            agent_id=role.get("agent_id"),
            state=state,
        )
        pm_result = parse_json_from_llm(result_text) or {}
    except Exception as e:
        pm_result = {"error": str(e)}

    new_state = _state_update(
        state,
        product_manager_result=pm_result,
        requirements=pm_result,
        current_phase="产品需求分析完成",
        progress=8.0,
    )

    update_task_phase(task_id, "requirements", pm_result)
    append_daily_note(f"产品经理完成需求分析: {task_title}", "product_manager")
    _notify_phase_progress({**state, **new_state})
    return new_state


def business_analyst_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    requirements = state.get("requirements", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "requirements": _safe_json(requirements),
        "user_stories": _safe_json(requirements.get("user_stories", [])),
        "acceptance_criteria": _safe_json(requirements.get("acceptance_criteria", [])),
    }
    new_state = _role_node(
        state,
        "business_analyst",
        prompt_inputs,
        {"current_phase": "业务分析完成", "progress": 15.0},
    )

    business_analysis = new_state.get("business_analyst_result", {})
    new_state["business_analysis"] = business_analysis
    update_task_phase(task_id, "business_analysis", business_analysis)
    append_daily_note(f"业务分析师完成分析: {state.get('task_title', '')}", "business_analyst")
    return new_state


def requirements_review_gate(state: TaskState) -> dict:
    requirements = state.get("requirements", {}) or {}
    business_analysis = state.get("business_analysis", {}) or {}

    open_questions = list(requirements.get("open_questions", []) or [])
    open_questions.extend(business_analysis.get("open_questions", []) or [])

    # 如已有审批反馈（上一轮循环后回到此节点），清除问题避免死循环
    existing_feedback = (state.get("human_feedback") or "").strip()
    if _is_approved(existing_feedback):
        req = dict(requirements)
        req["open_questions"] = []
        ba = dict(business_analysis)
        ba["open_questions"] = []
        return _state_update(
            state,
            requirements=req,
            business_analysis=ba,
            needs_human_review=False,
            human_review_phase="requirements_review",
            human_feedback="approved",
            current_phase="需求评审通过",
            progress=20.0,
        )

    if open_questions:
        return _state_update(
            state,
            needs_human_review=True,
            human_review_phase="requirements_review",
            human_review_question=(
                f"需求/业务分析存在以下待确认问题：{open_questions}。"
                "请确认（通过/驳回并说明原因）："
            ),
            current_phase="等待需求评审",
        )

    return _state_update(
        state,
        needs_human_review=False,
        human_review_phase="requirements_review",
        human_feedback="approved",
        current_phase="需求评审通过",
        progress=20.0,
    )


def route_requirements_review(state: TaskState) -> str:
    if state.get("needs_human_review") and not state.get("human_feedback"):
        return "human_review"
    if _is_approved(state.get("human_feedback", "")):
        return "system_architect"
    return "product_manager"


def system_architect_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    requirements = state.get("requirements", {})
    business_analysis = state.get("business_analysis", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "requirements": _safe_json(requirements),
        "business_analysis": _safe_json(business_analysis),
        "acceptance_criteria": _safe_json(requirements.get("acceptance_criteria", [])),
    }
    new_state = _role_node(
        state,
        "system_architect",
        prompt_inputs,
        {"current_phase": "架构设计完成", "progress": 30.0},
    )

    architecture_design = new_state.get("system_architect_result", {})
    new_state["architecture_design"] = architecture_design
    update_task_phase(task_id, "architecture", architecture_design)
    append_daily_note(f"架构师完成架构设计: {state.get('task_title', '')}", "system_architect")
    return new_state


def architecture_review_gate(state: TaskState) -> dict:
    architecture_design = state.get("architecture_design", {}) or {}
    open_questions = architecture_design.get("open_questions", []) or []

    existing_feedback = (state.get("human_feedback") or "").strip()
    if _is_approved(existing_feedback):
        ad = dict(architecture_design)
        ad["open_questions"] = []
        return _state_update(
            state,
            architecture_design=ad,
            needs_human_review=False,
            human_review_phase="architecture_review",
            human_feedback="approved",
            current_phase="架构评审通过",
            progress=35.0,
        )

    if open_questions:
        return _state_update(
            state,
            needs_human_review=True,
            human_review_phase="architecture_review",
            human_review_question=(
                f"架构设计存在以下待确认问题：{open_questions}。"
                "请确认（通过/驳回并说明原因）："
            ),
            current_phase="等待架构评审",
        )

    return _state_update(
        state,
        needs_human_review=False,
        human_review_phase="architecture_review",
        human_feedback="approved",
        current_phase="架构评审通过",
        progress=35.0,
    )


def route_architecture_review(state: TaskState) -> str:
    if state.get("needs_human_review") and not state.get("human_feedback"):
        return "human_review"
    if _is_approved(state.get("human_feedback", "")):
        return "api_designer"
    return "system_architect"


def api_designer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    architecture_design = state.get("architecture_design", {})
    business_analysis = state.get("business_analysis", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "architecture_design": _safe_json(architecture_design),
        "business_analysis": _safe_json(business_analysis),
    }
    new_state = _role_node(
        state,
        "api_designer",
        prompt_inputs,
        {"current_phase": "API 接口定义完成", "progress": 42.0},
    )

    api_contracts = new_state.get("api_designer_result", {})
    new_state["api_contracts"] = api_contracts
    update_task_phase(task_id, "api_design", api_contracts)
    append_daily_note(f"API 设计师完成接口定义: {state.get('task_title', '')}", "api_designer")
    return new_state


def database_engineer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    business_analysis = state.get("business_analysis", {})
    api_contracts = state.get("api_contracts", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "domain_entities": _safe_json(business_analysis.get("domain_entities", [])),
        "api_contracts": _safe_json(api_contracts),
    }
    new_state = _role_node(
        state,
        "database_engineer",
        prompt_inputs,
        {"current_phase": "数据库设计完成", "progress": 48.0},
    )

    database_design = new_state.get("database_engineer_result", {})
    new_state["database_design"] = database_design
    update_task_phase(task_id, "database_design", database_design)
    append_daily_note(f"数据库工程师完成设计: {state.get('task_title', '')}", "database_engineer")
    return new_state


def devops_engineer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    architecture_design = state.get("architecture_design", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "architecture_design": _safe_json(architecture_design),
        "tech_stack": _safe_json(architecture_design.get("tech_stack", [])),
    }
    new_state = _role_node(
        state,
        "devops_engineer",
        prompt_inputs,
        {"current_phase": "DevOps 计划完成", "progress": 50.0},
    )

    devops_plan = new_state.get("devops_engineer_result", {})
    new_state["devops_plan"] = devops_plan
    update_task_phase(task_id, "devops_plan", devops_plan)
    append_daily_note(f"DevOps 工程师完成计划: {state.get('task_title', '')}", "devops_engineer")
    return new_state


def tech_lead_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    architecture_design = state.get("architecture_design", {})
    api_contracts = state.get("api_contracts", {})
    database_design = state.get("database_design", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "architecture_design": _safe_json(architecture_design),
        "api_contracts": _safe_json(api_contracts),
        "database_design": _safe_json(database_design),
    }
    new_state = _role_node(
        state,
        "tech_lead",
        prompt_inputs,
        {"current_phase": "技术负责人拆件完成", "progress": 55.0},
    )

    tech_lead_result = new_state.get("tech_lead_result", {})
    new_state["implementation_plan"] = tech_lead_result.get("implementation_plan", {})
    new_state["tech_lead_decisions"] = tech_lead_result.get("decisions", [])
    update_task_phase(task_id, "tech_lead", tech_lead_result)
    append_daily_note(f"技术负责人完成拆件: {state.get('task_title', '')}", "tech_lead")
    return new_state


def backend_engineer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    implementation_plan = state.get("implementation_plan", {})
    api_contracts = state.get("api_contracts", {})
    database_design = state.get("database_design", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "backend_tasks": _safe_json(implementation_plan.get("backend_tasks", [])),
        "api_contracts": _safe_json(api_contracts),
        "database_design": _safe_json(database_design),
    }
    new_state = _role_node(
        state,
        "backend_engineer",
        prompt_inputs,
        {},
        call_opencode_label="backend_engineer",
        cwd=_resolve_cwd(state),
    )

    backend_result = new_state.get("backend_engineer_result", {})
    new_state["backend_result"] = backend_result
    update_task_phase(task_id, "backend_implementation", backend_result)
    append_daily_note(f"后端工程师完成实现: {state.get('task_title', '')}", "backend_engineer")
    return new_state


def frontend_engineer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    implementation_plan = state.get("implementation_plan", {})
    api_contracts = state.get("api_contracts", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "frontend_tasks": _safe_json(implementation_plan.get("frontend_tasks", [])),
        "api_contracts": _safe_json(api_contracts),
    }
    new_state = _role_node(
        state,
        "frontend_engineer",
        prompt_inputs,
        {},
        call_opencode_label="frontend_engineer",
        cwd=_resolve_cwd(state),
    )

    frontend_result = new_state.get("frontend_engineer_result", {})
    new_state["frontend_result"] = frontend_result
    update_task_phase(task_id, "frontend_implementation", frontend_result)
    append_daily_note(f"前端工程师完成实现: {state.get('task_title', '')}", "frontend_engineer")
    return new_state


def merge_implementation_node(state: TaskState) -> dict:
    """合并前后端实现结果，准备进入评审"""
    backend = state.get("backend_result", {})
    frontend = state.get("frontend_result", {})

    files_modified = []
    files_modified.extend(backend.get("files_to_create", []))
    files_modified.extend(backend.get("files_to_modify", []))
    files_modified.extend(frontend.get("files_to_create", []))
    files_modified.extend(frontend.get("files_to_modify", []))

    code_changes = backend.get("code_changes", []) + frontend.get("code_changes", [])

    return _state_update(
        state,
        files_modified=list(dict.fromkeys(files_modified)),
        code_changes=code_changes,
        current_phase="前后端实现合并完成",
        progress=68.0,
    )


def quality_validation_node(state: TaskState) -> dict:
    """执行一次真实的编译/测试验证，并将结果回传到状态中。"""
    task_id = state.get("task_id", "")
    task_title = state.get("task_title", "")
    requirements = state.get("requirements", {}) or {}
    acceptance_criteria = requirements.get("acceptance_criteria", [])
    code_dir = _resolve_cwd(state)

    instruction = _build_validation_instruction(task_title, acceptance_criteria)
    opencode_result = call_opencode(instruction, cwd=code_dir)
    validation_result = _parse_validation_report(opencode_result)

    update_task_phase(task_id, "quality_validation", validation_result)
    append_daily_note(
        f"自动化验证完成: {task_title} - 通过={validation_result.get('passed', False)}",
        "quality_validation",
    )

    update = _state_update(
        state,
        validation_result=validation_result,
        current_phase="自动化编译/测试验证完成",
        progress=max(state.get("progress", 0), 70.0),
        opencode_results=[{"role": "quality_validation", "result": opencode_result}],
    )
    dummy_state = dict(state)
    dummy_state.update(update)
    _notify_phase_progress(dummy_state)
    return update


def tech_lead_first_review_node(state: TaskState) -> dict:
    """技术负责人初审：检查前后端实现是否满足计划"""
    task_id = state.get("task_id", "")
    implementation_plan = state.get("implementation_plan", {})
    backend = state.get("backend_result", {})
    frontend = state.get("frontend_result", {})

    prompt = f"""你是一位资深技术负责人。请初审前后端实现是否满足拆件计划：

任务：{state.get('task_title', '')}
后端实现：{_safe_json(backend)}
前端实现：{_safe_json(frontend)}
实现计划：{_safe_json(implementation_plan)}

请输出 JSON：
{{
  "approved": true,
  "comments": ["初审意见1"],
  "issues": []
}}
只输出 JSON。"""

    try:
        result_text = call_llm(prompt, role="资深技术负责人", role_key="tech_lead", agent_id="tech_lead", state=state)
        result = parse_json_from_llm(result_text) or {}
    except Exception as e:
        result = {"approved": True, "error": str(e)}

    update_task_phase(task_id, "tech_lead_first_review", result)
    return _state_update(
        state,
        tech_lead_first_review=result,
        current_phase="技术负责人初审完成",
        progress=72.0,
    )


def code_reviewer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    backend = state.get("backend_result", {})
    frontend = state.get("frontend_result", {})
    requirements = state.get("requirements", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "backend_result": _safe_json(backend),
        "frontend_result": _safe_json(frontend),
        "acceptance_criteria": _safe_json(requirements.get("acceptance_criteria", [])),
        "opencode_changes": _summarize_opencode_changes(state.get("opencode_results", [])),
        "validation_result": _safe_json(state.get("validation_result", {})),
    }
    new_state = _role_node(
        state,
        "code_reviewer",
        prompt_inputs,
        {"current_phase": "代码评审完成", "progress": 78.0},
    )

    review_result = new_state.get("code_reviewer_result", {})
    new_state["review_result"] = review_result
    new_state["review_comments"] = review_result.get("review_comments", [])
    new_state["review_approved"] = review_result.get("review_approved", False)
    update_task_phase(task_id, "code_review", review_result)
    append_daily_note(
        f"代码评审完成: {state.get('task_title', '')} - 通过={review_result.get('review_approved', False)}",
        "code_reviewer",
    )
    return new_state


def code_review_gate(state: TaskState) -> dict:
    """代码评审门控：需要人类介入时暂停"""
    review_result = state.get("review_result", {})
    human_needed = review_result.get("human_review_needed", False)
    approved = state.get("review_approved", False)

    if human_needed or not approved:
        return _state_update(
            state,
            needs_human_review=True,
            human_review_phase="code_review",
            human_review_question=(
                f"代码评审意见：{state.get('review_comments', [])}。"
                "请确认（通过/驳回并说明原因）："
            ),
            current_phase="等待代码评审",
        )

    return _state_update(
        state,
        needs_human_review=False,
        human_review_phase="code_review",
        human_feedback="approved",
        current_phase="代码评审通过",
        progress=80.0,
    )


def route_code_review(state: TaskState) -> str:
    if state.get("needs_human_review") and not state.get("human_feedback"):
        return "human_review"
    if _is_approved(state.get("human_feedback", "")):
        return "post_code_review_router"
    return "tech_lead"


def security_engineer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    backend = state.get("backend_result", {})
    frontend = state.get("frontend_result", {})
    api_contracts = state.get("api_contracts", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "backend_result": _safe_json(backend),
        "frontend_result": _safe_json(frontend),
        "api_contracts": _safe_json(api_contracts),
    }
    new_state = _role_node(state, "security_engineer", prompt_inputs, {})

    security_result = new_state.get("security_engineer_result", {})
    new_state["security_result"] = security_result
    new_state["security_approved"] = security_result.get("approved", False)
    update_task_phase(task_id, "security_review", security_result)
    append_daily_note(
        f"安全评审完成: {state.get('task_title', '')} - 通过={security_result.get('approved', False)}",
        "security_engineer",
    )
    return new_state


def api_review_node(state: TaskState) -> dict:
    """API 设计师复核：可选节点，检查实现是否符合接口契约"""
    task_id = state.get("task_id", "")
    api_contracts = state.get("api_contracts", {})
    backend = state.get("backend_result", {})
    frontend = state.get("frontend_result", {})

    prompt = f"""你是一位资深 API 设计师。请复核实现是否符合接口契约：

任务：{state.get('task_title', '')}
API 契约：{_safe_json(api_contracts)}
后端实现：{_safe_json(backend)}
前端实现：{_safe_json(frontend)}

请输出 JSON：
{{
  "approved": true,
  "api_mismatches": [],
  "suggestions": []
}}
只输出 JSON。"""

    try:
        result_text = call_llm(prompt, role="资深 API 设计师", role_key="api_designer", agent_id="api_designer", state=state)
        result = parse_json_from_llm(result_text) or {}
    except Exception as e:
        result = {"approved": True, "error": str(e)}

    update_task_phase(task_id, "api_review", result)
    return _state_update(
        state,
        api_review_result=result,
        current_phase="API 复核完成",
        progress=max(state.get("progress", 0), 84.0),
    )


def qa_engineer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    requirements = state.get("requirements", {})
    backend = state.get("backend_result", {})
    frontend = state.get("frontend_result", {})

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "acceptance_criteria": _safe_json(requirements.get("acceptance_criteria", [])),
        "backend_result": _safe_json(backend),
        "frontend_result": _safe_json(frontend),
        "validation_result": _safe_json(state.get("validation_result", {})),
    }
    new_state = _role_node(
        state,
        "qa_engineer",
        prompt_inputs,
        {"current_phase": "测试完成", "progress": 88.0},
        call_opencode_label="qa_engineer",
        cwd=_resolve_cwd(state),
    )

    test_result = new_state.get("qa_engineer_result", {})
    validation_result = state.get("validation_result", {}) or {}
    fallback_passed = bool(validation_result.get("passed", False))
    llm_test_passed = bool(test_result.get("test_passed", fallback_passed))
    test_passed = bool(fallback_passed and llm_test_passed)
    test_results = test_result.get("test_results")
    if not isinstance(test_results, list) or not test_results:
        test_results = validation_result.get("results", [])

    next_retry_count = state.get("retry_count", 0)
    if test_passed:
        next_retry_count = 0
    else:
        next_retry_count += 1

    new_state["test_plan"] = test_result.get("test_plan", {})
    new_state["test_results"] = test_results if isinstance(test_results, list) else []
    new_state["test_passed"] = test_passed
    new_state["retry_count"] = next_retry_count
    if not fallback_passed:
        existing_comments = list(new_state.get("review_comments", []))
        new_state["review_comments"] = existing_comments + [
            f"自动化验证失败: {validation_result.get('summary', '未知原因')}"
        ]
    update_task_phase(task_id, "testing", test_result)
    append_daily_note(
        f"测试完成: {state.get('task_title', '')} - 通过={test_result.get('test_passed', False)}",
        "qa_engineer",
    )
    return new_state


def technical_writer_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    api_contracts = state.get("api_contracts", {})
    test_results = state.get("test_results", [])

    implementation_summary = {
        "backend": state.get("backend_result", {}),
        "frontend": state.get("frontend_result", {}),
        "files_modified": state.get("files_modified", []),
    }

    prompt_inputs = {
        "task_title": state.get("task_title", ""),
        "api_contracts": _safe_json(api_contracts),
        "implementation_summary": _safe_json(implementation_summary),
        "test_results": _safe_json(test_results),
    }
    new_state = _role_node(
        state,
        "technical_writer",
        prompt_inputs,
        {"current_phase": "技术文档更新完成", "progress": 94.0},
        call_opencode_label="technical_writer",
        cwd=_resolve_cwd(state),
    )

    documents = new_state.get("technical_writer_result", {})
    new_state["documents"] = documents
    update_task_phase(task_id, "documentation", documents)
    append_daily_note(f"技术文档更新完成: {state.get('task_title', '')}", "technical_writer")
    return new_state


def project_manager_node(state: TaskState) -> dict:
    task_id = state.get("task_id", "")
    task_title = state.get("task_title", "")

    implementation_summary = {
        "backend": state.get("backend_result", {}),
        "frontend": state.get("frontend_result", {}),
        "files_modified": state.get("files_modified", []),
    }

    prompt_inputs = {
        "task_title": task_title,
        "current_phase": state.get("current_phase", ""),
        "review_approved": state.get("review_approved", False),
        "security_approved": state.get("security_approved", False),
        "test_passed": state.get("test_passed", False),
        "implementation_summary": _safe_json(implementation_summary),
    }
    new_state = _role_node(
        state,
        "project_manager",
        prompt_inputs,
        {"current_phase": "任务完成", "progress": 100.0},
    )

    pm_result = new_state.get("project_manager_result", {})
    status = pm_result.get("status", "completed" if state.get("test_passed", False) else "failed")
    summary = pm_result.get("summary", f"任务 {task_title} 完成")
    blockers = pm_result.get("blockers", [])

    new_state["status"] = status
    new_state["summary"] = summary
    new_state["blockers"] = blockers
    new_state["next_actions"] = pm_result.get("next_actions", [])
    new_state["current_phase"] = "任务完成" if status == "completed" else "任务失败"

    complete_task(task_id, summary, status)
    append_daily_note(f"项目经理完成任务总结: {task_title} - {status}", "project_manager")

    if status == "completed":
        append_to_memory_md("经验教训", f"{task_title}: 任务顺利进行")
        append_to_memory_md("重要决策", f"{task_title}: 采用当前架构和测试策略")

    # 通知时必须使用完整状态（含 task_title/task_id），new_state 只是 update delta
    notify_state = dict(state)
    notify_state.update(new_state)
    _notify_phase_progress(notify_state)
    return new_state


# ============================================================
# 可选节点路由
# ============================================================

def route_security_or_qa(state: TaskState) -> str:
    if os.environ.get("ENABLE_SECURITY_REVIEW", "false").lower() in ("true", "1", "yes"):
        return "security_engineer"
    return "qa_engineer"


def route_after_security(state: TaskState) -> str:
    if os.environ.get("ENABLE_API_REVIEW", "false").lower() in ("true", "1", "yes"):
        return "api_review"
    return "qa_engineer"


def post_code_review_router_node(state: TaskState) -> dict:
    """代码评审通过后，根据开关决定进入安全评审还是测试"""
    return _state_update(
        state,
        current_phase="代码评审通过，准备进入可选安全/API复核",
        progress=max(state.get("progress", 0), 81.0),
    )


def route_after_test(state: TaskState) -> str:
    if state.get("test_passed"):
        return "technical_writer"
    retry_count = state.get("retry_count", 0)
    if retry_count >= state.get("max_retries", 3):
        return "project_manager"
    return "tech_lead"


# ============================================================
# 构建工作流图
# ============================================================

def build_graph():
    """构建多角色自动化编程工作流图"""
    graph = StateGraph(TaskState)

    # 注册节点
    graph.add_node("product_manager", product_manager_node)
    graph.add_node("business_analyst", business_analyst_node)
    graph.add_node("requirements_review_gate", requirements_review_gate)
    graph.add_node("human_review", human_review_node)
    graph.add_node("system_architect", system_architect_node)
    graph.add_node("architecture_review_gate", architecture_review_gate)
    graph.add_node("api_designer", api_designer_node)
    graph.add_node("database_engineer", database_engineer_node)
    graph.add_node("devops_engineer", devops_engineer_node)
    graph.add_node("tech_lead", tech_lead_node)
    graph.add_node("backend_engineer", backend_engineer_node)
    graph.add_node("frontend_engineer", frontend_engineer_node)
    graph.add_node("merge_implementation", merge_implementation_node)
    graph.add_node("quality_validation", quality_validation_node)
    graph.add_node("tech_lead_first_review", tech_lead_first_review_node)
    graph.add_node("code_reviewer", code_reviewer_node)
    graph.add_node("code_review_gate", code_review_gate)
    graph.add_node("security_engineer", security_engineer_node)
    graph.add_node("api_review", api_review_node)
    graph.add_node("post_code_review_router", post_code_review_router_node)
    graph.add_node("qa_engineer", qa_engineer_node)
    graph.add_node("technical_writer", technical_writer_node)
    graph.add_node("project_manager", project_manager_node)

    # 入口
    graph.set_entry_point("product_manager")

    # 主流程
    graph.add_edge("product_manager", "business_analyst")
    graph.add_edge("business_analyst", "requirements_review_gate")

    # 需求评审分支
    graph.add_conditional_edges("requirements_review_gate", route_requirements_review, {
        "human_review": "human_review",
        "system_architect": "system_architect",
        "product_manager": "product_manager",
    })

    # 人类评审后路由
    graph.add_conditional_edges("human_review", route_after_human_review, {
        "product_manager": "product_manager",
        "system_architect": "system_architect",
        "api_designer": "api_designer",
        "tech_lead": "tech_lead",
        "post_code_review_router": "post_code_review_router",
        "project_manager": "project_manager",
    })

    graph.add_edge("system_architect", "architecture_review_gate")

    # 架构评审分支
    graph.add_conditional_edges("architecture_review_gate", route_architecture_review, {
        "human_review": "human_review",
        "api_designer": "api_designer",
        "system_architect": "system_architect",
    })

    # 接口定义后进入设计（in-memory运行时串行避免死锁）
    graph.add_edge("api_designer", "database_engineer")
    graph.add_edge("database_engineer", "devops_engineer")
    graph.add_edge("devops_engineer", "tech_lead")

    # 技术负责人拆件后，前后端开发（in-memory运行时串行避免死锁）
    graph.add_edge("tech_lead", "backend_engineer")
    graph.add_edge("backend_engineer", "frontend_engineer")

    # 合并实现结果
    graph.add_edge("frontend_engineer", "merge_implementation")

    graph.add_edge("merge_implementation", "quality_validation")
    graph.add_edge("quality_validation", "tech_lead_first_review")
    graph.add_edge("tech_lead_first_review", "code_reviewer")

    # 代码评审分支
    graph.add_edge("code_reviewer", "code_review_gate")
    graph.add_conditional_edges("code_review_gate", route_code_review, {
        "human_review": "human_review",
        "post_code_review_router": "post_code_review_router",
        "tech_lead": "tech_lead",
    })

    # 代码评审通过后，根据开关进入可选安全评审/API复核
    graph.add_conditional_edges("post_code_review_router", route_security_or_qa, {
        "security_engineer": "security_engineer",
        "qa_engineer": "qa_engineer",
    })
    graph.add_conditional_edges("security_engineer", route_after_security, {
        "api_review": "api_review",
        "qa_engineer": "qa_engineer",
    })

    # 可选 API 复核
    graph.add_edge("api_review", "qa_engineer")

    # 测试失败可回退到技术负责人
    graph.add_conditional_edges("qa_engineer", route_after_test, {
        "technical_writer": "technical_writer",
        "tech_lead": "tech_lead",
        "project_manager": "project_manager",
    })

    graph.add_edge("technical_writer", "project_manager")
    graph.add_edge("project_manager", END)

    return graph.compile()


graph = build_graph()
