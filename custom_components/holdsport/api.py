"""Asynkron klient til Holdsport REST API (https://github.com/Holdsport/holdsport-api)."""

from __future__ import annotations

from datetime import date
from typing import Any

import aiohttp

from .const import API_BASE

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)


class HoldsportError(Exception):
    """Generel fejl fra Holdsport."""


class HoldsportAuthError(HoldsportError):
    """Forkert brugernavn/password (HTTP 401)."""


class HoldsportNotFound(HoldsportError):
    """Ressourcen findes ikke (HTTP 404)."""


class HoldsportRejected(HoldsportError):
    """Holdsport afviste ændringen (HTTP 422)."""


class HoldsportClient:
    """Tynd wrapper om Holdsport API'et.

    Alle kald tager et `login`: det indtastede brugernavn for hovedkontoen,
    eller profil-id'et for profiler hovedkontoen administrerer. Password er
    altid hovedkontoens.
    """

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self.username = username
        self._password = password

    async def _request(
        self,
        method: str,
        path: str,
        login: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        if not path.startswith("/"):
            raise HoldsportError(f"Uventet sti fra API: {path}")
        auth = aiohttp.BasicAuth(login, self._password, encoding="utf-8")
        try:
            async with self._session.request(
                method,
                API_BASE + path,
                auth=auth,
                params=params,
                json=json,
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status == 401:
                    raise HoldsportAuthError("Login afvist af Holdsport")
                if resp.status == 404:
                    raise HoldsportNotFound(path)
                if resp.status == 422:
                    raise HoldsportRejected(await resp.text())
                if resp.status >= 400:
                    raise HoldsportError(f"HTTP {resp.status} fra {path}")
                if resp.status == 204:
                    return None
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HoldsportError(f"Forbindelsesfejl: {err}") from err

    async def async_get_profiles(self) -> list[dict[str, Any]]:
        """Profiler brugeren administrerer. Første element er brugeren selv."""
        return await self._request("GET", "/v1/profiles", self.username) or []

    async def async_get_teams(self, login: str) -> list[dict[str, Any]]:
        return await self._request("GET", "/v1/teams", login) or []

    async def async_get_activities(
        self,
        login: str,
        team_id: int,
        *,
        from_date: date | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"page": page, "per_page": per_page}
        if from_date is not None:
            params["date"] = from_date.isoformat()
        return (
            await self._request(
                "GET", f"/v1/teams/{team_id}/activities", login, params=params
            )
            or []
        )

    async def async_get_activity(
        self, login: str, team_id: int, activity_id: int
    ) -> dict[str, Any]:
        return await self._request(
            "GET", f"/v1/teams/{team_id}/activities/{activity_id}", login
        )

    async def async_set_status(
        self, login: str, method: str, path: str, joined_status: int
    ) -> None:
        """Tilmeld/afmeld via aktivitetens action_method/action_path."""
        await self._request(
            method,
            path,
            login,
            json={"activities_user": {"joined_status": joined_status, "picked": 1}},
        )
