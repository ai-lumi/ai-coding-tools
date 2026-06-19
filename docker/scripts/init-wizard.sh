#!/bin/bash
# 多 Agent 工具链 - 配置向导
# 支持交互式（interactive）和非交互式（--auto）两种模式
# --auto 模式下从环境变量读取配置，实现容器启动时自动初始化

set -e

TEMPLATE_DIR="/etc/templates"
DATA_DIR="/data"
OPENCLAW_DIR="$DATA_DIR/openclaw/.openclaw"
OPENCLAW_SKILLS_DIR="$DATA_DIR/openclaw/skills"
OPENCODE_DIR="$DATA_DIR/opencode/.config/opencode"
OPENCODE_DIR_FOR_OPENCLAW="$DATA_DIR/openclaw/.config/opencode"
LANGGRAPH_DIR="$DATA_DIR/langgraph"

AUTO_MODE=false

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[信息]${NC}   $*"; }
ok()    { echo -e "${GREEN}[成功]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[警告]${NC}   $*"; }
error() { echo -e "${RED}[错误]${NC}   $*"; }

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --auto)
            AUTO_MODE=true
            shift
            ;;
        *)
            error "未知参数: $1"
            exit 1
            ;;
    esac
done

print_header() {
    echo ""
    echo "========================================"
    echo "  多 Agent 工具链 - 配置向导"
    echo "========================================"
    echo ""
}

print_step() {
    echo ""
    echo "--- 步骤 $1/$2: $3 ---"
    echo ""
}

