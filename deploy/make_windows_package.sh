#!/usr/bin/env bash
#
# 一条命令生成「完整 Windows 离线部署包」。
#
# 产物：dist/giant-tube-app-win/  以及压缩包 dist/giant-tube-app-win.zip
#   把这个包整个拷到 Windows，双击「部署.bat」即可运行（无需联网、无需构建、
#   依赖与本开发机完全一致）。
#
# 在「能用 Docker 的构建机」上运行（仓库根目录）：
#   bash deploy/make_windows_package.sh                # 标准镜像
#   bash deploy/make_windows_package.sh --hardened     # 加固镜像
#   bash deploy/make_windows_package.sh --no-zip       # 只生成目录，不压缩
#
# 注意架构：Windows + Docker Desktop 跑 linux/amd64 容器，脚本固定按 amd64 构建。
#   在 Apple Silicon(arm64) 上会用 QEMU 模拟 amd64，能成功但较慢；
#   如有 x86_64 机器，建议在那上面跑本脚本，更快更稳。
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

HARDENED=0
DO_ZIP=1
for arg in "$@"; do
  case "$arg" in
    --hardened) HARDENED=1 ;;
    --no-zip)   DO_ZIP=0 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

BACKEND_IMAGE="giant-tube-app-backend:latest"
FRONTEND_IMAGE="giant-tube-app-frontend:latest"
PKG_NAME="giant-tube-app-win"
STAGE="$ROOT_DIR/dist/$PKG_NAME"

export DOCKER_PLATFORM="linux/amd64"

# 版本/通道/commit：注入镜像（交付镜像一律 release 通道，门禁恒开）。
APP_VERSION="$(cat "$ROOT_DIR/VERSION" 2>/dev/null | tr -d ' \t\r\n' || echo 0.0.0)"
[[ -z "$APP_VERSION" ]] && APP_VERSION="0.0.0"
GIT_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
export APP_VERSION GIT_COMMIT
export BUILD_CHANNEL="release"
echo "==> 版本: $APP_VERSION  通道: $BUILD_CHANNEL  commit: $GIT_COMMIT"

# 0) 前置检查
echo "==> 检查 Docker..."
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker 守护进程未运行。请先启动 Docker Desktop 后重试。" >&2
  exit 1
fi

# 1) 构建镜像（amd64）
echo "==> 构建镜像（linux/amd64，首次较慢）..."
# 先构建标准后端 + 前端（compose 会读取 APP_VERSION/GIT_COMMIT/BUILD_CHANNEL）
docker compose build
if [[ "$HARDENED" == "1" ]]; then
  echo "    在标准镜像基础上加固（Nuitka 编译 core），覆盖 $BACKEND_IMAGE"
  docker build --platform linux/amd64 \
    --build-arg BASE_IMAGE="$BACKEND_IMAGE" \
    -f docker/backend.hardened.Dockerfile \
    -t "$BACKEND_IMAGE" .
fi

# 2) 准备打包目录
echo "==> 准备打包目录: $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE/images" "$STAGE/license" "$STAGE/backend"

# 3) 导出镜像
echo "==> 导出后端镜像..."
docker save "$BACKEND_IMAGE" | gzip > "$STAGE/images/backend.tar.gz"
echo "==> 导出前端镜像..."
docker save "$FRONTEND_IMAGE" | gzip > "$STAGE/images/frontend.tar.gz"
{
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "platform=linux/amd64"
  echo "hardened=$HARDENED"
  echo "app_version=$APP_VERSION"
  echo "build_channel=$BUILD_CHANNEL"
  echo "backend_image=$BACKEND_IMAGE"
  echo "frontend_image=$FRONTEND_IMAGE"
  echo "git_commit=$GIT_COMMIT"
} > "$STAGE/images/MANIFEST.txt"

# 4) 复制部署脚本 + compose + env 模板
echo "==> 复制部署脚本..."
cp deploy/windows/offline/docker-compose.yml "$STAGE/docker-compose.yml"
cp deploy/windows/offline/install.ps1        "$STAGE/install.ps1"
cp deploy/windows/offline/uninstall.ps1      "$STAGE/uninstall.ps1"
cp deploy/windows/get_host_fingerprint.ps1   "$STAGE/get_host_fingerprint.ps1"
cp deploy/windows/offline/部署.bat            "$STAGE/部署.bat"
cp deploy/windows/offline/卸载.bat            "$STAGE/卸载.bat"
cp deploy/windows/offline/deploy.bat         "$STAGE/deploy.bat"
cp deploy/windows/offline/uninstall.bat      "$STAGE/uninstall.bat"
cp .env.example                              "$STAGE/.env.example"

# 机器码工具（若已构建或现场构建）
MC_EXE="$ROOT_DIR/dist/tools/gt-machine-code.exe"
if [[ ! -f "$MC_EXE" ]] && command -v python3 >/dev/null 2>&1; then
  echo "==> 构建 gt-machine-code.exe ..."
  python3 -m pip install pyinstaller -q 2>/dev/null || true
  python3 -m PyInstaller --noconfirm --onefile --console \
    --name gt-machine-code \
    --distpath "$ROOT_DIR/dist/tools" \
    --workpath "$ROOT_DIR/build/pyi-machine-code" \
    --specpath "$ROOT_DIR/build/pyi-machine-code" \
    --paths "$ROOT_DIR/backend" \
    "$ROOT_DIR/tools/machine_code/get_machine_code.py" 2>/dev/null || true
