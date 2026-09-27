"""Holdsport-integration til Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import HoldsportAuthError, HoldsportClient, HoldsportError
from .const import (
    ATTR_ACTIVITY_ID,
    ATTR_DEVICE_ID,
    CARD_URL,
    CARD_URL_BASE,
    CONF_PROFILES,
    DOMAIN,
    JOINED_ATTEND,
    JOINED_DECLINE,
    SERVICE_ATTEND,
    SERVICE_DECLINE,
    SERVICE_TAKE_TASK,
    ATTR_TASK_ID,
    device_identifier,
)
from .coordinator import HoldsportCoordinator, Profile, resolve_device
from .websocket import async_register_websocket

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR]
# latest_message: Holdsports chat er ikke tilgængelig via API'et (fjernet i 0.4.0)
REMOVED_SENSOR_KEYS = ("latest_message",)
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

    async def _take_task(call: ServiceCall) -> None:
        coordinator, profile_id = resolve_device(hass, call.data[ATTR_DEVICE_ID])
        await coordinator.async_take_task(
            profile_id, call.data[ATTR_ACTIVITY_ID], call.data[ATTR_TASK_ID]
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_TAKE_TASK,
        _take_task,
        schema=SERVICE_SCHEMA.extend({vol.Required(ATTR_TASK_ID): vol.Coerce(int)}),
    )

    async_register_websocket(hass)
    await _async_register_card(hass)
    return True


async def _async_register_card(hass: HomeAssistant) -> None:
    """Server dashboard-kortet og indlæs det automatisk i frontend."""
    # Ikke tilgængeligt i tests / minimale opsætninger uden frontend
    if hass.http is None or "frontend" not in hass.config.components:
        return
    from homeassistant.components.frontend import add_extra_js_url

    from .card import CARD_PATH, HoldsportCardView

    content = await hass.async_add_executor_job(CARD_PATH.read_bytes)
    hass.http.register_view(HoldsportCardView(content))
    # Fast URL uden version – se HoldsportCardView for hvorfor
    url = CARD_URL
    add_extra_js_url(hass, url)
    await _async_ensure_lovelace_resource(hass, url)

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


async def _async_ensure_lovelace_resource(hass: HomeAssistant, url: str) -> None:
    """Registrer kortet som dashboard-resource (Indstillinger → Dashboards → Resources).

    add_extra_js_url alene når ikke altid at indlæse kortet før dashboardet
    bygges, så kortet kun virkede efter Holdsport-panelet var åbnet. En resource
    er den officielle vej. Kun i storage-mode; i YAML-mode styrer brugeren selv.
    Lovelaces resource-samling er intern API, så fejl her må aldrig stoppe opsætningen.
    """
    try:
        from homeassistant.components.lovelace.const import LOVELACE_DATA

        data = hass.data.get(LOVELACE_DATA)
        mode = getattr(data, "resource_mode", getattr(data, "mode", None))
        if data is None or mode != "storage":
            return
        resources = data.resources
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True
        for item in resources.async_items():
            if str(item.get("url", "")).startswith(f"{CARD_URL_BASE}/"):
                if item["url"] != url:
                    await resources.async_update_item(
                        item["id"], {"res_type": "module", "url": url}
                    )
                return
        await resources.async_create_item({"res_type": "module", "url": url})
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Kunne ikke registrere Holdsport-kortet som dashboard-resource – "
            "tilføj %s manuelt under Dashboards → Resources",
            url,
            exc_info=True,
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

    # Entiteter fra tidligere versioner der ikke findes længere
    ent_reg = er.async_get(hass)
    for p in profiles:
        for key in REMOVED_SENSOR_KEYS:
            if entity_id := ent_reg.async_get_entity_id(
                "sensor", DOMAIN, f"{account_id}_{p.id}_{key}"
            ):
                ent_reg.async_remove(entity_id)

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
