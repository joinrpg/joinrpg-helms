"""Проверки синхронизации без обращения к настоящему Coroot."""

import importlib.util
import json
import io
from pathlib import Path
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "sync_dashboard", Path(__file__).with_name("sync-dashboard.py")
)
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)


def dashboard():
    return {"name": "Тест", "description": "Описание", "config": {"groups": []}}


class FakeClient:
    def __init__(self, entries=None, config=None):
        self.entries = entries or []
        self.config = config or {"groups": []}
        self.calls = []

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "GET":
            if path.endswith("/dashboards"):
                return {"data": self.entries}
            return {"data": {"config": self.config}}
        if payload["action"] == "create":
            self.entries = [{"id": "new-id", **dashboard()}]
            return "new-id\n"
        if payload["action"] == "save":
            self.config = payload["config"]
        return ""


class SyncTests(unittest.TestCase):
    def test_password_from_environment_does_not_prompt_or_print(self):
        arguments = ["--url", "https://coroot.example", "--project", "project1",
                     "--machine-id", "a" * 32]
        with patch.dict("os.environ", {"COROOT_USER": "editor",
                                      "COROOT_PASSWORD": "secret-password"}, clear=True), \
                patch.object(sync, "Client") as client, \
                patch.object(sync.getpass, "getpass") as prompt, \
                patch.object(sync, "synchronize", return_value=(None, "План")), \
                patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(sync.main(arguments), 0)
            prompt.assert_not_called()
            client.return_value.request.assert_called_once_with(
                "POST", "/api/login", {"email": "editor", "password": "secret-password"})
            self.assertNotIn("secret-password", output.getvalue())

    def test_empty_password_uses_hidden_input(self):
        with patch.dict("os.environ", {"COROOT_PASSWORD": ""}, clear=True), \
                patch.object(sync, "Client"), \
                patch.object(sync.getpass, "getpass", return_value="typed-password") as prompt, \
                patch.object(sync, "synchronize", return_value=(None, "План")), \
                patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(sync.main(["--url", "https://coroot.example",
                                       "--project", "project1", "--machine-id", "a" * 32]), 0)
            prompt.assert_called_once()

    def test_preview_never_writes(self):
        client = FakeClient()
        sync.synchronize(client, "project1", dashboard(), apply=False)
        self.assertTrue(all(c[0] == "GET" for c in client.calls))

    def test_create_and_verify(self):
        client = FakeClient()
        sync.synchronize(client, "project1", dashboard(), apply=True)
        self.assertEqual([c[2]["action"] for c in client.calls if c[0] == "POST"],
                         ["create", "save"])
        self.assertEqual(client.calls[-1][0], "GET")

    def test_repeat_does_not_write(self):
        client = FakeClient([{"id": "existing", **dashboard()}])
        sync.synchronize(client, "project1", dashboard(), apply=True)
        self.assertFalse(any(c[0] == "POST" for c in client.calls))

    def test_duplicates_fail_before_writes(self):
        client = FakeClient([{"id": "a", **dashboard()}, {"id": "b", **dashboard()}])
        with self.assertRaises(sync.SyncError):
            sync.synchronize(client, "project1", dashboard(), apply=True)
        self.assertFalse(any(c[0] == "POST" for c in client.calls))

    def test_backup_precedes_update(self):
        old = {"groups": [{"name": "Старая группа", "panels": []}]}
        client = FakeClient([{"id": "existing", **dashboard()}], old)
        with tempfile.TemporaryDirectory() as temp:
            sync.synchronize(client, "project1", dashboard(), apply=True,
                             backup_dir=Path(temp))
            saved = list(Path(temp).glob("*.json"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(json.loads(saved[0].read_text(encoding="utf-8"))["config"], old)

    def test_failed_verification_is_not_success(self):
        class BrokenClient(FakeClient):
            def request(self, method, path, payload=None):
                result = super().request(method, path, payload)
                if method == "GET" and not path.endswith("/dashboards"):
                    return {"data": {"config": {"wrong": True}}}
                return result
        with self.assertRaises(sync.SyncError):
            sync.synchronize(BrokenClient(), "project1", dashboard(), apply=True)

    def test_invalid_server_id_cannot_change_route(self):
        client = FakeClient([{"id": "../users", **dashboard()}])
        with self.assertRaises(sync.SyncError):
            sync.synchronize(client, "project1", dashboard(), apply=True)

    def test_backup_failure_prevents_writes(self):
        client = FakeClient([{"id": "existing", **dashboard()}], {"groups": [{"name": "old"}]})
        with tempfile.TemporaryDirectory() as temp:
            not_directory = Path(temp) / "file"
            not_directory.write_text("existing file", encoding="utf-8")
            with self.assertRaises(OSError):
                sync.synchronize(client, "project1", dashboard(), apply=True,
                                 backup_dir=not_directory)
        self.assertFalse(any(c[0] == "POST" for c in client.calls))

    def test_key_with_newline_is_rejected(self):
        with self.assertRaises(sync.SyncError):
            sync.Client("https://coroot.example", token="key\nheader")

    def test_render_template_preserves_legends(self):
        path = Path(__file__).parents[1] / "dashboards" / "mssql.json"
        result = sync.load_dashboard(path, "a" * 32)
        text = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("__MACHINE_ID__", text)
        self.assertIn("{{container_id}}", text)
        self.assertNotIn("or vector(0)", text)

    def test_https_and_loopback_only(self):
        self.assertEqual(sync.validate_url("https://coroot.example/"),
                         "https://coroot.example")
        self.assertEqual(sync.validate_url("http://127.0.0.1:8080"),
                         "http://127.0.0.1:8080")
        for url in ("http://external.example", "https://user:secret@example.org",
                    "https://example.org?token=secret", "https://example.org/#secret"):
            with self.subTest(url=url), self.assertRaises(sync.SyncError):
                sync.validate_url(url)

    def test_http_protocol_and_safe_errors(self):
        requests = []
        cookies = []
        state = {"config": {"groups": []}, "created": False}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                requests.append(("GET", self.path))
                cookies.append(self.headers.get("Cookie"))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "https://untrusted.invalid/")
                    self.end_headers()
                    return
                if self.path == "/error":
                    self.send_response(403)
                    self.end_headers()
                    self.wfile.write(b"secret-marker")
                    return
                body = ([{"id": "http-id", **dashboard()}] if state["created"] else [])
                if self.path.endswith("/http-id"):
                    body = {"config": state["config"]}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({"data": body}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(("POST", self.path, body))
                if self.path == "/api/login":
                    self.send_response(200)
                    self.send_header("Set-Cookie", "coroot_session=test-session; Path=/; HttpOnly")
                    self.end_headers()
                    return
                if body["action"] == "create":
                    state["created"] = True
                    self.send_response(201)
                    self.end_headers()
                    self.wfile.write(b"http-id\n")
                else:
                    state["config"] = body["config"]
                    self.send_response(200)
                    self.end_headers()

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                client = sync.Client(f"http://127.0.0.1:{server.server_port}", token="test-key")
                sync.synchronize(client, "project1", dashboard(), apply=True)
                self.assertEqual(requests[1][1], "/api/project/project1/dashboards")
                self.assertEqual(requests[2][1], "/api/project/project1/dashboards/http-id")
                with self.assertRaises(sync.SyncError):
                    client.request("GET", "/redirect")
                with self.assertRaises(sync.SyncError) as error:
                    client.request("GET", "/error")
                self.assertNotIn("secret-marker", str(error.exception))
                self.assertIn("403", str(error.exception))
                cookie_client = sync.Client(f"http://127.0.0.1:{server.server_port}")
                cookie_client.request("POST", "/api/login", {"email": "admin", "password": "test-password"})
                cookie_client.request("GET", "/api/project/project1/dashboards")
                self.assertEqual(cookies[-1], "coroot_session=test-session")
            finally:
                server.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
