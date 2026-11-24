#!/usr/bin/env bash
set -euo pipefail

# 固定项目路径，确保从任何工作目录调用都使用本项目的配置和数据卷。
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
fi

# 显式指定配置文件，避免自动加载官方的开发端口覆盖文件。
compose() {
  docker compose --project-name opik-local \
    --env-file "$PROJECT_DIR/.env" \
    -f "$PROJECT_DIR/deployment/docker-compose.yaml" \
    -f "$PROJECT_DIR/deployment/compose.local.yaml" \
    --profile opik "$@"
}

# 等待原有数据库就绪，再恢复 Langfuse 的网页和后台处理服务。
wait_for_container() {
  local container_name="$1" # 只检查明确列出的既有 Langfuse 容器。
  local attempt
  for ((attempt = 0; attempt < 120; attempt++)); do
    if [[ "$(docker inspect --format '{{.State.Health.Status}}' "$container_name")" == healthy ]]; then
      return 0
    fi
    sleep 2
  done
  echo "等待 $container_name 就绪超时，请查看该容器日志。" >&2
  return 1
}

# 这六个容器是部署前经用户授权暂停的 Langfuse 实例；恢复时不创建或删除数据卷。
restore_langfuse() {
  local infrastructure=(
    deepagents-langfuse-demo-postgres-1
    deepagents-langfuse-demo-redis-1
    deepagents-langfuse-demo-clickhouse-1
    deepagents-langfuse-demo-minio-1
  )
  local container_name
  compose stop
  docker start "${infrastructure[@]}"
  for container_name in "${infrastructure[@]}"; do
    wait_for_container "$container_name"
  done
  docker start deepagents-langfuse-demo-langfuse-web-1 deepagents-langfuse-demo-langfuse-worker-1
  echo "Langfuse 已启动：http://127.0.0.1:3000；网页服务可能还需要数十秒完成初始化。"
}

# 子命令只管理 opik-local 项目，恢复 Langfuse 的显式命令除外。
COMMAND="${1:-start}"
if [[ $# -gt 0 ]]; then shift; fi
case "$COMMAND" in
  start)
    compose up --detach --no-build --pull missing --wait --wait-timeout 600 "$@"
    echo "Opik 已启动；运行 ./opik.sh smoke 可验证接口写入和读取。"
    ;;
  stop) compose stop "$@" ;;
  status) compose ps --all "$@" ;;
  logs) compose logs --follow --tail 100 "$@" ;;
  pull) compose pull "$@" ;;
  smoke) python3 "$PROJECT_DIR/smoke.py" "$@" ;;
  demo|session-demo)
    # 独立环境运行迁移后的真实订单样例；首次自动安装锁定的依赖。
    uv run --project "$PROJECT_DIR" --locked --default-index https://pypi.org/simple "$PROJECT_DIR/order_demo.py" "$@"
    ;;
  pdf-demo)
    # 复用 PDF 主子代理和逐页工具，将模型、工具、附件记录到 Opik。
    uv run --project "$PROJECT_DIR" --locked --default-index https://pypi.org/simple "$PROJECT_DIR/pdf_demo.py" "$@"
    ;;
  evidence-demo)
    # 同一条 PDF 轨迹包含真实压缩、HTTP skill、MCP 调用，取证评估也写入 Opik。
    uv run --project "$PROJECT_DIR" --locked --default-index https://pypi.org/simple "$PROJECT_DIR/pdf_demo.py" --full-evidence "$@"
    ;;
  restore-langfuse) restore_langfuse ;;
  *)
    echo "用法：$0 {start|stop|status|logs [服务名]|pull|smoke|demo|session-demo|pdf-demo|evidence-demo|restore-langfuse}" >&2
    exit 2
    ;;
esac
