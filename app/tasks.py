import os
import base64
import logging
import asyncio
from redis import Redis
from rq import Queue

from app.services.ai.saia_console_client import SAIAConsoleClient

logger = logging.getLogger("app.tasks")

# Redis connection will be created by the worker using REDIS_URL
redis_url = os.environ.get("REDIS_URL")
if redis_url:
    redis_conn = Redis.from_url(redis_url)
else:
    redis_conn = None

q = Queue(connection=redis_conn) if redis_conn else None


def process_upload(job_payload):
    """Worker function executed inside RQ worker.
    Job payload contains: file_b64, filename, prompt, folder, alias, assistant
    """
    try:
        tmp_dir = "/tmp/saia_demo"
        os.makedirs(tmp_dir, exist_ok=True)
        filename = job_payload.get("filename")
        data = job_payload.get("file_b64")
        path = os.path.join(tmp_dir, filename)
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

        client = SAIAConsoleClient(
            os.environ.get("GEAI_API_TOKEN"),
            os.environ.get("ORGANIZATION_ID"),
            os.environ.get("PROJECT_ID"),
            os.environ.get("ASSISTANT_ID"),
        )

        # send_pdf_and_query is async; run it in event loop
        async def _run():
            return await client.send_pdf_and_query(
                path,
                job_payload.get("prompt", ""),
                folder=job_payload.get("folder", "test1"),
                alias=job_payload.get("alias"),
                assistant_id=job_payload.get("assistant"),
            )

        res = asyncio.run(_run())

        # cleanup
        try:
            os.remove(path)
        except Exception:
            logger.debug("No se pudo borrar tmp file en worker")

        return res
    except Exception as e:
        logger.exception("Error procesando job")
        return {"error": "worker_error", "detail": str(e)}
