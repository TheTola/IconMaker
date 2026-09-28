"""Subscription-backed image generation through the local Codex App Server."""

from __future__ import annotations

import base64
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path

from PySide6 import QtCore, QtGui

from ImageGenerationBrief import codex_generation_request

import GenLog


def find_codex_executable() -> str | None:
    executable = shutil.which("codex")
    if executable:
        return executable
    if os.name != "nt":
        return None
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    bin_dir = Path(local_app_data) / "OpenAI" / "Codex" / "bin"
    candidates = [path for path in bin_dir.glob("*/codex.exe") if path.is_file()]
    direct = bin_dir / "codex.exe"
    if direct.is_file():
        candidates.append(direct)
    return str(max(candidates, key=lambda path: path.stat().st_mtime)) if candidates else None


class CodexImageService(QtCore.QObject):
    connectionChanged = QtCore.Signal(str)
    availabilityChanged = QtCore.Signal(bool)
    busyChanged = QtCore.Signal(bool)
    progress = QtCore.Signal(str)
    signInUrl = QtCore.Signal(str)
    imageReady = QtCore.Signal(str)
    failed = QtCore.Signal(str)
    cancellationConfirmed = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._workspace = tempfile.TemporaryDirectory(prefix="iconmaker-image-")
        self._process = QtCore.QProcess(self)
        self._process.setProcessChannelMode(QtCore.QProcess.SeparateChannels)
        self._process.setWorkingDirectory(self._workspace.name)
        self._process.started.connect(self._on_started)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._drain_stderr)
        self._process.errorOccurred.connect(self._on_process_error)
        self._process.finished.connect(self._on_process_finished)
        self._buffer = bytearray()
        self._callbacks: dict[int, object] = {}
        self._request_timers: dict[int, QtCore.QTimer] = {}
        self._next_id = 1
        self._connected = False
        self._account_checked = False
        self._auth_type: str | None = None
        self._plan_type: str | None = None
        self._image_capability: bool | None = None
        self._capability_error = False
        self._capability_reason = ""
        self._busy: str | None = None
        self._login_id: str | None = None
        self._thread_id: str | None = None
        self._turn_id: str | None = None
        self._image_path: Path | None = None
        self._reference_images: tuple[Path, ...] = ()
        self._image_failure: str | None = None
        self._cancel_requested = False
        self._stopping = False
        self._disconnect_reported = False

    @property
    def is_busy(self) -> bool:
        return self._busy is not None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def account_checked(self) -> bool:
        return self._account_checked

    @property
    def capability_checked(self) -> bool:
        return self._image_capability is not None or self._capability_error

    @property
    def is_chatgpt_signed_in(self) -> bool:
        return self._connected and self._account_checked and self._auth_type == "chatgpt"

    @property
    def is_ready(self) -> bool:
        return self.is_chatgpt_signed_in and self._image_capability is True

    def start(self) -> None:
        executable = find_codex_executable()
        if not executable:
            self.connectionChanged.emit("Codex CLI was not found. Install Codex or add codex to PATH.")
            return
        self.connectionChanged.emit("Connecting to Codex...")
        self._process.start(executable, ["app-server", "--stdio"])

    def _send(self, method: str, params: dict, callback=None) -> None:
        message: dict = {"method": method, "params": params}
        if callback is not None:
            request_id = self._next_id
            self._next_id += 1
            message["id"] = request_id
            self._callbacks[request_id] = callback
            timer = QtCore.QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda current=request_id, name=method: self._on_request_timeout(current, name))
            self._request_timers[request_id] = timer
            timer.start(30_000)
        self._process.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))

    def _on_request_timeout(self, request_id: int, method: str) -> None:
        callback = self._callbacks.pop(request_id, None)
        timer = self._request_timers.pop(request_id, None)
        if timer is not None:
            timer.deleteLater()
        if callback is None:
            return
        message = f"Codex did not respond to {method} within 30 seconds."
        if method in {"turn/interrupt", "account/login/cancel"}:
            self._finish_failure(f"{message} Cancellation was not confirmed; disconnecting Codex.")
            self._process.kill()
        else:
            callback({"error": {"message": message}})

    def _on_started(self) -> None:
        self._send(
            "initialize",
            {"clientInfo": {"name": "iconmaker_image_prototype", "title": "IconForge Image Generation", "version": "0.1.0"}},
            self._on_initialized,
        )

    def _on_initialized(self, message: dict) -> None:
        if "error" in message:
            self._fail(f"Codex App Server initialization failed: {self._error_text(message)}")
            return
        self._connected = True
        self._send("initialized", {})
        self._update_availability()
        self._read_account()
        self._send("modelProvider/capabilities/read", {}, self._on_capabilities)

    def _read_account(self) -> None:
        self._send("account/read", {"refreshToken": False}, self._on_account)

    def _on_account(self, message: dict) -> None:
        if "error" in message:
            detail = f"Codex sign-in check failed: {self._error_text(message)}"
            self.connectionChanged.emit(detail)
            self._fail(detail)
            return
        account = (message.get("result") or {}).get("account") or {}
        self._account_checked = True
        self._auth_type = account.get("type")
        self._plan_type = account.get("planType")
        self._update_availability()

    def _on_capabilities(self, message: dict) -> None:
        self._capability_error = "error" in message
        self._capability_reason = self._error_text(message) if self._capability_error else ""
        self._image_capability = bool((message.get("result") or {}).get("imageGeneration"))
        self._update_availability()

    def _update_availability(self) -> None:
        if not self._connected:
            self.availabilityChanged.emit(False)
            return
        if not self._account_checked:
            self.connectionChanged.emit("Checking ChatGPT sign-in...")
        elif self._auth_type == "chatgpt" and self._image_capability:
            self.connectionChanged.emit("ChatGPT signed in; Codex image tool available.")
        elif self._auth_type == "chatgpt" and self._image_capability is None:
            self.connectionChanged.emit("ChatGPT signed in; checking image generation capability...")
        elif self._auth_type == "chatgpt" and self._capability_error:
            self.connectionChanged.emit(f"ChatGPT signed in; Codex could not report image generation capability: {self._capability_reason}")
        elif self._auth_type == "chatgpt":
            self.connectionChanged.emit("ChatGPT signed in; this Codex connection has no image generation capability.")
        elif self._auth_type == "apiKey":
            self.connectionChanged.emit("Codex is using an API key. Sign in with ChatGPT to use subscription limits.")
        else:
            self.connectionChanged.emit("Sign in with ChatGPT to generate images.")
        self.availabilityChanged.emit(self.is_ready)

    def sign_in(self) -> None:
        if not self._connected or self.is_busy:
            return
        self._cancel_requested = False
        self._login_id = None
        self._account_checked = False
        self._update_availability()
        self._set_busy("login")
        self.progress.emit("Starting official ChatGPT sign-in...")
        self._send(
            "account/login/start",
            {"type": "chatgpt", "useHostedLoginSuccessPage": True, "appBrand": "chatgpt"},
            self._on_login_started,
        )

    def _on_login_started(self, message: dict) -> None:
        if "error" in message:
            self._finish_failure(f"Could not start ChatGPT sign-in: {self._error_text(message)}")
            return
        result = message.get("result") or {}
        self._login_id = result.get("loginId")
        url = result.get("authUrl")
        if not self._login_id or not url:
            self._finish_failure("Codex did not return a ChatGPT sign-in URL.")
            return
        if self._cancel_requested:
            self._send("account/login/cancel", {"loginId": self._login_id}, self._on_cancel_response)
            return
        self.progress.emit("Complete sign-in in your browser.")
        self.signInUrl.emit(url)

    def generate(self, prompt: str, reference_images: tuple[Path, ...] = ()) -> None:
        if self.is_busy:
            return
        if not self.is_ready:
            self.failed.emit("ChatGPT sign-in and built-in image generation are required.")
            return
        if not prompt.strip():
            self.failed.emit("Enter an image prompt first.")
            return
        images: list[Path] = []
        for source in reference_images:
            try:
                path = Path(source).resolve(strict=True)
            except OSError:
                self.failed.emit("A reference image was moved or removed. Drop it again.")
                return
            if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg"}:
                self.failed.emit("Reference images must be local PNG or JPEG files.")
                return
            if not QtGui.QImageReader(str(path)).canRead():
                self.failed.emit(f"Could not read reference image: {path.name}.")
                return
            images.append(path)
        self._set_busy("generation")
        self._thread_id = None
        self._turn_id = None
        self._image_path = None
        self._image_failure = None
        self._cancel_requested = False
        self._prompt = prompt.strip()
        self._reference_images = tuple(images)
        self.progress.emit("Starting image generation...")
        self._send(
            "thread/start",
            {
                "cwd": self._workspace.name,
                "sandbox": "workspace-write",
                "approvalPolicy": "never",
                "ephemeral": True,
                "serviceName": "iconmaker_image_prototype",
            },
            self._on_thread_started,
        )

    def _on_thread_started(self, message: dict) -> None:
        if "error" in message:
            self._finish_failure(f"Could not start a Codex image session: {self._error_text(message)}")
            return
        self._thread_id = ((message.get("result") or {}).get("thread") or {}).get("id")
        if not self._thread_id:
            self._finish_failure("Codex did not return a session ID.")
            return
        if self._cancel_requested:
            self._finish_cancelled()
            return
        if any(not path.is_file() for path in self._reference_images):
            self._finish_failure("A reference image was moved or removed. Drop it again.")
            return
        inputs = [{"type": "text", "text": f"$imagegen\n{codex_generation_request(self._prompt)}"}]
        inputs.extend({"type": "localImage", "path": str(path)} for path in self._reference_images)
        self._send(
            "turn/start",
            {
                "threadId": self._thread_id,
                "input": inputs,
            },
            self._on_turn_started,
        )

    def _on_turn_started(self, message: dict) -> None:
        if "error" in message:
            self._finish_failure(f"Could not start image generation: {self._error_text(message)}")
            return
        self._turn_id = ((message.get("result") or {}).get("turn") or {}).get("id")
        if not self._turn_id:
            self._finish_failure("Codex did not return a turn ID.")
            return
        if self._cancel_requested:
            self._request_interrupt()
        else:
            self.progress.emit("Waiting for Codex to generate the image...")

    def cancel(self) -> None:
        if not self.is_busy or self._cancel_requested:
            return
        self._cancel_requested = True
        self.progress.emit("Cancellation requested; waiting for Codex to confirm...")
        if self._busy == "login" and self._login_id:
            self._send("account/login/cancel", {"loginId": self._login_id}, self._on_cancel_response)
        elif self._busy == "generation" and self._turn_id:
            self._request_interrupt()

    def _request_interrupt(self) -> None:
        self._send(
            "turn/interrupt",
            {"threadId": self._thread_id, "turnId": self._turn_id},
            self._on_cancel_response,
        )

    def _on_cancel_response(self, message: dict) -> None:
        if "error" in message:
            self.progress.emit("Codex did not accept the cancellation request; waiting for the result...")
        elif self._busy == "login":
            status = (message.get("result") or {}).get("status")
            if status == "canceled":
                self._login_id = None
                self._finish_cancelled()
            elif status == "notFound":
                self._set_busy(None)
                self.progress.emit("Sign-in ended before cancellation was confirmed; checking account...")
                self._read_account()

    def _read_stdout(self) -> None:
        self._buffer.extend(bytes(self._process.readAllStandardOutput()))
        while b"\n" in self._buffer:
            line, _, rest = self._buffer.partition(b"\n")
            self._buffer = bytearray(rest)
            try:
                message = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            request_id = message.get("id")
            if request_id is not None and request_id in self._callbacks:
                timer = self._request_timers.pop(request_id)
                timer.stop()
                timer.deleteLater()
                self._callbacks.pop(request_id)(message)
            elif message.get("method"):
                self._on_notification(message["method"], message.get("params") or {})

    def _on_notification(self, method: str, params: dict) -> None:
        if method == "account/login/completed" and self._busy == "login":
            if params.get("loginId") != self._login_id:
                return
            self._login_id = None
            if params.get("success"):
                self._set_busy(None)
                self.progress.emit("ChatGPT sign-in completed." if not self._cancel_requested else "Sign-in completed before cancellation.")
                self._read_account()
            elif self._cancel_requested:
                self._finish_failure("Sign-in ended after cancellation was requested; cancellation was not confirmed.")
            else:
                self._finish_failure(f"ChatGPT sign-in failed: {params.get('error') or 'Unknown error'}")
        elif method == "account/updated" and self._busy != "login":
            self._read_account()
        elif method == "item/started" and self._busy == "generation":
            if (params.get("item") or {}).get("type") == "imageGeneration":
                self.progress.emit("Generating image...")
        elif method == "item/completed" and self._busy == "generation":
            item = params.get("item") or {}
            if item.get("type") == "imageGeneration":
                self._capture_image_item(item)
        elif method == "turn/completed" and self._busy == "generation":
            if params.get("threadId") != self._thread_id:
                return
            turn = params.get("turn") or {}
            if self._turn_id and turn.get("id") != self._turn_id:
                return
            for item in turn.get("items") or []:
                if item.get("type") == "imageGeneration":
                    self._capture_image_item(item)
            status = turn.get("status")
            if status == "interrupted":
                self._finish_cancelled()
            elif status == "completed" and self._image_path is not None:
                reader = QtGui.QImageReader(str(self._image_path))
                if reader.read().isNull():
                    self._finish_failure(f"Codex returned an image that could not be decoded: {reader.errorString()}")
                    return
                path = str(self._image_path)
                self._set_busy(None)
                self.progress.emit("Image ready.")
                self.imageReady.emit(path)
            elif self._image_failure:
                self._finish_failure(self._image_failure)
            else:
                detail = (turn.get("error") or {}).get("message")
                self._finish_failure(detail or "Codex finished without a retrievable generated image.")

    def _capture_image_item(self, item: dict) -> None:
        failure = item.get("failure") or {}
        if failure.get("type") == "usageLimitExceeded":
            self._image_failure = "Image generation usage limit reached for this ChatGPT account."
            return
        saved_path = item.get("savedPath")
        if saved_path:
            path = Path(saved_path)
            if path.is_file():
                destination = Path(self._workspace.name) / f"generated-original-{uuid.uuid4().hex}{path.suffix or '.png'}"
                try:
                    shutil.copyfile(path, destination)
                except OSError:
                    self._image_failure = "Codex returned an image that could not be kept for preview."
                    return
                self._image_path = destination
                return
        result = item.get("result") or ""
        if result.startswith("data:image/") and ";base64," in result:
            header, encoded = result.split(",", 1)
            suffix = ".png" if "png" in header else ".webp" if "webp" in header else ".jpg"
            path = Path(self._workspace.name) / f"generated-original-{uuid.uuid4().hex}{suffix}"
            try:
                path.write_bytes(base64.b64decode(encoded, validate=True))
                self._image_path = path
            except (ValueError, OSError):
                self._image_failure = "Codex returned an image that could not be saved."
        elif item.get("status") == "failed":
            self._image_failure = "Codex image generation failed."

    def _set_busy(self, mode: str | None) -> None:
        self._busy = mode
        self.busyChanged.emit(mode is not None)

    def _finish_cancelled(self) -> None:
        was_login = self._busy == "login"
        self._set_busy(None)
        self.progress.emit("Cancellation confirmed.")
        self.cancellationConfirmed.emit()
        if was_login and self._connected:
            self._read_account()

    def _finish_failure(self, message: str) -> None:
        was_login = self._busy == "login"
        self._set_busy(None)
        self._fail(message)
        if was_login and self._connected:
            self._read_account()

    def _fail(self, message: str) -> None:
        try:
            GenLog.write_line("image_prototype", message, level="error")
        except OSError:
            pass
        self.failed.emit(message)

    @staticmethod
    def _error_text(message: dict) -> str:
        return str((message.get("error") or {}).get("message") or "Unknown Codex error")

    def _drain_stderr(self) -> None:
        self._process.readAllStandardError()

    def _on_process_error(self, _error) -> None:
        if not self._stopping and self._process.state() == QtCore.QProcess.NotRunning:
            self._on_process_finished()

    def _on_process_finished(self, *_args) -> None:
        if self._stopping or self._disconnect_reported:
            return
        self._disconnect_reported = True
        for timer in self._request_timers.values():
            timer.stop()
            timer.deleteLater()
        self._request_timers.clear()
        self._callbacks.clear()
        self._connected = False
        self.availabilityChanged.emit(False)
        self.connectionChanged.emit("Codex App Server disconnected.")
        if self.is_busy:
            self._finish_failure("Codex disconnected before the request finished; cancellation is unconfirmed.")

    def shutdown(self) -> None:
        self._stopping = True
        for timer in self._request_timers.values():
            timer.stop()
            timer.deleteLater()
        self._request_timers.clear()
        self._callbacks.clear()
        if self._process.state() != QtCore.QProcess.NotRunning:
            self._process.terminate()
            if not self._process.waitForFinished(2000):
                self._process.kill()
                self._process.waitForFinished(1000)
        self._workspace.cleanup()
