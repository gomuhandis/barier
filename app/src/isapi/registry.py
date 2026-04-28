"""Process-wide registry of ISAPIClient instances keyed by camera role."""
from __future__ import annotations

from src.config import get_settings
from src.isapi.client import ISAPIClient


class CameraRegistry:
    def __init__(self) -> None:
        self._clients: dict[str, ISAPIClient] = {}

    def build(self) -> None:
        if self._clients:
            return
        for cam in get_settings().cameras():
            self._clients[cam.role] = ISAPIClient(cam)

    def all(self) -> list[ISAPIClient]:
        return list(self._clients.values())

    def by_role(self, role: str) -> ISAPIClient | None:
        return self._clients.get(role)

    def by_host(self, host: str) -> ISAPIClient | None:
        for c in self._clients.values():
            if c.cam.host == host:
                return c
        return None

    async def shutdown(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()


registry = CameraRegistry()
