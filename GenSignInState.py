"""Shared sign-in state and generator-window commands."""

from __future__ import annotations

from PySide6 import QtCore
from PySide6.QtNetwork import QLocalSocket

from AppIdentity import APP_NAME, APP_ORG


PROVIDERS = ("ChatGPT", "Gemini", "Microsoft Designer")
SIGN_IN_PROVIDERS = PROVIDERS
CONTROL_SERVER_NAME = f"{APP_ORG}.{APP_NAME}.GeneratorControl"
APP_OPENED_KEY = "image_generator/app_opened"
GENERATOR_OPENED_KEY = "image_generator/area_opened"


def _status_key(provider: str) -> str:
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported sign-in provider: {provider}")
    return "image_generator/sign_in/" + provider.lower().replace(" ", "_")


def sign_in_status(settings: QtCore.QSettings, provider: str) -> str:
    value = str(settings.value(_status_key(provider), "unknown") or "unknown")
    return value if value in {"signed_in", "signed_out"} else "unknown"


def set_sign_in_status(settings: QtCore.QSettings, provider: str, status: str) -> None:
    if status not in {"signed_in", "signed_out"}:
        return
    key = _status_key(provider)
    if sign_in_status(settings, provider) != status:
        settings.setValue(key, status)
        settings.sync()


def should_offer_sign_in(settings: QtCore.QSettings) -> bool:
    settings.sync()
    return (
        not settings.value(APP_OPENED_KEY, False, type=bool)
        or not settings.value(GENERATOR_OPENED_KEY, False, type=bool)
        or not any(sign_in_status(settings, name) == "signed_in" for name in SIGN_IN_PROVIDERS)
    )


def send_generator_command(action: str, provider: str = "ChatGPT") -> bool:
    if action not in {"generator", "sign_in"} or provider not in PROVIDERS:
        return False
    socket = QLocalSocket()
    socket.connectToServer(CONTROL_SERVER_NAME)
    if not socket.waitForConnected(120):
        socket.abort()
        return False
    socket.write(f"{action}\t{provider}\n".encode("utf-8"))
    sent = socket.waitForBytesWritten(200)
    acknowledged = sent and (socket.bytesAvailable() or socket.waitForReadyRead(500)) and bytes(socket.readAll()).strip() == b"ok"
    socket.disconnectFromServer()
    return acknowledged
