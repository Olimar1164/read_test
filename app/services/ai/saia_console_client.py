import hashlib
import httpx
import logging
import mimetypes
import os
import unicodedata
from typing import Optional, Dict, Any

from app.services.ai.processor import AIProcessor

logger = logging.getLogger("app.services.ai.saia_console_client")


class SAIAConsoleClient:
    """Cliente simple para interactuar con SAIA Console: subir archivos y enviar mensajes al chat."""

    def __init__(
        self,
        api_token: str,
        organization_id: str,
        project_id: str,
        assistant_id: str,
        base_url: str = "https://api.saia.ai",
        timeout: int = 60,
    ):
        self.api_token = api_token
        self.organization_id = organization_id
        self.project_id = project_id
        self.assistant_id = assistant_id
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.default_headers = {
            "Authorization": f"Bearer {self.api_token}",
            "organizationId": self.organization_id,
            "projectId": self.project_id,
        }
        self.processor = AIProcessor(
            api_token, organization_id, project_id, base_url=f"{self.base_url}/chat", request_timeout=timeout
        )
        # Simple in-memory caches per-process
        self._client: Optional[httpx.AsyncClient] = None
        self._upload_cache: Dict[str, Dict] = {}

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            # Use HTTP/1.1 by default to avoid requiring the 'h2' package on Heroku
            self._client = httpx.AsyncClient(http2=False, timeout=self.timeout, limits=limits)
        return self._client

    @staticmethod
    def _sanitize_header_value(v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        try:
            s = str(v)
        except Exception:
            s = repr(v)
        nfkd = unicodedata.normalize("NFKD", s)
        ascii_only = "".join(c for c in nfkd if ord(c) < 128)
        return "".join(ch for ch in ascii_only if ch.isprintable())

    @staticmethod
    def _guess_content_type(path: str) -> str:
        mt, _ = mimetypes.guess_type(path)
        return mt or "application/octet-stream"

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    async def upload_file(self, file_path: str, file_name: Optional[str] = None, folder: Optional[str] = None, alias: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/v1/files"
        orig_name = file_name or os.path.basename(file_path)
        multipart_filename = orig_name

        try:
            # prefer aiofiles when available (module-level HAVE_AIOFILES expected)
            try:
                from app.api.endpoints import HAVE_AIOFILES
            except Exception:
                HAVE_AIOFILES = False
            if HAVE_AIOFILES:
                try:
                    async with __import__('aiofiles').open(file_path, 'rb') as fh:
                        data = await fh.read()
                except Exception:
                    with open(file_path, "rb") as fh:
                        data = fh.read()
            else:
                with open(file_path, "rb") as fh:
                    data = fh.read()
        except Exception as e:
            logger.exception("Failed reading file for upload: %s", file_path)
            return {"error": "file_read_failed", "detail": str(e)}

        file_size = len(data)
        file_hash = self._sha256(data)
        content_type = self._guess_content_type(file_path)

        headers = dict(self.default_headers)
        headers["Accept"] = "application/json"
        alias_used = alias or os.path.splitext(multipart_filename)[0]
        headers["fileName"] = self._sanitize_header_value(alias_used)
        headers["folder"] = self._sanitize_header_value(folder or "test1")

        # Cache: if we already uploaded an identical file (by sha256), skip re-upload
        cache_key = f"{alias_used}:{file_hash}"
        cached = self._upload_cache.get(cache_key)
        if cached:
            logger.debug("Using cached upload result for %s", cache_key)
            return dict(cached)

        try:
            client = self._get_client()
            files = {"file": (multipart_filename, data, content_type)}
            resp = await client.post(url, headers=headers, files=files)
            status = resp.status_code
            text = resp.text
            try:
                j = resp.json()
            except Exception:
                j = None

            result: Dict[str, Any] = {
                "status_code": status,
                "headers": dict(resp.headers),
                "file_name_used": multipart_filename,
                "file_alias_used": alias_used,
                "file_size": file_size,
                "file_sha256": file_hash,
            }
            if j is not None:
                if isinstance(j, dict):
                    result.update(j)
                    result.setdefault("file_name_used", multipart_filename)
                else:
                    result["json"] = j
            else:
                result["text"] = text

            if status >= 400:
                logger.warning("Upload returned status %s: %s", status, text[:400])
            else:
                # store success in cache to avoid repeating
                try:
                    self._upload_cache[cache_key] = dict(result)
                    # Bound the cache size roughly
                    if len(self._upload_cache) > 256:
                        # drop an arbitrary first item
                        self._upload_cache.pop(next(iter(self._upload_cache)))
                except Exception:
                    pass
            return result
        except UnicodeEncodeError as ue:
            logger.warning("UnicodeEncodeError when sending headers, sanitized fileName: %s", ue)
            headers["fileName"] = self._sanitize_header_value(multipart_filename)
            return {"error": "unicode_encode_error", "detail": str(ue), "file_name_used": multipart_filename, "file_alias_used": alias_used}
        except (httpx.RequestError,) as re:
            logger.warning("Upload request error: %s", re)
            return {"error": "request_error", "detail": str(re), "file_name_used": multipart_filename, "file_alias_used": alias_used}
        except Exception as e:
            logger.exception("Unexpected error during upload: %s", e)
            return {"error": "upload_failed", "detail": str(e), "file_name_used": multipart_filename, "file_alias_used": alias_used}

    async def chat_with_file(self, prompt: str, file_id: str, stream: bool = False, assistant_id: Optional[str] = None, file_name_used: Optional[str] = None) -> Dict[str, Any]:
        content = prompt.replace("{file}", f"{{file:{file_id}}}") if "{file}" in prompt else f"{prompt} Referencia a archivo:{{file:{file_id}}}"
        aid = assistant_id or self.assistant_id
        extra_headers = {"fileName": file_name_used} if file_name_used else None
        try:
            try:
                sent_payload = self.processor._prepare_payload(aid, content, stream=stream)
            except Exception:
                sent_payload = {"model": f"saia:assistant:{aid}", "messages": [{"role": "user", "content": content}], "stream": stream}
            sent_headers = dict(self.processor.headers)
            if extra_headers:
                for k, v in extra_headers.items():
                    sent_headers[str(k)] = str(v)
            resp = await self.processor.process(aid, content, extra_headers=extra_headers, stream=stream)
            if isinstance(resp, dict):
                resp.setdefault("sent_payload", sent_payload)
                sh = dict(sent_headers)
                if "Authorization" in sh:
                    sh["Authorization"] = "Bearer *****"
                resp.setdefault("sent_headers", sh)
            return resp
        except Exception as e:
            logger.warning("Chat exception: %s", e)
            return {"error": "chat_failed", "detail": str(e)}

    async def send_pdf_and_query(self, file_path: str, prompt: str, folder: Optional[str] = None, stream: bool = False, alias: Optional[str] = None, assistant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Upload a file then call chat referencing that very file.
        - Use alias (fileName header) as the primary reference, mirroring Postman: {file:<alias>}.
        - Generate/poll dataFileUrl briefly to reduce ingestion races.
        - Retry when SAIA returns 8024.
        """
        alias_used = alias or os.path.splitext(os.path.basename(file_path))[0]
        up = await self.upload_file(file_path, file_name=None, folder=folder, alias=alias_used)

        # Prefer referencing by alias to match Postman behavior strictly
        file_id = alias_used
        file_name_used = alias_used
        if isinstance(up, dict):
            file_name_used = up.get("file_alias_used") or file_name_used

        # Optional: if the server returned an explicit id, keep it as a fallback only
        if isinstance(up, dict):
            fid = up.get("id") or up.get("fileId") or up.get("file_id")
            dfid = up.get("dataFileId") or up.get("data_file_id") or up.get("datafileid")
            if not file_id and (fid or dfid):
                file_id = fid or dfid

        # Skip pre-ingestion polling: attempt chat immediately; handle 8024 with quick retries
        max_retries = 5
        delay = 0.2
        for attempt in range(1, max_retries + 1):
            resp = await self.chat_with_file(
                prompt,
                file_id,
                stream=stream,
                assistant_id=assistant_id,
                file_name_used=file_name_used,
            )
            if isinstance(resp, dict) and (
                resp.get("error") == "document_no_pages"
                or str(resp.get("code") or resp.get("status_code") or "") == "8024"
            ):
                if attempt == max_retries:
                    return resp
                await __import__("asyncio").sleep(delay)
                delay *= 1.7
                continue
            return resp
        return {"error": "chat_failed", "detail": "Retries exhausted"}

    async def aclose(self) -> None:
        """Close the instance httpx client if created."""
        try:
            if hasattr(self, '_client') and self._client is not None:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
                self._client = None
        except Exception:
            pass

