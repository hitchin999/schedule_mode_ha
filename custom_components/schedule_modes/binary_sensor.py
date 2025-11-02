from __future__ import annotations
from datetime import timedelta
from typing import Optional, List
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity import EntityCategory

from .const import (
    DOMAIN, OPT_ENABLED_MODES, EVENT_MODE_KEYS, device_info_for_mode, device_info_main, mode_friendly
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    enabled = entry.options.get(OPT_ENABLED_MODES, [])
    ents: List[BinarySensorEntity] = []

    for k in enabled:
        ents.append(ModeMirrorBinarySensor(hass, entry, k, mode_friendly(k)))
        ents.append(ModeEventActiveBinarySensor(hass, entry, k, mode_friendly(k)))

    ents.append(EventModesSummaryBinarySensor(hass, entry, [k for k in enabled if k in EVENT_MODE_KEYS]))
    ents.append(EventRunningWithOverrideBinarySensor(hass, entry, enabled))
    ents.append(DSTBinarySensor(hass, entry))
    async_add_entities(ents, True)


class ModeMirrorBinarySensor(BinarySensorEntity):
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str, friendly: str):
        self.hass = hass
        self._entry = entry
        self._key = key
        self._attr_name = f"{friendly} Active"
        self._attr_unique_id = f"{entry.entry_id}_{key}_mirror"
        self._switch_eid = f"switch.{key}"
        self._active_helper = f"sensor.{DOMAIN}_active_{key}_event"
        self._next_helper = f"sensor.{DOMAIN}_next_{key}_event"
        self._controlled_by = "manual"
        self._next_start: Optional[str] = None
        self._next_end: Optional[str] = None
        self._active_start: Optional[str] = None
        self._active_end: Optional[str] = None
        self._last_ended: Optional[str] = None
        self._unsubs = []

    @property
    def device_info(self):
        return device_info_for_mode(self._entry.entry_id, self._key)

    async def async_added_to_hass(self):
        @callback
        def _on_change(_event):
            self._refresh_attrs()
            self.async_write_ha_state()
        # Track the mode switch, active/next helpers, and the calendar override switch for this mode
        override_eid = f"switch.{self._key}_calendar_override"
        self._unsubs.append(async_track_state_change_event(self.hass, [self._switch_eid, self._active_helper, self._next_helper, override_eid], _on_change))
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=60)))
        self._refresh_attrs()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        for u in self._unsubs:
            u()
        self._unsubs = []

    def _refresh_attrs(self):
        sw = self.hass.states.get(self._switch_eid)
        active = self.hass.states.get(self._active_helper)
        nxt = self.hass.states.get(self._next_helper)
        # Check if Calendar Override is enabled for this specific mode
        override_switch = self.hass.states.get(f"switch.{self._key}_calendar_override")
        calendar_override_on = override_switch and override_switch.state == STATE_ON

        # Determine who controls the switch
        if calendar_override_on:
            # When override is on, it's always manual
            self._controlled_by = "manual"
        elif sw and sw.state == STATE_ON and active and active.state == "active":
            # Switch is on and there's an active calendar event
            self._controlled_by = "calendar"
        else:
            self._controlled_by = "manual"

        # Next event attrs
        if nxt:
            self._next_start = nxt.attributes.get("next_start")
            self._next_end = nxt.attributes.get("next_end")
        else:
            self._next_start = self._next_end = None

        # Active event attrs (Active Started, Active End)
        if active and active.state == "active":
            self._active_start = active.attributes.get("start")
            self._active_end = active.attributes.get("end")
            # Update last_ended when event becomes inactive
        elif self._active_start:
            # Event was active but now isn't - record when it ended
            self._last_ended = dt_util.now().isoformat()
            self._active_start = None
            self._active_end = None
        else:
            self._active_start = None
            self._active_end = None

    async def _tick(self, _now):
        self._refresh_attrs()
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        st = self.hass.states.get(self._switch_eid)
        return bool(st and st.state == STATE_ON)

    @property
    def extra_state_attributes(self):
        return {
            "mode_key": self._key,
            "controlled_by": self._controlled_by,
            "active_started": self._active_start,
            "active_end": self._active_end,
            "last_ended": self._last_ended,
            "next_calendar_start": self._next_start,
            "next_calendar_end": self._next_end,
        }


class ModeEventActiveBinarySensor(BinarySensorEntity):
    """Binary sensor that shows if there's an active calendar event (regardless of override or manual control)."""
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str, friendly: str):
        self.hass = hass
        self._entry = entry
        self._key = key
        self._attr_name = f"{friendly} Event Active"
        self._attr_unique_id = f"{entry.entry_id}_{key}_event_active"
        self._active_helper = f"sensor.{DOMAIN}_active_{key}_event"
        self._override_eid = f"switch.{key}_calendar_override"
        self._switch_eid = f"switch.{key}"
        self._unsubs = []

    @property
    def device_info(self):
        return device_info_for_mode(self._entry.entry_id, self._key)

    async def async_added_to_hass(self):
        @callback
        def _on_change(_event):
            self.async_write_ha_state()
        # Track active event helper, override switch, and mode switch
        self._unsubs.append(async_track_state_change_event(
            self.hass,
            [self._active_helper, self._override_eid, self._switch_eid],
            _on_change
        ))
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        for u in self._unsubs:
            u()
        self._unsubs = []

    @property
    def is_on(self) -> bool:
        """ON when there's an active calendar event (even with override), but NOT when manually turned on."""
        active = self.hass.states.get(self._active_helper)
        sw = self.hass.states.get(self._switch_eid)

        # If there's an active calendar event, we're ON
        if active and active.state == "active":
            return True

        # If the switch is manually on (no active calendar event), we're OFF
        # This differentiates between calendar-triggered and manual activation
        return False

    @property
    def extra_state_attributes(self):
        active = self.hass.states.get(self._active_helper)
        override_switch = self.hass.states.get(self._override_eid)
        override_on = override_switch and override_switch.state == STATE_ON

        attrs = {
            "mode_key": self._key,
            "calendar_override_enabled": override_on,
        }

        # Add calendar event start/end times if there's an active event
        if active and active.state == "active":
            attrs["event_start"] = active.attributes.get("start")
            attrs["event_end"] = active.attributes.get("end")
            attrs["event_summary"] = active.attributes.get("summary")

        return attrs


