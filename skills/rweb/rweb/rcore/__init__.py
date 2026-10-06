"""平台原生核心模块的薄包装。

按当前解释器所在平台加载随包分发的扩展，只转发它的**单一入口** ``p``::

    p(code, ctype, body) -> (verdict, followup)

``verdict``: ``0`` 响应可直接当作数据读；``1`` 响应是会话页；``2`` 其它（含上游限流）。
``followup`` 非空时，表示把 ``BASE + followup`` 再请求一次即可完成会话初始化。

除 ``p`` 之外的实现细节不属于公开接口。
"""
from __future__ import annotations

import glob
import importlib.machinery
import importlib.util
import os
import platform
import sys

__all__ = ["p", "platform_tag", "native_path", "available"]

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.join(_HERE, "bin")
_NAME = "_m0"


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


def native_path():
    hits = sorted(glob.glob(os.path.join(_BIN, platform_tag(), _NAME + ".*")))
    return hits[0] if hits else None


def _load():
    path = native_path()
    if not path:
        raise ImportError(
            "当前平台没有对应的核心模块：%s（Python %s，%s）。本包已内置：%s。"
            "请到项目主页提 issue 附上这一行信息。"
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
p = _impl.p
