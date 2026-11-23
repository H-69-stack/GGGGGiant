#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"
export APP_PORT="${APP_PORT:-5890}"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "未检测到 Docker Compose。请先安装 Docker Desktop 或 Docker Engine + Compose。"
  exit 1
fi

case "${1:-start}" in
  start)
    "${COMPOSE[@]}" up -d --build
    echo "已启动：http://127.0.0.1:${APP_PORT:-5890}"
    ;;
  stop)
    "${COMPOSE[@]}" down
    ;;
  restart)
    "${COMPOSE[@]}" down
    "${COMPOSE[@]}" up -d --build
    echo "已重启：http://127.0.0.1:${APP_PORT}"
    ;;
  logs)
    "${COMPOSE[@]}" logs -f --tail=200
    ;;
  status)
    "${COMPOSE[@]}" ps
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|logs|status}"
    exit 1
    ;;
esac
