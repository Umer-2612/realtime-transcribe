import re
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from funasr import AutoModel

from app.config import MODEL_ID, CHUNK_SIZE

_TOKEN_RE = re.compile(r"<\|[^|>]+\|>")

print(f"[service] Loading model: {MODEL_ID} …", flush=True)
_model      = AutoModel(model=MODEL_ID, disable_update=True, disable_pbar=True)
_lock       = threading.Lock()
executor    = ThreadPoolExecutor(max_workers=4)
print("[service] Model ready.", flush=True)


def clean(text: str) -> str:
    return _TOKEN_RE.sub("", text).strip()


def infer_streaming(audio: np.ndarray, cache: dict, is_final: bool) -> list:
    with _lock:
        result = _model.generate(
            audio,
            cache=cache,
            is_final=is_final,
            chunk_size=CHUNK_SIZE,
            encoder_chunk_look_back=4,
            decoder_chunk_look_back=1,
        )
    return result or []


def infer_offline(audio: np.ndarray, language: str) -> list:
    with _lock:
        result = _model.generate(audio, language=language, use_itn=True)
    return result or []
