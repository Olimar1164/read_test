# Lightweight IO helpers used by endpoints and client to prefer aiofiles when available
import logging
import os

logger = logging.getLogger("app.api.utils")

# detect aiofiles at import time
try:
    import aiofiles

    HAVE_AIOFILES = True
except Exception:
    aiofiles = None
    HAVE_AIOFILES = False


async def write_bytes(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path) or "/tmp", exist_ok=True)
    if HAVE_AIOFILES:
        try:
            async with aiofiles.open(path, "wb") as f:
                await f.write(data)
            return
        except Exception:
            logger.debug("aiofiles write failed, falling back to sync write")
    # fallback
    with open(path, "wb") as f:
        f.write(data)


async def read_bytes(path: str) -> bytes:
    if HAVE_AIOFILES:
        try:
            async with aiofiles.open(path, "rb") as f:
                return await f.read()
        except Exception:
            logger.debug("aiofiles read failed, falling back to sync read")
    with open(path, "rb") as f:
        return f.read()
