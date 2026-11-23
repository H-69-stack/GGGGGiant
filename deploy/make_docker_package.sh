#!/usr/bin/env bash
#
# 生成 Mac/Linux Docker 离线部署包（托管部署场景）。
# 产物：dist/giant-tube-app-docker/ 及 dist/giant-tube-app-docker.zip
#
# 用法（仓库根目录）：
#   bash deploy/make_docker_package.sh
#   bash deploy/make_docker_package.sh --no-zip
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DO_ZIP=1
for arg in "$@"; do
  case "$arg" in
    --no-zip) DO_ZIP=0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

BACKEND_IMAGE="giant-tube-app-backend:latest"
FRONTEND_IMAGE="giant-tube-app-frontend:latest"
PKG_NAME="giant-tube-app-docker"
STAGE="$ROOT_DIR/dist/$PKG_NAME"

export DOCKER_PLATFORM="linux/amd64"

APP_VERSION="$(cat "$ROOT_DIR/VERSION" 2>/dev/null | tr -d ' \t\r\n' || echo 0.0.0)"
[[ -z "$APP_VERSION" ]] && APP_VERSION="0.0.0"
GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export APP_VERSION GIT_COMMIT BUILD_CHANNEL="release"

echo "==> 版本: $APP_VERSION  通道: $BUILD_CHANNEL  commit: $GIT_COMMIT"

if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker 守护进程未运行。请先启动 Docker Desktop。" >&2
  exit 1
fi

echo "==> 构建 release 镜像（linux/amd64）..."
docker compose build

echo "==> 验证镜像内含 plotly..."
docker run --rm "$BACKEND_IMAGE" python -c "import plotly.graph_objects; print('plotly OK')"

echo "==> 准备打包目录: $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE/images" "$STAGE/license" "$STAGE/backend"

echo "==> 导出镜像..."
docker save "$BACKEND_IMAGE" | gzip > "$STAGE/images/backend.tar.gz"
docker save "$FRONTEND_IMAGE" | gzip > "$STAGE/images/frontend.tar.gz"
{
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "platform=linux/amd64"
  echo "package=docker-offline"
  echo "app_version=$APP_VERSION"
  echo "build_channel=$BUILD_CHANNEL"
  echo "git_commit=$GIT_COMMIT"
  echo "backend_image=$BACKEND_IMAGE"
  echo "frontend_image=$FRONTEND_IMAGE"
} > "$STAGE/images/MANIFEST.txt"

echo "==> 复制部署文件..."
cp deploy/windows/offline/docker-compose.yml "$STAGE/docker-compose.yml"
cp deploy/docker/load_images.sh "$STAGE/load_images.sh"
chmod +x "$STAGE/load_images.sh"
cat > "$STAGE/.env.example" <<'ENVEOF'
APP_PORT=5890
SECRET_KEY=change-this-secret-key-before-deploy
DOCKER_PLATFORM=linux/amd64

# 托管公网部署（必改）
GTAPP_BIND_SERVER_MACHINE=0
GTAPP_ALLOWED_CIDRS=0.0.0.0/0,::/0
GTAPP_IP_ALLOWLIST=1
GTAPP_COOKIE_SECURE=0
ENVEOF
cp license/PUT_LICENSE_HERE.txt "$STAGE/license/PUT_LICENSE_HERE.txt" 2>/dev/null || true
if [[ -f license/license.dat ]]; then
  cp license/license.dat "$STAGE/license/license.dat"
  echo "    已附带 license/license.dat"
fi

echo "==> 复制初始数据 backend/database ..."
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete backend/database/ "$STAGE/backend/database/"
else
  mkdir -p "$STAGE/backend/database"
  cp -R backend/database/. "$STAGE/backend/database/"
fi

echo "==> 创建 backend/data 目录骨架..."
mkdir -p \
  "$STAGE/backend/data/uploads" \
  "$STAGE/backend/data/exports" \
  "$STAGE/backend/data/cache" \
  "$STAGE/backend/data/pp_saved_sources" \
  "$STAGE/backend/data/rfm_sessions" \
  "$STAGE/backend/data/rfm_shaping" \
  "$STAGE/backend/data/rfm_belling" \
  "$STAGE/backend/data/rfm_flattening" \
  "$STAGE/backend/data/rfm_bending" \
  "$STAGE/backend/data/tp_mold"
for d in "$STAGE/backend/data" "$STAGE/backend/data"/*; do
  [[ -d "$d" ]] && : > "$d/.keep"
done

cat > "$STAGE/启动.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export APP_PORT="${APP_PORT:-5890}"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已根据 .env.example 生成 .env，可按需修改后重新运行。"
fi
bash load_images.sh
docker compose up -d
echo ""
echo "已启动：http://127.0.0.1:${APP_PORT}"
echo "默认管理员：账号 10000 / 密码 Admin@2027"
EOF
chmod +x "$STAGE/启动.sh"

cat > "$STAGE/停止.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose down
echo "已停止服务（数据与许可证保留）。"
EOF
chmod +x "$STAGE/停止.sh"

cat > "$STAGE/使用说明.txt" <<TXT
巨管软件 - Docker 离线部署包（托管版）
========================================
版本: $APP_VERSION    commit: $GIT_COMMIT
构建时间: $(date -u +%Y-%m-%dT%H:%M:%SZ)

前置条件：
  已安装并启动 Docker Desktop（Mac/Windows）或 Docker Engine（Linux）。

部署步骤：
  1) 解压本包到任意目录。
  2) 将签发的 license.dat 放入 license/ 目录（若包内未附带）。
  3) 执行: bash 启动.sh
  4) 浏览器打开 http://127.0.0.1:5890

功能说明：
  - 用户公开注册（/signup）后需管理员审批。
  - 普通用户首次登录自动绑定浏览器；换电脑需管理员解绑设备。
  - 托管部署默认 GTAPP_BIND_SERVER_MACHINE=0（license 机器码填 ANY）。

常用命令：
  bash 停止.sh
  docker compose logs -f backend
TXT

echo ""
echo "==> 离线包目录: $STAGE"
du -sh "$STAGE" 2>/dev/null || true

if [[ "$DO_ZIP" == "1" ]]; then
  echo "==> 压缩为 zip..."
  rm -f "$ROOT_DIR/dist/$PKG_NAME.zip"
  ( cd "$ROOT_DIR/dist" && zip -r -q "$PKG_NAME.zip" "$PKG_NAME" )
  echo "==> 完成: $ROOT_DIR/dist/$PKG_NAME.zip"
  du -sh "$ROOT_DIR/dist/$PKG_NAME.zip" 2>/dev/null || true
fi

echo ""
echo "✅ Docker 离线包已生成。"
