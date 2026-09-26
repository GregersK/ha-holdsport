"""Holdsport-integration til Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import HoldsportAuthError, HoldsportClient, HoldsportError
from .const import (
    ATTR_ACTIVITY_ID,
    ATTR_DEVICE_ID,
    CONF_PROFILES,
    DOMAIN,
    JOINED_ATTEND,
    JOINED_DECLINE,
    SERVICE_ATTEND,
    SERVICE_DECLINE,
    device_identifier,
)
from .coordinator import HoldsportCoordinator, Profile

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_ACTIVITY_ID): vol.Coerce(int),
    }
)


@dataclass(slots=True)
class HoldsportRuntime:
    client: HoldsportClient
    coordinator: HoldsportCoordinator
    account_id: int


type HoldsportConfigEntry = ConfigEntry[HoldsportRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async def _handle(call: ServiceCall) -> None:
        joined = JOINED_ATTEND if call.service == SERVICE_ATTEND else JOINED_DECLINE
        device = dr.async_get(hass).async_get(call.data[ATTR_DEVICE_ID])
        if device is None:
            raise ServiceValidationError("Ukendt enhed")

        ident = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None)
        entry = next(
            (
                e
                for e_id in device.config_entries
                if (e := hass.config_entries.async_get_entry(e_id)) is not None
                and e.domain == DOMAIN
                and e.state is ConfigEntryState.LOADED
            ),
            None,
        )
        if ident is None or entry is None:
            raise ServiceValidationError("Enheden er ikke en aktiv Holdsport-profil")

        profile_id = int(ident.rsplit("_", 1)[1])
        await entry.runtime_data.coordinator.async_respond(
            profile_id, call.data[ATTR_ACTIVITY_ID], joined
        )

    for service in (SERVICE_ATTEND, SERVICE_DECLINE):
        hass.services.async_register(DOMAIN, service, _handle, schema=SERVICE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HoldsportConfigEntry) -> bool:
    client = HoldsportClient(
        async_get_clientsession(hass),
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    try:
        profiles_raw = await client.async_get_profiles()
    except HoldsportAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except HoldsportError as err:
        raise ConfigEntryNotReady(str(err)) from err
    if not profiles_raw:
        raise ConfigEntryNotReady("Holdsport returnerede ingen profiler")

    account_id = int(profiles_raw[0]["id"])
    selected = {int(p) for p in entry.options.get(CONF_PROFILES, [account_id])}
    profiles = [
        Profile(
            id=int(p["id"]),
            name=p.get("name") or str(p["id"]),
            # Hovedkontoen logger ind med brugernavn, administrerede profiler med id
            login=client.username if int(p["id"]) == account_id else str(p["id"]),
        )
        for p in profiles_raw
        if int(p["id"]) in selected
    ]

    # Fjern enheder for profiler der er fravalgt
    dev_reg = dr.async_get(hass)
    wanted = {device_identifier(account_id, p.id) for p in profiles}
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        if not device.identifiers & wanted:
            dev_reg.async_update_device(device.id, remove_config_entry_id=entry.entry_id)

    coordinator = HoldsportCoordinator(hass, entry, client, profiles)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = HoldsportRuntime(client, coordinator, account_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: HoldsportConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HoldsportConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
