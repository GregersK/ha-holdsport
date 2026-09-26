"""Holdsport-integration til Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .api import HoldsportAuthError, HoldsportClient, HoldsportError
from .const import (
    ATTR_ACTIVITY_ID,
    ATTR_DEVICE_ID,
    CARD_FILENAME,
    CARD_URL_BASE,
    CONF_PROFILES,
    DOMAIN,
    JOINED_ATTEND,
    JOINED_DECLINE,
    SERVICE_ATTEND,
    SERVICE_DECLINE,
    device_identifier,
)
from .coordinator import HoldsportCoordinator, Profile, resolve_device
from .websocket import async_register_websocket

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
        coordinator, profile_id = resolve_device(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_respond(profile_id, call.data[ATTR_ACTIVITY_ID], joined)

    for service in (SERVICE_ATTEND, SERVICE_DECLINE):
        hass.services.async_register(DOMAIN, service, _handle, schema=SERVICE_SCHEMA)

    async_register_websocket(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Server dashboard-kortet og indlæs det automatisk i frontend."""
    # Ikke tilgængeligt i tests / minimale opsætninger uden frontend
    if hass.http is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components.frontend import add_extra_js_url
    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(Path(__file__).parent / "frontend"), True)]
    )
    integration = await async_get_integration(hass, DOMAIN)
    # Versionen i URL'en tvinger browseren til at hente kortet igen efter opdatering
    url = f"{CARD_URL_BASE}/{CARD_FILENAME}?v={integration.version}"
    add_extra_js_url(hass, url)

    # "Holdsport" i sidemenuen: et kort pr. familiemedlem, uden dashboard-opsætning
    from homeassistant.components import panel_custom

    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=DOMAIN,
        webcomponent_name="holdsport-panel",
        sidebar_title="Holdsport",
        sidebar_icon="mdi:calendar-account",
        module_url=url,
        require_admin=False,
    )


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
