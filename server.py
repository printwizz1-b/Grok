#!/usr/bin/env python3
"""Video Desk: a local browser app for the official xAI video API.

The page is served only on 127.0.0.1. The API key is forwarded to
https://api.x.ai and is not written to disk.
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DEFAULT_API_BASE = "https://api.x.ai"
HOST = "127.0.0.1"

MODELS = ("grok-imagine-video-1.5-lite", "grok-imagine-video-1.5")
ASPECT_RATIOS = ("1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9", "5:2")
RESOLUTIONS = ("480p", "720p", "1080p")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
DATA_URL_RE = re.compile(r"^data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\s]+)$")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,180}\.mp4$")
USD_TICKS = 10_000_000_000
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_PROMPT = 4000


@dataclass(frozen=True)
class DeskConfig:
    api_base: str = DEFAULT_API_BASE
    videos_dir: Path = ROOT / "videos"
    port: int = 8787
    open_browser: bool = True


def config_from_env() -> DeskConfig:
    port_text = os.environ.get("DESK_PORT", "8787")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise SystemExit(f"DESK_PORT must be a number, got {port_text!r}.") from exc
    videos = Path(os.environ.get("DESK_VIDEOS_DIR", ROOT / "videos"))
    return DeskConfig(
        api_base=os.environ.get("DESK_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        videos_dir=videos,
        port=port,
        open_browser=os.environ.get("DESK_OPEN_BROWSER", "1") != "0",
    )


def clean_api_key(value: str | None) -> str:
    if value is None:
        raise ValueError("Enter your xAI API key.")
    text = value.strip()
    if text.lower().startswith("bearer "):
        text = text[7:].strip()
    if not text or any(char in text for char in "\r\n\x00"):
        raise ValueError("Enter your xAI API key.")
    if len(text) > 500:
        raise ValueError("That API key is too long.")
    return text


def _as_int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or isinstance(value, str) and not value.strip().lstrip("-").isdigit():
        raise ValueError(f"{name} must be a whole number from {low} to {high}.")
    if isinstance(value, str):
        number = int(value.strip())
    elif isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    else:
        raise ValueError(f"{name} must be a whole number from {low} to {high}.")
    if number < low or number > high:
        raise ValueError(f"{name} must be from {low} to {high}.")
    return number


def normalize_image_data_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("The starting image must be a PNG, JPEG, or WebP file.")
    match = DATA_URL_RE.match(value.strip())
    if not match:
        raise ValueError("The starting image must be a PNG, JPEG, or WebP file.")
    payload = re.sub(r"\s+", "", match.group(2))
    try:
        decoded_len = (len(payload) * 3) // 4
        padding = payload.count("=")
        decoded_len -= padding
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError("The starting image could not be read.") from exc
    if decoded_len <= 0 or decoded_len > MAX_IMAGE_BYTES:
        raise ValueError("The starting image must be under 6 MB.")
    subtype = match.group(1)
    return f"data:image/{subtype};base64,{payload}"


def generation_body(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("The request was not valid JSON.")
    prompt = data.get("prompt", "")
    if prompt is None:
        prompt = ""
    if not isinstance(prompt, str):
        raise ValueError("The prompt must be text.")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT:
        raise ValueError(f"Keep the prompt under {MAX_PROMPT} characters.")

    image = data.get("imageDataUrl")
    image_url = normalize_image_data_url(image) if image else None
    if not prompt and not image_url:
        raise ValueError("Write a prompt, or choose a starting image.")

    model = data.get("model") or MODELS[0]
    if model not in MODELS:
        raise ValueError("Choose one of the Grok Imagine video models.")
    aspect = data.get("aspectRatio") or "16:9"
    if aspect not in ASPECT_RATIOS:
        raise ValueError("Choose a supported aspect ratio.")
    resolution = data.get("resolution") or "720p"
    if resolution not in RESOLUTIONS:
        raise ValueError("Choose 480p, 720p, or 1080p.")
    duration = _as_int(data.get("duration", 6), "Duration", 1, 15)

    body: dict = {
        "model": model,
        "duration": duration,
        "aspect_ratio": aspect,
        "resolution": resolution,
    }
    if prompt:
        body["prompt"] = prompt
    if image_url:
        body["image"] = {"url": image_url}
    return body


def error_text(payload: object, fallback: str) -> str:
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            message = err.get("message") or err.get("code")
            if message:
                return str(message)
        if isinstance(err, str) and err.strip():
            return err.strip()
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return fallback


def cost_usd(payload: dict) -> float | None:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    ticks = usage.get("cost_in_usd_ticks")
    if isinstance(ticks, bool) or not isinstance(ticks, int):
        return None
    return round(ticks / USD_TICKS, 4)


def _origin_port(parsed) -> int:
    if parsed.port:
        return parsed.port
    return 443 if parsed.scheme == "https" else 80


def video_url_allowed(url: str, config: DeskConfig) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    scheme = (parsed.scheme or "").lower()
    if not host or scheme not in {"http", "https"}:
        return False
    if scheme == "https" and (host == "x.ai" or host.endswith(".x.ai")):
        return True
    api = urlparse(config.api_base)
    api_host = (api.hostname or "").lower()
    same_origin = scheme == (api.scheme or "").lower() and host == api_host and _origin_port(parsed) == _origin_port(api)
    return same_origin


def safe_library_name(name: str) -> str | None:
    if FILE_NAME_RE.match(name or ""):
        return name
    return None


class VideoDesk:
    def __init__(self, config: DeskConfig) -> None:
        self.config = config
        self._download_lock = threading.Lock()
        self.config.videos_dir.mkdir(parents=True, exist_ok=True)

    def start_generation(self, api_key: str, data: dict) -> dict:
        body = generation_body(data)
        status, payload = self._xai_json(
            "POST",
            "/v1/videos/generations",
            api_key,
            body,
            timeout=60,
        )
        if status >= 400:
            raise DeskError(status, error_text(payload, "xAI rejected the video request."))
        request_id = payload.get("request_id") if isinstance(payload, dict) else None
        if not isinstance(request_id, str) or not REQUEST_ID_RE.match(request_id):
            raise DeskError(502, "xAI did not return a video request id.")
        return {"requestId": request_id}

    def poll(self, api_key: str, request_id: str) -> dict:
        if not REQUEST_ID_RE.match(request_id or ""):
            raise DeskError(400, "That video request id is not valid.")
        status, payload = self._xai_json(
            "GET",
            f"/v1/videos/{request_id}",
            api_key,
            None,
            timeout=30,
        )
        if status >= 400:
            raise DeskError(status, error_text(payload, "xAI could not check this video."))
        if not isinstance(payload, dict):
            raise DeskError(502, "xAI returned an unexpected status response.")
        return self._present(request_id, payload)

    def library(self) -> list[dict]:
        items = []
        for path in self.config.videos_dir.glob("*.mp4"):
            if not safe_library_name(path.name):
                continue
            stat = path.stat()
            items.append(
                {
                    "fileName": path.name,
                    "localUrl": f"/library/{path.name}",
                    "bytes": stat.st_size,
                    "modified": int(stat.st_mtime),
                }
            )
        items.sort(key=lambda item: item["modified"], reverse=True)
        return items

    def library_file(self, name: str) -> Path | None:
        safe = safe_library_name(name)
        if not safe:
            return None
        path = (self.config.videos_dir / safe).resolve()
        if path.parent != self.config.videos_dir.resolve() or not path.is_file():
            return None
        return path

    def _present(self, request_id: str, payload: dict) -> dict:
        result = {
            "requestId": request_id,
            "status": payload.get("status") or "pending",
            "progress": payload.get("progress"),
            "model": payload.get("model"),
        }
        price = cost_usd(payload)
        if price is not None:
            result["costUsd"] = price
        state = result["status"]
        if state == "failed":
            result["error"] = error_text(payload, "Video generation failed.")
            return result
        if state != "done":
            return result

        video = payload.get("video") if isinstance(payload.get("video"), dict) else {}
        if video.get("respect_moderation") is False or not video.get("url"):
            result["error"] = "xAI finished without a video. The prompt may have been blocked by moderation."
            return result
        remote = video.get("url")
        if not isinstance(remote, str) or not video_url_allowed(remote, self.config):
            result["error"] = "The finished video URL is not on an xAI host, so it was not saved."
            return result
        try:
            saved = self._save_video(request_id, remote)
        except DeskError as exc:
            result["error"] = exc.message
            return result
        result["fileName"] = saved.name
        result["localUrl"] = f"/library/{saved.name}"
        result["duration"] = video.get("duration")
        return result

    def _save_video(self, request_id: str, remote: str) -> Path:
        with self._download_lock:
            existing = sorted(self.config.videos_dir.glob(f"*-{request_id}.mp4"))
            if existing:
                return existing[0]
            status, data, content_type = self._download(remote)
            if status >= 400:
                raise DeskError(502, "xAI did not provide the video file.")
            if not _looks_like_mp4(data, content_type):
                raise DeskError(502, "xAI did not return an MP4 file.")
            stamp = time.strftime("%Y%m%d-%H%M%S")
            destination = self.config.videos_dir / f"{stamp}-{request_id}.mp4"
            temporary = destination.with_suffix(".mp4.part")
            temporary.write_bytes(data)
            temporary.replace(destination)
            return destination

    def _download(self, url: str) -> tuple[int, bytes, str]:
        request = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                content_type = response.headers.get("Content-Type", "")
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > MAX_VIDEO_BYTES:
                    raise DeskError(502, "The video file is larger than 200 MB.")
                chunks = []
                total = 0
                while True:
                    block = response.read(1024 * 256)
                    if not block:
                        break
                    total += len(block)
                    if total > MAX_VIDEO_BYTES:
                        raise DeskError(502, "The video file is larger than 200 MB.")
                    chunks.append(block)
                return response.status, b"".join(chunks), content_type
        except DeskError:
            raise
        except urllib.error.HTTPError as exc:
            body = exc.read(64 * 1024)
            return exc.code, body, exc.headers.get("Content-Type", "")
        except urllib.error.URLError as exc:
            raise DeskError(502, f"The video file could not be downloaded ({exc.reason}).") from exc

    def _xai_json(
        self,
        method: str,
        path: str,
        api_key: str,
        body: dict | None,
        timeout: int,
    ) -> tuple[int, object]:
        url = f"{self.config.api_base}{path}"
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_JSON_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read(MAX_JSON_BYTES + 1)
            status = exc.code
        except urllib.error.URLError as exc:
            raise DeskError(502, f"Could not reach the xAI API ({exc.reason}).") from exc
        if len(raw) > MAX_JSON_BYTES:
            raise DeskError(502, "The xAI response was too large.")
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if status == 401:
            raise DeskError(401, "xAI rejected this API key.")
        return status, payload


class DeskError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _looks_like_mp4(data: bytes, content_type: str) -> bool:
    if len(data) < 12:
        return False
    media = content_type.split(";")[0].strip().lower()
    if media.startswith("text/") or media in {"text/html", "application/json"}:
        return False
    return b"ftyp" in data[:64]


class DeskHandler(BaseHTTPRequestHandler):
    server_version = "VideoDesk/1.0"

    @property
    def desk(self) -> VideoDesk:
        return self.server.desk  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        print(f"[desk] {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "apiBase": self.desk.config.api_base,
                    "models": list(MODELS),
                },
            )
            return
        if path == "/api/library":
            self._json(200, {"videos": self.desk.library()})
            return
        if path.startswith("/api/videos/"):
            self._poll(unquote(path.removeprefix("/api/videos/")))
            return
        if path.startswith("/library/"):
            self._library_file(unquote(path.removeprefix("/library/")))
            return
        self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path != "/api/generations":
            self._json(404, {"error": "Not found."})
            return
        try:
            api_key = clean_api_key(self.headers.get("X-Api-Key"))
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_JSON_BYTES:
                raise DeskError(413, "That request is too large.")
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise DeskError(400, "The request was not valid JSON.") from exc
            result = self.desk.start_generation(api_key, data)
        except DeskError as exc:
            self._json(exc.status, {"error": exc.message})
            return
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, result)

    def _poll(self, request_id: str) -> None:
        try:
            api_key = clean_api_key(self.headers.get("X-Api-Key"))
            result = self.desk.poll(api_key, request_id)
        except DeskError as exc:
            self._json(exc.status, {"error": exc.message})
            return
        except ValueError as exc:
            self._json(400, {"error": str(exc)})
            return
        self._json(200, result)

    def _library_file(self, name: str) -> None:
        path = self.desk.library_file(name)
        if path is None:
            self._json(404, {"error": "That saved video was not found."})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        if relative.startswith("/") or ".." in Path(relative).parts:
            self._json(404, {"error": "Not found."})
            return
        file_path = (STATIC_DIR / relative).resolve()
        if file_path.parent != STATIC_DIR.resolve() and STATIC_DIR.resolve() not in file_path.parents:
            self._json(404, {"error": "Not found."})
            return
        if not file_path.is_file():
            self._json(404, {"error": "Not found."})
            return
        media = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", media)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; media-src 'self'; "
            "style-src 'self'; script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'",
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)


class DeskServer(ThreadingHTTPServer):
    def __init__(self, config: DeskConfig, desk: VideoDesk) -> None:
        self.desk = desk
        super().__init__((HOST, config.port), DeskHandler)


def create_server(config: DeskConfig) -> DeskServer:
    desk = VideoDesk(config)
    return DeskServer(config, desk)


def main() -> None:
    config = config_from_env()
    try:
        server = create_server(config)
    except OSError as exc:
        raise SystemExit(
            f"Could not listen on {HOST}:{config.port} ({exc.strerror}). "
            "Close the other Video Desk window, or set DESK_PORT to a free port."
        ) from exc
    url = f"http://{HOST}:{server.server_address[1]}"
    print(f"Video Desk is running at {url}")
    print("Leave this window open while you use it. Ctrl+C stops it.")
    print("The API key stays in the browser and is sent only to the xAI API.")
    if config.open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.shutdown()


if __name__ == "__main__":
    main()
