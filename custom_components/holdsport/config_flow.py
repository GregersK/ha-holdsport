"""Config flow for Holdsport."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import HoldsportAuthError, HoldsportClient, HoldsportError
from .const import (
    CONF_MATCH_MEETING_MINUTES,
    CONF_MATCH_MEETING_PLACES,
    CONF_PROFILES,
    DEFAULT_MATCH_MEETING_MINUTES,
    DOMAIN,
)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
    }
)


async def _fetch_profiles(
    hass: HomeAssistant, username: str, password: str
) -> list[dict[str, Any]]:
    client = HoldsportClient(async_get_clientsession(hass), username, password)
    return await client.async_get_profiles()


def _profiles_schema(profiles: list[dict[str, Any]], default: list[str]) -> vol.Schema:
    options = [
        SelectOptionDict(value=str(p["id"]), label=p.get("name") or str(p["id"]))
        for p in profiles
    ]
    return vol.Schema(
        {
            vol.Required(CONF_PROFILES, default=default): SelectSelector(
                SelectSelectorConfig(
                    options=options, multiple=True, mode=SelectSelectorMode.LIST
                )
            )
        }
    )


class HoldsportConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._profiles: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return HoldsportOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input[CONF_PASSWORD]
            try:
                profiles = await _fetch_profiles(self.hass, username, password)
            except HoldsportAuthError:
                errors["base"] = "invalid_auth"
            except HoldsportError:
                errors["base"] = "cannot_connect"
            else:
                if not profiles:
                    errors["base"] = "no_profiles"
                else:
                    await self.async_set_unique_id(str(profiles[0]["id"]))
                    self._abort_if_unique_id_configured()
                    self._username, self._password = username, password
                    self._profiles = profiles
                    if len(profiles) == 1:
                        return self._create([int(profiles[0]["id"])])
                    return await self.async_step_profiles()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_profiles(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_PROFILES]:
                return self._create([int(x) for x in user_input[CONF_PROFILES]])
            errors["base"] = "no_selection"

        return self.async_show_form(
            step_id="profiles",
            data_schema=_profiles_schema(
                self._profiles, [str(p["id"]) for p in self._profiles]
            ),
            errors=errors,
        )

    def _create(self, profile_ids: list[int]) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"Holdsport ({self._profiles[0].get('name') or self._username})",
            data={CONF_USERNAME: self._username, CONF_PASSWORD: self._password},
            options={CONF_PROFILES: profile_ids},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                profiles = await _fetch_profiles(
                    self.hass, entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except HoldsportAuthError:
                errors["base"] = "invalid_auth"
            except HoldsportError:
                errors["base"] = "cannot_connect"
            else:
                if profiles:
                    await self.async_set_unique_id(str(profiles[0]["id"]))
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )


class HoldsportOptionsFlow(OptionsFlow):
    """Vælg hvilke familiemedlemmer der skal vises."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_PROFILES]:
                return self.async_create_entry(
                    data={
                        CONF_PROFILES: [int(x) for x in user_input[CONF_PROFILES]],
                        CONF_MATCH_MEETING_MINUTES: int(
                            user_input.get(CONF_MATCH_MEETING_MINUTES) or 0
                        ),
                        CONF_MATCH_MEETING_PLACES: (
                            user_input.get(CONF_MATCH_MEETING_PLACES) or ""
                        ).strip(),
                    }
                )
            errors["base"] = "no_selection"

        try:
            profiles = await _fetch_profiles(
                self.hass,
                self.config_entry.data[CONF_USERNAME],
                self.config_entry.data[CONF_PASSWORD],
            )
        except HoldsportError:
            return self.async_abort(reason="cannot_connect")

        opts = self.config_entry.options
        current = [str(x) for x in opts.get(CONF_PROFILES, [])]
        schema = _profiles_schema(profiles, current).extend(
            {
                vol.Optional(
                    CONF_MATCH_MEETING_MINUTES,
                    default=opts.get(
                        CONF_MATCH_MEETING_MINUTES, DEFAULT_MATCH_MEETING_MINUTES
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=240,
                        step=5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                ),
                vol.Optional(
                    CONF_MATCH_MEETING_PLACES,
                    description={
                        "suggested_value": opts.get(CONF_MATCH_MEETING_PLACES, "")
                    },
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