# ============================================================
# 自动模式：从环境变量读取配置
# ============================================================
load_auto_config() {
    # 若未设置通用 API_KEY，按优先级回退
    API_KEY="${API_KEY:-${OPENCODE_ZEN_API_KEY:-${KIMI_API_KEY:-${DEEPSEEK_API_KEY:-}}}}"

    if [ -z "$PROVIDER_NAME" ] || [ -z "$API_KEY" ] || [ -z "$MODEL_ID" ]; then
        error "自动模式缺少必要环境变量: PROVIDER_NAME, API_KEY, MODEL_ID"
        exit 1
    fi

    # 内置 provider（opencode, kimi）不需要 PROVIDER_BASE_URL，自动使用内置路由
    if [ "$PROVIDER_NAME" != "opencode" ] && [ "$PROVIDER_NAME" != "kimi" ]; then
        if [ -z "$PROVIDER_BASE_URL" ]; then
            error "自动模式缺少 PROVIDER_BASE_URL（非内置 provider 需要）"
            exit 1
        fi
    fi
    PROVIDER_BASE_URL="${PROVIDER_BASE_URL:-https://api.opencode.ai/v1}"

    PROVIDER_API_TYPE="${PROVIDER_API_TYPE:-openai-completions}"
    MODEL_NAME="${MODEL_NAME:-$MODEL_ID}"
    PRIMARY_MODEL="${PRIMARY_MODEL:-$PROVIDER_NAME/$MODEL_ID}"
    OPENCLAW_GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
    LANGGRAPH_PORT="${LANGGRAPH_PORT:-8000}"
    LANGGRAPH_PERSISTENCE="${LANGGRAPH_PERSISTENCE:-true}"
    LANGGRAPH_CHECKPOINT_PATH="${LANGGRAPH_CHECKPOINT_PATH:-/data/langgraph/checkpoints.sqlite}"
    OPENCODE_SKIP_PERMISSIONS="${OPENCODE_SKIP_PERMISSIONS:-true}"

    if [ -z "$OPENCLAW_GATEWAY_TOKEN" ]; then
        OPENCLAW_GATEWAY_TOKEN=$(openssl rand -hex 16)
        info "自动生成 Gateway Token: $OPENCLAW_GATEWAY_TOKEN"
    fi

    # 可选：其他 API Keys
    OPENAI_API_KEY="${OPENAI_API_KEY:-${API_KEY}}"
    ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
    KIMI_API_KEY="${KIMI_API_KEY:-${API_KEY}}"
    MOONSHOT_API_KEY="${MOONSHOT_API_KEY:-${API_KEY}}"
    DEEPSEEK_API_KEY="${DEEPSEEK_API_KEY:-${API_KEY}}"
    OPENCODE_ZEN_API_KEY="${OPENCODE_ZEN_API_KEY:-${API_KEY}}"
    FEISHU_NOTIFY_WEBHOOK="${FEISHU_NOTIFY_WEBHOOK:-}"
    FEISHU_NOTIFY_SECRET="${FEISHU_NOTIFY_SECRET:-}"
    TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

    # OpenCode 模型命名与 OpenClaw 不同，需要单独映射
    # OpenCode 内置 provider 列表：opencode, kimi-for-coding, openai, anthropic 等
    # 这些 provider 从模型前缀自动识别，无需在 config 中显式定义
    case "$PROVIDER_NAME" in
        kimi)
            OPENCODE_PRIMARY_MODEL="${OPENCODE_PRIMARY_MODEL:-kimi-for-coding/k2p5}"
            ;;
        opencode)
            OPENCODE_PRIMARY_MODEL="${OPENCODE_PRIMARY_MODEL:-opencode/gpt-5.1-codex}"
            ;;
        *)
            OPENCODE_PRIMARY_MODEL="${OPENCODE_PRIMARY_MODEL:-$PRIMARY_MODEL}"
            ;;
    esac

    # 模型层级（额度耗尽时切换用）
    MODEL_TIERS="${MODEL_TIERS:-$OPENCODE_PRIMARY_MODEL}"
    MODEL_TIER_NAMES="${MODEL_TIER_NAMES:-$MODEL_NAME}"

    # 各角色默认使用主模型，可通过 ROLE_*_MODEL 环境变量单独覆盖
    ROLE_PM_MODEL="${ROLE_PM_MODEL:-$PRIMARY_MODEL}"
    ROLE_BUSINESS_ANALYST_MODEL="${ROLE_BUSINESS_ANALYST_MODEL:-$PRIMARY_MODEL}"
    ROLE_ARCHITECT_MODEL="${ROLE_ARCHITECT_MODEL:-$PRIMARY_MODEL}"
    ROLE_API_DESIGNER_MODEL="${ROLE_API_DESIGNER_MODEL:-$PRIMARY_MODEL}"
    ROLE_DATABASE_ENGINEER_MODEL="${ROLE_DATABASE_ENGINEER_MODEL:-$PRIMARY_MODEL}"
    ROLE_TECH_LEAD_MODEL="${ROLE_TECH_LEAD_MODEL:-$PRIMARY_MODEL}"
    ROLE_BACKEND_ENGINEER_MODEL="${ROLE_BACKEND_ENGINEER_MODEL:-$PRIMARY_MODEL}"
    ROLE_FRONTEND_ENGINEER_MODEL="${ROLE_FRONTEND_ENGINEER_MODEL:-$PRIMARY_MODEL}"
    ROLE_DEVOPS_ENGINEER_MODEL="${ROLE_DEVOPS_ENGINEER_MODEL:-$PRIMARY_MODEL}"
    ROLE_CODE_REVIEWER_MODEL="${ROLE_CODE_REVIEWER_MODEL:-$PRIMARY_MODEL}"
    ROLE_SECURITY_ENGINEER_MODEL="${ROLE_SECURITY_ENGINEER_MODEL:-$PRIMARY_MODEL}"
    ROLE_QA_ENGINEER_MODEL="${ROLE_QA_ENGINEER_MODEL:-$PRIMARY_MODEL}"
    ROLE_TECHNICAL_WRITER_MODEL="${ROLE_TECHNICAL_WRITER_MODEL:-$PRIMARY_MODEL}"
    ROLE_PROJECT_MANAGER_MODEL="${ROLE_PROJECT_MANAGER_MODEL:-$PRIMARY_MODEL}"

    # 可选：消息渠道
    FEISHU_CHANNEL_CONFIG=""
    if [ -n "$FEISHU_APP_ID" ] && [ -n "$FEISHU_APP_SECRET" ]; then
        FEISHU_CHANNEL_CONFIG='"feishu":{"accounts":{"main":{"appId":"'"$FEISHU_APP_ID"'","appSecret":"'"$FEISHU_APP_SECRET"'"}},"typingIndicator":false,"streaming":true,"blockStreaming":true}'
    fi

    TELEGRAM_CHANNEL_CONFIG=""
    if [ -n "$TELEGRAM_BOT_TOKEN" ]; then
        TELEGRAM_CHANNEL_CONFIG='"telegram":{"token":"'"$TELEGRAM_BOT_TOKEN"'"}'
    fi

    ok "自动配置加载完成: $PRIMARY_MODEL"
}

