#!/bin/bash
# ================================================
# 多 Agent 工具链 - NAS 部署脚本（Docker Compose 方式）
# ================================================
# 推荐方式：cd docker && DATA_DIR=/share/Container/ai-workspace/data docker compose up -d
# 此脚本提供交互式引导，适用于不熟悉 docker compose 的用户。
# ================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_DIR="$(dirname "$SCRIPT_DIR")"
DEFAULT_DATA_DIR="/share/CACHEDEV1_DATA/Container/ai-workspace/data"
CONTAINER_NAME="agent-toolchain"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'
info()  { echo -e "${CYAN}[信息]${NC}   $*"; }
ok()    { echo -e "${GREEN}[成功]${NC}   $*"; }
warn()  { echo -e "${YELLOW}[警告]${NC}   $*"; }
error() { echo -e "${RED}[错误]${NC}   $*"; }

# ---- 前置检查 ----
if ! command -v docker &>/dev/null; then
    error "Docker 未安装，请先在 NAS 上安装 Docker"
    exit 1
fi

if ! command -v docker compose &>/dev/null; then
    error "Docker Compose 未安装"
    exit 1
fi

# ---- 检查是否已有容器运行 ----
if docker ps --format '{{.Names}}' | grep -q "^$CONTAINER_NAME$"; then
    error "容器 $CONTAINER_NAME 已在运行"
    info "管理: docker compose stop/rm/logs"
    exit 1
fi

echo "========================================"
echo "  多 Agent 工具链 - NAS 部署"
echo "========================================"
echo ""

# ---- 检查 .env 文件 ----
if [ ! -f "$COMPOSE_DIR/.env" ]; then
    info "未找到 $COMPOSE_DIR/.env，请从 .env.example 复制并修改"
    info "   cp .env.example .env"
    info "   关键配置项："
    info "     DATA_DIR=/share/Container/ai-workspace/data"
    info "     LANGGRAPH_BIND= （空=全接口）"
    info "     KIMI_API_KEY=sk-..."
    info "     OPENCODE_ZEN_API_KEY=sk-..."
    info "请编辑 $COMPOSE_DIR/.env 填入你的 API Key 后重新运行此脚本"
    echo ""
    echo "  关键配置项："
    echo "    DATA_DIR=$DEFAULT_DATA_DIR"
    echo "    KIMI_API_KEY=sk-..."
    echo "    OPENCODE_ZEN_API_KEY=sk-..."
    echo ""
    exit 0
fi

# ---- 确保数据目录存在 ----
# 从 .env 读取 DATA_DIR，若为空使用默认值
if grep -q "^DATA_DIR=" "$COMPOSE_DIR/.env" 2>/dev/null; then
    DATA_DIR=$(grep "^DATA_DIR=" "$COMPOSE_DIR/.env" | cut -d= -f2-)
fi
DATA_DIR="${DATA_DIR:-$DEFAULT_DATA_DIR}"
for dir in openclaw opencode langgraph memory logs photos code knowledge; do
    mkdir -p "$DATA_DIR/$dir"
done
ok "数据目录已就绪: $DATA_DIR"

# ---- 启动容器 ----
info "启动容器 ..."

cd "$COMPOSE_DIR"
DATA_DIR="$DATA_DIR" docker compose up -d

echo ""
ok "容器已启动！请使用 docker compose 管理："
echo ""
echo "========================================"
echo "  常用命令"
echo "========================================"
echo ""
echo "  查看日志:          docker compose logs -f"
echo "  进入容器:          docker compose exec agent-toolchain bash"
echo "  停止容器:          docker compose down"
echo "  完全重置:          docker compose down -v && rm -f data/.initialized"
echo ""
echo "  健康检查:"
echo "    curl http://<nas-ip>:18789/health"
echo "    curl http://<nas-ip>:8000/info"
echo ""
echo "  获取 Gateway Token:"
echo "    docker compose exec agent-toolchain jq -r '.gateway.auth.token' /data/openclaw/.openclaw/openclaw.json"
echo ""
echo "  触发自动化编程工作流:"
echo "    docker compose exec agent-toolchain langgraph-trigger \\"
echo '      --task-title "开发 Flask 用户管理 API" \'
echo '      --task-desc "用户注册、登录、CRUD" \'
echo '      --code-dir "/data/code/user-api"'
echo ""
echo "  重初始化:"
echo "    docker compose exec agent-toolchain rm -f /data/.initialized"
echo "    docker compose restart"
echo ""
