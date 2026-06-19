---
name: mermaid-diagrams
description: >
  强制所有文档中的图表使用 Mermaid 语法直接嵌入 Markdown，禁止生成图片文件或引用外部图片。
  当任务涉及编写文档、规范、PRD、设计稿、流程说明等需要可视化表达时启用。
---

# Mermaid 图表规范

## 核心原则

**所有图表必须使用 Mermaid 语法嵌入 Markdown，禁止以图片形式存在。**

- ❌ 禁止生成 `.png`、`.svg`、`.jpg` 等图片文件
- ❌ 禁止引用外部图片链接（包括 URL 或相对路径图片）
- ✅ 必须使用 Markdown 代码块 + `mermaid` 标识符嵌入图表
- ✅ 图表源码与文档文本共存，版本可控、diff 友好

## 何时使用图表

遇到以下场景时，必须优先使用 Mermaid 图表替代文字描述：

| 场景 | 推荐图表类型 |
|------|-------------|
| 流程、步骤、操作顺序 | flowchart（流程图） |
| 系统/应用架构、模块关系 | flowchart / graph（架构图） |
| 状态转换、生命周期 | stateDiagram（状态机） |
| 时序、交互过程 | sequenceDiagram（时序图） |
| 类结构、继承关系 | classDiagram（类图） |
| 项目计划、迭代排期 | gantt（甘特图） |
| 实体关系、数据模型 | erDiagram（ER 图） |
| 用户旅程、体验地图 | journey（用户旅程图） |
| 思维导图、层次结构 | mindmap（思维导图） |

## 嵌入格式

所有 Mermaid 图表必须使用标准 Markdown 代码块，语言标识符为 `mermaid`：

````markdown
```mermaid
graph TD
    A[开始] --> B{判断}
    B -->|条件1| C[处理1]
    B -->|条件2| D[处理2]
    C --> E[结束]
    D --> E
```
````

## 常用模板

### 1. 流程图（Flowchart）

用于描述业务流程、算法步骤、操作流程。

```mermaid
flowchart LR
    A[输入] --> B{校验}
    B -->|通过| C[处理]
    B -->|失败| D[报错]
    C --> E[输出]
    D --> E
```

方向选择：`LR`（左→右）、`TD/TB`（上→下）、`RL`（右→左）、`BT`（下→上）。

### 2. 状态机（State Diagram）

用于描述状态转换、生命周期、协议状态。

```mermaid
stateDiagram-v2
    [*] --> 待处理
    待处理 --> 处理中 : 开始执行
    处理中 --> 已完成 : 执行成功
    处理中 --> 已失败 : 执行失败
    已完成 --> [*]
    已失败 --> 待处理 : 重试
```

### 3. 时序图（Sequence Diagram）

用于描述模块/系统/角色之间的交互时序。

```mermaid
sequenceDiagram
    participant A as 用户
    participant B as 系统
    participant C as 数据库

    A->>B: 提交请求
    B->>C: 查询数据
    C-->>B: 返回结果
    B-->>A: 返回响应
```

### 4. 架构图（Graph）

用于描述系统组件关系、模块依赖、分层架构。

```mermaid
graph TB
    subgraph 前端层
        UI[Web UI]
        CLI[命令行工具]
    end

    subgraph 核心层
        API[API 网关]
        Core[业务核心]
    end

    subgraph 存储层
        DB[(关系数据库)]
        Cache[缓存]
    end

    UI --> API
    CLI --> API
    API --> Core
    Core --> DB
    Core --> Cache
```

### 5. 类图（Class Diagram）

用于描述数据结构、对象关系、接口定义。

```mermaid
classDiagram
    class User {
        +String id
        +String name
        +login()
        +logout()
    }

    class Order {
        +String orderId
        +Date createdAt
        +submit()
    }

    User "1" --> "*" Order : 拥有
```

### 6. 甘特图（Gantt）

用于项目排期、迭代计划、里程碑规划。

```mermaid
gantt
    title 迭代计划
    dateFormat  YYYY-MM-DD

    section 阶段一
    需求分析     :a1, 2026-06-01, 7d
    接口设计     :a2, after a1, 5d

    section 阶段二
    编码实现     :a3, after a2, 14d
    测试验收     :a4, after a3, 7d
```

### 7. ER 图（Entity Relationship）

用于描述数据模型、实体关系。

```mermaid
erDiagram
    USER ||--o{ ORDER : places
    USER {
        string id PK
        string name
        string email
    }
    ORDER {
        string id PK
        string user_id FK
        datetime created_at
    }
```

## 质量要求

1. **可读性优先**：节点命名使用中文或清晰的英文，避免缩写
2. **方向一致**：同一份文档中的同类图表保持方向统一
3. **颜色克制**：不滥用样式，默认配色已足够清晰；如需强调，仅对关键节点使用 `classDef`
4. **规模控制**：单张图表节点数不超过 20 个，过复杂时拆分为多张或分层展示
5. **文本完整**：图表必须配文字说明，不可只有图没有解释

## 红线规则

1. **禁止生成图片文件**：任何 `.png`、`.svg`、`.jpg`、`.gif` 等二进制图片文件均不允许作为文档图表产出
2. **禁止引用外部图片**：文档中不允许出现 `![...](...)` 形式的图片引用
3. **Mermaid 无法表达时**：若遇到 Mermaid 确实无法表达的复杂可视化需求（如精确 UI 原型、照片级示意图），以文字表格或 ASCII 艺术替代，仍不生成图片
