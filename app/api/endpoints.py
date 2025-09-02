from fastapi import APIRouter, UploadFile, File, Form, BackgroundTasks
from fastapi import Request
import base64
import os
import logging
from dotenv import load_dotenv
from app.services.ai.saia_console_client import SAIAConsoleClient
from app.background import job_store
from fastapi.responses import StreamingResponse
from typing import AsyncGenerator

# standard libs used inside
import re
import uuid
import json
import asyncio
from app.services.ai.processor import AIProcessor

# Optional async file I/O
try:
    import aiofiles
    HAVE_AIOFILES = True
except Exception:
    aiofiles = None
    HAVE_AIOFILES = False

# Optional PDF reader
PdfReader = None
try:
    import PyPDF2
    PdfReader = getattr(PyPDF2, 'PdfReader', None)
except Exception:
    PdfReader = None

# No DB persistence for demo: uploads go directly to SAIA files API
load_dotenv()

logger = logging.getLogger("app.api.endpoints")

router = APIRouter()


@router.get('/status/{job_id}')
def job_status(job_id: str):
    j = job_store.get(job_id)
    if not j:
        return {'status': 'not_found'}
    return {'id': job_id, 'status': j['status'], 'result': j['result'], 'error': j['error']}


@router.post('/upload_pdf')
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    prompt: str = Form(None),
    folder: str = Form(None),
    assistant: str = Form(None),
    alias: str = Form(None),
    background_tasks: BackgroundTasks = None,
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
    # save uploaded file to temp (use aiofiles when available to avoid blocking event loop)
    if HAVE_AIOFILES:
        try:
            async with aiofiles.open(tmp_path, 'wb') as f:
                await f.write(contents)
        except Exception:
            with open(tmp_path, 'wb') as f:
                f.write(contents)
    else:
        with open(tmp_path, 'wb') as f:
            f.write(contents)

    upload_resp = None
    try:
        # If PDF, check number of pages before uploading to avoid SAIA error 8024
        if ext.lower() == '.pdf':
            # Prefer using module-level PdfReader if available (imported at module load).
            if PdfReader is not None:
                try:
                    reader = PdfReader(tmp_path)
                    num_pages = len(reader.pages)
                    if num_pages == 0:
                        return {'error': 'document_no_pages', 'detail': 'El PDF no contiene páginas.'}
                except Exception:
                    logger.debug('PdfReader falló al analizar el PDF; se usará heurístico de bytes como fallback.')

            # Fallback heuristics when PyPDF2 is not available or fails.
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
        # In-process background task to avoid Heroku 30s timeouts without Redis
        # read bytes for payload; prefer aiofiles when available
        if HAVE_AIOFILES:
            try:
                async with aiofiles.open(tmp_path, 'rb') as f:
                    b = await f.read()
            except Exception:
                with open(tmp_path, 'rb') as f:
                    b = f.read()
        else:
            with open(tmp_path, 'rb') as f:
                b = f.read()
        payload = {
            'file_b64': base64.b64encode(b).decode('ascii'),
            'filename': file.filename,
            'prompt': prompt_text,
            'folder': folder or 'test1',
            'alias': alias or unique_alias,
            'assistant': assistant or os.environ.get('ASSISTANT_ID', 'test_read'),
        }

        job_id = job_store.create(payload)

        async def _worker():
            try:
                # Prefer shared instance from app.state created at startup; fallback to per-call client
                client = getattr(request.app.state, 'saia_client', None)
                if client is None:
                    client = SAIAConsoleClient(
                        os.environ.get('GEAI_API_TOKEN'),
                        os.environ.get('ORGANIZATION_ID'),
                        os.environ.get('PROJECT_ID'),
                        os.environ.get('ASSISTANT_ID', 'test_read'),
                    )
                tmp_dir = '/tmp/saia_demo'
                os.makedirs(tmp_dir, exist_ok=True)
                p = os.path.join(tmp_dir, payload['filename'])
                # write decoded payload to temp file (use aiofiles when possible)
                if HAVE_AIOFILES:
                    try:
                        async with aiofiles.open(p, 'wb') as fw:
                            await fw.write(base64.b64decode(payload['file_b64']))
                    except Exception:
                        with open(p, 'wb') as fw:
                            fw.write(base64.b64decode(payload['file_b64']))
                else:
                    with open(p, 'wb') as fw:
                        fw.write(base64.b64decode(payload['file_b64']))
                res = await client.send_pdf_and_query(
                    p,
                    payload['prompt'],
                    folder=payload['folder'],
                    alias=payload['alias'],
                    assistant_id=payload['assistant']
                )
                try:
                    os.remove(p)
                except Exception:
                    pass
                job_store.set_result(job_id, res)
            except Exception as e:
                job_store.set_error(job_id, str(e))

        if background_tasks is not None:
            background_tasks.add_task(_worker)
        else:
            import asyncio
            asyncio.create_task(_worker())

        return {'status': 'queued', 'job_id': job_id}

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


