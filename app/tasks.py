import os
import base64
import logging
from rq import Queue
from redis import Redis

from app.services.ai.saia_console_client import SAIAConsoleClient

logger = logging.getLogger('app.tasks')

# Simple Redis connection (Heroku will provide REDIS_URL)
redis_url = os.environ.get('REDIS_URL')
if redis_url:
    redis_conn = Redis.from_url(redis_url)
else:
    redis_conn = Redis()

q = Queue(connection=redis_conn)


def process_upload(job_payload):
    """Worker function: receives a dict with keys: file_b64, filename, prompt, folder, alias, assistant
    Writes temp file, uses SAIAConsoleClient to send and returns result dict.
    """
    try:
        tmp_dir = '/tmp/saia_demo'
        os.makedirs(tmp_dir, exist_ok=True)
        filename = job_payload.get('filename')
        data = job_payload.get('file_b64')
        path = os.path.join(tmp_dir, filename)
        with open(path, 'wb') as f:
            f.write(base64.b64decode(data))

        client = SAIAConsoleClient(
            os.environ.get('GEAI_API_TOKEN'),
            os.environ.get('ORGANIZATION_ID'),
            os.environ.get('PROJECT_ID'),
            os.environ.get('ASSISTANT_ID')
        )

        res = client.send_pdf_and_query(
            path,
            job_payload.get('prompt', ''),
            folder=job_payload.get('folder', 'test1'),
            alias=job_payload.get('alias'),
            assistant_id=job_payload.get('assistant')
        )

        # cleanup
        try:
            os.remove(path)
        except Exception:
            logger.debug('No se pudo borrar tmp file en worker')

        return res
    except Exception as e:
        logger.exception('Error procesando job')
        return {'error': 'worker_error', 'detail': str(e)}
