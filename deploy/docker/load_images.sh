#!/usr/bin/env bash
# 加载离线包内的 Docker 镜像（不重新构建）
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker 未运行，请先启动 Docker Desktop。" >&2
  exit 1
fi

for img in backend frontend; do
  f="images/${img}.tar.gz"
  if [[ ! -f "$f" ]]; then
    echo "❌ 未找到 $f" >&2
    exit 1
  fi
  echo "==> 加载镜像: $f"
  gunzip -c "$f" | docker load
done

echo "✅ 镜像加载完成"