fi
if [[ -f "$MC_EXE" ]]; then
  mkdir -p "$STAGE/tools"
  cp "$MC_EXE" "$STAGE/tools/gt-machine-code.exe"
  echo "==> 已包含 tools/gt-machine-code.exe"
fi

# 站点授权证书（ANY 指纹 + ALL 角色 + 远期到期）：预置后部署机开箱即用、不被许可拦截。
if [[ -f deploy/windows/offline/site_license.dat ]]; then
  cp deploy/windows/offline/site_license.dat "$STAGE/license/license.dat"
  echo "==> 已预置站点授权证书 license/license.dat（部署后无需再申请许可证）"
else
  echo "⚠️  未找到 deploy/windows/offline/site_license.dat，离线包将不含许可证（部署后需手动放入）。"
fi

# 5) 复制初始数据（database + data 骨架）
#    - database 完整复制（含初始 sqlite，里面可能已有出厂 demo 数据）
#    - data 只保留目录骨架，剔除开发机上的运行时缓存与中间产物：
#        * rfm_sessions / rfm_*（成型/扩口/弯曲/压扁中间结果）
#        * tp_mold（旧版 TP 模具几何缓存）
#        * pp_saved_sources / uploads（开发机上用户的 STL）
#        * exports / cache
#      部署机第一次启动时这些目录会自动重新生成，开发机几百 MB 的旧
#      数据没必要随包传给客户。
echo "==> 复制初始数据 backend/database ..."
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete backend/database/ "$STAGE/backend/database/"
else
  mkdir -p "$STAGE/backend/database"
  cp -R backend/database/. "$STAGE/backend/database/"
fi

echo "==> 创建 backend/data 目录骨架（不携带运行时缓存）..."
mkdir -p \
  "$STAGE/backend/data" \
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
# 在每个空目录里放一个 .keep 占位文件，避免 git/zip 把空目录吞掉。
for d in \
  "$STAGE/backend/data" \
  "$STAGE/backend/data/uploads" \
  "$STAGE/backend/data/exports" \
  "$STAGE/backend/data/cache" \
  "$STAGE/backend/data/pp_saved_sources" \
  "$STAGE/backend/data/rfm_sessions" \
  "$STAGE/backend/data/rfm_shaping" \
  "$STAGE/backend/data/rfm_belling" \
  "$STAGE/backend/data/rfm_flattening" \
  "$STAGE/backend/data/rfm_bending" \
  "$STAGE/backend/data/tp_mold"; do
  : > "$d/.keep"
done

# 6) 简要使用说明（纯文本，避免依赖 md 阅读器）
cat > "$STAGE/使用说明.txt" <<'TXT'
巨管软件 - Windows 离线部署包
=================================

前置条件：
  目标机器已安装并启动 Docker Desktop（Windows 版，使用 Linux 容器）。

部署步骤：
  1) 把本文件夹整个拷贝到目标机器（任意目录，路径不要含特殊字符）。
  2) 确认 Docker Desktop 已启动（右下角小鲸鱼图标）。
  3) 双击「部署.bat」，等待完成（脚本会显示本机机器码）。
  4) 把机器码发送给软件供应商，获取 license.dat。
  5) 浏览器打开提示的访问地址，用默认管理员 admin / Admin@2027 登录。
  6) 进入「管理 → 部署设置」，上传 license.dat 完成授权。
  7) 也可双击 tools\gt-machine-code.exe 查看机器码。

常用操作：
  - 部署：双击 deploy.bat 或「部署.bat」
  - 停止：双击 uninstall.bat 或「卸载.bat」（数据与许可证不会被删除）
  - 换端口部署：以管理员打开 PowerShell，在本目录执行
        powershell -ExecutionPolicy Bypass -File install.ps1 -Port 8080
  - 调整内网访问网段：管理员登录 → 部署设置 → 保存网络配置
  - 查看日志：在本目录执行  docker compose logs -f backend

说明：
  本包内置了与开发环境完全一致的程序镜像与依赖，部署时不联网、不重新构建，
  因此各机器运行结果一致（包括壁厚/内外壁等计算）。
TXT

echo ""
echo "==> 离线包目录已生成: $STAGE"
du -sh "$STAGE" 2>/dev/null || true

# 7) 压缩
if [[ "$DO_ZIP" == "1" ]]; then
  echo "==> 压缩为 zip（较大，请稍候）..."
  ( cd "$ROOT_DIR/dist" && zip -r -q "$PKG_NAME.zip" "$PKG_NAME" )
  echo "==> 完成: $ROOT_DIR/dist/$PKG_NAME.zip"
  du -sh "$ROOT_DIR/dist/$PKG_NAME.zip" 2>/dev/null || true
else
  echo "==> 已跳过压缩（--no-zip）。直接拷贝目录 $STAGE 即可。"
fi

echo ""
echo "✅ 全部完成。把上面的 zip（或目录）拷到 Windows，双击「部署.bat」即可。"
