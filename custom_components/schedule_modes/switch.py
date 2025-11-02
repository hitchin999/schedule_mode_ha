from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional
import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity import EntityCategory

from .const import (
    OPT_ENABLED_MODES,
    OPT_DEFAULT_DURATIONS,
    OPT_AUTO_RESET_TIME,
    device_info_for_mode,
    mode_friendly,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    opts = entry.options or {}
    enabled = opts.get(OPT_ENABLED_MODES, [])
    durs = opts.get(OPT_DEFAULT_DURATIONS, {})

    entities: list[SwitchEntity] = []
    # Mode switches
    for k in enabled:
        entities.append(ModeSwitch(hass, entry, k, mode_friendly(k), durs.get(k, 0)))
    # Calendar override per mode
    for k in enabled:
        entities.append(CalendarOverrideSwitch(hass, entry, k, mode_friendly(k)))

    async_add_entities(entities, True)

    auto_reset = (opts.get(OPT_AUTO_RESET_TIME) or "").strip()
    if auto_reset and entities:
        try:
            hh, mm = [int(x) for x in auto_reset.split(":")]
        except Exception:
            _LOGGER.warning("Invalid OPT_AUTO_RESET_TIME %r, expected HH:MM", auto_reset)
            hh, mm = None, None

        if hh is not None and mm is not None:

            @callback
            def _daily_reset(_now):
                # Only reset ModeSwitch instances (not override switches)
                for e in entities:
                    if isinstance(e, ModeSwitch) and e.is_on:
                        hass.create_task(e.async_turn_off(controlled_by="auto_reset"))

            async_track_time_change(hass, _daily_reset, hour=hh, minute=mm, second=0)


class ModeSwitch(SwitchEntity, RestoreEntity):
    """A timed/indefinite mode switch with reliable state restore and startup reassert."""
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str, name: str, default_minutes: int):
        self.hass = hass
        self._entry = entry
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"  # stable for registry & restore
        self._is_on = False
        self._expire_at: Optional[datetime] = None
        self._unsub_timer = None
        self._default_minutes = max(0, int(default_minutes or 0))
        self._controlled_by = "manual"
        self._restored_on = False  # whether we restored ON at init

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
        """Restore previous state, re-arm timers, and reassert after HA fully starts to beat races."""
        await super().async_added_to_hass()

        last = await self.async_get_last_state()
        if last:
            self._is_on = last.state == "on"
            self._controlled_by = last.attributes.get("controlled_by", "manual")
            exp = last.attributes.get("expires_at")
            self._expire_at = None
            if exp:
                try:
                    self._expire_at = dt_util.parse_datetime(exp)
                except Exception:
                    self._expire_at = None

            now = dt_util.now()
            if self._is_on:
                if self._expire_at is None:
                    # Indefinite ON before restart → remain ON
                    self._restored_on = True
                elif self._expire_at > now:
                    # Re-arm timer with remaining seconds
                    remaining = (self._expire_at - now).total_seconds()
                    if remaining > 1:
                        self._start_timer(remaining)
                        self._restored_on = True
                    else:
                        # Expired essentially now
                        self._is_on = False
                        self._expire_at = None
                        self._restored_on = False
                else:
                    # Expired while HA was down
                    self._is_on = False
                    self._expire_at = None
                    self._restored_on = False
            else:
                self._restored_on = False
        else:
            # No previous state - default OFF
            self._is_on = False
            self._expire_at = None
            self._controlled_by = "manual"
            self._restored_on = False

        self.async_write_ha_state()

        # Reassert ON once HA is fully started, in case something toggled us OFF during init.
        if self._restored_on:
            async def _reassert_on_started(_evt):
                # Only reassert if we intended to be ON and we are OFF now.
                if not self.is_on:
                    self._controlled_by = "restore"
                    # Recreate precise remaining timer if applicable
                    if self._expire_at:
                        remaining = (self._expire_at - dt_util.now()).total_seconds()
                        if remaining > 1:
                            self._is_on = True
                            # restart timer with precise remaining seconds
                            self._start_timer(remaining)
                            self.async_write_ha_state()
                            return
                        # If expired meanwhile, fall through to OFF
                        self._expire_at = None
                    # Indefinite ON
                    self._is_on = True
                    self.async_write_ha_state()

            self.async_on_remove(
                self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _reassert_on_started)
            )

    async def async_turn_on(self, **kwargs):
        self._is_on = True
        self._controlled_by = kwargs.get("controlled_by", "manual")
        minutes = kwargs.get("minutes", self._default_minutes)
        self._schedule_expiration(int(minutes) if minutes is not None else 0)
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

        # Guard against negative/zero scheduling
        delay = max(1.0, float(seconds))
        self._unsub_timer = async_call_later(self.hass, delay, _cb)


class CalendarOverrideSwitch(SwitchEntity, RestoreEntity):
    """When ON, calendar events for this mode are ignored; only manual switch changes apply."""
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str, name: str):
        self.hass = hass
        self._entry = entry
        self._key = key
        self._attr_name = f"{name} Calendar Override"
        self._attr_unique_id = f"{entry.entry_id}_{key}_calendar_override"
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
            "description": (
                f"When ON, calendar events for {self._attr_name.replace(' Calendar Override', '')} "
                f"are ignored and only manual switch controls this mode"
            ),
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._is_on = bool(last and last.state == "on")
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs):
        self._is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs):
        self._is_on = False
        self.async_write_ha_state()
