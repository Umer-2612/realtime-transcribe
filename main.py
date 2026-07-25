#!/usr/bin/env python3
import asyncio
import sys

import websockets
from websockets.datastructures import Headers
from websockets.http11 import Response

from app.config import PORT
from app.handler import handle


def is_websocket_upgrade(request):
    connection = request.headers.get("Connection", "")
    upgrade = request.headers.get("Upgrade", "")
    return "upgrade" in connection.lower() or upgrade.lower() == "websocket"


def health_check(_connection, request):
    if is_websocket_upgrade(request):
        return None

    if request.path not in {"/", "/health"}:
        return None

    body = b'{"status":"ok","service":"realtime-transcribe"}\n'
    headers = Headers()
    headers["Content-Type"] = "application/json"
    headers["Content-Length"] = str(len(body))
    return Response(200, "OK", headers, body)


async def main():
    print(f"[service] Listening on ws://0.0.0.0:{PORT}", flush=True)
    async with websockets.serve(handle, "0.0.0.0", PORT, process_request=health_check):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[service] Stopped.", flush=True)
        sys.exit(0)
