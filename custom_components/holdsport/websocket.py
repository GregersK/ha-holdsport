"""Websocket-API til dashboard-kortet."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .coordinator import resolve_device

WS_SUBSCRIBE = "holdsport/subscribe"


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_SUBSCRIBE,
        vol.Exclusive("device_id", "target"): str,
        vol.Exclusive("entity_id", "target"): str,
    }
)
@callback
def ws_subscribe(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Send en profils kommende aktiviteter nu og efter hver opdatering.

    Kortet angiver typisk en af profilens entiteter (fx kalenderen), så man
    ikke skal kende enheds-id'et.
    """
    device_id = msg.get("device_id")
    if device_id is None and (entity_id := msg.get("entity_id")):
        entity = er.async_get(hass).async_get(entity_id)
        device_id = entity.device_id if entity else None
    if not device_id:
        connection.send_error(msg["id"], "not_found", "Ingen Holdsport-enhed angivet")
        return
    try:
        coordinator, profile_id = resolve_device(hass, device_id)
    except ServiceValidationError as err:
        connection.send_error(msg["id"], "not_found", str(err))
        return

    @callback
    def _send() -> None:
        pdata = (coordinator.data or {}).get(profile_id)
        connection.send_message(
            websocket_api.event_message(
                msg["id"],
                {
                    "device_id": device_id,
                    "name": coordinator.profiles[profile_id].name,
                    "available": coordinator.last_update_success and pdata is not None,
                    "activities": [a.as_card_dict() for a in pdata.activities]
                    if pdata
                    else [],
                },
            )
        )

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(_send)
    connection.send_result(msg["id"])
    _send()
