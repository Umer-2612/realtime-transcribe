#!/usr/bin/env python3
"""
FunASR real-time transcription WebSocket service.

Two model modes, auto-detected from MODEL_ID:

  STREAMING  (paraformer-zh-streaming)
    Processes 256 ms chunks incrementally using a per-connection cache.
    Lowest latency; Chinese only.

  OFFLINE  (iic/SenseVoiceSmall, paraformer-en, …)
    Accumulates the full utterance in a buffer, re-transcribes periodically
    for partials, and runs a final pass on silence.
    Multilingual; ~1 s feedback lag.

Protocol:
  Client → Service:
    { "type": "start",  "language": "en" }
    { "type": "audio",  "data": "<hex int16 PCM @ 16 kHz>" }
    { "type": "end" }

  Service → Client:
    { "type": "ready" }
    { "type": "partial", "text": "..." }
    { "type": "final",   "text": "..." }
    { "type": "timeout" }
    { "type": "error",   "message": "..." }
"""

import asyncio
import json
import os
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import websockets
from funasr import AutoModel

# ── Config (all overridable via env vars) ─────────────────────────────────────

PORT          = int(os.getenv("PORT",          "8765"))
MODEL_ID      =     os.getenv("MODEL_ID",      "iic/SenseVoiceSmall")
LANGUAGE      =     os.getenv("LANGUAGE",      "en")
SILENCE_MS    = int(os.getenv("SILENCE_MS",    "1500"))
RMS_THRESHOLD = float(os.getenv("RMS_THRESHOLD", "0.01"))
SESSION_TIMEOUT = int(os.getenv("SESSION_TIMEOUT", "600"))

# paraformer-streaming only
CHUNK_SIZE = [5, 10, 5]

# Offline: emit a partial every N chunks (4 × 256 ms ≈ 1 s)
PARTIAL_EVERY_N = 4
# Avoid hallucinations on silence — require at least 1 s of audio
MIN_SAMPLES = 16000

# ── Model detection ───────────────────────────────────────────────────────────

_IS_STREAMING = "streaming" in MODEL_ID.lower() or "online" in MODEL_ID.lower()
print(
    f"[service] Model: {MODEL_ID}  "
    f"mode={'streaming' if _IS_STREAMING else 'offline'}  "
    f"language={LANGUAGE!r}",
    flush=True,
)

_TOKEN_RE = re.compile(r"<\|[^|>]+\|>")

def clean(text: str) -> str:
    return _TOKEN_RE.sub("", text).strip()

# ── Model loading ─────────────────────────────────────────────────────────────

print("[service] Loading model …", flush=True)
_model      = AutoModel(model=MODEL_ID, disable_update=True, disable_pbar=True)
_executor   = ThreadPoolExecutor(max_workers=4)
_model_lock = threading.Lock()
print("[service] Model ready.", flush=True)

# ── Thread-pool workers ───────────────────────────────────────────────────────

def _infer_streaming(audio: np.ndarray, cache: dict, is_final: bool) -> list:
    exc = result = None
    try:
        with _model_lock:
            result = _model.generate(
                audio,
                cache=cache,
                is_final=is_final,
                chunk_size=CHUNK_SIZE,
                encoder_chunk_look_back=4,
                decoder_chunk_look_back=1,
            )
    except Exception as e:
        exc = e
    if exc:
        raise exc
    return result or []


def _infer_offline(audio: np.ndarray, language: str) -> list:
    exc = result = None
    try:
        with _model_lock:
            result = _model.generate(audio, language=language, use_itn=True)
    except Exception as e:
        exc = e
    if exc:
        raise exc
    return result or []

# ── Per-connection handler ────────────────────────────────────────────────────

_conn_counter = 0


