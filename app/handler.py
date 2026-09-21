import asyncio
import json
import time

import numpy as np
import websockets

from app import config, turn_detector
from app.model import clean, executor, infer_offline, infer_streaming

_conn_counter = 0


async def handle(websocket):
    global _conn_counter
    _conn_counter += 1
    conn_id = _conn_counter
    print(f"[conn#{conn_id}] CONNECTED from {websocket.remote_address}", flush=True)

    loop             = asyncio.get_event_loop()
    chunk_count      = 0
    last_text        = ""
    last_speech_at   = 0.0
    last_turn_check  = 0.0
    session_lang     = config.LANGUAGE
    prev_line        = ""

    cache: dict                     = {}
    audio_buffer: list[np.ndarray]  = []
    turn_window: list[np.ndarray]   = []
    turn_window_samples             = 0
    _infer_lock                     = asyncio.Lock()
    _turn_lock                      = asyncio.Lock()

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
        nonlocal last_text, last_speech_at, last_turn_check, turn_window_samples

        if config.IS_STREAMING:
            tail  = await _run_streaming(np.zeros(160, dtype=np.float32), is_final=True)
            final = (last_text + tail) if tail else last_text
        else:
            final = await _run_offline() or last_text

        last_text = ""
        last_speech_at = 0.0
        last_turn_check = 0.0
        cache.clear()
        audio_buffer.clear()
        turn_window.clear()
        turn_window_samples = 0

        if final:
            print(f"[conn#{conn_id}] → final ({reason}): {final!r}", flush=True)
            await _send(websocket, {"type": "final", "text": final})

    # ── Silence / turn watchdog ──────────────────────────────────────────────

    async def _check_turn_complete(silence_ms: float) -> bool:
        """Ask vogent-turn if the pause looks like a finished turn.

        Debounced separately from the poll interval so a torch.compile-free
        CPU forward pass (tens of ms) doesn't run on every 100ms tick.
        """
        nonlocal last_turn_check
        if (time.monotonic() - last_turn_check) * 1000 < config.TURN_DEBOUNCE_MS:
            return False
        last_turn_check = time.monotonic()

        window_audio = np.concatenate(turn_window) if turn_window else np.zeros(1, dtype=np.float32)
        try:
            async with _turn_lock:
                result = await loop.run_in_executor(
                    executor, turn_detector.predict, window_audio, prev_line, last_text
                )
        except Exception as e:
            print(f"[conn#{conn_id}] turn-detector inference error: {e!r}", flush=True)
            return False

        print(
            f"[conn#{conn_id}] turn check: p_end={result['prob_endpoint']:.2f} "
            f"silence={silence_ms:.0f}ms curr={last_text!r}",
            flush=True,
        )
        return result["prob_endpoint"] >= config.TURN_PROB_THRESHOLD

    async def watchdog():
        while True:
            await asyncio.sleep(0.1)
            if not last_speech_at or not (last_text or audio_buffer):
                continue

            silence_ms = (time.monotonic() - last_speech_at) * 1000

            # SILENCE_MS is always the hard ceiling — reached if the model
            # keeps saying "continue", or if turn detection is unavailable.
            should_flush = silence_ms >= config.SILENCE_MS
            reason = "silence_timeout"

            if not should_flush and silence_ms >= config.TURN_DEBOUNCE_MS and turn_detector.available():
                if await _check_turn_complete(silence_ms):
                    should_flush = True
                    reason = "turn_complete"

            if should_flush:
                try:
                    await flush_final(reason)
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
                prev_line = msg.get("prev_line", prev_line)
                print(
                    f"[conn#{conn_id}] ← start  language={session_lang!r} prev_line={prev_line!r}",
                    flush=True,
                )

            elif t == "context":
                # Interview platform calls this whenever the interviewer asks
                # a new question, so turn detection has the right prompt for
                # judging whether the candidate's answer is finished.
                prev_line = msg.get("prev_line", "")
                print(f"[conn#{conn_id}] ← context  prev_line={prev_line!r}", flush=True)

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

                if turn_detector.available():
                    turn_window.append(audio)
                    turn_window_samples += len(audio)
                    max_window_samples = int(config.TURN_AUDIO_WINDOW_S * config.SAMPLE_RATE)
                    while turn_window_samples > max_window_samples and len(turn_window) > 1:
                        turn_window_samples -= len(turn_window.pop(0))

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
