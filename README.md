# realtime-transcribe — WebSocket Transcription Service

A self-hosted WebSocket service that accepts raw audio streams and returns live transcriptions using [FunASR](https://github.com/modelscope/FunASR).

**Default endpoint:** `ws://localhost:8765`

### What this covers

- A WebSocket service (`main.py`, `app/`) that takes raw 16kHz PCM audio and streams back live partial and final transcripts, using [FunASR](https://github.com/modelscope/FunASR).
- Turn detection via [vogent-turn](https://github.com/vogent/vogent-turn). Instead of always waiting a fixed 1.5s of silence, a model looks at the trailing audio plus the current transcript and decides whether the speaker is actually done.
- A browser demo (`examples/browser/`) that records your mic and streams it to the service, so you can try both pieces without writing a client.
- Docker and Render deploy config (`Dockerfile`, `render.yaml`) for hosting the WebSocket service.
- A small test suite (`tests/`) covering the deploy config, not the transcription logic itself.

---

## Quick start

```bash
make install   # create .venv and install dependencies
make dev       # start the WebSocket service AND the browser demo together
```

Wait for `[service] Model ready.` and `[service] Listening on ws://0.0.0.0:8765` in the terminal, then open `http://localhost:3000`. `make dev` runs both processes under one command and stops both together on Ctrl+C.

Turn detection needs its own Hugging Face token, with access granted on the gated `vogent/Vogent-Turn-80M` model:

```bash
HF_TOKEN=hf_xxx make dev
```

No token, or access not yet granted? The service still runs fine, it just falls back to the fixed `SILENCE_MS` timer for ending turns instead of the model.

Port 3000 already taken on your machine:

```bash
DEMO_PORT=3001 make dev
```

### Running the WebSocket service and the demo separately

```bash
make run       # just the WebSocket service, ws://localhost:8765
make example   # just the browser demo, http://localhost:3000 (or $DEMO_PORT)
```

Use these instead of `make dev` when you're deploying the WebSocket service on its own, e.g. Render, and don't need the demo page running alongside it.

### All make commands

| Command | Description |
|---|---|
| `make install` | Create `.venv` (Python 3.12) and install dependencies |
| `make dev` | Start the WebSocket service and the browser demo together |
| `make run` | Start just the transcription service |
| `make example` | Serve just the browser demo (`DEMO_PORT`, default `3000`) |
| `make test` | Run deployment/config tests |
| `make clean` | Remove `__pycache__` directories |

### Without make

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Then, in a second terminal, serve the browser demo:

```bash
python -m http.server 3000 -d examples/browser
```

## Configuration

All settings are controlled via environment variables.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8765` | WebSocket port |
| `MODEL_ID` | `iic/SenseVoiceSmall` | FunASR model. See models below. |
| `LANGUAGE` | `en` | Default language hint (`en`, `zh`, `ja`, `ko`, `auto`) |
| `SILENCE_MS` | `1500` | Silence duration (ms) that forces a final flush. Hard ceiling even with turn detection on |
| `RMS_THRESHOLD` | `0.01` | RMS level below which audio is treated as silence |
| `SESSION_TIMEOUT` | `600` | Seconds before an idle session is closed |
| `TURN_DETECTION_ENABLED` | `true` | Use [vogent-turn](https://github.com/vogent/vogent-turn) to end turns on model confidence instead of waiting the full `SILENCE_MS`. Requires an `HF_TOKEN` with access granted to the gated `vogent/Vogent-Turn-80M` repo; falls back to the plain `SILENCE_MS` timer if the model can't load |
| `TURN_MODEL_ID` | `vogent/Vogent-Turn-80M` | Hugging Face model id for turn detection |
| `TURN_MODEL_REVISION` | *(none)* | Pin a specific model revision |
| `TURN_COMPILE_MODEL` | `false` | Enable `torch.compile` for the turn model (slower startup, faster inference) |
| `TURN_PROB_THRESHOLD` | `0.5` | Minimum `prob_endpoint` to treat a pause as a finished turn |
| `TURN_DEBOUNCE_MS` | `300` | Silence (ms) before the first turn-detector check; also the minimum gap between checks |
| `TURN_AUDIO_WINDOW_S` | `8` | Seconds of trailing audio fed to the turn detector (model max is 8s) |

```bash
PORT=9000 MODEL_ID=iic/SenseVoiceSmall LANGUAGE=auto make run
```

Turn detection needs its own Hugging Face token:

```bash
HF_TOKEN=hf_xxx make run
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

Send once after `ready`. Optional, but lets you set a per-session language and seed turn-detection context.

```json
{ "type": "start", "language": "en", "prev_line": "What is your phone number" }
```

| Field | Type | Description |
|---|---|---|
| `language` | string | `en`, `zh`, `ja`, `ko`, `yue`, `auto` — overrides server default |
| `prev_line` | string | The other speaker's last line (e.g. the interviewer's question). Used by turn detection to judge whether the candidate's answer is finished. Ignored if `TURN_DETECTION_ENABLED` is off |

#### `context` — update turn-detection context mid-session

Send whenever the other speaker's line changes (e.g. the interviewer asks the next question), so turn detection keeps judging pauses against the current prompt instead of a stale one.

```json
{ "type": "context", "prev_line": "Tell me about a time you disagreed with a teammate" }
```

| Field | Type | Description |
|---|---|---|
| `prev_line` | string | Replaces the session's turn-detection context. Omit or send `""` to clear it |

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

Emitted when a pause looks like a finished turn (via `vogent-turn`, or after `SILENCE_MS` if turn detection is off or unavailable), or when you send `end`. Represents one complete utterance.

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
