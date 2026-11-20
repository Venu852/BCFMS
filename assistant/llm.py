"""Gemma 4 E2B, run in-process by llama.cpp from a local GGUF file.

Nothing leaves the machine: there is no HTTP client here, only a file on disk.
The model is loaded on first use and kept in memory.
"""
import atexit
import logging
import os
import threading
from importlib.util import find_spec
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)
_lock = threading.Lock()  # llama.cpp contexts are not thread-safe; also serialises loading
_state = {"llm": None, "error": ""}


def _add_cuda_dll_dirs():
    """The CUDA wheel of llama-cpp-python needs the CUDA runtime DLLs on Windows.
    PyTorch and the nvidia-* pip packages both ship them, so reuse those."""
    if os.name != "nt":
        return
    candidates = []
    for package, pattern in (("torch", "lib"), ("nvidia", "*/bin")):
        try:
            spec = find_spec(package)
        except (ImportError, ValueError):
            spec = None
        for root in (spec.submodule_search_locations or []) if spec else []:
            candidates.extend(Path(root).glob(pattern))
    for path in candidates:
        if path.is_dir():
            os.add_dll_directory(str(path))
            os.environ["PATH"] = f"{path}{os.pathsep}{os.environ.get('PATH', '')}"


def _load():
    if _state["llm"] is not None or _state["error"]:
        return
    if not settings.BCFMS_LLM_ENABLED:
        _state["error"] = "The language model is switched off (BCFMS_LLM_ENABLED=0)."
        return
    path = Path(settings.BCFMS_LLM_PATH)
    if not path.is_file():
        _state["error"] = f"Model file not found: {path}"
        return
    try:
        _add_cuda_dll_dirs()
        from llama_cpp import Llama

        _state["llm"] = Llama(
            model_path=str(path),
            n_gpu_layers=settings.BCFMS_LLM_GPU_LAYERS,
            n_ctx=settings.BCFMS_LLM_CONTEXT,
            verbose=False,
        )
        # Free the model explicitly; llama.cpp's own finaliser is unreliable at interpreter exit.
        atexit.register(_state["llm"].close)
    except Exception as exc:
        logger.exception("Could not load the language model")
        _state["error"] = f"Could not load the language model: {exc}"


def status():
    return {
        "enabled": settings.BCFMS_LLM_ENABLED,
        "loaded": _state["llm"] is not None,
        "model": Path(settings.BCFMS_LLM_PATH).name,
        "present": Path(settings.BCFMS_LLM_PATH).is_file(),
        "error": _state["error"],
    }


def chat(messages, max_tokens=None):
    """Return the model's reply, or None when the model is unavailable."""
    with _lock:
        _load()
        if _state["llm"] is None:
            return None
        result = _state["llm"].create_chat_completion(
            messages=messages,
            max_tokens=max_tokens or settings.BCFMS_LLM_MAX_NEW_TOKENS,
            # Low temperature: the task is faithful extraction from supplied text, not creativity.
            temperature=0.2,
            top_p=0.95,
            top_k=64,
            repeat_penalty=1.05,
        )
    return (result["choices"][0]["message"]["content"] or "").strip()
