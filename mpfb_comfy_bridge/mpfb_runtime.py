import importlib
import sys


def package_name():
    candidates = []
    direct = sys.modules.get("mpfb")
    if direct is not None:
        candidates.append(direct)
    for module in list(sys.modules.values()):
        name = getattr(module, "__name__", "")
        if not name.endswith(".mpfb"):
            continue
        candidates.append(module)

    for module in candidates:
        if _looks_like_mpfb(module):
            return module.__name__

    try:
        module = importlib.import_module("mpfb")
        if _looks_like_mpfb(module):
            return "mpfb"
    except Exception:
        pass

    raise ModuleNotFoundError(
        "Could not locate the MPFB addon module. Enable MPFB before using MPFB ComfyUI Bridge."
    )


def services():
    return importlib.import_module(f"{package_name()}.services")


def ai_panel():
    return importlib.import_module(f"{package_name()}.ui.operations.ai.aipanel")


def openpose_constants():
    return importlib.import_module(f"{package_name()}.ui.operations.ai.operators._openposeconstants")


def _looks_like_mpfb(module):
    return (
        hasattr(module, "MPFB_CONTEXTUAL_INFORMATION")
        or hasattr(module, "ClassManager")
        or getattr(module, "__name__", "") == "mpfb"
    )
