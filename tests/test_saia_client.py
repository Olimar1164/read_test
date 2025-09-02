import pytest
import respx
import httpx
from app.services.ai.saia_console_client import SAIAConsoleClient


@pytest.mark.asyncio
async def test_upload_and_chat(monkeypatch, tmp_path):
    # create dummy file
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"PDFDATA")

    client = SAIAConsoleClient(
        "token", "org", "proj", "assistant", "https://api.saia.ai"
    )

    async def fake_post_upload(request):
        return httpx.Response(200, json={"id": "file_123"})

    async def fake_post_chat(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"message": "ok"}'}}]}
        )

    # use respx to mock HTTPX
    with respx.mock(assert_all_called=False) as m:
        m.post("https://api.saia.ai/v1/files").mock(
            return_value=httpx.Response(200, json={"id": "file_123"})
        )
        m.post("https://api.saia.ai/chat").mock(
            return_value=httpx.Response(
                200, json={"choices": [{"message": {"content": '{"message": "ok"}'}}]}
            )
        )
        up = await client.upload_file(str(p))
        assert up.get("id") == "file_123"
        res = await client.send_pdf_and_query(str(p), "Resume el archivo")
        assert res.get("message") == "ok"
