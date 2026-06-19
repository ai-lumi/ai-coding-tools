---
name: commit
description: 分析 git 暂存区变更，自动生成中文 Conventional Commits 格式的提交消息并执行提交。当用户输入 /commit 或要求生成提交消息、提交代码时使用。
---

# 中文 Git 提交消息生成

## 工作流程

1. 运行 `git diff --cached --stat` 检查暂存区
2. 如果没有暂存的变更，提示用户先执行 `git add`，然后停止
3. 运行 `git diff --cached` 获取完整 diff
4. 分析变更内容，生成提交消息
5. 将提交消息展示给用户确认
6. 用户确认后执行 `git commit -m "消息内容"`

## 提交消息格式

### 标题行

```
type: 简短中文描述
```

- **type 使用英文**，从以下选择：`feat` / `fix` / `refactor` / `chore` / `docs` / `style` / `perf` / `test`
- 描述使用**中文**，不超过 50 个字符（不含 type 前缀）
- 不以句号结尾

### 正文（可选）

当变更涉及多个方面时，用正文列出关键改动点：

```
type: 简短中文描述

- 改动点1
- 改动点2
- 改动点3
```

- 标题与正文之间空一行
- 每个改动点以 `- ` 开头
- 使用中文描述
- 只列出关键改动，不超过 8 条

## Type 选择规则

| Type | 使用场景 |
|------|---------|
| `feat` | 新功能、新接口、新页面 |
| `fix` | 修复 bug、修正错误行为 |
| `refactor` | 重构代码，不改变外部行为 |
| `perf` | 性能优化 |
| `style` | 代码格式调整（空格、缩进、命名） |
| `docs` | 文档变更 |
| `test` | 测试相关变更 |
| `chore` | 构建、依赖、配置等杂项 |

## 示例

**示例1：单一功能**
```
feat: 实现分布式节点优雅停机协调
```

**示例2：多方面变更**
```
feat: 实现分布式节点优雅停机协调

- 新增 GracefulShutdownCoordinator，统一协调 8 阶段停机顺序
- SyncTaskExecutor 的 sleepWithSignalCheck 改为 CountDownLatch 信号驱动
- ConfigChangeService 的 60s sleep 改为可中断等待
- ThreadPoolManager 分段检测排空，超时输出未完成线程堆栈
```

**示例3：Bug 修复**
```
fix: 修复增量任务显示"待处理"状态的问题

- 任务执行后立即更新状态为 RUNNING
- 前端监控页面同步刷新状态
```

**示例4：重构**
```
refactor: 重构数据源关闭逻辑

- 移除各组件的 @PreDestroy，由协调器统一管理
- 关闭前检查 HikariCP 活跃连接数
```

## 注意事项

- 标题要**概括性强**，正文补充细节
- 不要把文件名列表作为改动点，要描述**做了什么**
- 如果所有变更都围绕同一件事，只需标题，无需正文
- 提交前必须让用户确认消息内容
