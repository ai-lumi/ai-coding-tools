#!/bin/bash
# 容器入口脚本
# 负责检测初始化状态、执行引导或直接启动服务

set -e

DATA_DIR="/data"
INIT_FLAG="$DATA_DIR/.initialized"
INIT_MODE="${INIT_MODE:-interactive}"

# 颜色
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${CYAN}[信息]${NC}   $*"; }
ok()    { echo -e "${GREEN}[成功]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[警告]${NC}   $*"; }

echo "========================================"
echo "  多 Agent 工具链 - 启动中..."
echo "========================================"

# 加载 .env 文件（如果存在）
if [ -f "$DATA_DIR/.env" ]; then
    info "加载环境变量: $DATA_DIR/.env"
    set -a
    source "$DATA_DIR/.env"
    set +a
fi

# 自动模式：如果关键环境变量都已提供，则强制使用非交互初始化
case "$INIT_MODE" in
    interactive)
        if [ ! -f "$INIT_FLAG" ]; then
            info "首次启动，进入配置向导..."
            /usr/local/bin/init-wizard.sh
        else
            info "检测到初始化标记，跳过向导"
        fi
        ;;
    auto)
        if [ ! -f "$INIT_FLAG" ]; then
            info "自动模式：根据环境变量生成配置..."
            /usr/local/bin/init-wizard.sh --auto
        else
            info "检测到初始化标记，跳过向导"
        fi
        ;;
    skip)
        info "跳过初始化向导"
        ;;
    reset)
        info "重置模式：清除初始化标记，重新进入向导"
        rm -f "$INIT_FLAG"
        /usr/local/bin/init-wizard.sh
        ;;
    *)
        echo "错误: 未知的 INIT_MODE=$INIT_MODE"
        echo "支持的模式: interactive, auto, skip, reset"
        exit 1
        ;;
esac

# 初始化 LangGraph Python 虚拟环境（镜像已预装在 /opt/langgraph/.venv，此处检测完整性）
if [ ! -x "/opt/langgraph/.venv/bin/langgraph" ]; then
    info "LangGraph 虚拟环境缺失或损坏，重新创建..."
    rm -rf /opt/langgraph/.venv
    mkdir -p /opt/langgraph
    python3 -m venv /opt/langgraph/.venv
    /opt/langgraph/.venv/bin/pip install --no-cache-dir -r /data/langgraph/src/requirements.txt
    ok "LangGraph 虚拟环境已创建并安装依赖"
fi

# 初始化记忆目录（如果不存在）
if [ ! -d "$DATA_DIR/memory" ]; then
    info "初始化记忆目录..."
    mkdir -p "$DATA_DIR/memory/session"
    mkdir -p "$DATA_DIR/memory/tasks"
    mkdir -p "$DATA_DIR/memory/daily"
    ok "记忆目录已初始化"
fi

# 复制记忆模板文件（如果 NAS/挂载卷上没有）
for f in MEMORY.md USER.md SOUL.md AGENTS.md; do
    if [ ! -f "$DATA_DIR/memory/$f" ]; then
        cp "/etc/templates/memory/$f" "$DATA_DIR/memory/$f" 2>/dev/null || warn "复制记忆模板 $f 失败"
    fi
done

# 重新加载 init-wizard 生成的 /data/.env，确保 supervisord 子进程继承新变量
if [ -f "$DATA_DIR/.env" ]; then
    info "重新加载环境变量: $DATA_DIR/.env"
    set -a
    source "$DATA_DIR/.env"
    set +a
fi

# 确保 OpenClaw 调用 OpenCode 时能读到配置
# 将 opencode 配置同步到 openclaw 用户的 HOME 下
if [ -f "/data/opencode/.config/opencode/config.json" ] && [ ! -f "/data/openclaw/.config/opencode/config.json" ]; then
    mkdir -p /data/openclaw/.config/opencode
    cp /data/opencode/.config/opencode/config.json /data/openclaw/.config/opencode/config.json
fi

# 设置默认端口（确保 supervisord %(ENV_*)s 展开有值）
: "${OPENCODE_PORT:=4096}"
: "${OPENCODE_HOSTNAME:=127.0.0.1}"
: "${OPENCLAW_GATEWAY_PORT:=18789}"
: "${OPENCLAW_GATEWAY_BIND:=0.0.0.0}"
: "${LANGGRAPH_PORT:=8000}"
: "${OPENCODE_SKIP_PERMISSIONS:=true}"
: "${LANGGRAPH_PERSISTENCE:=true}"
: "${LANGGRAPH_CHECKPOINT_PATH:=/data/langgraph/checkpoints.sqlite}"
export OPENCODE_PORT OPENCODE_HOSTNAME OPENCLAW_GATEWAY_PORT OPENCLAW_GATEWAY_BIND LANGGRAPH_PORT
export OPENCODE_SKIP_PERMISSIONS LANGGRAPH_PERSISTENCE LANGGRAPH_CHECKPOINT_PATH

# 信号处理器：将 SIGTERM/SIGINT 转发到 supervisord，实现优雅关闭
trap 'info "收到关闭信号，转发到 supervisord..."; kill -TERM $SUPERVISOR_PID 2>/dev/null' TERM INT

# 启动 supervisord（后台运行，等待各服务就绪后再继续）
info "启动服务管理器..."
/usr/bin/supervisord -c /etc/supervisor/supervisord.conf &
SUPERVISOR_PID=$!

# 等待 OpenClaw Gateway 就绪后安装飞书渠道插件
info "等待 OpenClaw Gateway 启动..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:${OPENCLAW_GATEWAY_PORT}/ >/dev/null 2>&1; then
        info "Gateway 已就绪，安装飞书渠道插件（${FEISHU_PLUGIN_VERSION:-latest}）..."
        if ! ls /data/openclaw/.openclaw/npm/projects/openclaw-feishu-* >/dev/null 2>&1; then
            HOME=/data/openclaw openclaw plugins install "@openclaw/feishu@${FEISHU_PLUGIN_VERSION:-latest}" 2>/dev/null || true
        fi
        ok "飞书渠道插件已加载"
        # 重启 Gateway 触发 schema migration（创建 acp_sessions 等缺失表）
        info "重启 Gateway 以加载插件并完成数据库迁移..."
        supervisorctl restart openclaw 2>/dev/null || true
        sleep 3
        break
    fi
    sleep 1
done

# 等待 OpenCode HTTP Server 就绪（LangGraph 工作流依赖它执行编码任务）
info "等待 OpenCode HTTP Server 启动（端口 ${OPENCODE_PORT}）..."
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:${OPENCODE_PORT}/global/health >/dev/null 2>&1; then
        ok "OpenCode HTTP Server 已就绪"
        break
    fi
    if [ "$i" -eq 30 ]; then
        warn "OpenCode HTTP Server 未在预期时间内就绪，LangGraph 将在首次调用时重试"
    fi
    sleep 2
done

# 将 supervisord 带回前台，确保信号正常传递
wait $SUPERVISOR_PID
