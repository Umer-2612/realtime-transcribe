import os

PORT             = int(os.getenv("PORT",            "8765"))
MODEL_ID         =     os.getenv("MODEL_ID",        "iic/SenseVoiceSmall")
LANGUAGE         =     os.getenv("LANGUAGE",        "en")
SILENCE_MS       = int(os.getenv("SILENCE_MS",      "1500"))
RMS_THRESHOLD    = float(os.getenv("RMS_THRESHOLD", "0.01"))
SESSION_TIMEOUT  = int(os.getenv("SESSION_TIMEOUT", "600"))

SAMPLE_RATE      = 16_000

# paraformer-streaming only
CHUNK_SIZE       = [5, 10, 5]

# Offline: run a partial inference every N chunks (~1 s at 256 ms/chunk)
PARTIAL_EVERY_N  = 4

# Minimum buffer before transcribing — avoids hallucinations on silence
MIN_SAMPLES      = SAMPLE_RATE   # 1 s at 16 kHz

IS_STREAMING     = "streaming" in MODEL_ID.lower() or "online" in MODEL_ID.lower()

# ── Turn detection (vogent-turn) ────────────────────────────────────────────
# Replaces the fixed SILENCE_MS timer with a model call once RMS silence has
# held for TURN_DEBOUNCE_MS. SILENCE_MS still applies as a hard ceiling, and
# as the sole trigger if the model failed to load (see app/turn_detector.py).
TURN_DETECTION_ENABLED = os.getenv("TURN_DETECTION_ENABLED", "true").lower() not in ("false", "0", "")
TURN_MODEL_ID          =     os.getenv("TURN_MODEL_ID",        "vogent/Vogent-Turn-80M")
TURN_MODEL_REVISION    =     os.getenv("TURN_MODEL_REVISION",  None)
TURN_COMPILE_MODEL     =     os.getenv("TURN_COMPILE_MODEL",   "false").lower() not in ("false", "0", "")
TURN_PROB_THRESHOLD    = float(os.getenv("TURN_PROB_THRESHOLD", "0.5"))
TURN_DEBOUNCE_MS       = int(os.getenv("TURN_DEBOUNCE_MS",      "300"))
TURN_AUDIO_WINDOW_S    = float(os.getenv("TURN_AUDIO_WINDOW_S", "8"))
