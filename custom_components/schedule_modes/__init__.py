from __future__ import annotations
from typing import Final

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform

from .const import DOMAIN, ensure_default_options

PLATFORMS: Final = [Platform.SWITCH, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.CALENDAR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Normalize / backfill options
    if not entry.options:
        hass.config_entries.async_update_entry(entry, options=ensure_default_options(entry.options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Allow options reconfigure to reload entities
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
