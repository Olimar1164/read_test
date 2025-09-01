from typing import Any, Dict, Optional, AsyncGenerator, Union
import re
import json
import traceback
import httpx
from dotenv import load_dotenv
from httpx import Response, HTTPStatusError, RequestError
import logging

# Configure a proper hierarchical logger
logger = logging.getLogger("app.services.ai.processor")

# Load environment variables
load_dotenv()

class AIProcessor:
    """
    Clase para procesamiento de solicitudes a servicios de IA externos.
    Maneja comunicación HTTP con la API de SAIA, procesamiento de respuestas
    y manejo de errores.
    """
    
    def __init__(
        self,
        api_token: str,
        organization_id: str,
        project_id: str,
        base_url: str = 'https://api.saia.ai/chat',
        request_timeout: int = 60
    ):
        self.api_token = api_token
        self.organization_id = organization_id
        self.project_id = project_id
        self.url = base_url
        self.request_timeout = request_timeout
        self.headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.api_token}',
            'organizationId': self.organization_id,
            'projectId': self.project_id
        }
        logger.info(f"AIProcessor inicializado para proyecto {project_id}")

    # Shared async client (HTTP/2 + connection pooling) per-process to cut handshake/setup costs
    _shared_client: Optional[httpx.AsyncClient] = None

    @classmethod
    def _get_client(cls, timeout: int) -> httpx.AsyncClient:
        if cls._shared_client is None:
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
            cls._shared_client = httpx.AsyncClient(http2=True, timeout=timeout, limits=limits)
        return cls._shared_client

    def _prepare_payload(self, assistant_id: str, content: Union[str, Dict[str, Any]], stream: bool = False) -> Dict[str, Any]:
        # Ensure content is a string (SAIA chat expects string content in messages)
        if not isinstance(content, str):
            try:
                content_str = json.dumps(content, ensure_ascii=False)
            except Exception:
                content_str = str(content)
        else:
            content_str = content

        return {
            "model": f"saia:assistant:{assistant_id}",
            "messages": [{"role": "user", "content": content_str}],
            "stream": stream
        }

    def _parse_ai_response(self, response_text: str) -> Dict[str, Any]:
        clean = re.sub(r'```json\s*|\s*```', '', response_text, flags=re.DOTALL).strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            logger.debug("Respuesta no es JSON válido, devolviendo como mensaje de texto")
            return {'message': response_text}

    async def process(self, assistant_id: str, content: Any, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        start_time = __import__('time').time()
        request_id = __import__('uuid').uuid4().hex[:8]
        logger.info(f"[{request_id}] Procesando solicitud para assistant_id={assistant_id}")
        try:
            payload = self._prepare_payload(assistant_id, content)
            # Merge base headers with any extra headers for this call (e.g., fileName)
            headers = dict(self.headers)
            if extra_headers:
                # ensure keys/values are strings
                for hk, hv in (extra_headers or {}).items():
                    try:
                        headers[str(hk)] = str(hv)
                    except Exception:
                        headers[str(hk)] = repr(hv)

            client = self._get_client(self.request_timeout)
            logger.debug(f"[{request_id}] Enviando solicitud a {self.url}")
            res = await client.post(
                self.url,
                headers=headers,
                json=payload,
            )
            res.raise_for_status()
            data = res.json()
            # Robust extraction of assistant textual content from common response shapes
            raw = None
            try:
                if isinstance(data, dict) and 'choices' in data and data['choices']:
                    choice0 = data['choices'][0]
                    # common: { choices: [{ message: { content: '...' } }] }
                    if isinstance(choice0, dict):
                        msg = choice0.get('message') or choice0.get('delta') or choice0
                        if isinstance(msg, dict):
                            raw = msg.get('content') or msg.get('text') or msg.get('payload')
                        elif isinstance(msg, str):
                            raw = msg
                    elif isinstance(choice0, str):
                        raw = choice0
                # Fallback: if no 'raw' found, try to find a first-long string in the response
                if raw is None:
                    def find_string(o):
                        if isinstance(o, str):
                            return o
                        if isinstance(o, list):
                            for v in o:
                                s = find_string(v)
                                if s and len(s) > 0:
                                    return s
                            return None
                        if isinstance(o, dict):
                            for k, v in o.items():
                                s = find_string(v)
                                if s and len(s) > 0:
                                    return s
                        return None
                    raw = find_string(data)

                # If we still don't have any textual payload, return the full JSON as fallback
                if raw is None:
                    result = data
                else:
                    # If the assistant returned an empty/whitespace-only string, treat as "no text found"
                    if isinstance(raw, str) and raw.strip() == '':
                        return {'error': 'no_text_found', 'user_message': 'No se detectó texto en el archivo o imagen.'}
                    result = self._parse_ai_response(raw)
                    # If parsing yields an object whose 'message' is empty, treat similarly
                    if isinstance(result, dict):
                        m = result.get('message')
                        if isinstance(m, str) and m.strip() == '':
                            return {'error': 'no_text_found', 'user_message': 'No se detectó texto en el archivo o imagen.'}
            except KeyError:
                # Defensive: if response shape is unexpected, return the raw data instead of raising
                result = data
            elapsed = __import__('time').time() - start_time
            logger.info(f"[{request_id}] Procesamiento completado en {elapsed:.2f}s")
            return result
        except HTTPStatusError as e:
            # include upstream response body for debugging
            resp = e.response
            text = None
            try:
                text = resp.text
            except Exception:
                text = str(resp)
            # try parse JSON error body to detect known SAIA errors
            detected = None
            try:
                j = resp.json()
                # SAIA doc-empty error example: {"error":{"message":"The document has no pages.","code":"8024"},"requestId":"..."}
                if isinstance(j, dict) and 'error' in j and isinstance(j['error'], dict):
                    errobj = j['error']
                    code = errobj.get('code')
                    msg = errobj.get('message') or text
                    if code == '8024' or code == 8024:
                        detected = {'error': 'document_no_pages', 'code': str(code), 'detail': msg, 'user_message': 'El documento parece no tener páginas. Verifica que el archivo contenga páginas válidas (PDF con páginas, o un archivo soportado).'}
            except Exception:
                pass

            # sanitize headers for logging (hide authorization)
            sanitized_headers = dict(headers) if 'headers' in locals() else dict(self.headers)
            if 'Authorization' in sanitized_headers:
                sanitized_headers['Authorization'] = 'Bearer *****'
            logger.error(f"[{request_id}] Error de estado HTTP {resp.status_code}: {text}")
            if detected:
                # attach payload and headers for debugging
                detected.update({'payload': payload, 'sent_headers': sanitized_headers, 'status_code': resp.status_code})
                return detected
            return {
                'error': f"Error HTTP {resp.status_code}",
                'status_code': resp.status_code,
                'detail': text,
                'payload': payload,
                'sent_headers': sanitized_headers
            }
        except RequestError as e:
            logger.error(f"[{request_id}] Error de red en solicitud: {e}")
            return {'error': "Error de red", 'detail': str(e)}
        except Exception as e:
            logger.error(f"[{request_id}] Error inesperado en process", exc_info=True)
            return {'error': "Error interno", 'detail': str(e)}

    async def process_stream(self, assistant_id: str, content: Any) -> AsyncGenerator[Dict[str, Any], None]:
        request_id = __import__('uuid').uuid4().hex[:8]
        logger.info(f"[{request_id}] Iniciando streaming para assistant_id={assistant_id}")
        payload = self._prepare_payload(assistant_id, content, stream=True)
        try:
            client = self._get_client(self.request_timeout)
            async with client.stream(
                "POST",
                self.url,
                headers=self.headers,
                json=payload,
            ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith('data: '):
                            json_data = line[len('data: '):]
                            if json_data.strip() == "[DONE]":
                                logger.debug(f"[{request_id}] Streaming completado")
                                break
                            try:
                                chunk = json.loads(json_data)
                                yield chunk
                            except json.JSONDecodeError:
                                logger.warning(f"[{request_id}] No se pudo parsear fragmento: {json_data[:50]}...")
                                continue
        except HTTPStatusError as e:
            logger.error(f"[{request_id}] Error de estado HTTP en streaming {e.response.status_code}: {e}")
            yield {'error': f"Error HTTP {e.response.status_code}", 'detail': str(e)}
        except RequestError as e:
            logger.error(f"[{request_id}] Error de red en streaming: {e}")
            yield {'error': "Error de red", 'detail': str(e)}
        except Exception as e:
            logger.error(f"[{request_id}] Error inesperado en process_stream", exc_info=True)
            yield {'error': "Error interno", 'detail': str(e)}
