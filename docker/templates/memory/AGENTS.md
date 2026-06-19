# 操作规范（AGENTS.md）

本文件定义多 Agent 协作的约定和规范。

## 工具链角色

| 工具 | 角色 | 职责 |
|---|---|---|
| OpenClaw | 网关与入口 | 接收指令、会话管理、调度子 Agent |
| LangGraph | 编排引擎 | 任务拆解、状态机、路由控制 |
| OpenCode | 编程 Agent | 代码编辑、测试运行、重构 |

## 记忆共享约定

- **全局记忆**（`/data/memory/`）：所有 Agent 可读，OpenClaw 负责维护
- **会话记忆**（`/data/memory/session/`）：OpenClaw 专属，其他 Agent 按需读取
- **任务记忆**（`/data/memory/tasks/`）：LangGraph 和 OpenCode 共享

## 任务完成归档

任务完成后，LangGraph 的 `project_manager` 节点负责：
1. 将任务结果写入 `/data/memory/tasks/{task_id}/`
2. 将重要经验提炼到 `/data/memory/MEMORY.md`
3. 将当日活动写入 `/data/memory/daily/YYYY-MM-DD.md`

## 编码规范

- 使用中文注释
- 遵循 PEP 8（Python）或项目已有规范
- 每个函数必须有 docstring
