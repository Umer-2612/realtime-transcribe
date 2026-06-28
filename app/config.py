import os

PORT             = int(os.getenv("PORT",            "8765"))
MODEL_ID         =     os.getenv("MODEL_ID",        "iic/SenseVoiceSmall")
LANGUAGE         =     os.getenv("LANGUAGE",        "en")
SILENCE_MS       = int(os.getenv("SILENCE_MS",      "1500"))
RMS_THRESHOLD    = float(os.getenv("RMS_THRESHOLD", "0.01"))
SESSION_TIMEOUT  = int(os.getenv("SESSION_TIMEOUT", "600"))

# paraformer-streaming only
CHUNK_SIZE       = [5, 10, 5]

# Offline: run a partial inference every N chunks (~1 s at 256 ms/chunk)
PARTIAL_EVERY_N  = 4

# Minimum buffer before transcribing — avoids hallucinations on silence
MIN_SAMPLES      = 16_000   # 1 s at 16 kHz

IS_STREAMING     = "streaming" in MODEL_ID.lower() or "online" in MODEL_ID.lower()
