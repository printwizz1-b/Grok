import json
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import server


TINY_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _json(self, status, payload):
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.server.seen.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            }
        )
        mode = self.server.mode
        if mode == "redirect-api":
            self.send_response(302)
            self.send_header("Location", "https://example.com/steal")
            self.end_headers()
            return
        if mode == "unauthorized":
            self._json(401, {"error": {"message": "bad key"}})
            return
        if mode == "reject":
            self._json(400, {"error": {"message": "prompt was rejected"}})
            return
        self._json(200, {"request_id": "req-local-001"})

    def do_GET(self):
        if self.path == "/bounce.mp4":
            self.send_response(302)
            self.send_header("Location", self.server.bounce_to)
            self.end_headers()
            return
        if self.path == "/sample.mp4":
            data = self.server.mp4
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if not self.path.startswith("/v1/videos/"):
            self._json(404, {"error": "missing"})
            return
        request_id = self.path.rsplit("/", 1)[-1]
        count = self.server.polls.get(request_id, 0)
        self.server.polls[request_id] = count + 1
        mode = self.server.mode
        if mode == "fail":
            self._json(
                200,
                {"status": "failed", "error": {"code": "invalid_argument", "message": "prompt was rejected"}},
            )
            return
        if mode == "moderation":
            self._json(200, {"status": "done", "video": {"url": "", "respect_moderation": False, "duration": 1}})
            return
        if mode == "redirect-file":
            host, port = self.server.server_address
            self._json(
                200,
                {
                    "status": "done",
                    "progress": 100,
                    "video": {
                        "url": f"http://{host}:{port}/bounce.mp4",
                        "duration": 4,
                        "respect_moderation": True,
                    },
                },
            )
            return
        if mode == "evil-url":
            self._json(
                200,
                {
                    "status": "done",
                    "video": {
                        "url": "https://example.com/clip.mp4",
                        "duration": 4,
                        "respect_moderation": True,
                    },
                },
            )
            return
        if count == 0:
            self._json(200, {"status": "pending", "progress": 40})
            return
        host, port = self.server.server_address
        self._json(
            200,
            {
                "status": "done",
                "progress": 100,
                "model": "grok-imagine-video-1.5-lite",
                "video": {
                    "url": f"http://{host}:{port}/sample.mp4",
                    "duration": 4,
                    "respect_moderation": True,
                },
                "usage": {"cost_in_usd_ticks": 5_000_000_000},
            },
        )


