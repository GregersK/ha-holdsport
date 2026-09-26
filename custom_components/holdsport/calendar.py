"""Kalender pr. familiemedlem."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HoldsportConfigEntry
from .coordinator import Activity
from .entity import HoldsportEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HoldsportConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    async_add_entities(
        HoldsportCalendar(rt.coordinator, rt.account_id, pid)
        for pid in rt.coordinator.profiles
    )


def to_event(act: Activity) -> CalendarEvent:
    lines = [
        f"Type: {act.event_type or '-'}",
        f"Hold: {act.team_name}",
        f"Status: {act.status_text}",
    ]
    if act.pickup_time or act.pickup_place:
        lines.append(f"Mødetid: {act.pickup_time} {act.pickup_place}".rstrip())
    elif act.meeting_start:
        lines.append(f"Mødetid: {dt_util.as_local(act.meeting_start):%H.%M}")
    if act.comment:
        lines.append("")
        lines.append(act.comment)
    if act.all_day:
        start, end = act.start.date(), act.end.date()
    else:
        start, end = act.start, act.end
    return CalendarEvent(
        start=start,
        end=end,
        summary=act.name,
        description="\n".join(lines),
        location=act.place or None,
        uid=str(act.id),
    )


class HoldsportCalendar(HoldsportEntity, CalendarEntity):
    """Alle aktiviteter (træning, kampe m.m.) for én profil."""

    _attr_name = None  # entity får enhedens navn, fx calendar.emma

    def __init__(self, coordinator, account_id: int, profile_id: int) -> None:
        super().__init__(coordinator, account_id, profile_id, "calendar")

    @property
    def event(self) -> CalendarEvent | None:
        pdata = self.profile_data
        if pdata is None:
            return None
        now = dt_util.now()
        act = next((a for a in pdata.activities if a.end > now), None)
        return to_event(act) if act else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        acts = await self.coordinator.async_fetch_range(
            self.profile_id, start_date, end_date
        )
        return [to_event(a) for a in acts]
