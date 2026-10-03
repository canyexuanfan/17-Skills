"""x-client-transaction-id 请求头签名（平台原生实现）。

本包只做一件事：按当前解释器所在平台，加载随包分发的原生扩展并转发它的
``XClId`` / ``build`` 两个入口。除这两个名字外的实现细节不属于公开接口。
"""
from __future__ import annotations

import glob
import importlib.machinery
import importlib.util
import os
import platform
import sys

__all__ = ["XClId", "build", "platform_tag", "native_path"]

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.join(_HERE, "bin")
_NAME = "_xclid"


def platform_tag() -> str:
    """当前平台标识，与 bin/<tag>/ 目录名一致。"""
    sysname = sys.platform
    machine = (platform.machine() or "").lower()
    arm = machine in ("aarch64", "arm64")
    if sysname.startswith("win"):
        return "windows-" + ("arm64" if arm else "amd64")
    if sysname == "darwin":
        return "macos-" + ("arm64" if arm else "x86_64")
    if sysname.startswith("linux"):
        return "linux-" + ("aarch64" if arm else "x86_64")
    return "%s-%s" % (sysname, machine or "unknown")


def available() -> list:
    """随包分发、本机可加载的平台标签。"""
    try:
        return sorted(d for d in os.listdir(_BIN) if os.path.isdir(os.path.join(_BIN, d)))
    except OSError:
        return []


def native_path() -> str | None:
    hits = sorted(glob.glob(os.path.join(_BIN, platform_tag(), _NAME + ".*")))
    return hits[0] if hits else None


def _load():
    path = native_path()
    if not path:
        raise ImportError(
            "当前平台没有对应的签名模块：%s（Python %s，%s）。本包已内置：%s。"
            "请到项目主页提 issue 说明这一行信息。"
            % (platform_tag(), platform.python_version(), platform.machine() or "?",
               ", ".join(available()) or "无"))
    loader = importlib.machinery.ExtensionFileLoader(_NAME, path)
    spec = importlib.util.spec_from_file_location(_NAME, path, loader=loader)
    if spec is None:
        raise ImportError("无法识别原生模块格式：%s" % path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_NAME] = mod
    try:
        loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(_NAME, None)
        raise
    return mod


_impl = _load()

XClId = _impl.XClId
build = getattr(_impl, "build", None)
