---
name: auto-programming
description: >
  触发 /auto-programming 启动 LangGraph 多 Agent 自动化编程工作流，也可恢复已暂停的工作流。
  当用户要求开发软件、实现功能、编写程序、构建应用、开发系统时触发。
  当用户提到"需要一个系统/应用/网站/小程序/功能"等开发需求时触发。
  当用户请求使用多 Agent 自动编程、自动开发、自动编码时触发。
  本技能是所有编程需求的唯一入口，优先级高于 spec-driven-development 和 engineering-workflow。
user-invocable: true
---

# 自动化软件开发工作流

## 定位

**本技能是所有编程/开发需求的唯一入口和调度器。** 当用户提出任何开发、编程、实现功能的请求时，必须由本技能接管，启动 LangGraph 多 Agent 工作流来分发任务。禁止由 OpenClaw 主 Agent 自行生成代码、编写规范或设计方案。

## 启动条件

当用户以下列任意方式表达开发需求时，必须触发本技能：

- 明确使用 `/auto-programming` 命令
- 说"我需要...系统/应用/网站/小程序"
- 说"帮我开发/实现/编写/构建..."
- 说"做一个...功能/模块/项目"
- 任何包含开发、编程、代码实现意图的自然语言描述

## 启动新工作流

当用户没有提供 `thread_id` 时，视为启动新任务：

1. 从用户输入中提取**任务标题**、**需求描述**和**项目名称**：
   - 如果用户输入格式为 `/auto-programming <标题> | <描述>`，则以 `|` 之前为标题，之后为描述。
   - 如果用户输入格式为 `/auto-programming --project <项目名> <描述>`，则提取项目名。
   - 如果用户没有指定项目，主动询问"请问在哪个项目目录下开发？"（对应 `/data/code/` 下的子目录名）。
   - 如果用户输入为 `/auto-programming <一段描述>`，则前 30 个字符（截断到最近词语边界）作为标题，完整内容作为描述。
2. 使用 `exec` tool 执行：
   ```bash
   /usr/local/bin/langgraph-trigger --project "<项目名>" --task-title "<任务标题>" --task-desc "<需求详细描述>"
   ```

## 恢复暂停工作流

当用户提到恢复、继续、审批某个工作流，或提供 `thread_id` 时：

1. 提取 `thread_id` 和用户反馈（通过/驳回/修改意见）。
2. 使用 `exec` tool 执行：
   ```bash
   /usr/local/bin/langgraph-resume "<thread_id>" "<用户反馈>"
   ```

## 查询待审批的工作流

当用户想查看是否有需要人工审批的工作流时：

1. 使用 `exec` tool 读取 LangGraph 持久化的审批文件：
   ```bash
   cat /data/langgraph/pending_human_reviews.json
   ```
2. 如果文件存在且有待审批记录，向用户展示任务列表。
3. 如果文件不存在或为空，告知用户当前没有待审批的工作流。

## 工具调用要求

- 必须调用 `exec` tool，不能只在回复中描述应该做什么。
- `exec` 的 `command` 参数必须是上述完整命令字符串。
- 启动新工作流时，若用户只给了一句话，标题不要过长，描述保留完整原意。

## 启动后告知用户

成功启动工作流后，必须向用户说明：

- **工作流已启动**，正在由多 Agent 自动执行。
- **进度查看**：可通过发送 "进度" 或 "/status <task_id>" 查询。
- **人工审批**：如果工作流在需求评审、架构评审或代码评审阶段需要人类介入，我会通知你。
- **恢复方式**：收到审批通知后，可直接回复 "通过" 或 "驳回，请修改 xxx"，我会自动转发给 LangGraph 恢复工作流。

## 示例

### 启动新工作流

用户： `/auto-programming 开发一个 Todo 应用，支持任务增删改查与标签筛选`

你应该先询问项目名，然后调用 exec：
```bash
/usr/local/bin/langgraph-trigger --project "<项目名>" --task-title "开发一个 Todo 应用" --task-desc "开发一个 Todo 应用，支持任务增删改查与标签筛选"
```

用户： `/auto-programming --project alpha 开发一个博客系统`

你应该调用 exec：
```bash
/usr/local/bin/langgraph-trigger --project "alpha" --task-title "开发一个博客系统" --task-desc "开发一个博客系统"
```

启动后回复用户：
> 工作流已启动！
>
> **任务 ID**: `task-xxx`
> **线程 ID**: `xxx`
> **项目**: `alpha`
> 
> 工作流会自动完成需求分析、架构设计、代码生成、测试验证等步骤。
> 如果过程中需要人工审批，我会在群里通知你。
> 也可通过发送 `/status task-xxx` 查询进度。

### 恢复暂停工作流

用户： `/auto-programming resume:thread-abc123 通过，继续下一步`

你应该调用 exec：
```bash
/usr/local/bin/langgraph-resume "thread-abc123" "通过，继续下一步"
```

### 查询待审批

用户： `/auto-programming check` 或 "帮我看看有没有待审批的任务"

你应该调用 exec：
```bash
cat /data/langgraph/pending_human_reviews.json
```

## 禁止事项

- 不要改写用户原始需求的核心含义。
- 不要自行生成代码、架构设计或技术方案。
- 不要直接回答 "应该怎么实现"。
- 不要跳过 `exec` tool 调用，只在文本中描述命令。
- **不要自行执行 `write`、`edit`、`read` 等文件操作工具来完成编程任务** —— 这些操作必须由 LangGraph 工作流中的专业 Agent 执行。
