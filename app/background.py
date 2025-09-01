import asyncio
import threading
import time
import uuid
from typing import Any, Dict, Optional


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: Dict[str, Dict[str, Any]] = {}

    def create(self, payload: Optional[Dict[str, Any]] = None) -> str:
        job_id = uuid.uuid4().hex
        with self._lock:
            self._jobs[job_id] = {
                'status': 'queued',
                'created_at': time.time(),
                'updated_at': time.time(),
                'result': None,
                'error': None,
                'payload': payload or {},
            }
        return job_id

    def set_status(self, job_id: str, status: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]['status'] = status
                self._jobs[job_id]['updated_at'] = time.time()

    def set_result(self, job_id: str, result: Any) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]['result'] = result
                self._jobs[job_id]['status'] = 'finished'
                self._jobs[job_id]['updated_at'] = time.time()

    def set_error(self, job_id: str, error: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]['error'] = error
                self._jobs[job_id]['status'] = 'failed'
                self._jobs[job_id]['updated_at'] = time.time()

    def get(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._jobs.get(job_id)


job_store = JobStore()


async def run_async(coro):
    return await coro
