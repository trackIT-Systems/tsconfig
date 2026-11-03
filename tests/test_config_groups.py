"""Config group handling in tracker mode (unchanged) and server mode (nested {project}/{group} layout)."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi.testclient import TestClient

from app.auth.session import SESSION_COOKIE, create_session_token
from app.config_loader import ConfigLoader
from app.main import app

REPO_CONFIGS = Path(__file__).resolve().parent.parent / "configs"


def _make_group(root: Path, *parts: str, timestamp: str = "20260101_000000") -> Path:
    """Create a group dir with one timestamp dir holding schedule.yml and a relative 'latest' symlink."""
    group_root = root.joinpath(*parts)
    version = group_root / timestamp
    version.mkdir(parents=True)
    shutil.copy2(REPO_CONFIGS / "schedule.yml", version / "schedule.yml")
    (group_root / "latest").symlink_to(timestamp, target_is_directory=True)
    return group_root


class _ConfigRootTestCase(unittest.TestCase):
    server_mode = ""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        env = {"TSCONFIG_SERVER_MODE": self.server_mode, "TSCONFIG_CONFIG_ROOT": str(self.root)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for var in ("OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_REDIRECT_URI"):
            os.environ.pop(var, None)
        self.loader = ConfigLoader()
        self.client = TestClient(app)


class TrackerModeTests(_ConfigRootTestCase):
    """Tracker mode ignores config groups entirely, even if a config root exists on disk."""

    server_mode = ""

    def setUp(self):
        super().setUp()
        _make_group(self.root, "flat")
        _make_group(self.root, "p1", "g1")
        # Tracker mode requires an operator login for requests that aren't local
        self.client.cookies = {SESSION_COOKIE: create_session_token()}

    def test_loader_ignores_config_groups(self):
        self.assertFalse(self.loader.is_server_mode())
        self.assertIsNone(self.loader.get_config_root())
        self.assertEqual(self.loader.list_config_groups(), [])
        self.assertEqual(self.loader.list_config_groups(project="p1"), [])
        self.assertIsNone(self.loader.get_config_group_dir("flat"))
        self.assertIsNone(self.loader.get_config_group_dir("p1/g1"))
        with self.assertRaises(ValueError):
            self.loader.create_versioned_config_dir("p1/g1")

    def test_server_mode_endpoint(self):
        response = self.client.get("/api/server-mode", params={"project": "p1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"enabled": False, "config_groups": [], "config_root": None, "oidc_enabled": False, "debug": None},
        )

    def test_home_does_not_validate_config_group(self):
        response = self.client.get("/", params={"config_group": "missing"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('window.INITIAL_CONFIG_GROUP = "missing"', response.text)

    def test_config_group_param_is_not_resolved(self):
        response = self.client.get("/api/schedule", params={"config_group": "flat"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "Config group 'flat' not found")


class ServerModeTests(_ConfigRootTestCase):
    """Server mode reads config groups from the nested {project}/{group} layout."""

    server_mode = "true"

    def setUp(self):
        super().setUp()
        _make_group(self.root, "p1", "default_audio")
        _make_group(self.root, "p1", "g1")
        _make_group(self.root, "p2", "g1")
        _make_group(self.root, "flat")  # Old flat layout: not a group anymore
        (self.root / "p2" / "nolatest" / "20260101_000000").mkdir(parents=True)
        (self.root / "p2" / "empty" / "20260101_000000").mkdir(parents=True)
        (self.root / "p2" / "empty" / "latest").symlink_to("20260101_000000", target_is_directory=True)

    def test_list_config_groups(self):
        self.assertEqual(self.loader.list_config_groups(), ["p1/default_audio", "p1/g1", "p2/g1"])
        self.assertEqual(self.loader.list_config_groups(project="p1"), ["p1/default_audio", "p1/g1"])
        self.assertEqual(self.loader.list_config_groups(project="p3"), [])

    def test_get_config_group_dir(self):
        self.assertEqual(self.loader.get_config_group_dir("p2/g1"), self.root / "p2" / "g1" / "latest")
        self.assertIsNone(self.loader.get_config_group_dir("p1/missing"))
        for key in ("flat", "g1", "p1/g1/latest", "../p1/g1", "p1/..", "p1/", "/g1", ""):
            with self.subTest(key=key):
                self.assertIsNone(self.loader.get_config_group_dir(key))

    def test_create_versioned_config_dir(self):
        versioned = self.loader.create_versioned_config_dir("p1/g1")
        group_root = self.root / "p1" / "g1"
        self.assertEqual(versioned.parent, group_root)
        latest = group_root / "latest"
        self.assertTrue(latest.is_symlink())
        self.assertEqual(os.readlink(latest), versioned.name)
        self.assertEqual(self.loader.get_config_group_dir("p1/g1"), latest)
        for key in ("g1", "../escape/g1", "p1/../g1"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.loader.create_versioned_config_dir(key)
        self.assertFalse((self.root.parent / "escape").exists())

    def test_server_mode_endpoint(self):
        data = self.client.get("/api/server-mode").json()
        self.assertEqual(data["config_groups"], ["p1/default_audio", "p1/g1", "p2/g1"])
        self.assertIn("p1/g1", data["debug"]["directory_details"])
        data = self.client.get("/api/server-mode", params={"project": "p2"}).json()
        self.assertEqual(data["config_groups"], ["p2/g1"])

    def test_home(self):
        response = self.client.get("/", params={"project": "p1", "config_group": "g1"})
        self.assertEqual(response.status_code, 200)
        self.assertIn('window.INITIAL_CONFIG_GROUP = "p1/g1"', response.text)

        response = self.client.get("/", params={"project": "p1", "config_group": "missing"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["available"], ["p1/default_audio", "p1/g1"])

        # Group in another project is not reachable through this project
        response = self.client.get("/", params={"project": "p2", "config_group": "default_audio"})
        self.assertEqual(response.status_code, 404)

        # Without project, no group is opened: the page lists the groups by project
        for params in ({}, {"config_group": "g1"}):
            with self.subTest(params=params):
                response = self.client.get("/", params=params)
                self.assertEqual(response.status_code, 200)
                self.assertIn('window.INITIAL_CONFIG_GROUP = ""', response.text)

    def test_save_creates_version_in_project_group(self):
        config = self.client.get("/api/schedule", params={"config_group": "p2/g1"}).json()
        response = self.client.put("/api/schedule", params={"config_group": "p2/g1"}, json=config)
        self.assertEqual(response.status_code, 200, response.text)
        versions = sorted(p.name for p in (self.root / "p2" / "g1").iterdir() if not p.is_symlink())
        self.assertEqual(len(versions), 2)
        self.assertEqual(os.readlink(self.root / "p2" / "g1" / "latest"), versions[-1])
        # Other project's group with the same id is untouched
        self.assertEqual(len(list((self.root / "p1" / "g1").iterdir())), 2)

    def test_old_flat_group_not_found(self):
        response = self.client.get("/api/schedule", params={"config_group": "flat"})
        self.assertEqual(response.status_code, 404)

    def test_deploy_proxies_to_project_endpoint(self):
        calls = []

        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, headers):
                calls.append((url, headers))
                return httpx.Response(200, json={"success": True, "deployed_count": 1})

        with (
            mock.patch.dict(os.environ, {"DEPLOYMENT_API_URL": "http://ecohub:8000"}),
            mock.patch("app.routers.deployment.httpx.AsyncClient", FakeClient),
        ):
            response = self.client.post("/api/deploy/p1/g1", headers={"Authorization": "Bearer tok"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"success": True, "deployed_count": 1})
            self.assertEqual(calls[0][0], "http://ecohub:8000/api/projects/p1/config-groups/g1/deploy")
            self.assertEqual(calls[0][1]["Authorization"], "Bearer tok")

            self.assertEqual(self.client.post("/api/deploy/g1").status_code, 404)
            self.assertEqual(self.client.post("/api/deploy/p1/g%2E%2E").status_code, 422)
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
