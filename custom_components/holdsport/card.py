"""Serverer dashboard-kortet på én fast URL."""

from __future__ import annotations

import hashlib
from pathlib import Path

from aiohttp import web

from homeassistant.components.http import HomeAssistantView

from .const import CARD_URL

CARD_PATH = Path(__file__).parent / "frontend" / "holdsport-card.js"


class HoldsportCardView(HomeAssistantView):
    """Kortets JavaScript uden versionsnummer i URL'en.

    Tidligere lå versionen i URL'en (…?v=0.5.0). En browser med en gammel side
    i cachen importerede så den gamle URL, og fordi et custom element kun kan
    defineres én gang, vandt den gamle kode over den nye. Med én fast URL og
    `no-cache` spørger browseren altid HA, og en uændret fil koster kun et 304.
    """

    url = CARD_URL
    name = "holdsport:card"
    requires_auth = False  # indlæses før login-tjek, ligesom HA's egne statiske filer

    def __init__(self, content: bytes) -> None:
        self._content = content
        self._etag = f'"{hashlib.sha256(content).hexdigest()[:32]}"'

    async def get(self, request: web.Request) -> web.Response:
        headers = {"Cache-Control": "no-cache", "ETag": self._etag}
        if request.headers.get("If-None-Match") == self._etag:
            return web.Response(status=304, headers=headers)
        return web.Response(
            body=self._content,
            content_type="application/javascript",
            charset="utf-8",
            headers=headers,
        )
