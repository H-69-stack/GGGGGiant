#!/usr/bin/env bash
# 用 Nuitka 把后端核心算法包 core/ 编译成二进制扩展，删除对应 .py 源码，
# 显著抬高"客户阅读/篡改源码删除许可证校验"的门槛。
#
# 在 Linux（容器内）执行最稳妥——基于标准镜像(python:slim + pip)，依赖均已就绪。
# 本脚本被 docker/backend.hardened.Dockerfile 调用，也可手动运行。
#
# 说明：
#   - 编译 core 包（含子包），保留 api/ app.py config.py 为普通 .py
#     （这些只是薄薄的接线层，真正的算法 IP 在 core/）。
#   - 编译失败时整体退出，避免产出"半生不熟"的镜像。
set -euo pipefail

APP_DIR="${1:-/app/backend}"
cd "$APP_DIR"

echo "[harden] 安装 Nuitka 工具链..."
python -m pip install --no-input nuitka

echo "[harden] 用 Nuitka 编译 core 包为二进制扩展..."
python -m nuitka \
  --module core \
  --include-package=core \
  --output-dir=/tmp/nuitka_out \
  --remove-output \
  --no-pyi-file \
  --assume-yes-for-downloads

# 用编译产物替换源码目录
echo "[harden] 替换源码为二进制..."
COMPILED="$(find /tmp/nuitka_out -maxdepth 1 -name 'core*.so' | head -n1)"
if [ -z "$COMPILED" ]; then
  echo "[harden] 错误：未找到编译产物 core*.so" >&2
  exit 1
fi

rm -rf core
cp "$COMPILED" "$APP_DIR/"
echo "[harden] 完成：已用 $(basename "$COMPILED") 替换 core/ 源码目录。"
