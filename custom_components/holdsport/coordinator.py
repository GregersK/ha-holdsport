"""Datahentning for Holdsport."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    HoldsportAuthError,
    HoldsportClient,
    HoldsportError,
    HoldsportNotFound,
    HoldsportRejected,
)
from .const import (
    ACTIVITIES_PER_PAGE,
    CALENDAR_MAX_PAGES,
    DOMAIN,
    STATUS_FROM_TEXT,
    STATUS_NONE,
    STATUS_TEXT,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_DURATION = timedelta(hours=1)


@dataclass(slots=True)
class Profile:
    """Et familiemedlem (Holdsport-profil)."""

    id: int
    name: str
    login: str


@dataclass(slots=True)
class Activity:
    """En aktivitet set fra én profils perspektiv."""

    id: int
    team_id: int
    team_name: str
    name: str
    start: datetime
    end: datetime
    all_day: bool
    event_type: str | None
    event_type_id: int | None
    status: int
    place: str
    comment: str
    pickup_place: str
    pickup_time: str
    action_method: str
    action_path: str

    @property
    def status_text(self) -> str:
        return STATUS_TEXT.get(self.status, str(self.status))

    @property
    def can_respond(self) -> bool:
        # action_method GET = betalingsaktivitet der kræver web-login
        return self.action_method in ("POST", "PUT")

    def as_attributes(self) -> dict[str, Any]:
        return {
            "activity_id": self.id,
            "name": self.name,
            "event_type": self.event_type,
            "team": self.team_name,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "all_day": self.all_day,
            "place": self.place,
            "status": self.status_text,
            "status_code": self.status,
            "meeting_time": self.pickup_time,
            "meeting_place": self.pickup_place,
            "comment": self.comment,
            "can_respond": self.can_respond,
        }


@dataclass(slots=True)
class ProfileData:
    profile: Profile
    teams: dict[int, str] = field(default_factory=dict)
    activities: list[Activity] = field(default_factory=list)


def parse_status(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.strip().isdigit():
            return int(value)
        return STATUS_FROM_TEXT.get(value.strip().lower(), STATUS_NONE)
    return STATUS_NONE


def parse_activity(raw: dict[str, Any], team_id: int, team_name: str) -> Activity | None:
    start = dt_util.parse_datetime(raw.get("starttime") or "")
    if start is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt_util.get_default_time_zone())
    end = dt_util.parse_datetime(raw.get("endtime") or "")
    if end is not None and end.tzinfo is None:
        end = end.replace(tzinfo=dt_util.get_default_time_zone())

    # Aktiviteter kl. 00:00 uden sluttid er i praksis heldagsaktiviteter
    all_day = end is None and start.hour == 0 and start.minute == 0
    if all_day:
        end = start + timedelta(days=1)
    elif end is None or end <= start:
        end = start + DEFAULT_DURATION

    return Activity(
        id=int(raw["id"]),
        team_id=team_id,
        team_name=team_name,
        name=(raw.get("name") or "").replace("\u00a0", " ").strip(),
        start=start,
        end=end,
        all_day=all_day,
        event_type=raw.get("event_type"),
        event_type_id=raw.get("event_type_id"),
        status=parse_status(raw.get("status")),
        place=raw.get("place") or "",
        comment=raw.get("comment") or "",
        pickup_place=raw.get("pickup_place") or "",
        pickup_time=raw.get("pickup_time") or "",
        action_method=(raw.get("action_method") or "").upper(),
        action_path=raw.get("action_path") or "",
    )


class HoldsportCoordinator(DataUpdateCoordinator[dict[int, ProfileData]]):
    """Henter kommende aktiviteter for alle valgte profiler."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: HoldsportClient,
        profiles: list[Profile],
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.profiles = {p.id: p for p in profiles}

    async def _async_update_data(self) -> dict[int, ProfileData]:
        today = dt_util.now().date()
        result: dict[int, ProfileData] = {}
        try:
            for profile in self.profiles.values():
                pdata = ProfileData(profile)
                for team in await self.client.async_get_teams(profile.login):
                    pdata.teams[int(team["id"])] = team.get("name") or str(team["id"])
                seen: set[int] = set()
                for team_id, team_name in pdata.teams.items():
                    raw_list = await self.client.async_get_activities(
                        profile.login,
                        team_id,
                        from_date=today,
                        per_page=ACTIVITIES_PER_PAGE,
                    )
                    for raw in raw_list:
                        act = parse_activity(raw, team_id, team_name)
                        # Klubaktiviteter kan optræde på flere hold
                        if act is not None and act.id not in seen:
                            seen.add(act.id)
                            pdata.activities.append(act)
                pdata.activities.sort(key=lambda a: a.start)
                result[profile.id] = pdata
        except HoldsportAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HoldsportError as err:
            raise UpdateFailed(str(err)) from err
        return result

    async def async_fetch_range(
        self, profile_id: int, start: datetime, end: datetime
    ) -> list[Activity]:
        """Hent aktiviteter i et vilkårligt interval (til kalenderen, også historik)."""
        pdata = (self.data or {}).get(profile_id)
        if pdata is None:
            return []
        profile = pdata.profile
        from_date: date = dt_util.as_local(start).date()
        found: dict[int, Activity] = {}
        try:
            for team_id, team_name in pdata.teams.items():
                for page in range(1, CALENDAR_MAX_PAGES + 1):
                    raw_list = await self.client.async_get_activities(
                        profile.login,
                        team_id,
                        from_date=from_date,
                        page=page,
                        per_page=ACTIVITIES_PER_PAGE,
                    )
                    if not raw_list:
                        break
                    last_start: datetime | None = None
                    for raw in raw_list:
                        act = parse_activity(raw, team_id, team_name)
                        if act is None:
                            continue
                        last_start = act.start
                        if act.start < end and act.end > start:
                            found.setdefault(act.id, act)
                    if len(raw_list) < ACTIVITIES_PER_PAGE or (
                        last_start is not None and last_start >= end
                    ):
                        break
        except HoldsportError as err:
            raise HomeAssistantError(f"Kunne ikke hente fra Holdsport: {err}") from err
        return sorted(found.values(), key=lambda a: a.start)

    async def async_respond(
        self, profile_id: int, activity_id: int, joined_status: int
    ) -> None:
        """Tilmeld eller afmeld en profil på en aktivitet."""
        pdata = (self.data or {}).get(profile_id)
        if pdata is None:
            raise ServiceValidationError("Profilen er ikke aktiv i integrationen")
        login = pdata.profile.login

        cached = next((a for a in pdata.activities if a.id == activity_id), None)
        team_ids = [cached.team_id] if cached else list(pdata.teams)

        raw: dict[str, Any] | None = None
        team_id = 0
        try:
            for team_id in team_ids:
                try:
                    raw = await self.client.async_get_activity(login, team_id, activity_id)
                    break
                except HoldsportNotFound:
                    continue
            if raw is None:
                raise ServiceValidationError(
                    f"Aktivitet {activity_id} blev ikke fundet for {pdata.profile.name}"
                )
            act = parse_activity(raw, team_id, pdata.teams.get(team_id, ""))
            if act is None:
                raise HomeAssistantError("Aktiviteten mangler starttidspunkt")
            if act.status == joined_status:
                return
            if not act.can_respond:
                raise ServiceValidationError(
                    f"'{act.name}' kan ikke besvares via API'et "
                    "(fx betalingsaktivitet) – brug Holdsport-appen"
                )
            await self.client.async_set_status(
                login, act.action_method, act.action_path, joined_status
            )
        except HoldsportRejected as err:
            raise HomeAssistantError(
                "Holdsport afviste ændringen (fx overskredet frist eller fuldt hold)"
            ) from err
        except HoldsportAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError("Login til Holdsport fejlede") from err
        except HoldsportError as err:
            raise HomeAssistantError(f"Fejl fra Holdsport: {err}") from err

        await self.async_refresh()
