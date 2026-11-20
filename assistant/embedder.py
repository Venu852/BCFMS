"""Sentence embeddings from a local all-MiniLM-L6-v2 checkpoint.

Runs on the CPU (it is tiny) so the GPU stays free for Gemma. If the model or
torch is missing, embed() returns None and retrieval falls back to keywords.
"""
import logging
import os
import threading

import numpy as np
from django.conf import settings

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_state = {"tokenizer": None, "model": None, "failed": False}


def _load():
    if _state["model"] is not None or _state["failed"]:
        return
    with _lock:
        if _state["model"] is not None or _state["failed"]:
            return
        try:
            # Offline by design: never contact the Hugging Face Hub, and skip TensorFlow.
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
            os.environ.setdefault("USE_TF", "0")
            from transformers import AutoModel, AutoTokenizer

            path = str(settings.BCFMS_EMBED_PATH)
            _state["tokenizer"] = AutoTokenizer.from_pretrained(path, local_files_only=True)
            _state["model"] = AutoModel.from_pretrained(path, local_files_only=True).eval()
        except Exception:
            logger.exception("Embedding model unavailable; retrieval will use keywords only")
            _state["failed"] = True


def available():
    _load()
    return _state["model"] is not None


def embed(texts):
    """Return an (n, 384) float32 array of unit vectors, or None if unavailable."""
    _load()
    if _state["model"] is None or not texts:
        return None
    import torch

    out = []
    with _lock, torch.no_grad():
        for start in range(0, len(texts), 32):
            batch = _state["tokenizer"](texts[start : start + 32], padding=True, truncation=True, max_length=256, return_tensors="pt")
            hidden = _state["model"](**batch).last_hidden_state
            mask = batch["attention_mask"].unsqueeze(-1).float()
            pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            out.append(torch.nn.functional.normalize(pooled, dim=1).numpy())
    return np.vstack(out).astype(np.float32)