def start_http(handler, extra):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    for key, value in extra.items():
        setattr(httpd, key, value)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def request(port, method, path, payload=None, api_key=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if api_key is not None:
        headers["X-Api-Key"] = api_key
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            body = response.read()
            return response.status, json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            parsed = {"error": raw.decode("utf-8", "replace")}
        return exc.code, parsed


class VideoDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.mp4 = Path(cls.tmpdir.name) / "sample.mp4"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x224466:s=160x90:d=1",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                str(cls.mp4),
            ],
            check=True,
        )
        cls.mp4_bytes = cls.mp4.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def setUp(self):
        self.videos = tempfile.TemporaryDirectory()
        self.fake = start_http(
            FakeHandler,
            {
                "seen": [],
                "polls": {},
                "mode": "ok",
                "mp4": self.mp4_bytes,
                "bounce_to": "https://example.com/clip.mp4",
            },
        )
        host, port = self.fake.server_address
        self.config = server.DeskConfig(
            api_base=f"http://{host}:{port}",
            videos_dir=Path(self.videos.name),
            port=0,
            open_browser=False,
        )
        self.desk = start_http_desk(self.config)

    def tearDown(self):
        self.desk.shutdown()
        self.desk.server_close()
        self.fake.shutdown()
        self.fake.server_close()
        self.videos.cleanup()

    @property
    def port(self):
        return self.desk.server_address[1]

    def test_page_and_health_stay_local(self):
        self.assertEqual(self.desk.server_address[0], "127.0.0.1")
        status, health = request(self.port, "GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertTrue(health["apiBase"].startswith("http://127.0.0.1:"))
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=5) as page:
            html = page.read().decode("utf-8")
        self.assertIn("Video Desk", html)
        self.assertIn("/app.js", html)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/styles.css", timeout=5) as style:
            css = style.read().decode("utf-8")
            self.assertEqual(style.status, 200)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/app.js", timeout=5) as script:
            self.assertIn("/api/generations", script.read().decode("utf-8"))
        self.assertIn(".stage", css)

    def test_missing_key_and_empty_prompt_are_rejected(self):
        status, body = request(self.port, "POST", "/api/generations", {"prompt": "A boat"})
        self.assertEqual(status, 400)
        self.assertIn("API key", body["error"])
        status, body = request(
            self.port,
            "POST",
            "/api/generations",
            {"prompt": "   "},
            api_key="test-key",
        )
        self.assertEqual(status, 400)
        self.assertIn("prompt", body["error"].lower())

    def test_generation_poll_saves_mp4(self):
        status, started = request(
            self.port,
            "POST",
            "/api/generations",
            {
                "prompt": "A red paper boat on a rainy street",
                "model": "grok-imagine-video-1.5-lite",
                "duration": 4,
                "aspectRatio": "16:9",
                "resolution": "720p",
            },
            api_key="Bearer test-key",
        )
        self.assertEqual(status, 200)
        self.assertEqual(started["requestId"], "req-local-001")
        sent = self.fake.seen[0]
        self.assertEqual(sent["auth"], "Bearer test-key")
        self.assertEqual(
            sent["body"],
            {
                "model": "grok-imagine-video-1.5-lite",
                "duration": 4,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "prompt": "A red paper boat on a rainy street",
            },
        )

        status, pending = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertEqual(status, 200)
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["progress"], 40)

        status, done = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertEqual(status, 200)
        self.assertEqual(done["status"], "done")
        self.assertEqual(done["costUsd"], 0.5)
        self.assertTrue(done["localUrl"].startswith("/library/"))
        saved = Path(self.videos.name) / done["fileName"]
        self.assertTrue(saved.is_file())
        self.assertEqual(saved.read_bytes(), self.mp4_bytes)

        status, library = request(self.port, "GET", "/api/library")
        self.assertEqual(library["videos"][0]["fileName"], done["fileName"])
        self.assertEqual(library["videos"][0]["prompt"], "A red paper boat on a rainy street")
        self.assertEqual(library["videos"][0]["duration"], 4)
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{done['localUrl']}", timeout=5) as video:
            self.assertEqual(video.headers.get("Content-Type"), "video/mp4")
            self.assertIsNone(video.headers.get("Content-Disposition"))
            self.assertEqual(video.read(), self.mp4_bytes)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{done['localUrl']}?download=1", timeout=5
        ) as video:
            disposition = video.headers.get("Content-Disposition")
            self.assertEqual(disposition, f'attachment; filename="{done["fileName"]}"')
            self.assertEqual(video.read(), self.mp4_bytes)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}{done['localUrl']}?download=0", timeout=5
        ) as video:
            self.assertIsNone(video.headers.get("Content-Disposition"))

        status, again = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertEqual(again["fileName"], done["fileName"])
        self.assertEqual(len(list(Path(self.videos.name).glob("*.mp4"))), 1)

    def test_image_prompt_is_forwarded_as_data_url(self):
        status, _started = request(
            self.port,
            "POST",
            "/api/generations",
            {"prompt": "Animate the still", "imageDataUrl": TINY_PNG, "duration": 2},
            api_key="test-key",
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.fake.seen[0]["body"]["image"]["url"], TINY_PNG)

    def test_upstream_failures_and_unsafe_urls(self):
        self.fake.mode = "fail"
        request(self.port, "POST", "/api/generations", {"prompt": "A boat"}, api_key="test-key")
        status, body = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"], "prompt was rejected")

        self.fake.mode = "moderation"
        status, body = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertIn("moderation", body["error"])
        self.assertNotIn("localUrl", body)

        self.fake.mode = "evil-url"
        status, body = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertIn("not on an xAI host", body["error"])
        self.assertEqual(list(Path(self.videos.name).glob("*.mp4")), [])

        status, body = request(self.port, "GET", "/library/../server.py")
        self.assertEqual(status, 404)

    def test_delete_removes_the_saved_clip(self):
        request(
            self.port,
            "POST",
            "/api/generations",
            {"prompt": "A boat to delete", "duration": 3},
            api_key="test-key",
        )
        request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        status, done = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertEqual(status, 200)
        name = done["fileName"]
        saved = Path(self.videos.name) / name
        self.assertTrue(saved.with_suffix(".json").is_file())

        status, body = request(self.port, "DELETE", f"/api/library/{name}")
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(saved.exists())
        self.assertFalse(saved.with_suffix(".json").exists())
        status, library = request(self.port, "GET", "/api/library")
        self.assertEqual(library["videos"], [])
        status, missing = request(self.port, "DELETE", "/api/library/../server.py")
        self.assertEqual(status, 404)
        self.assertIn("not found", missing["error"].lower())

    def test_redirects_are_not_followed_off_host(self):
        self.fake.mode = "redirect-api"
        status, body = request(
            self.port,
            "POST",
            "/api/generations",
            {"prompt": "A boat"},
            api_key="test-key",
        )
        self.assertEqual(status, 502)
        self.assertIn("redirect", body["error"].lower())
        self.assertEqual(len(self.fake.seen), 1)

        self.fake.mode = "ok"
        request(self.port, "POST", "/api/generations", {"prompt": "A boat"}, api_key="test-key")
        self.fake.mode = "redirect-file"
        self.fake.bounce_to = "https://example.com/clip.mp4"
        status, body = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertEqual(status, 200)
        self.assertIn("redirected away", body["error"].lower())
        self.assertEqual(list(Path(self.videos.name).glob("*.mp4")), [])

        host, port = self.fake.server_address
        self.fake.bounce_to = f"http://{host}:{port}/sample.mp4"
        status, done = request(self.port, "GET", "/api/videos/req-local-001", api_key="test-key")
        self.assertEqual(status, 200)
        self.assertTrue(done["localUrl"].startswith("/library/"))
        self.assertTrue((Path(self.videos.name) / done["fileName"]).is_file())

    def test_rejected_key_does_not_start_a_job(self):
        self.fake.mode = "unauthorized"
        status, body = request(
            self.port,
            "POST",
            "/api/generations",
            {"prompt": "A boat"},
            api_key="nope",
        )
        self.assertEqual(status, 401)
        self.assertIn("API key", body["error"])


