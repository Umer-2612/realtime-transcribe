# realtime-transcribe — WebSocket Transcription Service

A self-hosted WebSocket service that accepts raw audio streams and returns live transcriptions using [FunASR](https://github.com/modelscope/FunASR).

**Default endpoint:** `ws://localhost:8765`

---

## Quick start

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python transcribe_server.py
```

The service prints `Model ready.` then `Listening on ws://0.0.0.0:8765` when ready.

---

## Configuration

All settings are controlled via environment variables.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8765` | WebSocket port |
| `MODEL_ID` | `iic/SenseVoiceSmall` | FunASR model. See models below. |
| `LANGUAGE` | `en` | Default language hint (`en`, `zh`, `ja`, `ko`, `auto`) |
| `SILENCE_MS` | `1500` | Silence duration (ms) that triggers a final flush |
| `RMS_THRESHOLD` | `0.01` | RMS level below which audio is treated as silence |
| `SESSION_TIMEOUT` | `600` | Seconds before an idle session is closed |

```bash
PORT=9000 MODEL_ID=iic/SenseVoiceSmall LANGUAGE=auto python transcribe_server.py
```

### Available models

| `MODEL_ID` | Mode | Languages | Latency |
|---|---|---|---|
| `iic/SenseVoiceSmall` *(default)* | Offline | en, zh, ja, ko, yue | ~1 s |
| `paraformer-zh-streaming` | Streaming | zh only | ~250 ms |

Models are downloaded automatically from ModelScope on first run.

---

## Protocol

### 1. Connect

```
ws://localhost:8765
```

On connection the service immediately sends:

```json
{ "type": "ready" }
```

Do not send audio until you receive this.

---

### 2. Messages you send

#### `start` — begin a session

Send once after `ready`. Optional, but lets you set a per-session language.

```json
{ "type": "start", "language": "en" }
```

| Field | Type | Description |
|---|---|---|
| `language` | string | `en`, `zh`, `ja`, `ko`, `yue`, `auto` — overrides server default |

#### `audio` — stream a chunk

Send continuously while recording. Each chunk must be exactly **256 ms** of audio.

```json
{ "type": "audio", "data": "1a2b3c..." }
```

| Field | Type | Description |
|---|---|---|
| `data` | string | Hex-encoded **Int16 PCM**, **16 kHz**, **mono**. Each chunk = 4096 samples = 8192 bytes → 16384 hex chars. |

**Audio format:**
- Sample rate: **16 000 Hz**
- Encoding: **Int16 (signed 16-bit little-endian)**
- Channels: **mono**
- Chunk size: **4096 samples** (256 ms)

#### `end` — stop and flush

Tells the service to finalize any buffered audio and emit the last `final` result.

```json
{ "type": "end" }
```

---

### 3. Messages you receive

#### `partial` — in-progress transcript

Emitted roughly every 1 second while the speaker is talking. Text may change with each update.

```json
{ "type": "partial", "text": "hello how are" }
```

#### `final` — committed transcript

Emitted after a pause in speech (`SILENCE_MS`) or when you send `end`. Represents one complete utterance.

```json
{ "type": "final", "text": "Hello, how are you?" }
```

#### `timeout`

Session has been idle for `SESSION_TIMEOUT` seconds. Connection is closed after this.

```json
{ "type": "timeout" }
```

#### `error`

Sent when the service receives malformed input.

```json
{ "type": "error", "message": "audio data must be hex-encoded int16 PCM" }
```

---

## Typical session flow

```
CLIENT                          SERVICE
  |                                |
  |── connect ─────────────────────▶|
  |◀──────────────── {"type":"ready"}
  |                                |
  |── {"type":"start","language":"en"}
  |── {"type":"audio","data":"..."} |
  |── {"type":"audio","data":"..."} |
  |◀──────── {"type":"partial","text":"hello"}
  |── {"type":"audio","data":"..."} |
  |── {"type":"audio","data":"..."} |
  |◀──────── {"type":"partial","text":"hello world"}
  |                                |
  |   (speaker pauses)             |
  |◀────────── {"type":"final","text":"Hello world."}
  |                                |
  |── {"type":"end"} ──────────────▶|
  |── disconnect ──────────────────▶|
```

---

## Requirements

- Python 3.12
- PyTorch 2.0+ (CPU or CUDA)
- Dependencies: `pip install -r requirements.txt`
