#!/usr/bin/env python3
import asyncio
import sys

import websockets

from app.config import PORT
from app.handler import handle


async def main():
    print(f"[service] Listening on ws://0.0.0.0:{PORT}", flush=True)
    async with websockets.serve(handle, "0.0.0.0", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[service] Stopped.", flush=True)
        sys.exit(0)
