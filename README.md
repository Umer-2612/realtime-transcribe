# Real-time Transcribe

A local, real-time speech transcription system powered by [FunASR](https://github.com/modelscope/FunASR). Speak into your browser microphone and see live partial + final transcriptions streamed back instantly.

## How it works

```
Browser (mic) → AudioWorklet → hex PCM → WebSocket → Python server → FunASR model → transcript
```

- **`transcribe_server.py`** — Python WebSocket server (port 8765) that loads a FunASR model and returns `asr` (partial) and `asr_ended` (final) events.
- **`client/`** — Static HTML/JS frontend served over HTTP (port 3000). Captures mic at 16 kHz, encodes as Int16 hex, and streams to the server.

## Supported models

| Model ID | Mode | Languages |
|---|---|---|
| `iic/SenseVoiceSmall` *(default)* | Offline — ~1 s lag, highest accuracy | en, zh, ja, ko, yue |
| `paraformer-zh-streaming` | Streaming — lowest latency | Chinese only |

Change the `MODEL_ID` and `LANGUAGE` constants at the top of `transcribe_server.py` to switch models.

## Requirements

- Python **3.12**
- PyTorch (CPU or CUDA)
- A working microphone
- A modern browser (Chrome/Edge recommended — Firefox may need flags for AudioWorklet)

## Setup

### 1. Create and activate the virtual environment

```bash
python3.12 -m venv .venv
source .venv/bin/activate   # macOS / Linux
# .venv\Scripts\activate    # Windows
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

> First run also downloads the FunASR model (~300 MB for SenseVoiceSmall). This happens automatically when the server starts.

## Running

You need **two terminal windows** open at the same time.

### Terminal 1 — WebSocket transcription server

```bash
source .venv/bin/activate
python transcribe_server.py
```

You'll see `[server] Model ready.` and `[server] Listening on ws://0.0.0.0:8765` when it's ready.

### Terminal 2 — Static file server for the browser client

```bash
python -m http.server 3000 -d client
```

### Open the app

Navigate to [http://localhost:3000](http://localhost:3000) in your browser, click the microphone button, and start speaking.

## Project structure

```
realtime-transcribe/
├── transcribe_server.py   # FunASR WebSocket server
├── requirements.txt       # Python dependencies
├── client/
│   ├── index.html         # Browser UI
│   └── worklet.js         # AudioWorklet mic processor
└── .python-version        # Pins Python 3.12 (used by pyenv)
```

## WebSocket protocol

The browser and server communicate over `ws://localhost:8765` using JSON messages.

**Browser → Server**

| Message | Description |
|---|---|
| `{ "type": "mic_test", "language": "en" }` | Session init |
| `{ "type": "audio", "data": "<hex int16 PCM>" }` | 256 ms audio chunk |
| `{ "type": "end" }` | Stop and flush |

**Server → Browser**

| Message | Description |
|---|---|
| `{ "type": "ready" }` | Model loaded, session open |
| `{ "type": "asr", "data": { "results": [{ "text": "...", "definite": false }] } }` | Partial transcript |
| `{ "type": "asr_ended", "text": "..." }` | Final committed transcript |
| `{ "type": "timeout" }` | Session expired (10 min) |

## Configuration

All tuneable constants are at the top of `transcribe_server.py`:

| Constant | Default | Description |
|---|---|---|
| `MODEL_ID` | `iic/SenseVoiceSmall` | FunASR model to load |
| `LANGUAGE` | `en` | Language hint for offline models |
| `SILENCE_MS` | `1500` | Silence duration (ms) that triggers a final flush |
| `RMS_THRESHOLD` | `0.01` | RMS level below which audio is treated as silence |
| `SESSION_TIMEOUT` | `600` | Auto-close idle session after N seconds |

## Troubleshooting

**Mic not working in the browser** — The AudioWorklet API requires the page to be served over HTTP (not `file://`). Always use `python -m http.server 3000 -d client`.

**Model downloads on first run** — FunASR fetches the model weights from ModelScope automatically. Ensure you have internet access the first time.

**Port conflicts** — Change `PORT = 8765` in `transcribe_server.py` and update `WS_URL` in `client/index.html` to match.
