SAIA Console demo project

Minimal FastAPI app that uploads a file to SAIA Files API and then calls SAIA Chat referencing that same file. Includes a small HTML UI with light/dark mode.

How it works

- POST /v1/files: we upload the user file with headers: Authorization, organizationId, projectId, folder, fileName (alias). The alias is unique per upload to avoid reusing a previous file.
- POST /chat: we send a prompt that references the file as `{file:<alias>}` and include `fileName` header for parity with Postman.
- We briefly poll `dataFileUrl` to reduce ingestion races and retry chat when SAIA returns 8024 (document has no pages).

Setup

1) Create environment and install deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2) Configure env vars in `.env`

- GEAI_API_TOKEN
- ORGANIZATION_ID
- PROJECT_ID
- ASSISTANT_ID

3) Run server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

UI

- Upload a PDF/PNG/JPG/CSV/TXT (<= 800 KB). While uploading, a full-screen loading overlay is shown.
- The response appears in the dark panel. Use the “Modo claro/oscuro” toggle to switch themes; preference is persisted.
- “Reiniciar” clears the form/state.

Troubleshooting

- If chat returns a 8024 error, it will auto-retry a few times; ensure the uploaded file is valid and not empty.
- If you see the previous file’s content, ensure your alias isn’t reused. The app auto-generates a unique alias per upload.
- Check server logs for `status_code` and `dataFileUrl` to confirm successful upload and ingestion.