async def handle_connection(websocket):
    global _conn_counter
    _conn_counter += 1
    conn_id = _conn_counter
    addr    = websocket.remote_address

    print(f"[conn#{conn_id}] CONNECTED from {addr}", flush=True)

    loop           = asyncio.get_event_loop()
    chunk_count    = 0
    last_text      = ""
    last_speech_at = 0.0
    session_lang   = LANGUAGE

    cache: dict = {}
    audio_buffer: list[np.ndarray] = []
    _infer_lock = asyncio.Lock()

    await websocket.send(json.dumps({"type": "ready"}))

    # ── Inference helpers ─────────────────────────────────────────────────────

    async def infer_streaming(audio: np.ndarray, is_final: bool) -> str:
        async with _infer_lock:
            result = await loop.run_in_executor(
                _executor, _infer_streaming, audio, cache, is_final
            )
            return clean(result[0].get("text", "")) if result else ""

    async def infer_offline_buffer() -> str:
        if not audio_buffer:
            return ""
        combined = np.concatenate(audio_buffer)
        if len(combined) < MIN_SAMPLES:
            return ""
        async with _infer_lock:
            result = await loop.run_in_executor(
                _executor, _infer_offline, combined, session_lang
            )
            return clean(result[0].get("text", "")) if result else ""

    # ── Emit helpers ──────────────────────────────────────────────────────────

    async def emit_partial(text: str):
        nonlocal last_text
        if not text or text == last_text:
            return
        last_text = text
        print(f"[conn#{conn_id}] → partial: {text!r}", flush=True)
        await websocket.send(json.dumps({"type": "partial", "text": text}))

    async def flush_final(reason: str):
        nonlocal last_text, last_speech_at

        if _IS_STREAMING:
            tail  = await infer_streaming(np.zeros(160, dtype=np.float32), is_final=True)
            final = (last_text + tail) if tail else last_text
        else:
            final = await infer_offline_buffer()
            if not final:
                final = last_text

        last_text      = ""
        last_speech_at = 0.0
        cache.clear()
        audio_buffer.clear()

        if final:
            print(f"[conn#{conn_id}] → final ({reason}): {final!r}", flush=True)
            await websocket.send(json.dumps({"type": "final", "text": final}))

    # ── Silence watchdog ──────────────────────────────────────────────────────

    async def silence_watchdog():
        while True:
            await asyncio.sleep(0.2)
            if last_speech_at > 0:
                silent_ms   = (time.monotonic() - last_speech_at) * 1000
                has_content = bool(last_text or audio_buffer)
                if silent_ms >= SILENCE_MS and has_content:
                    try:
                        await flush_final(reason="silence")
                    except Exception as e:
                        print(f"[conn#{conn_id}] watchdog error: {e!r}", flush=True)

    watchdog = asyncio.create_task(silence_watchdog())

    # ── Session timeout ───────────────────────────────────────────────────────

    async def auto_timeout():
        await asyncio.sleep(SESSION_TIMEOUT)
        try:
            await websocket.send(json.dumps({"type": "timeout"}))
            await websocket.close()
        except Exception:
            pass

    timeout_task = asyncio.create_task(auto_timeout())

    # ── Message loop ─────────────────────────────────────────────────────────

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type": "error", "message": "invalid JSON"}))
                continue

            msg_type = msg.get("type")

            if msg_type == "start":
                # Allow client to override language per-session
                client_lang = msg.get("language", "").strip()
                if client_lang:
                    session_lang = client_lang
                print(f"[conn#{conn_id}] ← start  language={session_lang!r}", flush=True)

            elif msg_type == "audio":
                hex_data = msg.get("data", "")
                if not hex_data:
                    continue

                chunk_count += 1

                try:
                    pcm_bytes = bytes.fromhex(hex_data)
                except ValueError:
                    await websocket.send(json.dumps({"type": "error", "message": "audio data must be hex-encoded int16 PCM"}))
                    continue

                audio     = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                rms       = float(np.sqrt(np.mean(audio ** 2)))
                is_speech = rms > RMS_THRESHOLD

                if is_speech:
                    last_speech_at = time.monotonic()

                if _IS_STREAMING:
                    text = await infer_streaming(audio, is_final=False)
                    await emit_partial(text)
                else:
                    audio_buffer.append(audio)
                    buffer_samples = sum(len(a) for a in audio_buffer)
                    at_boundary    = len(audio_buffer) % PARTIAL_EVERY_N == 0
                    if at_boundary and is_speech and buffer_samples >= MIN_SAMPLES:
                        text = await infer_offline_buffer()
                        await emit_partial(text)

            elif msg_type == "end":
                print(f"[conn#{conn_id}] ← end", flush=True)
                await flush_final(reason="client_end")

            else:
                await websocket.send(json.dumps({"type": "error", "message": f"unknown message type: {msg_type!r}"}))

    except websockets.exceptions.ConnectionClosed as e:
        print(f"[conn#{conn_id}] closed: code={e.code}", flush=True)
    except Exception as e:
        print(f"[conn#{conn_id}] error: {e!r}", flush=True)
    finally:
        watchdog.cancel()
        timeout_task.cancel()
        print(f"[conn#{conn_id}] DISCONNECTED  chunks={chunk_count}", flush=True)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    print(f"[service] Listening on ws://0.0.0.0:{PORT}", flush=True)
    async with websockets.serve(handle_connection, "0.0.0.0", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[service] Stopped.", flush=True)
        sys.exit(0)
