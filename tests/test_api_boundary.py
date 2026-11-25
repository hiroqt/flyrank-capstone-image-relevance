"""Shared requirement #2: bad input gets a clean 4xx with one error shape, never a 500.
Shared requirement #4: every request is scoped to a tenant.
"""
from fastapi import APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from app.api.deps import TenantDep
from app.main import create_app


class EchoIn(BaseModel):
    name: str = Field(min_length=1, max_length=50)


def _app_with_probe_routes():
    """A fresh app plus throwaway routes that exercise the shared plumbing."""
    app = create_app()
    router = APIRouter(prefix="/_probe")

    @router.get("/tenant")
    def which_tenant(tenant_id: TenantDep):
        return {"tenant": tenant_id}

    @router.post("/echo")
    def echo(body: EchoIn, tenant_id: TenantDep):
        return {"name": body.name, "tenant": tenant_id}

    @router.get("/boom")
    def boom():
        raise RuntimeError("internal detail that must not leak")

    app.include_router(router)
    return app


def _assert_error(resp, status, code):
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body["error"]["code"] == code
    assert body["error"]["message"]


def test_health_reports_db_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


def test_unknown_route_is_clean_404(client):
    _assert_error(client.get("/nope"), 404, "not_found")


def test_wrong_method_is_clean_405(client):
    _assert_error(client.post("/health"), 405, "method_not_allowed")


def test_default_tenant_is_demo():
    with TestClient(_app_with_probe_routes()) as c:
        assert c.get("/_probe/tenant").json() == {"tenant": "demo"}


def test_tenant_header_selects_tenant():
    with TestClient(_app_with_probe_routes()) as c:
        assert c.get("/_probe/tenant", headers={"X-Tenant-Id": "other"}).json() == {"tenant": "other"}


def test_malformed_tenant_header_is_400():
    with TestClient(_app_with_probe_routes()) as c:
        _assert_error(c.get("/_probe/tenant", headers={"X-Tenant-Id": "Bad Tenant!"}), 400, "bad_request")


def test_unknown_tenant_is_404():
    with TestClient(_app_with_probe_routes()) as c:
        _assert_error(c.get("/_probe/tenant", headers={"X-Tenant-Id": "ghost"}), 404, "not_found")


def test_invalid_body_is_422_with_details():
    with TestClient(_app_with_probe_routes()) as c:
        resp = c.post("/_probe/echo", json={"name": ""})
        _assert_error(resp, 422, "validation_error")
        assert resp.json()["error"]["details"][0]["loc"] == ["body", "name"]


def test_malformed_json_is_422():
    with TestClient(_app_with_probe_routes()) as c:
        resp = c.post("/_probe/echo", content=b"{not json", headers={"Content-Type": "application/json"})
        _assert_error(resp, 422, "validation_error")


def test_oversized_body_is_413():
    with TestClient(_app_with_probe_routes()) as c:
        _assert_error(c.post("/_probe/echo", content=b"x" * 1_000_001,
                             headers={"Content-Type": "application/json"}), 413, "payload_too_large")


def test_oversized_chunked_body_is_413():
    def chunks():
        for _ in range(11):
            yield b"x" * 100_000

    with TestClient(_app_with_probe_routes()) as c:
        _assert_error(c.post("/_probe/echo", content=chunks(),
                             headers={"Content-Type": "application/json"}), 413, "payload_too_large")


def test_unhandled_error_is_generic_500_without_leaking():
    with TestClient(_app_with_probe_routes(), raise_server_exceptions=False) as c:
        resp = c.get("/_probe/boom")
        _assert_error(resp, 500, "internal_error")
        assert "internal detail" not in resp.text