def start_http_desk(config):
    httpd = server.create_server(config)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


class BodyValidationTests(unittest.TestCase):
    def test_duration_and_model_limits(self):
        with self.assertRaises(ValueError):
            server.generation_body({"prompt": "hello", "duration": 0})
        with self.assertRaises(ValueError):
            server.generation_body({"prompt": "hello", "duration": 16})
        with self.assertRaises(ValueError):
            server.generation_body({"prompt": "hello", "model": "other"})
        body = server.generation_body({"imageDataUrl": TINY_PNG})
        self.assertNotIn("prompt", body)
        self.assertEqual(body["image"]["url"], TINY_PNG)

    def test_video_hosts(self):
        official = server.DeskConfig()
        self.assertTrue(server.video_url_allowed("https://vidgen.x.ai/clip.mp4", official))
        self.assertFalse(server.video_url_allowed("https://example.com/clip.mp4", official))
        self.assertFalse(server.video_url_allowed("http://vidgen.x.ai/clip.mp4", official))
        local = server.DeskConfig(api_base="http://127.0.0.1:9")
        self.assertTrue(server.video_url_allowed("http://127.0.0.1:9/sample.mp4", local))
        self.assertFalse(server.video_url_allowed("http://127.0.0.1:10/sample.mp4", local))


if __name__ == "__main__":
    unittest.main()
