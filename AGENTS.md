# AGENTS.md

AI 代理在本仓库中工作所需的高信噪比上下文。只记录不读文件就猜错的内容。

---

## 项目本质

**本仓库不是传统软件项目。** 它是两类交付物的混合体：

1. **Agent 技能库** — `.agents/skills/<name>/SKILL.md` 下的行为规则文档。由支持 Skill 机制的 AI 代理运行时读取，作为工作准则。
2. **多 Agent 工具链 Docker 部署** — `docker/` 目录下的完整容器化系统（OpenClaw + OpenCode + LangGraph），含 CI 自动构建。

根目录下没有 `package.json`、`pyproject.toml` 等包管理文件。无构建、测试、lint、typecheck 命令。

---

## 核心结构

| 路径 | 用途 |
|---|---|
| `.agents/skills/<name>/SKILL.md` | 技能定义：YAML frontmatter（`name` + `description`）+ Markdown 正文。触发词写在 `description` 中。 |
| `docker/` | 完整 Docker 部署系统：Dockerfile、docker-compose.yml、supervisord 配置、入口脚本、初始化向导、所有配置模板。 |
| `scripts/install-multi-agent-toolchain.sh` | macOS 本地一键安装脚本（Homebrew + npm + pip 安装 OpenClaw/OpenCode/Kimi Code/LangGraph）。 |
| `scripts/configs/` | 本地安装的默认配置模板（OpenClaw、OpenCode、Kimi Code）。 |
| `.github/workflows/docker.yml` | CI：推送 `docker/` 变更时自动构建 `luowenqiang/ai-coding-tools:latest` 到 Docker Hub。 |

**两套配置模板共存**，内容不完全相同：
- `scripts/configs/` → 本地 macOS 使用（含 Moonshot/OpenAI/Anthropic 等 provider）
- `docker/templates/` → 容器内使用（含渠道、回调、LangGraph、技能注册等完整配置）

模板中的 `${VAR}` 占位符需要渲染，不可直接复制使用。本地脚本用 `perl` 替换，Docker 内用 `sed` 替换。

**Docker 构建必须传版本号：** `source VERSIONS && docker build --build-arg ...`，否则使用 Dockerfile 中的旧默认值。详见 `docker/README.md` 镜像构建章节。

---

## 关键约定

- **中文**：所有技能文档标题、描述、规则使用中文（示例代码除外）。
- **Mermaid**：文档图表必须使用 ```mermaid 代码块嵌入 Markdown，禁止图片。
- **commit 技能**：生成提交消息后必须展示给用户确认，不得自动提交。
- **技能新增**：在 `.agents/skills/` 下创建英文小写连字符目录，内含 `SKILL.md`，frontmatter 必须含 `name` 和 `description`。

---

## 技能触发速查

| 技能 | 关键触发词（见 `description`） |
|---|---|
| `commit` | `/commit`、生成提交消息 |
| `engineering-workflow` | 规划、执行、评审、验收、流程 |
| `mermaid-diagrams` | 文档、图表、流程说明 |
| `spec-driven-development` | 规范、接口、契约、文档先行 |
| `test-driven-development` | 测试、TDD、覆盖率、红绿重构 |
| `multi-agent-coding-toolchain` | 多 Agent 协作、远程编程 Agent、LangGraph 编排 |

---

## 修改原则

- 修改技能文件前通读原文件全部内容，避免破坏红线规则或触发条件。
- 技能规则变更时同步更新 `description` 中的触发条件。
- 本文件只应包含无法从文件树或语言惯例中直接推断出的上下文。
