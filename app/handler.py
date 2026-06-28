import asyncio
import json
import time

import numpy as np
import websockets

from app import config
from app.model import clean, executor, infer_offline, infer_streaming

_conn_counter = 0


async def handle(websocket):
    global _conn_counter
    _conn_counter += 1
    conn_id = _conn_counter
    print(f"[conn#{conn_id}] CONNECTED from {websocket.remote_address}", flush=True)

    loop           = asyncio.get_event_loop()
    chunk_count    = 0
    last_text      = ""
    last_speech_at = 0.0
    session_lang   = config.LANGUAGE

    cache: dict             = {}
    audio_buffer: list[np.ndarray] = []
    _infer_lock             = asyncio.Lock()

    await _send(websocket, {"type": "ready"})

    # ── Inference ─────────────────────────────────────────────────────────────

    async def _run_streaming(audio: np.ndarray, is_final: bool) -> str:
        async with _infer_lock:
            result = await loop.run_in_executor(
                executor, infer_streaming, audio, cache, is_final
            )
            return clean(result[0].get("text", "")) if result else ""

    async def _run_offline() -> str:
        if not audio_buffer:
            return ""
        combined = np.concatenate(audio_buffer)
        if len(combined) < config.MIN_SAMPLES:
            return ""
        async with _infer_lock:
            result = await loop.run_in_executor(
                executor, infer_offline, combined, session_lang
            )
            return clean(result[0].get("text", "")) if result else ""

    # ── Emitters ──────────────────────────────────────────────────────────────

    async def emit_partial(text: str):
        nonlocal last_text
        if not text or text == last_text:
            return
        last_text = text
        print(f"[conn#{conn_id}] → partial: {text!r}", flush=True)
        await _send(websocket, {"type": "partial", "text": text})

    async def flush_final(reason: str):
        nonlocal last_text, last_speech_at

        if config.IS_STREAMING:
            tail  = await _run_streaming(np.zeros(160, dtype=np.float32), is_final=True)
            final = (last_text + tail) if tail else last_text
        else:
            final = await _run_offline() or last_text

        last_text = ""
        last_speech_at = 0.0
        cache.clear()
        audio_buffer.clear()

        if final:
            print(f"[conn#{conn_id}] → final ({reason}): {final!r}", flush=True)
            await _send(websocket, {"type": "final", "text": final})

    # ── Silence watchdog ──────────────────────────────────────────────────────

    async def watchdog():
        while True:
            await asyncio.sleep(0.2)
            if last_speech_at and (time.monotonic() - last_speech_at) * 1000 >= config.SILENCE_MS:
                if last_text or audio_buffer:
                    try:
                        await flush_final("silence")
                    except Exception as e:
                        print(f"[conn#{conn_id}] watchdog error: {e!r}", flush=True)

    async def timeout_guard():
        await asyncio.sleep(config.SESSION_TIMEOUT)
        try:
            await _send(websocket, {"type": "timeout"})
            await websocket.close()
        except Exception:
            pass

    wd_task  = asyncio.create_task(watchdog())
    to_task  = asyncio.create_task(timeout_guard())

    # ── Message loop ──────────────────────────────────────────────────────────

    try:
        async for raw in websocket:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await _send(websocket, {"type": "error", "message": "invalid JSON"})
                continue

            t = msg.get("type")

            if t == "start":
                lang = msg.get("language", "").strip()
                if lang:
                    session_lang = lang
                print(f"[conn#{conn_id}] ← start  language={session_lang!r}", flush=True)

            elif t == "audio":
                hex_data = msg.get("data", "")
                if not hex_data:
                    continue
                chunk_count += 1
                try:
                    pcm = bytes.fromhex(hex_data)
                except ValueError:
                    await _send(websocket, {"type": "error", "message": "data must be hex-encoded int16 PCM"})
                    continue

                audio     = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                rms       = float(np.sqrt(np.mean(audio ** 2)))
                is_speech = rms > config.RMS_THRESHOLD

                if is_speech:
                    last_speech_at = time.monotonic()

                if config.IS_STREAMING:
                    await emit_partial(await _run_streaming(audio, is_final=False))
                else:
                    audio_buffer.append(audio)
                    buf_samples = sum(len(a) for a in audio_buffer)
                    if (len(audio_buffer) % config.PARTIAL_EVERY_N == 0
                            and is_speech
                            and buf_samples >= config.MIN_SAMPLES):
                        await emit_partial(await _run_offline())

            elif t == "end":
                print(f"[conn#{conn_id}] ← end", flush=True)
                await flush_final("client_end")

            else:
                await _send(websocket, {"type": "error", "message": f"unknown type: {t!r}"})

    except websockets.exceptions.ConnectionClosed as e:
        print(f"[conn#{conn_id}] closed: code={e.code}", flush=True)
    except Exception as e:
        print(f"[conn#{conn_id}] error: {e!r}", flush=True)
    finally:
        wd_task.cancel()
        to_task.cancel()
        print(f"[conn#{conn_id}] DISCONNECTED  chunks={chunk_count}", flush=True)


async def _send(ws, payload: dict):
    await ws.send(json.dumps(payload))