# ============================================================
# 步骤 1: 选择 AI 模型提供商
# ============================================================
step_model() {
    print_step 1 6 "选择 AI 模型提供商"

    echo "请选择你要使用的 AI 模型提供商："
    echo "  1) OpenAI (GPT-4o)"
    echo "  2) Anthropic (Claude Sonnet 4)"
    echo "  3) Moonshot / Kimi (Kimi for Coding)"
    echo "  4) OpenCode Zen (OpenCode 官方免费/付费模型)"
    echo "  5) 自定义 (OpenAI 兼容接口)"
    echo ""

    while true; do
        read -p "请选择 [1-5]: " choice
        case $choice in
            1)
                PROVIDER_NAME="openai"
                PROVIDER_BASE_URL="https://api.openai.com/v1"
                PROVIDER_API_TYPE="openai-responses"
                MODEL_ID="gpt-4o"
                MODEL_NAME="GPT-4o"
                PRIMARY_MODEL="openai/gpt-4o"
                break
                ;;
            2)
                PROVIDER_NAME="anthropic"
                PROVIDER_BASE_URL="https://api.anthropic.com"
                PROVIDER_API_TYPE="anthropic-messages"
                MODEL_ID="claude-sonnet-4-20250514"
                MODEL_NAME="Claude Sonnet 4"
                PRIMARY_MODEL="anthropic/claude-sonnet-4-20250514"
                break
                ;;
            3)
                PROVIDER_NAME="kimi"
                PROVIDER_BASE_URL="https://api.kimi.com/coding/"
                PROVIDER_API_TYPE="anthropic-messages"
                MODEL_ID="kimi-for-coding"
                MODEL_NAME="Kimi Code"
                PRIMARY_MODEL="kimi/kimi-for-coding"
                break
                ;;
            4)
                PROVIDER_NAME="opencode"
                PROVIDER_BASE_URL="https://api.opencode.ai/v1"
                PROVIDER_API_TYPE="openai-completions"
                MODEL_ID="gpt-5.1-codex"
                MODEL_NAME="OpenCode Zen GPT 5.1 Codex"
                PRIMARY_MODEL="opencode/gpt-5.1-codex"
                echo ""
                echo "  可选模型（在后续步骤可修改 OPENCODE_PRIMARY_MODEL）："
                echo "    - opencode/gpt-5.1-codex   (编程优化)"
                echo "    - opencode/gpt-5.2         (最新旗舰)"
                echo "    - opencode/claude-sonnet-4-5"
                echo "    - opencode/claude-opus-4-5"
                break
                ;;
            5)
                read -p "API Base URL (例如 https://api.example.com/v1): " PROVIDER_BASE_URL
                read -p "模型 ID (例如 gpt-4o): " MODEL_ID
                read -p "模型显示名称 (例如 GPT-4o): " MODEL_NAME
                read -p "Provider 名称 (例如 custom): " PROVIDER_NAME
                PRIMARY_MODEL="${PROVIDER_NAME}/${MODEL_ID}"
                PROVIDER_API_TYPE="openai-responses"
                break
                ;;
            *)
                error "无效选择，请输入 1-5"
                ;;
        esac
    done

    echo ""
    read -s -p "请输入 API Key: " API_KEY
    echo ""
    if [ -z "$API_KEY" ]; then
        error "API Key 不能为空"
        exit 1
    fi
    ok "模型配置完成: $PRIMARY_MODEL"
}

# ============================================================
# 步骤 2: 配置 OpenClaw Gateway
# ============================================================
step_gateway() {
    print_step 2 6 "配置 OpenClaw Gateway"

    read -p "Gateway 端口 [默认 18789]: " port
    OPENCLAW_GATEWAY_PORT="${port:-18789}"

    # 生成随机 token
    OPENCLAW_GATEWAY_TOKEN=$(openssl rand -hex 16)
    echo ""
    info "已自动生成 Gateway 认证 Token: $OPENCLAW_GATEWAY_TOKEN"
    info "请妥善保存此 Token，后续通过 Web 或 API 访问时需要使用"
    echo ""
    ok "Gateway 配置完成: 端口 $OPENCLAW_GATEWAY_PORT"
}

