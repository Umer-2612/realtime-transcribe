# realtime-transcribe

A self-hosted WebSocket service for live speech-to-text. You stream raw 16 kHz audio in, and it streams partial and final transcripts back. Transcription runs on [FunASR](https://github.com/modelscope/FunASR).

It also decides when a speaker has finished talking. A fixed silence timer always waits the full timeout (1.5 s here) before committing a line. This service asks a small turn-detection model, [vogent-turn](https://github.com/vogent/vogent-turn), after 300 ms of silence, so a finished answer can be committed well before the timer runs out. The 1.5 s timer stays as a hard limit.

```
mic ──▶ WebSocket ──▶ FunASR ──▶ partial transcripts (~every 1 s)
                        │
             silence ≥ 300 ms?
                        ▼
                  vogent-turn ──▶ turn finished? ──▶ final transcript
            (last 8 s of audio + current text
             + the other speaker's last line)
```

## What's in the repo

| Path | What it is |
|---|---|
| `main.py`, `app/` | The WebSocket service: transcription, silence detection and turn detection |
| `examples/browser/` | A browser demo that records your mic and streams it to the service |
| `Dockerfile`, `render.yaml` | Container build and a Render blueprint for hosting |
| `.github/workflows/render-keepalive.yml` | Pings the hosted service every 10 minutes |
| `tests/` | Tests for the deploy config (the transcription logic has no tests yet) |

## Quick start

You need Python 3.12.

```bash
make install   # create .venv and install dependencies
make dev       # start the WebSocket service AND the browser demo together
```

When the terminal shows `[service] Model ready.` and `[service] Listening on ws://0.0.0.0:8765`, open `http://localhost:3000` and start talking. Ctrl+C stops both processes.

Turn detection uses the gated `vogent/Vogent-Turn-80M` model on Hugging Face. Request access to it, then pass your token:

```bash
HF_TOKEN=hf_xxx make dev
```

Without a token, or before access is granted, the service still runs. It logs that turn detection is unavailable and ends every turn on the `SILENCE_MS` timer.

If port 3000 is taken:

```bash
DEMO_PORT=3001 make dev
```

### Other commands

| Command | What it does |
|---|---|
| `make run` | Start only the WebSocket service on `ws://localhost:8765` |
| `make example` | Serve only the browser demo on `http://localhost:3000` (or `$DEMO_PORT`) |
| `make test` | Run the deploy config tests |
| `make clean` | Remove `__pycache__` directories |

Without make:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

Then serve the demo from a second terminal:

```bash
python -m http.server 3000 -d examples/browser
```

## How it works

1. The client sends 256 ms chunks of 16 kHz mono Int16 PCM, hex encoded, over the WebSocket.
2. Each chunk's RMS level is compared with `RMS_THRESHOLD` to tell speech from silence.
3. With the default offline model, the service re-transcribes the buffered audio every 4 chunks (about 1 s) while you speak and sends a `partial`. Streaming models can send a partial on every chunk.
4. A watchdog runs every 100 ms. Once silence reaches `TURN_DEBOUNCE_MS` (300 ms), it passes the last `TURN_AUDIO_WINDOW_S` seconds of audio, the current transcript and the other speaker's last line (`prev_line`) to vogent-turn. It re-checks at most every 300 ms.
5. If the model's end-of-turn probability reaches `TURN_PROB_THRESHOLD` (0.5), the service sends a `final` and clears the buffer. If it never does, `SILENCE_MS` (1500 ms) forces the `final`.

`prev_line` gives the model the other side of the conversation. In an interview, a pause after "My phone number is" should not end the answer, and knowing the question was "What is your phone number" helps the model see that.

## Configuration

Everything is set through environment variables. `.env.example` lists the basic ones.

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8765` | WebSocket and health check port |
| `MODEL_ID` | `iic/SenseVoiceSmall` | FunASR model (see below) |
| `LANGUAGE` | `en` | Default language: `en`, `zh`, `ja`, `ko`, `yue` or `auto` |
| `SILENCE_MS` | `1500` | Silence in ms that always forces a `final`, with or without turn detection |
| `RMS_THRESHOLD` | `0.01` | RMS level below which audio counts as silence |
| `SESSION_TIMEOUT` | `600` | Maximum session length in seconds, counted from connect. The service then sends `timeout` and closes the connection |
| `HF_TOKEN` | *(none)* | Hugging Face token with access to `vogent/Vogent-Turn-80M` |
| `TURN_DETECTION_ENABLED` | `true` | Set to `false` to use only the `SILENCE_MS` timer |
| `TURN_MODEL_ID` | `vogent/Vogent-Turn-80M` | Turn-detection model on Hugging Face |
| `TURN_MODEL_REVISION` | *(none)* | Pin a specific model revision |
| `TURN_COMPILE_MODEL` | `false` | Run `torch.compile` on the turn model: slower startup, faster checks |
| `TURN_PROB_THRESHOLD` | `0.5` | End-of-turn probability needed to send a `final` |
| `TURN_DEBOUNCE_MS` | `300` | Silence before the first turn check, and the minimum gap between checks |
| `TURN_AUDIO_WINDOW_S` | `8` | Seconds of recent audio sent to the turn model (8 s is its maximum) |

```bash
PORT=9000 LANGUAGE=auto HF_TOKEN=hf_xxx make run
```

### Models

| `MODEL_ID` | Mode | Languages | Latency |
|---|---|---|---|
| `iic/SenseVoiceSmall` (default) | Offline | en, zh, ja, ko, yue | ~1 s |
| `paraformer-zh-streaming` | Streaming | zh only | ~250 ms |

The first run downloads the model from ModelScope.

## WebSocket protocol

All messages are JSON with a `type` field.

### Session flow

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

Wait for `ready` before sending audio.

### Client to service

| Type | When | Fields |
|---|---|---|
| `start` | Once after `ready` (optional) | `language` overrides the server default. `prev_line` is the other speaker's last line, used by turn detection |
| `context` | Whenever the other speaker says something new | `prev_line` replaces the current context. Send `""` to clear it |
| `audio` | Continuously while recording | `data`: hex-encoded audio chunk (format below) |
| `end` | When recording stops | Transcribes any buffered audio and sends the last `final` |

```json
{ "type": "start", "language": "en", "prev_line": "What is your phone number" }
{ "type": "context", "prev_line": "Tell me about a time you disagreed with a teammate" }
{ "type": "audio", "data": "1a2b3c..." }
{ "type": "end" }
```

Audio chunks are 16,000 Hz, mono, signed 16-bit little-endian PCM. Send 4096 samples (256 ms) per chunk: 8192 bytes, or 16,384 hex characters. The partial timing assumes this chunk size.

### Service to client

| Type | Meaning |
|---|---|
| `ready` | Connection is open. Start sending audio |
| `partial` | Transcript so far for the current turn. It can change on the next update |
| `final` | One finished turn, sent after turn detection, the `SILENCE_MS` limit, or `end` |
| `timeout` | `SESSION_TIMEOUT` was reached. The connection closes next |
| `error` | The message was not valid JSON, had an unknown `type`, or carried audio that isn't hex |

```json
{ "type": "partial", "text": "hello how are" }
{ "type": "final", "text": "Hello, how are you?" }
{ "type": "error", "message": "data must be hex-encoded int16 PCM" }
```

## Deploy to Render

`render.yaml` defines a Docker web service on the `standard` plan with `/health` as its health check. FunASR and PyTorch are too heavy for Render's free instances.

1. In Render, choose **New > Blueprint** and connect this repository (`main` branch).
2. Confirm the `realtime-transcribe` service.
3. Add `HF_TOKEN` under the service's environment variables. The blueprint doesn't set it, so without it the service runs with the silence timer only.

For a manual setup, pick the Docker runtime with `./Dockerfile`, context `.`, and health check path `/health`. Leave the build and start commands empty; the Dockerfile runs `python main.py`.

Check the deployment:

```bash
curl https://<your-render-service>.onrender.com/health
# {"status":"ok","service":"realtime-transcribe"}
```

Clients connect over `wss://<your-render-service>.onrender.com`.

To turn on keepalive pings, add a repository secret `RENDER_SERVICE_URL=https://<your-render-service>.onrender.com`. The keepalive workflow then pings `/health` every 10 minutes. GitHub only runs scheduled workflows from the default branch, and you can also start it from the Actions tab.

## Requirements

- Python 3.12
- PyTorch 2.0 or later, on CPU or CUDA
- Everything else is in `requirements.txt`