class EventModesSummaryBinarySensor(BinarySensorEntity):
    _attr_name = "Shul Modes · Event Modes"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, event_keys: List[str]):
        self.hass = hass
        self._entry = entry
        self._event_keys = event_keys
        self._attr_unique_id = f"{entry.entry_id}_event_modes"
        self._unsub = None

    @property
    def device_info(self):
        return device_info_main(self._entry.entry_id)

    async def async_added_to_hass(self):
        @callback
        def _on_change(_ev):
            self.async_write_ha_state()
        sw_ids = [f"switch.{k}" for k in self._event_keys]
        self._unsub = async_track_state_change_event(self.hass, sw_ids, _on_change)
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def is_on(self) -> bool:
        return any((self.hass.states.get(f"switch.{k}") or {}).state == "on" for k in self._event_keys)

    @property
    def extra_state_attributes(self):
        active = [
            k
            for k in self._event_keys
            if (self.hass.states.get(f"switch.{k}") or None)
            and self.hass.states.get(f"switch.{k}").state == "on"
        ]
        return {"active_event_modes": active}


class EventRunningWithOverrideBinarySensor(BinarySensorEntity):
    """Binary sensor that is ON when any mode is running AND its calendar override is enabled."""
    _attr_name = "Shul Modes · Event Running with Calendar Override"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, mode_keys: List[str]):
        self.hass = hass
        self._entry = entry
        self._mode_keys = mode_keys
        self._attr_unique_id = f"{entry.entry_id}_event_running_with_override"
        self._unsub = None

    @property
    def device_info(self):
        return device_info_main(self._entry.entry_id)

    async def async_added_to_hass(self):
        @callback
        def _on_change(_ev):
            self.async_write_ha_state()

        # Track all mode switches and their calendar override switches
        entity_ids = []
        for k in self._mode_keys:
            entity_ids.append(f"switch.{k}")
            entity_ids.append(f"switch.{k}_calendar_override")

        self._unsub = async_track_state_change_event(self.hass, entity_ids, _on_change)
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        if self._unsub:
            self._unsub()
            self._unsub = None

    @property
    def is_on(self) -> bool:
        """Check if any mode is ON AND its calendar override is also ON."""
        for k in self._mode_keys:
            mode_switch = self.hass.states.get(f"switch.{k}")
            override_switch = self.hass.states.get(f"switch.{k}_calendar_override")

            # Check if both the mode and its override are ON
            if (mode_switch and mode_switch.state == STATE_ON and
                override_switch and override_switch.state == STATE_ON):
                return True
        return False

    @property
    def extra_state_attributes(self):
        """List all modes that are running with calendar override enabled."""
        modes_with_override = []
        for k in self._mode_keys:
            mode_switch = self.hass.states.get(f"switch.{k}")
            override_switch = self.hass.states.get(f"switch.{k}_calendar_override")

            if (mode_switch and mode_switch.state == STATE_ON and
                override_switch and override_switch.state == STATE_ON):
                modes_with_override.append(k)

        return {
            "modes_with_override": modes_with_override,
            "description": "Shows when any mode is running AND its calendar override is enabled"
        }


class DSTBinarySensor(BinarySensorEntity):
    _attr_name = "Shul Modes · DST"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_dst"

    @property
    def device_info(self):
        return device_info_main(self._entry.entry_id)

    @property
    def is_on(self) -> bool:
        now = dt_util.now()
        tz = now.tzinfo
        return bool(tz.dst(now))  # type: ignore[arg-type]

    @staticmethod
    def _next_dst_transition():
        now = dt_util.now()
        tz = now.tzinfo
        cur = tz.dst(now)  # type: ignore[arg-type]
        t = now
        limit = now + timedelta(days=730)
        while t < limit:
            nxt = t + timedelta(hours=6)
            if tz.dst(nxt) != cur:  # type: ignore[arg-type]
                lo, hi = t, nxt
                while (hi - lo) > timedelta(minutes=60):
                    mid = lo + (hi - lo) / 2
                    if tz.dst(mid) == cur:  # type: ignore[arg-type]
                        lo = mid
                    else:
                        hi = mid
                while (hi - lo) > timedelta(minutes=1):
                    mid = lo + (hi - lo) / 2
                    if tz.dst(mid) == cur:  # type: ignore[arg-type]
                        lo = mid
                    else:
                        hi = mid
                return hi.replace(second=0, microsecond=0).isoformat()
            t = nxt
        return None

    @property
    def extra_state_attributes(self):
        return {"in_dst": self.is_on, "next_change": self._next_dst_transition()}
