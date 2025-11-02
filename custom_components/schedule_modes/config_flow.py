from __future__ import annotations
from typing import Any, Dict
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN, ALL_MODE_KEYS, ensure_default_options,
    OPT_ENABLED_MODES, OPT_DEFAULT_DURATIONS, OPT_AUTO_RESET_TIME,
    OPT_LINK_NO_TACHANUN_FOR_BRIS,  # NEW
)


class ScheduleModesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: Dict[str, Any] | None = None):
        # Only allow one instance of this integration
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is not None:
            enabled = list(user_input[OPT_ENABLED_MODES])
            sync_bris = user_input.get(OPT_LINK_NO_TACHANUN_FOR_BRIS, False)
            options = ensure_default_options({
                OPT_ENABLED_MODES: enabled,
                OPT_LINK_NO_TACHANUN_FOR_BRIS: sync_bris,
            })
            return self.async_create_entry(
                title="Schedule Modes",
                data={},
                options=options,
            )

        schema = vol.Schema({
            vol.Required(OPT_ENABLED_MODES, default=ALL_MODE_KEYS()): cv.multi_select({k: k for k in ALL_MODE_KEYS()}),
            vol.Optional(OPT_LINK_NO_TACHANUN_FOR_BRIS, default=False): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ScheduleModesOptionsFlow(config_entry)


class ScheduleModesOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, entry: config_entries.ConfigEntry):
        self.entry = entry
        self._pending: Dict[str, Any] = {}

    async def async_step_init(self, _=None):
        return await self.async_step_select_modes()

    async def async_step_select_modes(self, user_input=None):
        opts = ensure_default_options(self.entry.options or {})
        enabled = opts.get(OPT_ENABLED_MODES, ALL_MODE_KEYS())
        schema = vol.Schema({
            vol.Required(OPT_ENABLED_MODES, default=enabled): cv.multi_select({k: k for k in ALL_MODE_KEYS()}),
        })
        if user_input is not None:
            self._pending = dict(opts)
            self._pending[OPT_ENABLED_MODES] = list(user_input[OPT_ENABLED_MODES])
            return await self.async_step_durations()
        return self.async_show_form(step_id="select_modes", data_schema=schema)

    async def async_step_durations(self, user_input=None):
        opts = self._pending or ensure_default_options(self.entry.options or {})
        durs = opts.get(OPT_DEFAULT_DURATIONS, {})
        fields = {}
        for k in opts.get(OPT_ENABLED_MODES, ALL_MODE_KEYS()):
            fields[vol.Optional(f"dur_{k}", default=int(durs.get(k, 0)))] = vol.Coerce(int)
        fields[vol.Optional(OPT_AUTO_RESET_TIME, default=opts.get(OPT_AUTO_RESET_TIME, ""))] = str
        # NEW: UI toggle for linking NT during Bris
        fields[vol.Optional(OPT_LINK_NO_TACHANUN_FOR_BRIS, default=opts.get(OPT_LINK_NO_TACHANUN_FOR_BRIS, False))] = bool

        if user_input is not None:
            nd = {}
            for k in opts[OPT_ENABLED_MODES]:
                nd[k] = int(user_input.get(f"dur_{k}", 0))
            opts[OPT_DEFAULT_DURATIONS] = nd
            opts[OPT_AUTO_RESET_TIME] = user_input.get(OPT_AUTO_RESET_TIME, "").strip()
            # NEW: persist link setting
            opts[OPT_LINK_NO_TACHANUN_FOR_BRIS] = bool(user_input.get(OPT_LINK_NO_TACHANUN_FOR_BRIS, False))
            return self.async_create_entry(title="", data=opts)

        return self.async_show_form(step_id="durations", data_schema=vol.Schema(fields))
