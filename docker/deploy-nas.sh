#!/bin/bash
# ================================================
# 多 Agent 工具链 - NAS Docker 部署脚本
# 适用：无 Docker Compose 的 NAS 环境
# 数据目录: /share/CACHEDEV1_DATA/Container/ai-workspace
# ================================================

set -e

WORKSPACE="/share/CACHEDEV1_DATA/Container/ai-workspace"
CONTAINER_NAME="agent-toolchain"
IMAGE_NAME="luowenqiang/ai-coding-tools:latest"

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

if ! docker image inspect "$IMAGE_NAME" &>/dev/null; then
    error "镜像 $IMAGE_NAME 不存在"
    info "请先将镜像加载到 NAS: docker load -i /path/to/ai-coding-tools.tar"
    info "或从 Docker Hub 拉取: docker pull $IMAGE_NAME"
    exit 1
fi

# ---- 检查是否已有容器运行 ----
if docker ps --format '{{.Names}}' | grep -q "^$CONTAINER_NAME$"; then
    error "容器 $CONTAINER_NAME 已在运行"
    info "管理命令: docker stop/rm/logs $CONTAINER_NAME"
    exit 1
fi

# ---- 清理已停止的残留容器 ----
if docker ps -a --format '{{.Names}}' | grep -q "^$CONTAINER_NAME$"; then
    warn "删除已停止的残留容器..."
    docker rm "$CONTAINER_NAME" >/dev/null
fi

echo "========================================"
echo "  多 Agent 工具链 - NAS 部署"
echo "========================================"
echo ""

# ---- 创建工作目录 ----
info "工作目录: $WORKSPACE"
for dir in openclaw opencode langgraph memory logs; do
    mkdir -p "$WORKSPACE/$dir"
done
ok "数据目录已就绪"

# ---- 检查业务目录 ----
for pair in "codes 代码" "phtos 图片" "knowledge-base 知识库"; do
    dir="${pair%% *}"
    label="${pair##* }"
    [ -d "$WORKSPACE/$dir" ] && ok "发现$label目录: $dir" || warn "$label目录 $dir 不存在，将自动创建"
done

# ---- 获取 API Key（如果环境变量为空则提示输入） ----
if [ -z "${KIMI_API_KEY:-}" ]; then
    echo ""
    info "需要 Kimi Code API Key"
    echo "------------------------------------------------"
    echo "  获取: https://platform.moonshot.cn/console/api-keys"
    echo "------------------------------------------------"
    read -p "请输入 Kimi API Key (sk-...): " KIMI_API_KEY
    [ -z "$KIMI_API_KEY" ] && { error "API Key 不能为空"; exit 1; }
fi

# ---- 启动容器 ----
info "启动容器 $CONTAINER_NAME ..."

docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  --memory 6g \
  -p 18789:18789 \
  -p 8000:8000 \
  -v "$WORKSPACE/openclaw:/data/openclaw" \
  -v "$WORKSPACE/opencode:/data/opencode" \
  -v "$WORKSPACE/langgraph:/data/langgraph" \
  -v "$WORKSPACE/memory:/data/memory" \
  -v "$WORKSPACE/logs:/data/logs" \
  -v "$WORKSPACE/codes:/data/code:rw" \
  -v "$WORKSPACE/phtos:/data/photos:rw" \
  -v "$WORKSPACE/knowledge-base:/data/knowledge:rw" \
  -e "INIT_MODE=auto" \
  -e "PROVIDER_NAME=kimi" \
  -e "PROVIDER_BASE_URL=https://api.kimi.com/coding/" \
  -e "PROVIDER_API_TYPE=anthropic-messages" \
  -e "MODEL_ID=kimi-for-coding" \
  -e "MODEL_NAME=Kimi Code" \
  -e "PRIMARY_MODEL=kimi/kimi-for-coding" \
  -e "KIMI_API_KEY=$KIMI_API_KEY" \
  -e "API_KEY=$KIMI_API_KEY" \
  -e "OPENCODE_PORT=4096" \
  -e "OPENCODE_SKIP_PERMISSIONS=true" \
  -e "OPENCODE_PRIMARY_MODEL=kimi-for-coding/k2p5" \
  -e "OPENCLAW_GATEWAY_PORT=18789" \
  -e "LANGGRAPH_PORT=8000" \
  -e "LANGGRAPH_PERSISTENCE=true" \
  -e "LANGGRAPH_CHECKPOINT_PATH=/data/langgraph/checkpoints.sqlite" \
  "$IMAGE_NAME"

echo ""
if [ $? -eq 0 ]; then
    ok "容器 $CONTAINER_NAME 已启动！"
    echo ""
    echo "========================================"
    echo "  常用命令"
    echo "========================================"
    echo ""
    echo "  查看日志:          docker logs -f $CONTAINER_NAME"
    echo "  进入容器:          docker exec -it $CONTAINER_NAME bash"
    echo "  停止容器:          docker stop $CONTAINER_NAME"
    echo "  重启容器:          docker restart $CONTAINER_NAME"
    echo "  删除容器:          docker rm -f $CONTAINER_NAME"
    echo ""
    echo "  健康检查:"
    echo "    curl http://127.0.0.1:18789/health"
    echo "    curl http://127.0.0.1:8000/info"
    echo ""
    echo "  获取 Gateway Token:"
    echo "    docker exec $CONTAINER_NAME jq -r '.gateway.auth.token' /data/openclaw/.openclaw/openclaw.json"
    echo ""
    echo "  触发自动化编程工作流:"
    echo "    docker exec $CONTAINER_NAME langgraph-trigger \\"
    echo '      --task-title "开发 Flask 用户管理 API" \'
    echo '      --task-desc "用户注册、登录、CRUD" \'
    echo '      --code-dir "/data/code/user-api"'
    echo ""
    echo "  重初始化（清除标记后重启会自动重新配置）:"
    echo "    docker exec $CONTAINER_NAME rm -f /data/.initialized"
    echo "    docker restart $CONTAINER_NAME"
    echo ""
    echo "  初始化标记位于容器内 /data/.initialized，宿主机上不可见"
    echo ""
else
    error "启动失败，请检查日志: docker logs $CONTAINER_NAME"
    exit 1
fi