@router.post('/upload_stream')
async def upload_stream(request: Request, file: UploadFile = File(...), alias: str = Form(None)):
    """Accept a file and prepare it for streaming; returns alias to use with /stream/{alias}."""
    tmp_dir = '/tmp/saia_demo'
    os.makedirs(tmp_dir, exist_ok=True)
    name = file.filename or 'file'
    alias_used = alias or os.path.splitext(name)[0]
    # save to temp first
    path = os.path.join(tmp_dir, f"{alias_used}-{__import__('uuid').uuid4().hex[:6]}")
    contents = await file.read()
    if HAVE_AIOFILES:
        try:
            async with aiofiles.open(path, 'wb') as fw:
                await fw.write(contents)
        except Exception:
            with open(path, 'wb') as fw:
                fw.write(contents)
    else:
        with open(path, 'wb') as fw:
            fw.write(contents)

    # Attempt to upload to SAIA so the assistant can reference the file immediately.
    client = getattr(request.app.state, 'saia_client', None)
    if client is None:
        # fallback: construct a temporary client
        try:
            client = SAIAConsoleClient(
                os.environ.get('GEAI_API_TOKEN'),
                os.environ.get('ORGANIZATION_ID'),
                os.environ.get('PROJECT_ID'),
                os.environ.get('ASSISTANT_ID', 'test_read')
            )
        except Exception:
            client = None

    upload_result = None
    if client is not None:
        try:
            # use the alias as the requested fileName header to mirror Postman parity
            upload_result = await client.upload_file(path, file_name=name, folder='test1', alias=alias_used)
        except Exception as e:
            upload_result = {'error': 'upload_failed', 'detail': str(e)}

    # register for streaming locally as well (for cleanup)
    try:
        request.app.state.stream_uploads[alias_used] = path
    except Exception:
        request.app.state.stream_uploads = {alias_used: path}

    # return alias and optional upload response for debugging
    resp = {'alias': alias_used}
    if upload_result is not None:
        resp['upload'] = upload_result
    return resp


