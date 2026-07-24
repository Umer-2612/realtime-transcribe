# realtime-transcribe — WebSocket Transcription Service

A self-hosted WebSocket service that accepts raw audio streams and returns live transcriptions using [FunASR](https://github.com/modelscope/FunASR).

**Default endpoint:** `ws://localhost:8765`

---

## Quick start

```bash
make install   # create .venv and install dependencies
make run       # start the WebSocket service on ws://localhost:8765
```

The service prints `Model ready.` then `Listening on ws://0.0.0.0:8765` when ready.

### All make commands

| Command | Description |
|---|---|
| `make install` | Create `.venv` (Python 3.12) and install dependencies |
| `make run` | Start the transcription service |
| `make example` | Serve the browser demo at `http://localhost:3000` |
| `make test` | Run deployment/config tests |
| `make clean` | Remove `__pycache__` directories |

### Without make

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

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
PORT=9000 MODEL_ID=iic/SenseVoiceSmall LANGUAGE=auto make run
```

### Available models

| `MODEL_ID` | Mode | Languages | Latency |
|---|---|---|---|
| `iic/SenseVoiceSmall` *(default)* | Offline | en, zh, ja, ko, yue | ~1 s |
| `paraformer-zh-streaming` | Streaming | zh only | ~250 ms |

Models are downloaded automatically from ModelScope on first run.

---

## Deploy to Render

This repo is ready for Render blueprint deploys through `render.yaml`. Render builds the Docker image, injects the configured environment variables, and uses `/health` as the service health check.

### 1. Create the Render service

1. Open Render and choose **New > Blueprint**.
2. Connect this GitHub repository.
3. Select the `service` branch, or whichever branch contains `render.yaml`.
4. Confirm the `realtime-transcribe` web service from `render.yaml`.
5. Use at least the `standard` instance type. FunASR and Torch are too heavy for small free instances.

If you create the service manually instead of using the blueprint, use these settings:

| Field | Value |
|---|---|
| Root Directory | Leave blank / repository root |
| Runtime | Docker |
| Dockerfile Path | `./Dockerfile` |
| Docker Context | `.` |
| Build Command | Leave blank |
| Start Command | Leave blank |
| Health Check Path | `/health` |

Docker services use the `CMD ["python", "main.py"]` instruction in the Dockerfile unless you explicitly set a Docker command.

### 2. Environment variables

The blueprint defines these defaults:

| Variable | Value |
|---|---|
| `PORT` | `8765` |
| `MODEL_ID` | `iic/SenseVoiceSmall` |
| `LANGUAGE` | `en` |
| `SILENCE_MS` | `1500` |
| `RMS_THRESHOLD` | `0.01` |
| `SESSION_TIMEOUT` | `600` |

### 3. Verify the deployment

Render exposes HTTPS for health checks:

```bash
curl https://<your-render-service>.onrender.com/health
```

Expected response:

```json
{"status":"ok","service":"realtime-transcribe"}
```

Use WebSocket Secure for transcription traffic:

```text
wss://<your-render-service>.onrender.com
```

### 4. Keepalive pings

The repo includes `.github/workflows/render-keepalive.yml`, which pings the Render health endpoint every 10 minutes.

After Render gives you the service URL, add this GitHub repository secret:

```text
RENDER_SERVICE_URL=https://<your-render-service>.onrender.com
```

GitHub scheduled workflows run from the repository's default branch, so make sure this workflow is on the default branch if you rely on scheduled pings. You can also run it manually from the **Actions** tab.

### 5. Connect the frontend

Set this in your frontend staging/production environment:

```env
NEXT_PUBLIC_FUNASR_URL=wss://<your-render-service>.onrender.com
```

Redeploy the frontend after changing the env.

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
