from __future__ import annotations
import logging
from datetime import timedelta
from typing import Dict, List, Any
from homeassistant.components.sensor import SensorEntity, RestoreSensor
from homeassistant.core import HomeAssistant, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers import entity_registry as er

from .const import SIGNAL_EVENTS_UPDATED, device_info_for_mode, device_info_main

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    mgr = _Mgr(hass, entry, async_add_entities)
    await mgr.async_setup()


class _Mgr:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, add):
        self.hass = hass
        self.entry = entry
        self.add = add
        self.ents: Dict[str, _EventSensor] = {}
        self.unsub = None
        self.unsub_tick = None
        self.old = _OldEventsSensor(entry)  # single sink sensor

    async def async_setup(self):
        self.add([self.old])  # create the sink
        self.unsub = async_dispatcher_connect(self.hass, SIGNAL_EVENTS_UPDATED, self._on_events)
        self.unsub_tick = async_track_time_interval(self.hass, self._tick, timedelta(minutes=1))

    async def _tick(self, _now):
        # remove finished event sensors; add them to Old Events
        now = dt_util.now()
        for eid, ent in list(self.ents.items()):
            if ent.has_finished(now):
                self.old.add_old_event(ent.event_payload())
                ent.async_remove()
                self.ents.pop(eid, None)
            else:
                ent.async_write_ha_state()
        self.old.async_write_ha_state()

    @callback
    def _on_events(self, entry_id: str, events: List[Dict[str, Any]]):
        _LOGGER.info("Sensor manager received SIGNAL_EVENTS_UPDATED: entry_id=%s, events=%d", entry_id, len(events))

        if entry_id != self.entry.entry_id:
            _LOGGER.debug("Ignoring events for different entry: %s vs %s", entry_id, self.entry.entry_id)
            return

        cur = set(self.ents.keys())
        _LOGGER.info("Current sensors: %d (%s)", len(cur), cur)

        incoming = set([ev["uid"] for ev in events])
        _LOGGER.info("Incoming events: %d (%s)", len(incoming), incoming)

        new = []
        for ev in events:
            i = ev["uid"]
            if i not in self.ents:
                ent = _EventSensor(self.entry, ev)
                self.ents[i] = ent
                new.append(ent)
                _LOGGER.info("Creating new event sensor: %s (uid=%s)", ent.name, i)
            else:
                self.ents[i].update_event(ev)
                _LOGGER.debug("Updating event sensor: uid=%s", i)

        if new:
            _LOGGER.info("Adding %d new event sensors", len(new))
            self.add(new)

        # events deleted externally → treat as finished & archive
        deleted_ids = cur - incoming
        if deleted_ids:
            _LOGGER.warning("DELETING %d event sensors: %s", len(deleted_ids), deleted_ids)

        for rid in list(deleted_ids):
            ent = self.ents.pop(rid, None)
            if ent:
                _LOGGER.warning("⚠️ REMOVING EVENT SENSOR: %s (uid=%s) - calling async_remove()", ent.name, rid)
                self.old.add_old_event(ent.event_payload())

                # Method 1: Call async_remove on the entity
                try:
                    async def _remove_with_callback():
                        _LOGGER.warning("🔄 Starting async_remove for %s", ent.name)
                        try:
                            await ent.async_remove()
                            _LOGGER.warning("✅ Successfully removed entity %s", ent.name)
                        except Exception as ex:
                            _LOGGER.error("❌ async_remove failed for %s: %s", ent.name, ex, exc_info=True)

                    task = self.hass.async_create_task(_remove_with_callback())
                    _LOGGER.warning("⚠️ Scheduled removal task for %s", ent.name)
                except Exception as e:
                    _LOGGER.error("Failed to schedule async_remove for %s: %s", ent.name, e)

                # Method 2: Force remove from entity registry as backup
                try:
                    registry = er.async_get(self.hass)
                    entity_id = ent.entity_id
                    if entity_id and registry.async_get(entity_id):
                        _LOGGER.warning("⚠️ Force removing %s from entity registry", entity_id)
                        registry.async_remove(entity_id)
                except Exception as e:
                    _LOGGER.error("Failed to force remove from registry: %s", e)
            else:
                _LOGGER.error("Could not find sensor for uid=%s to remove!", rid)

        _LOGGER.info("Sensor manager update complete. Current sensors: %d", len(self.ents))
        self.old.async_write_ha_state()


class _EventSensor(RestoreSensor):
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = True

    def __init__(self, entry: ConfigEntry, ev: Dict[str, Any]):
        self.entry = entry
        self._ev: Dict[str, Any] = dict(ev)
        self._attr_unique_id = f"{entry.entry_id}_event_{ev['uid']}"

    @property
    def device_info(self):
        return device_info_for_mode(self.entry.entry_id, self._ev.get("mode_key", "event_mode"))

    @property
    def name(self) -> str:
        return self._ev.get("summary") or f"Event {self._ev.get('uid', '')}"

    def has_finished(self, now=None) -> bool:
        """Returns True if event ended more than 1 day ago"""
        now = now or dt_util.now()
        en = dt_util.parse_datetime(self._ev.get("end"))
        if not en:
            return False
        # Delete sensor 1 day after event ends
        return now >= (en + timedelta(days=1))

    def event_payload(self) -> Dict[str, Any]:
        return dict(self._ev)

    @property
    def state(self) -> str:
        now = dt_util.now()
        st = dt_util.parse_datetime(self._ev.get("start"))
        en = dt_util.parse_datetime(self._ev.get("end"))
        if not st or not en:
            return "unknown"
        if now < st:
            return "upcoming"
        if st <= now < en:
            return "running"
        return "ended"

    @property
    def extra_state_attributes(self):
        st = dt_util.parse_datetime(self._ev.get("start") or "")
        en = dt_util.parse_datetime(self._ev.get("end") or "")
        dur = (en - st).total_seconds() if (st and en) else None
        out = dict(self._ev)
        out["duration_seconds"] = dur
        return out

    def update_event(self, ev: Dict[str, Any]):
        self._ev = dict(ev)


class _OldEventsSensor(SensorEntity):
    """Single sensor that accumulates past events in attributes."""
    _attr_should_poll = False
    _attr_name = "Old Events"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = True

    def __init__(self, entry: ConfigEntry):
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_old_events"
        self._events: Dict[str, Dict[str, Any]] = {}

    @property
    def device_info(self):
        return device_info_main(self._entry.entry_id)

    @property
    def state(self) -> int:
        return len(self._events)

    def add_old_event(self, payload: Dict[str, Any]):
        eid = payload.get("uid") or f"e_{len(self._events)+1}"
        self._events[str(eid)] = payload

    @property
    def extra_state_attributes(self):
        # flatten each event into its own attribute key
        attrs = {"count": len(self._events)}
        for k, ev in self._events.items():
            attrs[f"event_{k}"] = ev
        return attrs