@router.get('/stream/{alias}')
async def stream_alias(request: Request, alias: str):
    """Stream assistant response for a previously uploaded file alias using SSE."""
    path = request.app.state.stream_uploads.get(alias)
    if not path or not os.path.exists(path):
        return {'error': 'not_found', 'detail': 'Alias no preparado o archivo no existe'}

    # prefer shared processor if available
    processor = getattr(request.app.state, 'ai_processor', None)
    if processor is None:
        # build a local processor to stream once
        processor = None
        try:
            processor = __import__('app.services.ai.processor', fromlist=['AIProcessor']).AIProcessor(
                os.environ.get('GEAI_API_TOKEN'), os.environ.get('ORGANIZATION_ID'), os.environ.get('PROJECT_ID')
            )
        except Exception:
            return {'error': 'server_misconfigured'}

    async def event_gen() -> AsyncGenerator[bytes, None]:
        # read minimal prompt referencing the file by alias
        prompt = f"Lee este archivo y resume: {{file:{alias}}}"
        import asyncio, json
        ag = None
        try:
            # try to use streaming endpoint first, but guard with a short timeout for first fragment
            try:
                ag = processor.process_stream(os.environ.get('ASSISTANT_ID', 'test_read'), prompt)
                first = await asyncio.wait_for(anext(ag), timeout=0.8)
                # helper to split large text into smaller chunks and emit them with tiny pauses
                async def emit_text_pieces(text: str, piece_size: int = 120, pause: float = None):
                    # pause can be configured via STREAM_PAUSE env (seconds). If not set, default to 0.02
                    try:
                        if pause is None:
                            pause = float(os.environ.get('STREAM_PAUSE', '0.02'))
                    except Exception:
                        pause = 0.02
                    try:
                        i = 0
                        L = len(text)
                        while i < L:
                            part = text[i:i+piece_size]
                            i += piece_size
                            try:
                                yield f"data: {json.dumps({'text': part}, ensure_ascii=False)}\n\n".encode('utf-8')
                            except Exception:
                                continue
                            try:
                                await asyncio.sleep(pause)
                            except Exception:
                                pass
                    except Exception:
                        return

                # send the first chunk and subsequent chunks, normalizing to {'text': ...}
                try:
                    if isinstance(first, dict):
                        txt = first.get('text') or first.get('message') or first.get('content') or json.dumps(first, ensure_ascii=False)
                    else:
                        txt = str(first)
                    async for piece in emit_text_pieces(txt):
                        yield piece
                except Exception:
                    pass
                # continue streaming remaining chunks
                async for chunk in ag:
                    try:
                        if isinstance(chunk, dict):
                            txt = chunk.get('text') or chunk.get('message') or chunk.get('content') or json.dumps(chunk, ensure_ascii=False)
                        else:
                            txt = str(chunk)
                        async for piece in emit_text_pieces(txt):
                            yield piece
                    except Exception:
                        continue
                # signal completion to client
                try:
                    yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n".encode('utf-8')
                except Exception:
                    pass
                return
            except (asyncio.TimeoutError, StopAsyncIteration):
                # upstream didn't stream quickly; fallback to non-streaming call
                pass

            # Fallback: call the non-streaming processor and emit the textual result in small chunks
            try:
                resp = await processor.process(os.environ.get('ASSISTANT_ID', 'test_read'), prompt, stream=False)
            except Exception as e:
                resp = {'error': 'processing_failed', 'detail': str(e)}

            # Try to extract main text from common fields
            def extract_text(o):
                if not o:
                    return ''
                if isinstance(o, str):
                    return o
                if isinstance(o, dict):
                    for k in ('message','content','text','output','answer','result'):
                        v = o.get(k)
                        if isinstance(v, str) and v.strip():
                            return v
                    # choices shape
                    ch = o.get('choices')
                    if isinstance(ch, list) and ch:
                        first = ch[0]
                        if isinstance(first, dict):
                            msg = first.get('message') or first.get('delta') or first
                            if isinstance(msg, dict):
                                c = msg.get('content') or msg.get('text')
                                if isinstance(c, str):
                                    return c
                            elif isinstance(msg, str):
                                return msg
                if isinstance(o, list):
                    return '\n'.join([extract_text(i) for i in o if isinstance(i, (str, dict))])
                return ''

            text = extract_text(resp) or json.dumps(resp, ensure_ascii=False)
            # split into small chunks (by sentence-ish) to simulate streaming
            max_chunk = 240
            i = 0
            while i < len(text):
                part = text[i:i+max_chunk]
                i += max_chunk
                try:
                    yield f"data: {json.dumps({'text': part}, ensure_ascii=False)}\n\n".encode('utf-8')
                except Exception:
                    continue
                # small pause to allow client to render progressively; controlled by STREAM_PAUSE
                try:
                    try:
                        sp = float(os.environ.get('STREAM_PAUSE', '0.02'))
                    except Exception:
                        sp = 0.02
                    if sp and sp > 0:
                        await asyncio.sleep(sp)
                    else:
                        # yield control briefly to event loop
                        await asyncio.sleep(0)
                except Exception:
                    pass
            # final marker
            try:
                yield f"data: {json.dumps({'done': True}, ensure_ascii=False)}\n\n".encode('utf-8')
            except Exception:
                pass

        finally:
            # cleanup temp file after streaming
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    return StreamingResponse(event_gen(), media_type='text/event-stream')








