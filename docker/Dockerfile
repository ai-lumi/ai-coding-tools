FROM node:24-slim

LABEL maintainer="luowenqiang"
LABEL description="Multi-Agent Coding Toolchain (OpenClaw + OpenCode + LangGraph)"

# ============================================================
# 版本控制：所有组件版本集中在 VERSIONS 文件中，
# 通过 Docker ARG 注入，确保构建可复现。
# 构建时可覆盖：docker build --build-arg OPENCLAW_VERSION=x.y.z
# ============================================================
ARG OPENCLAW_VERSION=2026.6.8
ARG OPENCODE_VERSION=1.17.8
ARG FEISHU_PLUGIN_VERSION=2026.6.8

# 避免交互式提示
ENV DEBIAN_FRONTEND=noninteractive

# 设置时区为 GMT+8
ENV TZ=Asia/Shanghai

# 设置默认字符集为 UTF-8
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

# 将构建 ARG 持久化为运行时可访问的环境变量（entrypoint 需要读取）
ENV FEISHU_PLUGIN_VERSION=${FEISHU_PLUGIN_VERSION}

# 更换为阿里云 Debian 源，加速国内构建
# Debian 12 使用 .sources 格式，位于 /etc/apt/sources.list.d/debian.sources
# 使用 HTTP 而非 HTTPS，避免 slim 镜像缺少 ca-certificates 导致证书验证失败
RUN sed -i 's|http://deb.debian.org|http://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    sed -i 's|Components: main|Components: main contrib non-free non-free-firmware|g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖（含时区数据和 locales）
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    python3-pip \
    supervisor \
    gettext-base \
    jq \
    curl \
    git \
    openssl \
    tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

# 锁定版本安装 OpenClaw 与 OpenCode，保证构建可复现
# 分开安装避免依赖解析冲突，同时便于定位哪个包安装失败
RUN npm install -g openclaw@${OPENCLAW_VERSION}
RUN npm install -g opencode-ai@${OPENCODE_VERSION}

# 预安装飞书渠道插件（npm 侧），将包下载到全局 npm cache 中，
# 运行时 entrypoint 执行 openclaw plugins install 时会直接命中缓存，无需联网
# 同时将包 tarball 保留在镜像中作为离线回退
RUN mkdir -p /opt/feishu-plugin && \
    cd /opt/feishu-plugin && \
    npm pack @openclaw/feishu@${FEISHU_PLUGIN_VERSION} && \
    cd / && \
    rm -rf /opt/feishu-plugin

# 预创建 LangGraph Python 虚拟环境并安装依赖，避免每次容器启动都重新下载
# 使用清华大学 PyPI 镜像加速国内构建；venv 放在 /opt 下，避免被 /data/langgraph 挂载覆盖
COPY templates/langgraph-src/requirements.txt /tmp/requirements.txt
RUN mkdir -p /opt/langgraph && \
    python3 -m venv /opt/langgraph/.venv && \
    /opt/langgraph/.venv/bin/pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple -r /tmp/requirements.txt && \
    rm -f /tmp/requirements.txt && \
    /opt/langgraph/.venv/bin/pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 创建 /data 目录结构
RUN mkdir -p /data/openclaw/.openclaw \
    /data/openclaw/.config/opencode \
    /data/openclaw/skills \
    /data/opencode/.config/opencode \
    /data/langgraph/src \
    /data/memory/session \
    /data/memory/tasks \
    /data/memory/daily \
    /data/photos \
    /data/code \
    /data/knowledge \
    /data/logs

# 复制配置模板到 /etc/templates/
COPY templates/ /etc/templates/

# 复制引导脚本、入口脚本以及 OpenCode wrapper
COPY scripts/ /usr/local/bin/
RUN chmod +x \
    /usr/local/bin/init-wizard.sh \
    /usr/local/bin/entrypoint.sh \
    /usr/local/bin/opencode-tool \
    /usr/local/bin/langgraph-trigger \
    /usr/local/bin/langgraph-trigger-photo-sorter \
    /usr/local/bin/langgraph-resume

# 复制 supervisord 主配置（直接作为主配置文件，而非 include 片段）
COPY supervisord.conf /etc/supervisor/supervisord.conf

# 设置环境变量默认值
ENV INIT_MODE=interactive
ENV OPENCLAW_GATEWAY_PORT=18789
ENV LANGGRAPH_PORT=8000

# 暴露端口
EXPOSE 18789 8000

# 健康检查：检查 OpenClaw Gateway HTTP health 端点
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://127.0.0.1:18789/health || exit 1

# 入口点
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
