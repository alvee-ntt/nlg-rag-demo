"""M10 — openable citation links (Track B proxy route).

Covers open_document (id -> bytes), the url field on citations, and the
GET /v1/documents/{id}/open endpoint (content-type/disposition, 404, 502).
No network/DB — blob store and DB access are mocked.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.rag_layer import service  # noqa: E402


# --- open_document ---------------------------------------------------------------

@contextmanager
def _fake_conn(*a, **k):
    yield object()


def test_open_document_unknown_id_returns_none(monkeypatch):
    monkeypatch.setattr(service, "connect", _fake_conn)
    monkeypatch.setattr(service, "get_document_blob_name", lambda conn, did: None)
    out = service.open_document(settings=SimpleNamespace(), document_id=999)
    assert out is None


def test_open_document_returns_blob_name_and_bytes(monkeypatch):
    monkeypatch.setattr(service, "connect", _fake_conn)
    monkeypatch.setattr(service, "get_document_blob_name", lambda conn, did: "FlexLife/Brochure.pdf")
    monkeypatch.setattr(service, "get_blob_store",
                        lambda settings: SimpleNamespace(download_blob=lambda name: b"%PDF-1.7 bytes"))
    name, data = service.open_document(settings=SimpleNamespace(), document_id=42)
    assert name == "FlexLife/Brochure.pdf"
    assert data == b"%PDF-1.7 bytes"


# --- format_sources carries a url ------------------------------------------------

def test_format_sources_includes_open_url():
    row = {"document_id": 7, "blob_name": "d.pdf", "chunk_index": 0,
           "content": "x", "metadata": {"page": 1}, "similarity": 0.8}
    s = service.format_sources([row])[0]
    assert s["url"] == "/v1/documents/7/open"
    assert s["document_id"] == 7


def test_format_sources_url_none_without_document_id():
    row = {"blob_name": "d.pdf", "chunk_index": 0, "content": "x",
           "metadata": {}, "similarity": 0.8}
    s = service.format_sources([row])[0]
    assert s["url"] is None


# --- endpoint (offline; app.state set by hand, lifespan not run) ------------------

@pytest.fixture()
def client():
    from starlette.testclient import TestClient
    from src.rag_layer import server

    app = server.app
    app.state.auth = None
    app.state.settings = SimpleNamespace()
    return TestClient(app)


def test_open_endpoint_pdf_inline(client, monkeypatch):
    from src.rag_layer import server
    monkeypatch.setattr(server, "open_document", lambda **k: ("FlexLife/Brochure.pdf", b"%PDF-1.7"))
    resp = client.get("/v1/documents/42/open")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"].startswith("inline")
    assert "Brochure.pdf" in resp.headers["content-disposition"]
    assert resp.content == b"%PDF-1.7"


def test_open_endpoint_docx_downloads(client, monkeypatch):
    from src.rag_layer import server
    monkeypatch.setattr(server, "open_document", lambda **k: ("notes.docx", b"PKzip"))
    resp = client.get("/v1/documents/7/open")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("attachment")
    assert "wordprocessingml" in resp.headers["content-type"]


def test_open_endpoint_unknown_id_404(client, monkeypatch):
    from src.rag_layer import server
    monkeypatch.setattr(server, "open_document", lambda **k: None)
    resp = client.get("/v1/documents/123/open")
    assert resp.status_code == 404


def test_open_endpoint_blob_error_502_hides_detail(client, monkeypatch):
    from src.rag_layer import server

    def boom(**k):
        raise RuntimeError("https://acct.blob.core.windows.net/secret?sig=TOKEN")

    monkeypatch.setattr(server, "open_document", boom)
    resp = client.get("/v1/documents/5/open")
    assert resp.status_code == 502
    # The SAS token / blob URL must not leak into the error detail.
    assert "sig=" not in resp.json()["detail"]
    assert "blob.core.windows.net" not in resp.json()["detail"]