# ============================================================
# 步骤 3: 配置飞书对接（可选）
# ============================================================
step_feishu() {
    print_step 3 6 "配置飞书对接（可选）"

    echo "是否启用飞书（Lark）消息渠道？"
    read -p "启用飞书？[y/N]: " enable_feishu

    if [[ "$enable_feishu" =~ ^[Yy]$ ]]; then
        read -p "飞书 App ID: " FEISHU_APP_ID
        read -s -p "飞书 App Secret: " FEISHU_APP_SECRET
        echo ""

        FEISHU_CHANNEL_CONFIG='"feishu":{"accounts":{"main":{"appId":"'"$FEISHU_APP_ID"'","appSecret":"'"$FEISHU_APP_SECRET"'"}},"typingIndicator":false,"streaming":true,"blockStreaming":true}'
        ok "飞书渠道配置完成"
    else
        FEISHU_CHANNEL_CONFIG=""
        info "跳过飞书配置"
    fi
}

# ============================================================
# 步骤 4: 配置其他消息渠道（可选）
# ============================================================
step_other_channels() {
    print_step 4 6 "配置其他消息渠道（可选）"

    echo "是否启用 Telegram 消息渠道？"
    read -p "启用 Telegram？[y/N]: " enable_telegram

    if [[ "$enable_telegram" =~ ^[Yy]$ ]]; then
        read -s -p "Telegram Bot Token: " TELEGRAM_BOT_TOKEN
        echo ""
        TELEGRAM_CHANNEL_CONFIG='"telegram":{"token":"'"$TELEGRAM_BOT_TOKEN"'"}'
        ok "Telegram 渠道配置完成"
    else
        TELEGRAM_CHANNEL_CONFIG=""
        info "跳过 Telegram 配置"
    fi
}

