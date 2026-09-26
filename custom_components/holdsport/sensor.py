"""Sensorer pr. familiemedlem."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HoldsportConfigEntry
from .const import EVENT_TYPE_MATCH, EVENT_TYPE_TRAINING, STATUS_NONE
from .coordinator import Activity, Comment
from .entity import HoldsportEntity

# Grænsen for en entity-state i HA
MAX_STATE_LENGTH = 255


@dataclass(frozen=True, kw_only=True)
class NextActivityDescription(SensorEntityDescription):
    predicate: Callable[[Activity], bool]


NEXT_SENSORS: tuple[NextActivityDescription, ...] = (
    NextActivityDescription(
        key="next_activity",
        translation_key="next_activity",
        device_class=SensorDeviceClass.TIMESTAMP,
        predicate=lambda a: True,
    ),
    NextActivityDescription(
        key="next_training",
        translation_key="next_training",
        device_class=SensorDeviceClass.TIMESTAMP,
        predicate=lambda a: a.event_type_id == EVENT_TYPE_TRAINING,
    ),
    NextActivityDescription(
        key="next_match",
        translation_key="next_match",
        device_class=SensorDeviceClass.TIMESTAMP,
        predicate=lambda a: a.event_type_id == EVENT_TYPE_MATCH,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HoldsportConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    entities: list[SensorEntity] = []
    for pid in rt.coordinator.profiles:
        entities.extend(
            HoldsportNextSensor(rt.coordinator, rt.account_id, pid, d) for d in NEXT_SENSORS
        )
        entities.append(HoldsportUnansweredSensor(rt.coordinator, rt.account_id, pid))
        entities.append(HoldsportLatestMessageSensor(rt.coordinator, rt.account_id, pid))
    async_add_entities(entities)


class HoldsportNextSensor(HoldsportEntity, SensorEntity):
    """Starttidspunkt for næste aktivitet af en given type."""

    entity_description: NextActivityDescription

    def __init__(self, coordinator, account_id, profile_id, description) -> None:
        super().__init__(coordinator, account_id, profile_id, description.key)
        self.entity_description = description

    def _activity(self) -> Activity | None:
        pdata = self.profile_data
        if pdata is None:
            return None
        now = dt_util.now()
        return next(
            (
                a
                for a in pdata.activities
                if a.start >= now and self.entity_description.predicate(a)
            ),
            None,
        )

    @property
    def native_value(self) -> datetime | None:
        act = self._activity()
        return act.start if act else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        act = self._activity()
        return act.as_attributes() if act else None


class HoldsportUnansweredSensor(HoldsportEntity, SensorEntity):
    """Antal kommende aktiviteter der mangler svar."""

    _attr_translation_key = "unanswered"

    def __init__(self, coordinator, account_id, profile_id) -> None:
        super().__init__(coordinator, account_id, profile_id, "unanswered")

    def _pending(self) -> list[Activity]:
        pdata = self.profile_data
        if pdata is None:
            return []
        now = dt_util.now()
        return [
            a
            for a in pdata.activities
            if a.start >= now and a.status == STATUS_NONE and a.can_respond
        ]

    @property
    def native_value(self) -> int | None:
        return len(self._pending()) if self.profile_data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "activities": [
                {
                    "activity_id": a.id,
                    "name": a.name,
                    "event_type": a.event_type,
                    "team": a.team_name,
                    "start": a.start.isoformat(),
                }
                for a in self._pending()
            ]
        }


class HoldsportLatestMessageSensor(HoldsportEntity, SensorEntity):
    """Nyeste besked skrevet på en af profilens kommende aktiviteter.

    Staten er beskedteksten, så en automation kan sende den videre som
    notifikation, når den ændrer sig.
    """

    _attr_translation_key = "latest_message"

    def __init__(self, coordinator, account_id, profile_id) -> None:
        super().__init__(coordinator, account_id, profile_id, "latest_message")

    def _latest(self) -> tuple[Activity, Comment] | None:
        pdata = self.profile_data
        if pdata is None:
            return None
        return max(
            ((a, c) for a in pdata.activities for c in a.comments),
            key=lambda ac: ac[1].created,
            default=None,
        )

    @property
    def native_value(self) -> str | None:
        latest = self._latest()
        if latest is None:
            return None
        text = " ".join(latest[1].text.split())
        if len(text) > MAX_STATE_LENGTH:
            text = text[: MAX_STATE_LENGTH - 1] + "…"
        return text

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        latest = self._latest()
        if latest is None:
            return None
        act, comment = latest
        return {
            "author": comment.author,
            "created": comment.created.isoformat(),
            "message": comment.text,
            "comment_id": comment.id,
            "activity": act.name,
            "activity_id": act.id,
            "activity_start": act.start.isoformat(),
        }
