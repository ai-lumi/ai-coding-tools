---
name: query-progress
description: >
  查询正在运行或已完成的工作流进度。
  当用户提供 task_id、询问进度、查看状态、检查任务、查询工作情况时触发。
  支持 /status、/progress、/task <id>、查询进度等命令。
user-invocable: true
---

# 查询工作流进度

## 定位

查询 LangGraph 自动化编程工作流的当前进度、阶段、状态等信息。当用户想知道某个任务进行到哪一步时触发。

## 查询方式

### 按 task_id 查询

如果用户提供了 `task_id`（如 `task-xxx`），使用 `exec` tool 查询 LangGraph API：

```bash
# 先从 pending_human_reviews.json 检查是否有该任务的审批请求
cat /data/langgraph/pending_human_reviews.json | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    task_id = sys.argv[1] if len(sys.argv) > 1 else ''
    for item in data:
        if item.get('task_id') == task_id:
            print(f'任务 {task_id} 等待人工审批：')
            print(f'  阶段: {item.get(\"phase\")}')
            print(f'  问题: {item.get(\"question\")}')
            print(f'  进度: {item.get(\"progress\")}%')
            break
    else:
        print(f'任务 {task_id} 不在待审批列表中')
except Exception:
    print('没有待审批的任务')
" "<task_id>"
```

如果有 `thread_id`，可以直接查询 LangGraph checkpoint：

```bash
curl -sf http://127.0.0.1:8000/threads/<thread_id>/runs | python3 -c "
import sys, json
try:
    runs = json.load(sys.stdin)
    if runs:
        r = runs[-1]  # 最新一次 run
        state = r.get('values', {})
        print(f'任务: {state.get(\"task_title\", \"未知\")}')
        print(f'阶段: {state.get(\"current_phase\", \"未知\")}')
        print(f'进度: {state.get(\"progress\", 0)}%')
        print(f'状态: {state.get(\"status\", \"未知\")}')
        print(f'阻塞项: {state.get(\"blockers\", [])}')
    else:
        print('没有找到运行记录')
except Exception as e:
    print(f'查询失败: {e}')
"
```

### 查看所有待审批任务

```bash
cat /data/langgraph/pending_human_reviews.json
```

## 示例

用户： `/status task-abc123`

你应该调用 exec：
```bash
cat /data/langgraph/pending_human_reviews.json | python3 -c "
import sys, json
data = json.load(sys.stdin)
task_id = 'task-abc123'
for item in data:
    if item.get('task_id') == task_id:
        print(f'等待审批 - {item.get(\"phase\")}: {item.get(\"question\")}')
        break
else:
    print('该任务不在待审批列表中')
"
```

用户： "看看有没有待审批的任务"

你应该调用 exec：
```bash
cat /data/langgraph/pending_human_reviews.json
```

## 工具调用要求

- 必须调用 `exec` tool，不能只在回复中描述应该做什么。
- 优先从 pending_human_reviews.json 查询；如果需要最新进度，用 curl 调 LangGraph API。

## 禁止事项

- 不要自行猜测任务状态。
- 不要修改任何文件。
