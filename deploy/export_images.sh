#!/usr/bin/env bash
#
# 构建并导出离线镜像（在你的开发机 / 受控构建机上运行一次）。
#
# 目的：把"当前代码 + 锁定好的全部依赖"烤进 Docker 镜像，并导出成 .tar.gz 文件。
#       客户的 Windows 机器只需 docker load 这两个文件，不再现场重新构建、
#       不再重新解析依赖，从而与开发环境做到二进制级一模一样。
#
# 注意架构：Windows + Docker Desktop 跑的是 linux/amd64 容器。
#   - 本脚本固定按 linux/amd64 构建（compose 里已写死 platform）。
#   - 若你在 Apple Silicon (arm64) 上构建，Docker 会用 QEMU 模拟 amd64，
#     能构建成功但较慢；如条件允许，建议在一台 x86_64 机器上跑本脚本。
#
# 用法（仓库根目录）：
#   bash deploy/export_images.sh            # 标准镜像
#   bash deploy/export_images.sh --hardened # 加固镜像（Nuitka 编译 core）
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HARDENED=0
if [[ "${1:-}" == "--hardened" ]]; then HARDENED=1; fi

BACKEND_IMAGE="giant-tube-app-backend:latest"
FRONTEND_IMAGE="giant-tube-app-frontend:latest"
OUT_DIR="$ROOT_DIR/deploy/images"
mkdir -p "$OUT_DIR"

export DOCKER_PLATFORM="linux/amd64"

echo "==> 构建镜像（linux/amd64）..."
if [[ "$HARDENED" == "1" ]]; then
  echo "    使用加固后端镜像（Nuitka）"
  docker build --platform linux/amd64 \
    -f docker/backend.hardened.Dockerfile \
    -t "$BACKEND_IMAGE" .
fi
docker compose build

echo "==> 导出后端镜像 -> $OUT_DIR/backend.tar.gz"
docker save "$BACKEND_IMAGE" | gzip > "$OUT_DIR/backend.tar.gz"

echo "==> 导出前端镜像 -> $OUT_DIR/frontend.tar.gz"
docker save "$FRONTEND_IMAGE" | gzip > "$OUT_DIR/frontend.tar.gz"

# 记录构建信息，便于排查
{
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "platform=linux/amd64"
  echo "hardened=$HARDENED"
  echo "backend_image=$BACKEND_IMAGE"
  echo "frontend_image=$FRONTEND_IMAGE"
} > "$OUT_DIR/MANIFEST.txt"

echo ""
echo "==> 完成。请把以下文件随安装包一起发给客户："
echo "    $OUT_DIR/backend.tar.gz"
echo "    $OUT_DIR/frontend.tar.gz"
echo "    $OUT_DIR/MANIFEST.txt"
echo "    Windows 端运行 deploy\\windows\\install.ps1 时会自动 docker load 这两个文件。"
