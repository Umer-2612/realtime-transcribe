#!/usr/bin/env python3
"""
FunASR real-time transcription WebSocket server.

Two model modes, auto-detected from MODEL_ID:

  STREAMING  (paraformer-zh-streaming)
    Processes 256 ms chunks incrementally using a per-connection cache.
    Lowest latency; Chinese only.

  OFFLINE  (iic/SenseVoiceSmall, paraformer-en, …)
    Accumulates the full utterance in a buffer, re-transcribes periodically
    for partials, and runs a final pass on silence.
    Multilingual; ~1 s feedback lag.

Protocol  (identical to aural-oss voice-relay mic_test mode):
  Browser → Server:
    { "type": "mic_test", "language": "en" }
    { "type": "audio",    "data": "<hex int16 PCM @ 16 kHz>" }
    { "type": "end" }

  Server → Browser:
    { "type": "ready" }
    { "type": "asr",      "data": { "results": [{ "text": "...", "definite": false }] } }
    { "type": "asr_ended", "text": "..." }
    { "type": "timeout" }
"""

import asyncio
import json
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import websockets
from funasr import AutoModel

# ── Config ────────────────────────────────────────────────────────────────────

PORT = 8765

# "paraformer-zh-streaming" → streaming Chinese
# "iic/SenseVoiceSmall"     → offline multilingual (en/zh/ja/ko/yue)
MODEL_ID = "iic/SenseVoiceSmall"

# Language passed to SenseVoiceSmall.
# "en" forces English — avoids the model guessing Chinese on short/silent buffers.
# Change to "auto" if you want multilingual auto-detection.
LANGUAGE = "en"

# paraformer-streaming only — ignored for offline models
CHUNK_SIZE = [5, 10, 5]

# Silence detection
SILENCE_MS    = 1500    # ms of quiet before auto-flush
RMS_THRESHOLD = 0.01    # RMS below this → silence

# Offline mode: emit a partial every N chunks (4 × 256 ms ≈ 1 s refresh)
PARTIAL_EVERY_N = 4
# Minimum buffer before attempting a transcription (avoid hallucinations on silence)
MIN_SAMPLES_FOR_TRANSCRIPTION = 16000   # 1 s at 16 kHz

SESSION_TIMEOUT = 10 * 60   # seconds

# ── Model detection ───────────────────────────────────────────────────────────

_IS_STREAMING = "streaming" in MODEL_ID.lower() or "online" in MODEL_ID.lower()
print(
    f"[server] Model: {MODEL_ID}  "
    f"mode={'streaming' if _IS_STREAMING else 'offline'}  "
    f"language={LANGUAGE!r}",
    flush=True,
)

# SenseVoiceSmall wraps output in metadata tokens, e.g.:
#   <|en|><|EMO_UNKNOWN|><|Speech|><|woitn|>hello there
# Strip them all before sending to the browser.
_TOKEN_RE = re.compile(r"<\|[^|>]+\|>")


def clean(text: str) -> str:
    return _TOKEN_RE.sub("", text).strip()


# ── Model loading ─────────────────────────────────────────────────────────────

print(f"[server] Loading model …", flush=True)
_model      = AutoModel(model=MODEL_ID, disable_update=True, disable_pbar=True)
_executor   = ThreadPoolExecutor(max_workers=4)
_model_lock = threading.Lock()
print("[server] Model ready.", flush=True)


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
    print(
        f"[infer-streaming] is_final={is_final}  samples={len(audio)}"
        f"  text={result[0].get('text', '') if result else ''}  exc={exc!r}",
        flush=True,
    )
    if exc:
        raise exc
    return result or []


