"""Model-based turn-completion detection (vogent-turn).

Loaded eagerly at import time, same as app/model.py. Wrapped in try/except
because the weights live in a gated Hugging Face repo — a missing HF_TOKEN,
unapproved access, or a network blip must not take down transcription, so a
failure here just disables turn detection and app/handler.py falls back to
the plain SILENCE_MS timer.
"""

import numpy as np

from app import config

_detector = None

if config.TURN_DETECTION_ENABLED:
    try:
        from vogent_turn import TurnDetector

        print(f"[turn] Loading turn-detection model: {config.TURN_MODEL_ID} …", flush=True)
        _detector = TurnDetector(
            model_name=config.TURN_MODEL_ID,
            revision=config.TURN_MODEL_REVISION,
            compile_model=config.TURN_COMPILE_MODEL,
        )
        print("[turn] Turn-detection model ready.", flush=True)
    except Exception as e:
        print(f"[turn] Turn-detection unavailable, falling back to silence timer: {e!r}", flush=True)


def available() -> bool:
    return _detector is not None


def predict(audio: np.ndarray, prev_line: str, curr_line: str) -> dict:
    return _detector.predict(
        audio,
        prev_line=prev_line,
        curr_line=curr_line,
        sample_rate=config.SAMPLE_RATE,
        return_probs=True,
    )
