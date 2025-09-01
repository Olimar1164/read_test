from fastapi import APIRouter, UploadFile, File, Form
import base64
from redis import Redis
from rq import Queue
from rq.job import Job
import os
import logging
from dotenv import load_dotenv
from app.services.ai.saia_console_client import SAIAConsoleClient
import httpx

# No DB persistence for demo: uploads go directly to SAIA files API
load_dotenv()

logger = logging.getLogger("app.api.endpoints")

router = APIRouter()


@router.get('/status/{job_id}')
def job_status(job_id: str):
    redis_url = os.environ.get('REDIS_URL')
    if redis_url:
        redis_conn = Redis.from_url(redis_url)
    else:
        redis_conn = Redis()
    try:
        job = Job.fetch(job_id, connection=redis_conn)
    except Exception:
        return {'status': 'not_found'}

    return {'id': job.get_id(), 'status': job.get_status(), 'result': job.result}


@router.post('/upload_pdf')
async def upload_pdf(
    file: UploadFile = File(...),
    prompt: str = Form(None),
    folder: str = Form(None),
    assistant: str = Form(None),
    model: str = Form(None),
    alias: str = Form(None),
):
    # server-side validation: limit size and allowed extensions
    allowed_ext = {'.pdf', '.png', '.jpg', '.jpeg', '.csv', '.txt'}
    max_bytes = 800 * 1024  # 800 KB
    contents = await file.read()
    if len(contents) > max_bytes:
        return {'error': 'file_too_large', 'detail': f'El archivo excede {max_bytes} bytes'}

    _, ext = os.path.splitext(file.filename or '')
    if ext.lower() not in allowed_ext:
        return {'error': 'invalid_file_type', 'detail': f'Extensión no soportada: {ext}'}

    tmp_dir = '/tmp/saia_demo'
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, file.filename)
    # save uploaded file to temp
    with open(tmp_path, 'wb') as f:
        f.write(contents)

    upload_resp = None
    file_id = None
    try:
        # If PDF, check number of pages before uploading to avoid SAIA error 8024
        if ext.lower() == '.pdf':
            try:
                import importlib
                PdfReader = None
                try:
                    mod = importlib.import_module('PyPDF2')
                    PdfReader = getattr(mod, 'PdfReader', None)
                except Exception:
                    PdfReader = None
                if PdfReader is not None:
                    reader = PdfReader(tmp_path)
                    num_pages = len(reader.pages)
                    if num_pages == 0:
                        return {'error': 'document_no_pages', 'detail': 'El PDF no contiene páginas.'}
                else:
                    raise ImportError('PyPDF2 not available')
            except Exception:
                # Fallback heuristics when PyPDF2 is not available or fails.
                # Use simple byte-level checks to avoid uploading clearly-empty/invalid PDFs.
                try:
                    # 'contents' was read earlier from UploadFile
                    if not contents or len(contents) < 200:
                        return {'error': 'document_no_pages', 'detail': 'El PDF parece vacío o demasiado pequeño.'}
                    # basic PDF header/footer check
                    if not contents.startswith(b'%PDF'):
                        return {'error': 'document_no_pages', 'detail': 'El archivo no parece un PDF válido (sin encabezado %PDF).'}
                    if b'%%EOF' not in contents[-2048:]:
                        # EOF marker may be near the end; if missing, consider invalid
                        logger.debug('PDF parece no tener marcador EOF, pero se continuará (heurístico).')
                    # look for page objects heuristically
                    if contents.count(b'/Type /Page') == 0 and contents.count(b'/Page') == 0:
                        # no obvious page markers found
                        return {'error': 'document_no_pages', 'detail': 'No se detectaron páginas en el PDF (comprobación heurística).'}
                except Exception:
                    logger.debug('Fallback heurístico de PDF falló; se intentará subir y dejar que SAIA lo valide.')

        # Orchestrate upload->chat. Use a unique alias per upload to avoid reusing previous files.
        # Alias format: <filename-stem>-<shortid>
        prompt_text = prompt or ""
        try:
            import re, uuid
            stem = os.path.splitext(file.filename or "file")[0]
            stem = re.sub(r"[^A-Za-z0-9_\-]", "_", stem)[:40] or "file"
            unique_alias = f"{stem}-{uuid.uuid4().hex[:6]}"
        except Exception:
            unique_alias = os.path.splitext(file.filename or "file")[0] or "file"
        # enqueue job in Redis to avoid Heroku request timeouts
        redis_url = os.environ.get('REDIS_URL')
        if not redis_url:
            logger.error('REDIS_URL not configured; cannot enqueue job')
            return {'error': 'redis_not_configured', 'detail': 'Configure REDIS_URL in environment (Heroku addon)'}
        redis_conn = Redis.from_url(redis_url)
        q = Queue(connection=redis_conn)

        with open(tmp_path, 'rb') as f:
            b = f.read()

        payload = {
            'file_b64': base64.b64encode(b).decode('ascii'),
            'filename': file.filename,
            'prompt': prompt_text,
            'folder': folder or 'test1',
            'alias': alias or unique_alias,
            'assistant': assistant,
        }

        job = q.enqueue('app.tasks.process_upload', payload)
        return {'status': 'queued', 'job_id': job.get_id()}

    except Exception as e:
        logger.exception("Error en upload_pdf")
        return {'error': 'internal_error', 'detail': str(e), 'upload_response': upload_resp}

    finally:
        # cleanup temp file if exists
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            logger.warning(f"No se pudo borrar tmp file: {tmp_path}")