# ============================================================
# 步骤 5: 预装 Skills
# ============================================================
step_skills() {
    print_step 5 6 "预装 Skills"

    mkdir -p "$OPENCLAW_SKILLS_DIR"

    # --- 类别 1: OpenClaw registry skills ---
    info "安装 OpenClaw registry skills..."
    while IFS= read -r skill; do
        skill=$(echo "$skill" | tr -d '[:space:]')
        [ -z "$skill" ] && continue
        [[ "$skill" == \#* ]] && continue

        if openclaw skills install "$skill" --global --dir "$OPENCLAW_SKILLS_DIR" 2>/dev/null; then
            ok "已安装 registry skill: $skill"
        else
            warn "安装 registry skill $skill 失败（可能不在 registry 中）"
        fi
    done < "$TEMPLATE_DIR/skills-list.txt"

    # --- 类别 2: OpenClaw 本地 skills（openclaw-skills/）---
    if [ -d "$TEMPLATE_DIR/openclaw-skills" ]; then
        info "安装 OpenClaw 本地 skills..."
        for skill_dir in "$TEMPLATE_DIR/openclaw-skills"/*; do
            [ -d "$skill_dir" ] || continue
            skill_name=$(basename "$skill_dir")
            cp -R "$skill_dir" "$OPENCLAW_SKILLS_DIR/"
            ok "已安装 OpenClaw 本地 skill: $skill_name"
        done
    fi

    # --- 类别 3: OpenCode skills（opencode-skills/）---
    if [ -d "$TEMPLATE_DIR/opencode-skills" ]; then
        info "安装 OpenCode skills..."
        for skill_dir in "$TEMPLATE_DIR/opencode-skills"/*; do
            [ -d "$skill_dir" ] || continue
            skill_name=$(basename "$skill_dir")
            # OpenCode skills 也复制到 OpenClaw 的 extraDirs 中，统一加载
            cp -R "$skill_dir" "$OPENCLAW_SKILLS_DIR/"
            ok "已安装 OpenCode skill: $skill_name"
        done
    fi
}

# ============================================================
# 步骤 6: 生成配置文件
# ============================================================
step_generate_configs() {
    print_step 6 6 "生成配置文件"

    # 创建目录
    mkdir -p "$OPENCLAW_DIR"
    mkdir -p "$OPENCODE_DIR"
    mkdir -p "$OPENCODE_DIR_FOR_OPENCLAW"
    mkdir -p "$LANGGRAPH_DIR/src"

    # 构建 channels JSON 块（展平为单行，避免 sed 多行替换问题）
    local channels_json=""
    if [ -n "$FEISHU_CHANNEL_CONFIG" ] && [ -n "$TELEGRAM_CHANNEL_CONFIG" ]; then
        channels_json="\"channels\": {$FEISHU_CHANNEL_CONFIG,$TELEGRAM_CHANNEL_CONFIG}"
    elif [ -n "$FEISHU_CHANNEL_CONFIG" ]; then
        channels_json="\"channels\": {$FEISHU_CHANNEL_CONFIG}"
    elif [ -n "$TELEGRAM_CHANNEL_CONFIG" ]; then
        channels_json="\"channels\": {$TELEGRAM_CHANNEL_CONFIG}"
    else
        channels_json='"channels": {}'
    fi

    # 生成 openclaw.json
    info "生成 OpenClaw 配置..."
    local template="$TEMPLATE_DIR/openclaw.json"
    local output="$OPENCLAW_DIR/openclaw.json"

    sed \
        -e "s|\${PROVIDER_NAME}|$PROVIDER_NAME|g" \
        -e "s|\${PROVIDER_BASE_URL}|$PROVIDER_BASE_URL|g" \
        -e "s|\${PROVIDER_API_TYPE}|$PROVIDER_API_TYPE|g" \
        -e "s|\${MODEL_ID}|$MODEL_ID|g" \
        -e "s|\${MODEL_NAME}|$MODEL_NAME|g" \
        -e "s|\${PRIMARY_MODEL}|$PRIMARY_MODEL|g" \
        -e "s|\${API_KEY}|$API_KEY|g" \
        -e "s|\${OPENCLAW_GATEWAY_TOKEN}|$OPENCLAW_GATEWAY_TOKEN|g" \
        -e "s|\${OPENCLAW_GATEWAY_PORT}|$OPENCLAW_GATEWAY_PORT|g" \
        -e "s|\${LANGGRAPH_PORT}|${LANGGRAPH_PORT:-8000}|g" \
        -e "s|\${OPENAI_API_KEY}|${OPENAI_API_KEY:-}|g" \
        -e "s|\${ANTHROPIC_API_KEY}|${ANTHROPIC_API_KEY:-}|g" \
        -e "s|\${KIMI_API_KEY}|${KIMI_API_KEY:-}|g" \
        -e "s|\${MOONSHOT_API_KEY}|${MOONSHOT_API_KEY:-}|g" \
        -e "s|\${ROLE_PM_MODEL}|${ROLE_PM_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_BUSINESS_ANALYST_MODEL}|${ROLE_BUSINESS_ANALYST_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_ARCHITECT_MODEL}|${ROLE_ARCHITECT_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_API_DESIGNER_MODEL}|${ROLE_API_DESIGNER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_DATABASE_ENGINEER_MODEL}|${ROLE_DATABASE_ENGINEER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_TECH_LEAD_MODEL}|${ROLE_TECH_LEAD_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_BACKEND_ENGINEER_MODEL}|${ROLE_BACKEND_ENGINEER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_FRONTEND_ENGINEER_MODEL}|${ROLE_FRONTEND_ENGINEER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_DEVOPS_ENGINEER_MODEL}|${ROLE_DEVOPS_ENGINEER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_CODE_REVIEWER_MODEL}|${ROLE_CODE_REVIEWER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_SECURITY_ENGINEER_MODEL}|${ROLE_SECURITY_ENGINEER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_QA_ENGINEER_MODEL}|${ROLE_QA_ENGINEER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_TECHNICAL_WRITER_MODEL}|${ROLE_TECHNICAL_WRITER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${ROLE_PROJECT_MANAGER_MODEL}|${ROLE_PROJECT_MANAGER_MODEL:-$PRIMARY_MODEL}|g" \
        -e "s|\${CHANNELS_BLOCK}|$channels_json|g" \
        "$template" > "$output"

    ok "OpenClaw 配置已生成: $output"

    # 生成 opencode config.json（仅替换模型名，provider 由 OpenCode 内置支持自动选取）
    info "生成 OpenCode 配置..."
    local opencode_template="$TEMPLATE_DIR/opencode-config.json"
    local opencode_output="$OPENCODE_DIR/config.json"

    sed \
        -e "s|\${OPENCODE_PRIMARY_MODEL}|$OPENCODE_PRIMARY_MODEL|g" \
        "$opencode_template" > "$opencode_output"

    # 同步到 OpenClaw 用户的 HOME，确保 OpenClaw 调用 opencode 时能读到配置
    cp "$opencode_output" "$OPENCODE_DIR_FOR_OPENCLAW/config.json"

    ok "OpenCode 配置已生成: $opencode_output"

    # 复制 langgraph.json
    info "生成 LangGraph 配置..."
    cp "$TEMPLATE_DIR/langgraph.json" "$LANGGRAPH_DIR/langgraph.json"
    ok "LangGraph 配置已生成: $LANGGRAPH_DIR/langgraph.json"

    # 复制示例工作流代码
    if [ ! -f "$LANGGRAPH_DIR/src/photo_sorter.py" ]; then
        cp "$TEMPLATE_DIR/langgraph-src/photo_sorter.py" "$LANGGRAPH_DIR/src/photo_sorter.py"
        ok "照片分拣示例工作流已复制"
    fi
    if [ ! -f "$LANGGRAPH_DIR/src/auto_programming.py" ]; then
        cp "$TEMPLATE_DIR/langgraph-src/auto_programming.py" "$LANGGRAPH_DIR/src/auto_programming.py"
        ok "自动化编程示例工作流已复制"
    fi
    if [ ! -f "$LANGGRAPH_DIR/src/memory.py" ]; then
        cp "$TEMPLATE_DIR/langgraph-src/memory.py" "$LANGGRAPH_DIR/src/memory.py"
        ok "统一记忆层已复制"
    fi
    if [ ! -f "$LANGGRAPH_DIR/src/model_switch.py" ]; then
        cp "$TEMPLATE_DIR/langgraph-src/model_switch.py" "$LANGGRAPH_DIR/src/model_switch.py"
        ok "模型切换模块已复制"
    fi
    if [ ! -f "$LANGGRAPH_DIR/src/checkpointer.py" ]; then
        cp "$TEMPLATE_DIR/langgraph-src/checkpointer.py" "$LANGGRAPH_DIR/src/checkpointer.py"
        ok "SQLite 持久化检查点模块已复制"
    fi
    if [ ! -f "$LANGGRAPH_DIR/src/requirements.txt" ]; then
        cp "$TEMPLATE_DIR/langgraph-src/requirements.txt" "$LANGGRAPH_DIR/src/requirements.txt"
        ok "依赖文件已复制"
    fi
    if [ ! -f "$LANGGRAPH_DIR/pyproject.toml" ]; then
        cp "$TEMPLATE_DIR/langgraph-src/pyproject.toml" "$LANGGRAPH_DIR/pyproject.toml"
        ok "项目配置已复制"
    fi

    # 生成 .env 文件
    info "生成环境变量文件..."
    cat > "$DATA_DIR/.env" <<EOF
# OpenCode HTTP Server 端口（opencode serve 默认 4096）
OPENCODE_PORT="${OPENCODE_PORT:-4096}"
OPENCODE_SERVER_PASSWORD="${OPENCODE_SERVER_PASSWORD:-}"
OPENCODE_SKIP_PERMISSIONS="${OPENCODE_SKIP_PERMISSIONS:-true}"

# AI 模型配置
PROVIDER_NAME="$PROVIDER_NAME"
PROVIDER_BASE_URL="$PROVIDER_BASE_URL"
PROVIDER_API_TYPE="$PROVIDER_API_TYPE"
API_KEY="$API_KEY"
MODEL_ID="$MODEL_ID"
MODEL_NAME="$MODEL_NAME"
PRIMARY_MODEL="$PRIMARY_MODEL"

# OpenClaw Gateway
OPENCLAW_GATEWAY_TOKEN="$OPENCLAW_GATEWAY_TOKEN"
OPENCLAW_GATEWAY_PORT="$OPENCLAW_GATEWAY_PORT"

# LangGraph
LANGGRAPH_PORT="${LANGGRAPH_PORT:-8000}"
LANGGRAPH_PERSISTENCE="${LANGGRAPH_PERSISTENCE:-true}"
LANGGRAPH_CHECKPOINT_PATH="${LANGGRAPH_CHECKPOINT_PATH:-/data/langgraph/checkpoints.sqlite}"

# OpenCode 模型映射（与 OpenClaw 命名可能不同）
OPENCODE_PRIMARY_MODEL="$OPENCODE_PRIMARY_MODEL"

# 各角色模型配置
ROLE_PM_MODEL="$ROLE_PM_MODEL"
ROLE_BUSINESS_ANALYST_MODEL="$ROLE_BUSINESS_ANALYST_MODEL"
ROLE_ARCHITECT_MODEL="$ROLE_ARCHITECT_MODEL"
ROLE_API_DESIGNER_MODEL="$ROLE_API_DESIGNER_MODEL"
ROLE_DATABASE_ENGINEER_MODEL="$ROLE_DATABASE_ENGINEER_MODEL"
ROLE_TECH_LEAD_MODEL="$ROLE_TECH_LEAD_MODEL"
ROLE_BACKEND_ENGINEER_MODEL="$ROLE_BACKEND_ENGINEER_MODEL"
ROLE_FRONTEND_ENGINEER_MODEL="$ROLE_FRONTEND_ENGINEER_MODEL"
ROLE_DEVOPS_ENGINEER_MODEL="$ROLE_DEVOPS_ENGINEER_MODEL"
ROLE_CODE_REVIEWER_MODEL="$ROLE_CODE_REVIEWER_MODEL"
ROLE_SECURITY_ENGINEER_MODEL="$ROLE_SECURITY_ENGINEER_MODEL"
ROLE_QA_ENGINEER_MODEL="$ROLE_QA_ENGINEER_MODEL"
ROLE_TECHNICAL_WRITER_MODEL="$ROLE_TECHNICAL_WRITER_MODEL"
ROLE_PROJECT_MANAGER_MODEL="$ROLE_PROJECT_MANAGER_MODEL"

# 模型层级（额度耗尽切换）
MODEL_TIERS="$MODEL_TIERS"
MODEL_TIER_NAMES="$MODEL_TIER_NAMES"
MODEL_STATE_FILE="${MODEL_STATE_FILE:-/data/langgraph/model_state.json}"

# 可选节点开关
ENABLE_SECURITY_REVIEW="${ENABLE_SECURITY_REVIEW:-false}"
ENABLE_API_REVIEW="${ENABLE_API_REVIEW:-false}"

# 其他 API Keys（如需要）
OPENAI_API_KEY="${OPENAI_API_KEY:-}"
OPENAI_BASE_URL="${PROVIDER_BASE_URL}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
KIMI_API_KEY="${KIMI_API_KEY:-}"
MOONSHOT_API_KEY="${MOONSHOT_API_KEY:-}"
OPENCODE_ZEN_API_KEY="${OPENCODE_ZEN_API_KEY:-}"

# 飞书（如启用）
FEISHU_APP_ID="${FEISHU_APP_ID:-}"
FEISHU_APP_SECRET="${FEISHU_APP_SECRET:-}"
FEISHU_VERIFICATION_TOKEN="${FEISHU_VERIFICATION_TOKEN:-}"
FEISHU_NOTIFY_WEBHOOK="${FEISHU_NOTIFY_WEBHOOK:-}"
FEISHU_NOTIFY_SECRET="${FEISHU_NOTIFY_SECRET:-}"

# Telegram（如启用）
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"
EOF
    ok "环境变量文件已生成: $DATA_DIR/.env"
}

# ============================================================
# 主流程
# ============================================================
main() {
    if [ "$AUTO_MODE" = true ]; then
        print_header
        load_auto_config
        step_skills
        step_generate_configs
    else
        print_header
        step_model
        step_gateway
        step_feishu
        step_other_channels
        step_skills
        step_generate_configs
    fi

    # 标记初始化完成
    touch "$DATA_DIR/.initialized"

    echo ""
    echo "========================================"
    echo "  配置完成！"
    echo "========================================"
    echo ""
    echo "配置文件位置："
    echo "  OpenClaw:  $OPENCLAW_DIR/openclaw.json"
    echo "  OpenCode:  $OPENCODE_DIR/config.json"
    echo "  LangGraph: $LANGGRAPH_DIR/langgraph.json"
    echo "  环境变量:  $DATA_DIR/.env"
    echo ""
    echo "Gateway Token: $OPENCLAW_GATEWAY_TOKEN"
    echo ""
    echo "正在启动服务..."
    echo ""
}

main
