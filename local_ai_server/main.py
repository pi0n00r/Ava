from __future__ import annotations

import asyncio

from server import main as run_server


if __name__ == "__main__":
    asyncio.run(run_server())

