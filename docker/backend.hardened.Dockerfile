# 加固版后端镜像：在标准镜像基础上，用 Nuitka 把 core/ 算法包编译成二进制，
# 删除 .py 源码，提升知识产权保护强度（用于交付到客户服务器的长期方案）。
#
# 用法（在仓库根目录，需先构建出标准镜像 giant-tube-app-backend:latest）：
#   docker build -f docker/backend.Dockerfile -t giant-tube-app-backend:latest .
#   docker build -f docker/backend.hardened.Dockerfile -t giant-tube-app-backend:hardened .
#
# ⚠️ Nuitka 编译较重，正式交付前请在目标构建机上完整跑通并验证壁厚/内外壁结果。
# ⚠️ 依赖、版本、发布通道(release)全部继承自标准镜像，保证计算结果一致。
ARG BASE_IMAGE=giant-tube-app-backend:latest
FROM ${BASE_IMAGE}

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc g++ patchelf \
    && rm -rf /var/lib/apt/lists/*

# 此时 /app/backend/core/common/build_info.py 已是标准镜像写入的 release 版，
# Nuitka 会把它一并编译进 core*.so，因此加固后仍是 release 通道。
COPY docker/harden_compile.sh /tmp/harden_compile.sh
RUN bash /tmp/harden_compile.sh /app/backend

EXPOSE 5060

CMD ["python", "-c", "from app import create_app, socketio; app = create_app(); socketio.run(app, host='0.0.0.0', port=5060, debug=False, allow_unsafe_werkzeug=True)"]
