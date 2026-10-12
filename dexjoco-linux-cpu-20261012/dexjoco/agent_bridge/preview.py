"""Loopback-only, authenticated, read-only preview of cached simulation frames.
No request handler or encoder thread may access MuJoCo or the Runner.
"""
from __future__ import annotations
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlsplit

from task_registry import DEFAULT_TASK, require_supported

# Even the fallback/default preview uses official click_mouse policy inputs.
CAMERAS = tuple(require_supported(DEFAULT_TASK)["camera_keys"])
ALLOWED_CAMERAS = ("ego_right", "wrist", "front", "ego_left", "ego", "wrist_left", "wrist_right")
MAX_CLIENTS = 12


class Preview:
    def __init__(self, port=8766, fps=8.0, cameras=CAMERAS):
        if not 1 <= port <= 65535 or not 1 <= fps <= 20:
            raise ValueError("preview port must be 1..65535 and fps 1..20")
        self.cameras = tuple(cameras)
        if not self.cameras or len(set(self.cameras)) != len(self.cameras) or any(key not in ALLOWED_CAMERAS for key in self.cameras):
            raise ValueError("Preview cameras must be a nonempty subset of the allowlist")
        self.port, self.fps = int(port), float(fps)
        self.token = secrets.token_urlsafe(32)
        self.cookie_name = f"dexjoco_preview_{self.port}"
        self.condition = threading.Condition()
        self.clients = threading.BoundedSemaphore(MAX_CLIENTS)
        self.closed = False
        self.pending = None
        self.images = {}
        self.status = {}
        self.frame_sequence = 0
        self.last_publish = 0.0
        self.html = Path(__file__).with_name("preview.html").read_bytes()
        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), self._handler())
        self.server.daemon_threads = True
        self.encoder = threading.Thread(target=self._encode, daemon=True, name="preview-encoder")
        self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True,
                                            name="preview-http")
        self.encoder.start()
        self.http_thread.start()

    def descriptor(self):
        # This capability URL is returned only by the protected SSH command socket.
        return {"enabled": True, "read_only": True, "bind": "127.0.0.1",
                "port": self.port, "target_fps": self.fps, "camera_keys": self.cameras,
                "url": f"http://127.0.0.1:{self.port}/?token={self.token}",
                "authentication": "per-run capability URL then HttpOnly cookie"}

    def publish(self, frames, status, force=False):
        now = time.monotonic()
        with self.condition:
            if self.closed:
                return
            # Keep the metadata of the currently cached JPEG across state-only updates.
            image_meta = {key: self.status[key] for key in
                          ("image_step_count", "image_seed", "frame_wall_time", "encoder_error")
                          if key in self.status}
            self.status = {**status, **image_meta}
            self.status["target_fps"] = self.fps
            self.status["read_only"] = True
            self.status["camera_keys"] = self.cameras
            self.status["frame_sequence"] = self.frame_sequence
            if force or now - self.last_publish >= 1.0 / self.fps:
                # Only the simulation thread calls publish. Arrays are copied so
                # MuJoCo/observation-buffer reuse cannot race with JPEG encoding.
                self.pending = ({key: frames[key].copy() for key in self.cameras},
                                status["step_count"], status["seed"], now)
                self.last_publish = now
                self.condition.notify_all()

    def _encode(self):
        from PIL import Image, ImageDraw
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self.closed or self.pending is not None)
                if self.closed:
                    return
                frames, step, seed, created = self.pending
                self.pending = None
            try:
                images = {}
                board = Image.new("RGB", (960, 505 * ((len(self.cameras) + 1) // 2)), (20, 25, 35))
                for index, key in enumerate(self.cameras):
                    image = Image.fromarray(frames[key])
                    out = BytesIO()
                    image.save(out, format="JPEG", quality=82)
                    images[key] = out.getvalue()
                    tile = Image.new("RGB", (480, 505), (20, 25, 35))
                    tile.paste(image.resize((480, 480)), (0, 25))
                    ImageDraw.Draw(tile).text((8, 5), key, fill="white")
                    board.paste(tile, ((index % 2) * 480, (index // 2) * 505))
                out = BytesIO()
                board.save(out, format="JPEG", quality=82)
                images["grid"] = out.getvalue()
                with self.condition:
                    if self.closed:
                        return
                    self.images = images
                    self.frame_sequence += 1
                    self.status.update(frame_sequence=self.frame_sequence,
                                       image_step_count=step, image_seed=seed,
                                       frame_wall_time=time.time(), encoder_error=None)
                    self.condition.notify_all()
            except Exception as exc:
                with self.condition:
                    self.status["encoder_error"] = str(exc)

    def snapshot(self):
        with self.condition:
            return dict(self.status)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        self.server.shutdown()
        self.server.server_close()
        self.encoder.join(timeout=5)
        self.http_thread.join(timeout=5)

    def _handler(self):
        preview = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self):
                super().setup()
                self.connection.settimeout(8)

            def log_message(self, *args):
                # Never write capability tokens or browser access logs to disk.
                pass

            def response_headers(self, content_type, length=None):
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy",
                                 "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                 "style-src 'self' 'unsafe-inline'; img-src 'self' blob:; "
                                 "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                if length is not None:
                    self.send_header("Content-Length", str(length))

            def reply(self, code, body, content_type="text/plain; charset=utf-8", head=False):
                self.send_response(code)
                self.response_headers(content_type, len(body))
                self.end_headers()
                if not head:
                    self.wfile.write(body)

            def authenticated(self):
                if self.headers.get("Sec-Fetch-Site") == "cross-site":
                    return False
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                    value = cookie.get(preview.cookie_name)
                    return value is not None and secrets.compare_digest(value.value, preview.token)
                except Exception:
                    return False

            def do_GET(self):
                self.handle_read()

            def do_HEAD(self):
                self.handle_read(head=True)

            def handle_read(self, head=False):
                parsed = urlsplit(self.path)
                query = parse_qs(parsed.query)
                token = query.get("token", [""])[0]
                if (parsed.path == "/" and token
                        and self.headers.get("Sec-Fetch-Site") != "cross-site"
                        and secrets.compare_digest(token.encode(), preview.token.encode())):
                    self.send_response(303)
                    self.response_headers("text/plain", 0)
                    self.send_header("Location", "/")
                    self.send_header("Set-Cookie", f"{preview.cookie_name}={preview.token}; "
                                     "Path=/; HttpOnly; SameSite=Strict")
                    self.end_headers()
                    return
                if not self.authenticated():
                    self.reply(401, b"Open the authenticated preview URL returned by bridge status.", head=head)
                    return
                if parsed.path == "/":
                    self.reply(200, preview.html, "text/html; charset=utf-8", head=head)
                elif parsed.path == "/status":
                    data = json.dumps(preview.snapshot(), allow_nan=False).encode()
                    self.reply(200, data, "application/json", head=head)
                elif parsed.path in ("/frame.jpg", "/stream"):
                    camera = query.get("camera", ["grid"])[0]
                    if camera not in (*preview.cameras, "grid"):
                        self.reply(400, b"Unknown camera", head=head)
                        return
                    if parsed.path == "/frame.jpg":
                        with preview.condition:
                            frame = preview.images.get(camera)
                        if frame is None:
                            self.reply(503, b"Waiting for first frame", head=head)
                        else:
                            self.reply(200, frame, "image/jpeg", head=head)
                    elif head:
                        self.reply(200, b"", "multipart/x-mixed-replace; boundary=dexjoco", head=True)
                    else:
                        self.stream(camera)
                else:
                    # No filesystem serving, command endpoints, or arbitrary paths.
                    self.reply(404, b"Not found", head=head)

            def stream(self, camera):
                if not preview.clients.acquire(blocking=False):
                    self.reply(503, b"Too many preview streams")
                    return
                try:
                    self.send_response(200)
                    self.response_headers("multipart/x-mixed-replace; boundary=dexjoco")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True
                    previous = -1
                    last_sent = 0.0
                    while not preview.closed:
                        with preview.condition:
                            preview.condition.wait_for(
                                lambda: preview.closed or preview.frame_sequence != previous,
                                timeout=1.0)
                            if preview.closed:
                                break
                            frame = preview.images.get(camera)
                            sequence = preview.frame_sequence
                        if frame is None:
                            continue
                        now = time.monotonic()
                        if sequence == previous and now - last_sent < 3.0:
                            continue
                        # Slow clients skip intermediate frames; never touch the
                        # simulator, request its socket, or build an unbounded queue.
                        self.wfile.write(b"--dexjoco\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
                        self.wfile.flush()
                        previous, last_sent = sequence, now
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                finally:
                    preview.clients.release()

            def deny_write(self):
                self.close_connection = True
                self.reply(405, b"Read-only preview: only GET and HEAD are allowed.")

            do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = deny_write

        return Handler
