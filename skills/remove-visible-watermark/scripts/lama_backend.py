"""Optional local deep-repair backend: big-lama via simple-lama-inpainting.

This backend is opt-in only. The classical pipeline stays the default and the
``auto`` method never selects this backend silently; it runs only when the
caller passes ``--method lama``. Weights load from local storage and inference
is fully offline; nothing is uploaded.

The caller remains responsible for the write scope: this module returns a full
candidate image, and ``restore`` copies only pixels inside the authorized mask
and re-runs the same scope validation as every other backend.
"""
from __future__ import annotations

import numpy as np

INSTALL_HINT = (
    "Deep repair needs optional dependencies that are not installed: run "
    "PYTHON -m pip install simple-lama-inpainting (installs PyTorch and pins "
    "numpy<2/Pillow<10, so prefer a dedicated virtual environment; the "
    "big-lama weights load locally and processing never touches the network)."
)

_STATE = {"model": None, "loaded": False}


def availability() -> dict:
    """JSON-safe backend status without loading weights."""
    try:
        import simple_lama_inpainting  # noqa: F401
        import torch
    except Exception as exc:
        return {"available": False, "install_hint": INSTALL_HINT,
                "import_error": str(exc)}
    try:
        device = "cuda" if torch.cuda.is_available() else "cpu"
        torch_version = str(torch.__version__)
    except Exception as exc:  # pragma: no cover - broken torch install
        return {"available": False, "install_hint": INSTALL_HINT,
                "import_error": str(exc)}
    return {"available": True, "package": "simple-lama-inpainting",
            "model": "big-lama (fp32)", "device": device,
            "torch": torch_version}


def _model():
    if not _STATE["loaded"]:
        import simple_lama_inpainting
        _STATE["model"] = simple_lama_inpainting.SimpleLama()
        _STATE["loaded"] = True
    return _STATE["model"]


def inpaint(rgb_uint8: np.ndarray, holes_bool: np.ndarray) -> np.ndarray:
    """Fill every True pixel of ``holes_bool`` with big-lama inpainting.

    ``rgb_uint8`` is H x W x 3 uint8 RGB and ``holes_bool`` a boolean array of
    the same height and width. Returns uint8 RGB with the original shape; the
    backend may internally pad to a multiple of 8 and crops back. Raises
    ValueError with an install hint when the optional package is missing.
    """
    rgb = np.asarray(rgb_uint8)
    holes = np.asarray(holes_bool)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("lama backend expects an H x W x 3 uint8 RGB array.")
    if holes.dtype != np.bool_ or holes.shape != rgb.shape[:2]:
        raise ValueError("lama backend holes mask must be boolean and match "
                         "the image height and width.")
    if not holes.any():
        return rgb.copy()
    info = availability()
    if not info["available"]:
        raise ValueError(info["install_hint"])
    from PIL import Image
    image = Image.fromarray(np.ascontiguousarray(rgb), mode="RGB")
    mask = Image.fromarray(
        np.ascontiguousarray(holes.astype(np.uint8)) * 255, mode="L")
    result = _model()(image, mask)
    out = np.asarray(result.convert("RGB"), dtype=np.uint8)
    if out.shape != rgb.shape:
        raise ValueError("lama backend returned an unexpected image size.")
    return out
