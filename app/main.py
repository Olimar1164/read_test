from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.endpoints import router
from app.whiteboard import register_whiteboard

# Import shared clients at module level as requested (keeps imports visible and predictable)
from app.services.ai.processor import AIProcessor
from app.services.ai.saia_console_client import SAIAConsoleClient

app = FastAPI()
app.include_router(router)
register_whiteboard(app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: create shared service instances and store on app.state for DI
    try:
        import os

        token = os.environ.get("GEAI_API_TOKEN")
        org = os.environ.get("ORGANIZATION_ID")
        proj = os.environ.get("PROJECT_ID")
        assistant = os.environ.get("ASSISTANT_ID", "test_read")
        if token and org and proj:
            # store instances for reuse by endpoints
            app.state.ai_processor = AIProcessor(token, org, proj)
            app.state.saia_client = SAIAConsoleClient(token, org, proj, assistant)
    except Exception:
        # don't block startup on misconfiguration; endpoints will fallback
        app.state.ai_processor = None
        app.state.saia_client = None

    # mapping for uploads prepared for streaming: alias -> file_path
    try:
        app.state.stream_uploads = {}
    except Exception:
        app.state.stream_uploads = {}

    yield

    # shutdown: close shared http clients used by services and instance clients
    try:
        # class-level shared httpx client used by AIProcessor
        await AIProcessor.close_client()
    except Exception:
        pass
    try:
        # close instance-level httpx client if created
        if getattr(app.state, "saia_client", None) is not None:
            try:
                await app.state.saia_client.aclose()
            except Exception:
                pass
            app.state.saia_client = None
    except Exception:
        pass


app.router.lifespan_context = lifespan

templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
