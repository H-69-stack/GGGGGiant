"""构建信息与发布通道。

本文件的默认值代表「开发态(dev)」。打包/构建镜像时，会被 Dockerfile
（或打包脚本）覆盖为 release 通道，并写入真实版本号与 git commit。

通道(BUILD_CHANNEL)的作用：
  - dev     ：开发态。允许用环境变量 GTAPP_LICENSE_GATE=0 临时关闭许可证门禁，
              方便本地调试。
  - release ：发布态。许可证门禁**恒定开启、忽略环境变量**，防止交付后被绕过。

⚠️ 不要在 release 镜像里把本文件改回 dev，否则等于关掉知识产权保护。
"""
from __future__ import annotations

import os

# ── 以下三项在构建 release 镜像时由 Dockerfile/打包脚本覆盖 ──
BUILD_CHANNEL = "dev"
VERSION = "dev"
GIT_COMMIT = "unknown"


def is_release() -> bool:
    """是否为发布通道（发布通道下门禁恒开、忽略环境变量）。"""
    return str(BUILD_CHANNEL).strip().lower() == "release"


def get_version() -> str:
    """返回版本号。

    release 通道：返回构建时写入的 VERSION 常量。
    dev 通道：尝试读取仓库根目录的 VERSION 文件，方便本地也能显示真实版本。
    """
    if VERSION and VERSION != "dev":
        return VERSION
    here = os.path.dirname(os.path.abspath(__file__))
    # backend/core/common -> 仓库根目录（向上三级）
    root_version = os.path.normpath(os.path.join(here, "..", "..", "..", "VERSION"))
    try:
        with open(root_version, "r", encoding="utf-8") as f:
            v = f.read().strip()
            return v or "dev"
    except OSError:
        return "dev"


def summary() -> str:
    return f"{get_version()} ({BUILD_CHANNEL}, commit {GIT_COMMIT})"
