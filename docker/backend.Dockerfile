# 后端镜像：纯 Python 基础镜像 + 全 pip 安装，依赖版本与开发机 (conda env: giant)
# 完全一致。不使用 conda，避免不同时间/平台 conda 解析出不同版本（曾导致
# trimesh/numpy 漂移、Windows 上内外壁分不出、壁厚算错）。
FROM python:3.12.2-slim-bookworm

# cadquery-ocp(OpenCASCADE) 与 trimesh 渲染/几何运算所需的系统库
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl ca-certificates \
        libgl1 libglu1-mesa \
        libglib2.0-0 \
        libsm6 libice6 libxext6 libxrender1 libxi6 libx11-6 \
        libfontconfig1 libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app/backend
ENV HTTP_PROXY=
ENV HTTPS_PROXY=
ENV NO_PROXY=localhost,127.0.0.1,frontend,backend

WORKDIR /app/backend

# 先装依赖（利用层缓存），版本全部锁定见 requirements.txt
COPY backend/requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r /tmp/requirements.txt \
    # cadquery 2.5.2 强制 nlopt>=2.9 -> 在 linux 上会拉高 numpy 到 2.x，破坏几何计算。
    # 故用 --no-deps 单独安装，保住 numpy 1.26.4。nlopt 2.7.1 仅供 cadquery 导入，
    # 本应用不使用其草图求解功能，版本不影响结果。
    && python -m pip install --no-deps nlopt==2.7.1 cadquery==2.5.2

# 构建期冒烟测试：确保关键库与 numpy 版本正确、内外壁/STEP 依赖均可导入。
RUN python -c "import numpy; assert numpy.__version__=='1.26.4', numpy.__version__; \
import scipy, trimesh, shapely, networkx; \
from scipy.spatial import ConvexHull; \
import nlopt; import cadquery; from cadquery import Shape; \
from OCP.STEPControl import STEPControl_Reader; \
import plotly.graph_objects; \
print('smoke-test OK: numpy', numpy.__version__, 'trimesh', trimesh.__version__)"

COPY backend /app/backend

# 发布通道与版本信息：覆盖 build_info.py 为 release（门禁恒开、忽略环境变量）。
ARG APP_VERSION=0.0.0
ARG GIT_COMMIT=unknown
ARG BUILD_CHANNEL=release
RUN printf 'BUILD_CHANNEL = "%s"\nVERSION = "%s"\nGIT_COMMIT = "%s"\n\n\ndef is_release():\n    return str(BUILD_CHANNEL).strip().lower() == "release"\n\n\ndef get_version():\n    return VERSION\n\n\ndef summary():\n    return f"{get_version()} ({BUILD_CHANNEL}, commit {GIT_COMMIT})"\n' \
        "$BUILD_CHANNEL" "$APP_VERSION" "$GIT_COMMIT" \
        > /app/backend/core/common/build_info.py \
    && python -c "from core.common.build_info import is_release, summary; assert is_release(); print('build:', summary())"

EXPOSE 5060

CMD ["python", "-c", "from app import create_app, socketio; app = create_app(); socketio.run(app, host='0.0.0.0', port=5060, debug=False, allow_unsafe_werkzeug=True)"]
