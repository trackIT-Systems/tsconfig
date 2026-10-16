"""The application's own static files are revalidated by the browser on every
use, so an update is never served from its cache; vendored libraries are not."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app, client=("127.0.0.1", 40000))


@pytest.mark.parametrize("path", ["/static/js/app.js", "/static/css/style.css"])
def test_application_files_must_be_revalidated(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_unchanged_files_answer_304_to_a_revalidation(client):
    etag = client.get("/static/js/app.js").headers["etag"]
    response = client.get("/static/js/app.js", headers={"If-None-Match": etag})
    assert response.status_code == 304


def test_vendored_libraries_stay_cacheable(client):
    response = client.get("/static/vendor/alpinejs/js/cdn.min.js")
    assert response.status_code == 200
    assert "cache-control" not in response.headers