def _infer_offline(audio: np.ndarray, language: str) -> list:
    exc = result = None
    try:
        with _model_lock:
            result = _model.generate(
                audio,
                language=language,
                use_itn=True,
            )
    except Exception as e:
        exc = e
    print(
        f"[infer-offline]  samples={len(audio)}  language={language!r}"
        f"  text={result[0].get('text', '') if result else ''}  exc={exc!r}",
        flush=True,
    )
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

    # streaming only
    cache: dict = {}

    # offline only — list of float32 arrays, reset after each flush
    audio_buffer: list[np.ndarray] = []

    # Serialises inference: prevents the silence watchdog from submitting
    # is_final=True while a chunk inference is still running in the thread pool.
    _infer_lock = asyncio.Lock()

    await websocket.send(json.dumps({"type": "ready"}))
    print(f"[conn#{conn_id}] → ready", flush=True)

    # ── Inference helpers ─────────────────────────────────────────────────────

    async def infer_streaming(audio: np.ndarray, is_final: bool) -> str:
        async with _infer_lock:
            result = await loop.run_in_executor(
                _executor, _infer_streaming, audio, cache, is_final
            )
            return clean(result[0].get("text", "")) if result else ""

    async def infer_offline_buffer() -> str:
        """Transcribe the full accumulated buffer. Returns '' if buffer is too short."""
        if not audio_buffer:
            return ""
        combined = np.concatenate(audio_buffer)
        if len(combined) < MIN_SAMPLES_FOR_TRANSCRIPTION:
            print(
                f"[conn#{conn_id}] buffer too short ({len(combined)} samples < "
                f"{MIN_SAMPLES_FOR_TRANSCRIPTION}) — skipping",
                flush=True,
            )
            return ""
        async with _infer_lock:
            result = await loop.run_in_executor(
                _executor, _infer_offline, combined, LANGUAGE
            )
            return clean(result[0].get("text", "")) if result else ""

    # ── Emit helpers ──────────────────────────────────────────────────────────

    async def emit_partial(text: str):
        nonlocal last_text
        if not text or text == last_text:
            return
        last_text = text
        print(f"[conn#{conn_id}] → asr partial: {text!r}", flush=True)
        await websocket.send(json.dumps({
            "type": "asr",
            "data": {"results": [{"text": text, "definite": False}]},
        }))

    async def flush_final(reason: str):
        nonlocal last_text, last_speech_at

        print(
            f"[conn#{conn_id}] flush_final  reason={reason}"
            f"  last_text={last_text!r}  buffer_chunks={len(audio_buffer)}",
            flush=True,
        )

        if _IS_STREAMING:
            # Drain the look-ahead: is_final=True returns the remaining tail.
            # Tail is ADDITIONAL text after last_text — concatenate, not replace.
            tail  = await infer_streaming(np.zeros(160, dtype=np.float32), is_final=True)
            final = (last_text + tail) if tail else last_text
        else:
            # Re-transcribe the whole buffer for the most accurate final result.
            final = await infer_offline_buffer()
            if not final:
                final = last_text   # fall back to last partial if buffer was too short

        print(f"[conn#{conn_id}] flush_final  final={final!r}", flush=True)

        # Reset all mutable state BEFORE the next await — no context switch
        # between these lines so the watchdog cannot re-fire immediately.
        last_text      = ""
        last_speech_at = 0.0
        cache.clear()
        audio_buffer.clear()

        if final:
            print(f"[conn#{conn_id}] → asr_ended: {final!r}", flush=True)
            await websocket.send(json.dumps({"type": "asr_ended", "text": final}))
        else:
            print(f"[conn#{conn_id}] flush_final  nothing to emit", flush=True)

    # ── Silence watchdog ──────────────────────────────────────────────────────

    async def silence_watchdog():
        while True:
            await asyncio.sleep(0.2)
            if last_speech_at > 0:
                silent_ms = (time.monotonic() - last_speech_at) * 1000
                has_content = bool(last_text or audio_buffer)
                if silent_ms >= SILENCE_MS and has_content:
                    print(
                        f"[conn#{conn_id}] silence {silent_ms:.0f} ms — auto-flush",
                        flush=True,
                    )
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
                continue

            msg_type = msg.get("type")

            if msg_type == "mic_test":
                # honour the language the client sends if we're in auto mode
                client_lang = msg.get("language", "")
                print(
                    f"[conn#{conn_id}] ← mic_test  client_language={client_lang!r}"
                    f"  using_language={LANGUAGE!r}",
                    flush=True,
                )

            elif msg_type == "audio":
                hex_data = msg.get("data", "")
                if not hex_data:
                    continue

                chunk_count += 1

                try:
                    pcm_bytes = bytes.fromhex(hex_data)
                except ValueError as e:
                    print(f"[conn#{conn_id}] bad hex: {e!r}", flush=True)
                    continue

                audio = (
                    np.frombuffer(pcm_bytes, dtype=np.int16)
                    .astype(np.float32) / 32768.0
                )
                rms       = float(np.sqrt(np.mean(audio ** 2)))
                is_speech = rms > RMS_THRESHOLD

                print(
                    f"[conn#{conn_id}] chunk#{chunk_count}"
                    f"  samples={len(audio)}  rms={rms:.4f}  speech={is_speech}",
                    flush=True,
                )

                if is_speech:
                    last_speech_at = time.monotonic()

                if _IS_STREAMING:
                    text = await infer_streaming(audio, is_final=False)
                    await emit_partial(text)

                else:
                    # Accumulate all chunks (including short silences within speech).
                    audio_buffer.append(audio)

                    # Only run partial transcription when:
                    #   1. We're at a multiple-of-N chunk boundary (rate limit)
                    #   2. The current chunk had speech (not just silence)
                    #   3. Buffer is long enough to avoid hallucinations
                    buffer_samples = sum(len(a) for a in audio_buffer)
                    at_boundary    = len(audio_buffer) % PARTIAL_EVERY_N == 0
                    long_enough    = buffer_samples >= MIN_SAMPLES_FOR_TRANSCRIPTION

                    if at_boundary and is_speech and long_enough:
                        text = await infer_offline_buffer()
                        await emit_partial(text)

            elif msg_type == "end":
                print(f"[conn#{conn_id}] ← end  (explicit flush)", flush=True)
                await flush_final(reason="client_end")

            else:
                print(f"[conn#{conn_id}] ← unknown type {msg_type!r}", flush=True)

    except websockets.exceptions.ConnectionClosed as e:
        print(f"[conn#{conn_id}] closed: code={e.code} reason={e.reason!r}", flush=True)
    except Exception as e:
        print(f"[conn#{conn_id}] error: {e!r}", flush=True)
    finally:
        watchdog.cancel()
        timeout_task.cancel()
        print(
            f"[conn#{conn_id}] DISCONNECTED  total_chunks={chunk_count}",
            flush=True,
        )


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    print(f"[server] Listening on ws://0.0.0.0:{PORT}", flush=True)
    async with websockets.serve(handle_connection, "0.0.0.0", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[server] Stopped.", flush=True)
        sys.exit(0)
