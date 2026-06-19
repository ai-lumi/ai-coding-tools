"""
统一记忆层 - 分级记忆管理
支持全局、会话、任务三个级别的记忆读写
"""

import os
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List


# 记忆根目录（可通过环境变量覆盖）
MEMORY_ROOT = os.environ.get("MEMORY_ROOT", "/data/memory")

# 各级记忆目录
GLOBAL_DIR = os.path.join(MEMORY_ROOT)
SESSION_DIR = os.path.join(MEMORY_ROOT, "session")
TASK_DIR = os.path.join(MEMORY_ROOT, "tasks")
DAILY_DIR = os.path.join(MEMORY_ROOT, "daily")


def _ensure_dir(path: str) -> None:
    """确保目录存在"""
    os.makedirs(path, exist_ok=True)


# 初始化目录
_ensure_dir(SESSION_DIR)
_ensure_dir(TASK_DIR)
_ensure_dir(DAILY_DIR)


# ============================================================
# 全局记忆（Global Memory）
# 所有 Agent 共享，持久化到文件
# ============================================================

def read_global_memory(filename: str) -> str:
    """读取全局记忆文件"""
    path = os.path.join(GLOBAL_DIR, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def write_global_memory(filename: str, content: str) -> None:
    """写入全局记忆文件"""
    path = os.path.join(GLOBAL_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def append_to_memory_md(section: str, entry: str) -> None:
    """向 MEMORY.md 追加条目"""
    path = os.path.join(GLOBAL_DIR, "MEMORY.md")
    _ensure_dir(GLOBAL_DIR)

    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 长期记忆\n\n")

    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 查找对应 section，如果不存在则创建
    if f"## {section}" not in content:
        content += f"\n## {section}\n\n"

    # 在 section 后追加条目
    lines = content.split("\n")
    insert_idx = len(lines)
    for i, line in enumerate(lines):
        if line.strip() == f"## {section}":
            # 找到下一个 ## 或文件末尾
            for j in range(i + 1, len(lines)):
                if lines[j].startswith("## "):
                    insert_idx = j
                    break
            break

    lines.insert(insert_idx, f"- {entry}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def load_global_context() -> Dict[str, str]:
    """加载所有全局记忆文件，供 Agent 启动时读取"""
    context = {}
    for filename in ["MEMORY.md", "USER.md", "SOUL.md", "AGENTS.md"]:
        content = read_global_memory(filename)
        if content:
            context[filename] = content
    return context


# ============================================================
# 会话记忆（Session Memory）
# OpenClaw 专属，按 session_id 隔离
# ============================================================

def create_session(session_id: str) -> str:
    """创建新会话目录"""
    session_path = os.path.join(SESSION_DIR, session_id)
    _ensure_dir(session_path)

    # 创建会话上下文文件
    context_file = os.path.join(session_path, "context.json")
    if not os.path.exists(context_file):
        context = {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "messages": [],
            "metadata": {}
        }
        with open(context_file, "w", encoding="utf-8") as f:
            json.dump(context, f, ensure_ascii=False, indent=2)

    return session_path


def add_session_message(session_id: str, role: str, content: str) -> None:
    """添加会话消息"""
    context_file = os.path.join(SESSION_DIR, session_id, "context.json")
    if not os.path.exists(context_file):
        create_session(session_id)
        context_file = os.path.join(SESSION_DIR, session_id, "context.json")

    with open(context_file, "r", encoding="utf-8") as f:
        context = json.load(f)

    context["messages"].append({
        "role": role,
        "content": content,
        "timestamp": datetime.now().isoformat()
    })

    with open(context_file, "w", encoding="utf-8") as f:
        json.dump(context, f, ensure_ascii=False, indent=2)


def get_session_context(session_id: str) -> Dict[str, Any]:
    """获取会话上下文"""
    context_file = os.path.join(SESSION_DIR, session_id, "context.json")
    if os.path.exists(context_file):
        with open(context_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ============================================================
# 任务记忆（Task Memory）
# LangGraph 和 OpenCode 共享，按 task_id 隔离
# ============================================================

def create_task(task_id: str, title: str, description: str) -> str:
    """创建新任务目录"""
    task_path = os.path.join(TASK_DIR, task_id)
    _ensure_dir(task_path)

    # 创建任务状态文件
    state_file = os.path.join(task_path, "state.json")
    state = {
        "task_id": task_id,
        "title": title,
        "description": description,
        "created_at": datetime.now().isoformat(),
        "status": "pending",
        "phases": [],
        "outputs": {}
    }
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    return task_path


def update_task_phase(task_id: str, phase: str, output: Dict[str, Any]) -> None:
    """更新任务阶段输出"""
    state_file = os.path.join(TASK_DIR, task_id, "state.json")
    if not os.path.exists(state_file):
        return

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    state["phases"].append({
        "phase": phase,
        "output": output,
        "timestamp": datetime.now().isoformat()
    })
    state["outputs"][phase] = output

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def complete_task(task_id: str, summary: str, status: str = "completed") -> None:
    """完成任务并归档"""
    state_file = os.path.join(TASK_DIR, task_id, "state.json")
    if not os.path.exists(state_file):
        return

    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    state["status"] = status
    state["completed_at"] = datetime.now().isoformat()
    state["summary"] = summary

    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    # 写入任务总结文件
    summary_file = os.path.join(TASK_DIR, task_id, "summary.md")
    with open(summary_file, "w", encoding="utf-8") as f:
        f.write(f"# 任务总结: {state['title']}\n\n")
        f.write(f"- **任务ID**: {task_id}\n")
        f.write(f"- **状态**: {status}\n")
        f.write(f"- **完成时间**: {state['completed_at']}\n\n")
        f.write(f"## 摘要\n\n{summary}\n\n")
        f.write(f"## 阶段记录\n\n")
        for phase in state["phases"]:
            f.write(f"### {phase['phase']}\n\n")
            f.write(f"- 时间: {phase['timestamp']}\n")
            f.write(f"- 输出: {json.dumps(phase['output'], ensure_ascii=False, indent=2)}\n\n")


def get_task_state(task_id: str) -> Dict[str, Any]:
    """获取任务状态"""
    state_file = os.path.join(TASK_DIR, task_id, "state.json")
    if os.path.exists(state_file):
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ============================================================
# 每日笔记（Daily Notes）
# 所有 Agent 的当日活动自动汇总
# ============================================================

def append_daily_note(entry: str, category: str = "general") -> None:
    """追加每日笔记"""
    today = datetime.now().strftime("%Y-%m-%d")
    daily_file = os.path.join(DAILY_DIR, f"{today}.md")

    if not os.path.exists(daily_file):
        with open(daily_file, "w", encoding="utf-8") as f:
            f.write(f"# {today}\n\n")

    timestamp = datetime.now().strftime("%H:%M")
    with open(daily_file, "a", encoding="utf-8") as f:
        f.write(f"- [{timestamp}] [{category}] {entry}\n")


# ============================================================
# 语义搜索（简易版）
# 基于关键词的记忆检索
# ============================================================

def search_memory(query: str, scope: str = "all") -> List[Dict[str, str]]:
    """搜索记忆（简易关键词匹配）"""
    results = []
    query_lower = query.lower()

    # 搜索全局记忆
    if scope in ("all", "global"):
        for filename in ["MEMORY.md", "USER.md", "SOUL.md", "AGENTS.md"]:
            content = read_global_memory(filename)
            if query_lower in content.lower():
                # 提取包含关键词的行
                for line in content.split("\n"):
                    if query_lower in line.lower():
                        results.append({
                            "source": f"global/{filename}",
                            "content": line.strip()
                        })

    # 搜索任务记忆
    if scope in ("all", "task"):
        if os.path.exists(TASK_DIR):
            for task_id in os.listdir(TASK_DIR):
                state_file = os.path.join(TASK_DIR, task_id, "state.json")
                if os.path.exists(state_file):
                    with open(state_file, "r", encoding="utf-8") as f:
                        state = json.load(f)
                    if query_lower in json.dumps(state, ensure_ascii=False).lower():
                        results.append({
                            "source": f"task/{task_id}",
                            "content": f"任务: {state.get('title', '')} - {state.get('description', '')}"
                        })

    return results
