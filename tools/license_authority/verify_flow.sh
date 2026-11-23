#!/usr/bin/env bash
# 许可证全流程冒烟测试：密钥 → 签发 → 本地校验 →（可选）Docker release 校验
#
# 用法（仓库根目录）：
#   bash tools/license_authority/verify_flow.sh
#   bash tools/license_authority/verify_flow.sh --skip-docker
#
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKIP_DOCKER=0
for arg in "$@"; do
  case "$arg" in
    --skip-docker) SKIP_DOCKER=1 ;;
    *) echo "未知参数: $arg"; exit 2 ;;
  esac
done

cd "$ROOT_DIR"
ISSUED="$LA_DIR/issued/test_flow_license.dat"
LICENSE_DST="$ROOT_DIR/license/license.dat"

echo "==> [1/5] 检查发证密钥..."
python "$LA_DIR/generate_keys.py" || true
if [[ ! -f "$LA_DIR/keys/private_key.pem" ]]; then
  echo "❌ 未找到私钥。请先运行: cd tools/license_authority && python generate_keys.py"
  exit 1
fi
echo "    私钥 OK"

echo "==> [2/5] 签发测试许可证（machine_fingerprint=ANY，托管模式）..."
mkdir -p "$LA_DIR/issued" "$ROOT_DIR/license"
python "$LA_DIR/issue_license.py" \
  --customer "流程验证-$(date +%Y%m%d)" \
  --machine-code ANY \
  --roles ALL \
  --expires "$(date -v+1y +%Y-%m-%d 2>/dev/null || date -d '+1 year' +%Y-%m-%d)" \
  --note "verify_flow.sh 自动签发" \
  --out "$ISSUED"
python "$LA_DIR/issue_license.py" --verify "$ISSUED"
cp "$ISSUED" "$LICENSE_DST"
echo "    已安装: $LICENSE_DST"

echo "==> [3/5] 本地 LicenseManager 校验..."
PYTHONPATH=backend GTAPP_LICENSE_PATH="$LICENSE_DST" GTAPP_BIND_SERVER_MACHINE=0 python - <<PY
import os
os.environ['GTAPP_LICENSE_PATH'] = os.path.abspath('$LICENSE_DST')
from core.common.license import get_license_manager
s = get_license_manager().get_status()
assert s.valid, s.reason
print('    valid=True customer=%s expires=%s' % (s.customer, s.expires_at))
PY

if [[ "$SKIP_DOCKER" == "1" ]]; then
  echo "==> [4/5] 已跳过 Docker（--skip-docker）"
  echo "✅ 本地流程验证完成"
  exit 0
fi

if ! docker info >/dev/null 2>&1; then
  echo "==> [4/5] Docker 未运行，跳过容器校验（可启动 Docker Desktop 后重跑）"
  echo "✅ 本地流程验证完成（未测 Docker）"
  exit 0
fi

echo "==> [5/5] Docker release 通道校验..."
cat > "$ROOT_DIR/.env" <<EOF
APP_PORT=5890
SECRET_KEY=license-flow-verify
GTAPP_BIND_SERVER_MACHINE=0
GTAPP_IP_ALLOWLIST=1
EOF
docker compose up -d
sleep 6

STATUS="$(curl -sf http://127.0.0.1:5890/api/auth/license-status)"
echo "$STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['license']['valid'], d; print('    API license-status: valid=True')"

HTTP_CODE="$(curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:5890/api/normal/mold-options)"
[[ "$HTTP_CODE" == "200" ]] || { echo "❌ mold-options 期望 200，实际 $HTTP_CODE"; exit 1; }
echo "    API mold-options: HTTP 200"

docker compose logs backend 2>&1 | grep -E '通道|许可证状态' | tail -3 | sed 's/^/    /'

echo "✅ 全流程验证完成（含 Docker release）"
