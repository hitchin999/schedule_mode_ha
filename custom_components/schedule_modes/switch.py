from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity import EntityCategory

from .const import (
    OPT_ENABLED_MODES, OPT_DEFAULT_DURATIONS, OPT_AUTO_RESET_TIME,
    device_info_for_mode, device_info_main, mode_friendly
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    opts = entry.options
    enabled = opts.get(OPT_ENABLED_MODES, [])
    durs = opts.get(OPT_DEFAULT_DURATIONS, {})
    entities = [ModeSwitch(hass, entry, k, mode_friendly(k), durs.get(k, 0)) for k in enabled]

    # Add Calendar Override switch for each mode
    for k in enabled:
        entities.append(CalendarOverrideSwitch(hass, entry, k, mode_friendly(k)))

    async_add_entities(entities, True)

    auto_reset = opts.get(OPT_AUTO_RESET_TIME, "")
    if auto_reset and entities:
        hh, mm = [int(x) for x in auto_reset.split(":")]

        @callback
        def _daily_reset(_now):
            for e in entities:
                # Only reset switches that are currently ON
                if getattr(e, "is_on", False):
                    hass.create_task(e.async_turn_off(controlled_by="auto_reset"))
        async_track_time_change(hass, _daily_reset, hour=hh, minute=mm, second=0)


class ModeSwitch(SwitchEntity, RestoreEntity):
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str, name: str, default_minutes: int):
        self.hass = hass
        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        # Predictable entity_id for mirrors/templating
        self.entity_id = f"switch.{self._key}"
        self._is_on = False
        self._expire_at: Optional[datetime] = None
        self._unsub_timer = None
        self._default_minutes = max(0, int(default_minutes))
        self._controlled_by = "manual"

    @property
    def device_info(self):
        return device_info_for_mode(self._entry.entry_id, self._key)

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def extra_state_attributes(self):
        return {
            "mode_key": self._key,
            "expires_at": self._expire_at.isoformat() if self._expire_at else None,
            "default_minutes": self._default_minutes,
            "controlled_by": self._controlled_by,
        }

    async def async_added_to_hass(self):
        """Restore previous state without force-turning off when no expiration existed."""
        last = await self.async_get_last_state()
        if last:
            self._is_on = last.state == "on"
            self._controlled_by = last.attributes.get("controlled_by", "manual")
            exp = last.attributes.get("expires_at")
            # Try to restore the timestamp (may be None if it was an indefinite ON)
            self._expire_at = None
            if exp:
                try:
                    self._expire_at = dt_util.parse_datetime(exp)
                except Exception:
                    self._expire_at = None

            now = dt_util.now()

            if self._is_on:
                if self._expire_at is None:
                    # Indefinite ON before restart → stay ON
                    pass
                elif self._expire_at > now:
                    # Re-arm the timer for the remaining duration
                    self._start_timer((self._expire_at - now).total_seconds())
                else:
                    # The stored expiration is in the past → treat as expired
                    self._is_on = False
                    self._expire_at = None
        else:
            # No previous state - default to off
            self._is_on = False

        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        self._is_on = True
        self._controlled_by = kwargs.get("controlled_by", "manual")
        minutes = kwargs.get("minutes", self._default_minutes)
        self._schedule_expiration(minutes)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._is_on = False
        self._controlled_by = kwargs.get("controlled_by", "manual")
        self._expire_at = None
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        self.async_write_ha_state()

    def _schedule_expiration(self, minutes: int):
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if minutes <= 0:
            # No expiration → indefinite ON
            self._expire_at = None
            return
        now = dt_util.now()
        self._expire_at = now + timedelta(minutes=minutes)
        self._start_timer((self._expire_at - now).total_seconds())

    def _start_timer(self, seconds: float):
        @callback
        def _cb(_now):
            self.hass.create_task(self.async_turn_off(controlled_by="timer"))
        self._unsub_timer = async_call_later(self.hass, seconds, _cb)


class CalendarOverrideSwitch(SwitchEntity, RestoreEntity):
    """When ON, only manual switch changes affect binary sensors (calendar is ignored) for this specific mode."""
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str, name: str):
        self.hass = hass
        self._entry = entry
        self._key = key
        self._attr_name = f"{name} Calendar Override"
        self._attr_unique_id = f"{entry.entry_id}_{key}_calendar_override"
        self.entity_id = f"switch.{key}_calendar_override"
        self._is_on = False

    @property
    def device_info(self):
        return device_info_for_mode(self._entry.entry_id, self._key)

    @property
    def is_on(self) -> bool:
        return self._is_on

    @property
    def icon(self) -> str:
        return "mdi:calendar-remove" if self._is_on else "mdi:calendar-check"

    @property
    def extra_state_attributes(self):
        return {
            "mode_key": self._key,
            "description": f"When ON, calendar events for {self._attr_name.replace(' Calendar Override', '')} are ignored and only manual switch controls this mode"
        }

    async def async_added_to_hass(self):
        last = await self.async_get_last_state()
        if last:
            self._is_on = last.state == "on"
        else:
            self._is_on = False
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._is_on = False
        self.async_write_ha_state()
