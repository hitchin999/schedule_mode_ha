from __future__ import annotations
import uuid
import logging
from datetime import datetime, timedelta, date, time
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.components.calendar import (
    CalendarEntity, CalendarEvent, CalendarEntityFeature
)
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity import DeviceInfo

# Jewish date/holiday libs (local, no HTTP)
from zoneinfo import ZoneInfo
from astral import LocationInfo
from astral.sun import sun
from pyluach.hebrewcal import HebrewDate as PHebrewDate, Year as PYear
from hdate import HDateInfo
from hdate.translator import set_language

# IMPORTANT: import your constants from the actual domain package
from .const import (
    DOMAIN, STORAGE_VERSION, STORAGE_EVENTS_KEY, SIGNAL_EVENTS_UPDATED,
    OPT_ENABLED_MODES, device_info_for_mode, device_info_main, mode_friendly,
    OPT_LINK_NO_TACHANUN_FOR_BRIS,  # NEW
)

_LOGGER = logging.getLogger(__name__)

def _to_iso(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, str):
        dt = dt_util.parse_datetime(val)
        if dt is None:
            d = dt_util.parse_date(val)
            if d:
                dt = datetime.combine(d, time(0, 0), tzinfo=dt_util.DEFAULT_TIME_ZONE)
            else:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return dt.isoformat()
    if isinstance(val, datetime):
        return (val if val.tzinfo else val.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)).isoformat()
    if isinstance(val, date):
        return datetime.combine(val, time(0, 0), tzinfo=dt_util.DEFAULT_TIME_ZONE).isoformat()
    try:
        dt = dt_util.parse_datetime(val)  # type: ignore[arg-type]
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return dt.isoformat() if dt else None
    except Exception:
        return None

def _coerce_endpoint(v: Any, *, is_end: bool = False) -> Optional[str]:
    if isinstance(v, dict):
        if v.get("dateTime"):
            return _to_iso(v["dateTime"])
        if v.get("date"):
            d = dt_util.parse_date(v["date"])
            if not d:
                return None
            if is_end:
                d = d + timedelta(days=1)
            return _to_iso(d)
    if isinstance(v, (str, datetime, date)):
        return _to_iso(v)
    for k in ("dateTime", "datetime", "value", "date"):
        if isinstance(v, dict) and v.get(k):
            if k == "date":
                d = dt_util.parse_date(v[k])
                if d and is_end:
                    d = d + timedelta(days=1)
                return _to_iso(d or v[k])
            return _to_iso(v[k])
    return None

def _extract_summary(payload: Dict[str, Any]) -> str:
    return payload.get("summary") or payload.get("title") or payload.get("name") or "Event"

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}_events.json")
    data = await store.async_load() or {}
    events: List[Dict[str, Any]] = data.get(STORAGE_EVENTS_KEY, [])

    enabled = entry.options.get(OPT_ENABLED_MODES, [])
    entities: List[CalendarEntity] = [ModeCalendar(hass, entry, store, events, mk) for mk in enabled]
    entities.append(JewishDatesCalendar(hass, entry))
    async_add_entities(entities, True)

    # Dispatch initial events to sensor manager after calendars are created
    async_dispatcher_send(hass, SIGNAL_EVENTS_UPDATED, entry.entry_id, list(events))

class ModeCalendar(CalendarEntity):
    _attr_has_entity_name = True
    _attr_supported_features = CalendarEntityFeature.CREATE_EVENT | CalendarEntityFeature.UPDATE_EVENT | CalendarEntityFeature.DELETE_EVENT

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, store: Store, events_all: List[Dict[str, Any]], mode_key: str):
        self.hass = hass
        self.entry = entry
        self.store = store
        self.mode_key = mode_key
        self._name = f"{mode_friendly(mode_key)}"
        self._attr_name = self._name
        self._attr_unique_id = f"{entry.entry_id}_calendar_{mode_key}"
        self._events_all: List[Dict[str, Any]] = list(events_all)
        self._event: Optional[CalendarEvent] = None
        self._unsub_tick = None
        # NEW: track if we enabled No Tachanun because of Bris
        self._linked_nt_on: bool = False

    @property
    def device_info(self) -> DeviceInfo:
        return device_info_for_mode(self.entry.entry_id, self.mode_key)

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id

    async def async_added_to_hass(self):
        self._unsub_tick = async_track_time_interval(self.hass, self._tick, timedelta(seconds=30))
        await self._tick(dt_util.now())

    async def async_will_remove_from_hass(self):
        if self._unsub_tick:
            self._unsub_tick()

    async def _persist(self):
        await self.store.async_save({STORAGE_EVENTS_KEY: self._events_all})

    def _my_events(self) -> List[Dict[str, Any]]:
        return [e for e in self._events_all if e.get("mode_key") == self.mode_key]

    @property
    def event(self) -> Optional[CalendarEvent]:
        return self._event

    async def async_get_events(self, hass, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        out: List[CalendarEvent] = []
        for e in self._my_events():
            st = dt_util.parse_datetime(e.get("start"))
            en = dt_util.parse_datetime(e.get("end"))
            if not st or not en:
                continue
            if st <= end_date and en >= start_date:
                # Include uid so HA can identify events for update/delete operations
                out.append(CalendarEvent(
                    start=st,
                    end=en,
                    summary=e.get("summary") or self._name,
                    uid=e.get("uid"),
                    description=e.get("extra", {}).get("description")
                ))
        return out

    async def async_create_event(self, **kwargs) -> None:
        try:
            summary = _extract_summary(kwargs)
            start = _coerce_endpoint(kwargs.get("start"), is_end=False) or _coerce_endpoint(kwargs.get("dtstart"), is_end=False) or _to_iso(kwargs.get("start_date"))
            end = _coerce_endpoint(kwargs.get("end"), is_end=True) or _coerce_endpoint(kwargs.get("dtend"), is_end=True) or _to_iso(kwargs.get("end_date"))
            if not start and isinstance(kwargs.get("start"), dict) and kwargs["start"].get("date"):
                start = _coerce_endpoint(kwargs["start"], is_end=False)
            if not end and isinstance(kwargs.get("end"), dict) and kwargs["end"].get("date"):
                end = _coerce_endpoint(kwargs["end"], is_end=True)
            if not start or not end:
                _LOGGER.debug("create_event: missing/invalid start/end: %s", kwargs); return
            st = dt_util.parse_datetime(start); en = dt_util.parse_datetime(end)
            if not st or not en or en <= st:
                _LOGGER.debug("create_event: invalid range start=%s end=%s", start, end); return
            await self._add_event_internal(start, end, summary, original=kwargs)
        except Exception as exc:
            _LOGGER.warning("create_event failed: %s payload=%s", exc, kwargs); return

    async def async_update_event(
        self,
        uid: str,
        event: dict[str, Any],
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Update an existing event on the calendar."""
        try:
            # Validate uid
            if not uid:
                _LOGGER.error("update_event called with empty uid. uid=%r, event=%r", uid, event)
                return

            # Validate event data
            if not event or not isinstance(event, dict):
                _LOGGER.error("update_event called with invalid event data. uid=%r, event=%r", uid, event)
                return

            updated_data = dict(event)

            found = False
            for e in self._events_all:
                if e.get("uid") == uid and e["mode_key"] == self.mode_key:
                    found = True

                    # Prevent editing No Tachnun events that are linked from Bris
                    if self.mode_key == "no_tachnun" and e.get("extra", {}).get("linked_from_bris"):
                        _LOGGER.warning("Cannot edit No Tachnun event %s - it is linked from a Bris event", uid)
                        return

                    old_event = dict(e)  # Save for Bris sync

                    # Update summary
                    if "summary" in updated_data or "title" in updated_data or "name" in updated_data:
                        e["summary"] = _extract_summary(updated_data)

                    # Update description (only if it's not None and is present)
                    if "description" in updated_data:
                        desc = updated_data.get("description")
                        if desc is not None:  # Allow empty string, but not None
                            e.setdefault("extra", {})["description"] = desc

                    # Update start/end times
                    if "start" in updated_data:
                        e["start"] = _coerce_endpoint(updated_data["start"], is_end=False)
                    if "end" in updated_data:
                        e["end"] = _coerce_endpoint(updated_data["end"], is_end=True)
                    if "dtstart" in updated_data:
                        e["start"] = _coerce_endpoint(updated_data["dtstart"], is_end=False)
                    if "dtend" in updated_data:
                        e["end"] = _coerce_endpoint(updated_data["dtend"], is_end=True)
                    if "start_date" in updated_data:
                        e["start"] = _to_iso(updated_data["start_date"])
                    if "end_date" in updated_data:
                        e["end"] = _to_iso(updated_data["end_date"])

                    # Update extra fields (excluding known fields and None values)
                    excluded = {"summary", "title", "name", "start", "end", "dtstart", "dtend",
                               "start_date", "end_date", "description", "uid"}
                    e.setdefault("extra", {}).update({
                        k: v for k, v in updated_data.items()
                        if k not in excluded and v is not None
                    })

                    await self._persist()
                    await self._tick(dt_util.now())
                    async_dispatcher_send(self.hass, SIGNAL_EVENTS_UPDATED, self.entry.entry_id, list(self._events_all))

                    # Sync Bris -> No Tachnun (only if this is a Bris event)
                    if self.mode_key == "bris":
                        await self._sync_bris_to_no_tachnun_update(old_event, e)
                    return

            if not found:
                _LOGGER.warning("update_event: event with uid=%s not found in mode=%s", uid, self.mode_key)
        except Exception as exc:
            _LOGGER.error("update_event failed: %s uid=%s event=%s", exc, uid, event, exc_info=True)

    async def async_delete_event(
        self,
        uid: str,
        recurrence_id: str | None = None,
        recurrence_range: str | None = None,
    ) -> None:
        """Delete an event on the calendar."""
        try:
            # Validate uid
            if not uid:
                _LOGGER.error("delete_event called with empty uid. uid=%r", uid)
                return

            # Find the event before deleting for Bris sync
            deleted_event = None
            for e in self._events_all:
                if e.get("uid") == uid and e.get("mode_key") == self.mode_key:
                    deleted_event = dict(e)
                    break

            if not deleted_event:
                _LOGGER.warning("delete_event: event with uid=%s not found in mode=%s", uid, self.mode_key)
                return

            # Prevent deleting No Tachnun events that are linked from Bris
            if self.mode_key == "no_tachnun" and deleted_event.get("extra", {}).get("linked_from_bris"):
                _LOGGER.warning("Cannot delete No Tachnun event %s - it is linked from a Bris event. Delete the Bris event instead.", uid)
                return

            # Remove the event
            _LOGGER.info("Deleting event uid=%s from mode=%s, summary='%s'", uid, self.mode_key, deleted_event.get("summary"))
            self._events_all = [e for e in self._events_all if not (e.get("uid") == uid and e.get("mode_key") == self.mode_key)]
            _LOGGER.info("Event removed. Remaining events: %d", len(self._events_all))

            await self._persist()
            await self._tick(dt_util.now())

            # Dispatch updated event list to sensor manager
            _LOGGER.info("Dispatching SIGNAL_EVENTS_UPDATED with %d events", len(self._events_all))
            async_dispatcher_send(self.hass, SIGNAL_EVENTS_UPDATED, self.entry.entry_id, list(self._events_all))

            # Sync Bris -> No Tachnun deletion (only if this is a Bris event)
            if self.mode_key == "bris":
                await self._sync_bris_to_no_tachnun_delete(deleted_event)

        except Exception as exc:
            _LOGGER.error("delete_event failed: %s uid=%s", exc, uid, exc_info=True)

    async def _add_event_internal(self, start_iso: str, end_iso: str, summary: str, *, original: Dict[str, Any] | None = None):
        ev = {
            "uid": str(uuid.uuid4()),
            "mode_key": self.mode_key,
            "start": start_iso,
            "end": end_iso,
            "summary": summary,
            "created": dt_util.now().isoformat(),
        }
        if original:
            ev["extra"] = {k: v for k, v in original.items() if k not in ("summary","title","name","start","end","dtstart","dtend","start_date","end_date")}
        self._events_all.append(ev)
        await self._persist(); await self._tick(dt_util.now())
        async_dispatcher_send(self.hass, SIGNAL_EVENTS_UPDATED, self.entry.entry_id, list(self._events_all))
        self.async_write_ha_state()

        # Sync Bris -> No Tachnun creation (only if this is a Bris event)
        if self.mode_key == "bris":
            await self._sync_bris_to_no_tachnun_create(ev)

    async def _sync_bris_to_no_tachnun_create(self, bris_event: Dict[str, Any]) -> None:
        """When a Bris event is created, create a linked No Tachnun event."""
        try:
            sync_enabled = self.entry.options.get(OPT_LINK_NO_TACHANUN_FOR_BRIS, False)
            _LOGGER.info("Bris event created '%s' - sync enabled: %s, mode: %s",
                        bris_event.get("summary"), sync_enabled, self.mode_key)

            if not sync_enabled:
                _LOGGER.debug("Bris->No Tachnun sync is disabled in options")
                return

            if self.mode_key != "bris":
                _LOGGER.debug("Not a Bris calendar, skipping sync")
                return

            # Create a linked event in No Tachnun with reference to Bris event
            nt_event = {
                "uid": str(uuid.uuid4()),
                "mode_key": "no_tachnun",
                "start": bris_event["start"],
                "end": bris_event["end"],
                "summary": f"Bris: {bris_event['summary']}",
                "created": dt_util.now().isoformat(),
                "extra": {
                    "linked_from_bris": True,
                    "bris_event_uid": bris_event["uid"],
                }
            }
            self._events_all.append(nt_event)
            await self._persist()
            async_dispatcher_send(self.hass, SIGNAL_EVENTS_UPDATED, self.entry.entry_id, list(self._events_all))
            _LOGGER.info("✓ Created linked No Tachnun event '%s' for Bris event '%s'",
                        nt_event["summary"], bris_event["summary"])
        except Exception as exc:
            _LOGGER.error("Failed to sync Bris create to No Tachnun: %s", exc, exc_info=True)

    async def _sync_bris_to_no_tachnun_update(self, old_bris: Dict[str, Any], new_bris: Dict[str, Any]) -> None:
        """When a Bris event is updated, update the linked No Tachnun event."""
        try:
            sync_enabled = self.entry.options.get(OPT_LINK_NO_TACHANUN_FOR_BRIS, False)
            _LOGGER.info("Bris event updated '%s' -> '%s' - sync enabled: %s, mode: %s",
                        old_bris.get("summary"), new_bris.get("summary"), sync_enabled, self.mode_key)

            if not sync_enabled:
                return
            if self.mode_key != "bris":
                return

            # Find linked No Tachnun event
            bris_uid = new_bris["uid"]
            for nt_event in self._events_all:
                if nt_event.get("mode_key") == "no_tachnun":
                    extra = nt_event.get("extra", {})
                    if extra.get("bris_event_uid") == bris_uid:
                        # Update the linked event
                        nt_event["start"] = new_bris["start"]
                        nt_event["end"] = new_bris["end"]
                        nt_event["summary"] = f"Bris: {new_bris['summary']}"
                        await self._persist()
                        async_dispatcher_send(self.hass, SIGNAL_EVENTS_UPDATED, self.entry.entry_id, list(self._events_all))
                        _LOGGER.info("✓ Updated linked No Tachnun event for Bris '%s'", new_bris["summary"])
                        return

            _LOGGER.warning("Could not find linked No Tachnun event for Bris event %s", bris_uid)
        except Exception as exc:
            _LOGGER.error("Failed to sync Bris update to No Tachnun: %s", exc, exc_info=True)

    async def _sync_bris_to_no_tachnun_delete(self, bris_event: Dict[str, Any]) -> None:
        """When a Bris event is deleted, delete the linked No Tachnun event."""
        try:
            sync_enabled = self.entry.options.get(OPT_LINK_NO_TACHANUN_FOR_BRIS, False)
            _LOGGER.info("Bris event deleted '%s' - sync enabled: %s, mode: %s",
                        bris_event.get("summary"), sync_enabled, self.mode_key)

            if not sync_enabled:
                return
            if self.mode_key != "bris":
                return

            # Find and delete linked No Tachnun event
            bris_uid = bris_event["uid"]
            original_count = len(self._events_all)
            self._events_all = [
                e for e in self._events_all
                if not (e.get("mode_key") == "no_tachnun" and e.get("extra", {}).get("bris_event_uid") == bris_uid)
            ]
            if len(self._events_all) < original_count:
                await self._persist()
                async_dispatcher_send(self.hass, SIGNAL_EVENTS_UPDATED, self.entry.entry_id, list(self._events_all))
                _LOGGER.info("✓ Deleted linked No Tachnun event for Bris '%s'", bris_event["summary"])
            else:
                _LOGGER.warning("Could not find linked No Tachnun event to delete for Bris %s", bris_uid)
        except Exception as exc:
            _LOGGER.error("Failed to sync Bris delete to No Tachnun: %s", exc, exc_info=True)

    async def _tick(self, _now):
        """Toggle switch during event windows + publish active/next helper sensors."""
        now = dt_util.now()
        sw_eid = f"switch.{self.mode_key}"

        # Check if Calendar Override is enabled for this specific mode
        override_switch = self.hass.states.get(f"switch.{self.mode_key}_calendar_override")
        calendar_override_on = override_switch and override_switch.state == "on"

        active = None
        next_up: Optional[Tuple[datetime, Dict[str, Any]]] = None

        for e in self._my_events():
            st = dt_util.parse_datetime(e.get("start")); en = dt_util.parse_datetime(e.get("end"))
            if not st or not en:
                continue
            sw = self.hass.states.get(sw_eid)
            if st <= now < en:
                active = e
                # Only control switch if Calendar Override is OFF
                if not calendar_override_on:
                    if not sw or sw.state != "on":
                        # NOTE: no extra keys in service data!
                        await self.hass.services.async_call("switch", "turn_on", {"entity_id": sw_eid}, blocking=False)
            elif st > now and (not next_up or st < next_up[0]):
                next_up = (st, e)
            # Only control switch if Calendar Override is OFF
            if not calendar_override_on:
                if now >= en and sw and sw.state == "on":
                    await self.hass.services.async_call("switch", "turn_off", {"entity_id": sw_eid}, blocking=False)

        # If no active event and switch is on, turn it off (handles deleted events or events ending)
        # This ensures that when an event is deleted while active, the switch turns off
        if not active and not calendar_override_on:
            sw = self.hass.states.get(sw_eid)
            if sw and sw.state == "on":
                await self.hass.services.async_call("switch", "turn_off", {"entity_id": sw_eid}, blocking=False)

        # Calendar tile state
        if active:
            st = dt_util.parse_datetime(active.get("start")); en = dt_util.parse_datetime(active.get("end"))
            self._event = CalendarEvent(
                start=st,
                end=en,
                summary=active.get("summary") or self._name,
                uid=active.get("uid"),
                description=active.get("extra", {}).get("description")
            )
        elif next_up:
            st, e = next_up
            en = dt_util.parse_datetime(e.get("end"))
            self._event = CalendarEvent(
                start=st,
                end=en,
                summary=e.get("summary") or self._name,
                uid=e.get("uid"),
                description=e.get("extra", {}).get("description")
            )
        else:
            self._event = None

        # Helper sensors that binaries can read
        ns = ne = None
        if next_up:
            ns, ne = next_up[1].get("start"), next_up[1].get("end")

        self.hass.states.async_set(
            f"sensor.{DOMAIN}_next_{self.mode_key}_event",
            "scheduled" if ns else "none",
            {"next_start": ns, "next_end": ne, "mode_key": self.mode_key},
        )

        if active:
            self.hass.states.async_set(
                f"sensor.{DOMAIN}_active_{self.mode_key}_event",
                "active",
                {"start": active.get("start"), "end": active.get("end"), "summary": active.get("summary"), "mode_key": self.mode_key},
            )
        else:
            self.hass.states.async_set(
                f"sensor.{DOMAIN}_active_{self.mode_key}_event",
                "none",
                {"mode_key": self.mode_key},
            )

        # --- NEW: Link "Bris" -> "No Tachanun" if enabled in options ---
        try:
            link_enabled = bool(self.entry.options.get(OPT_LINK_NO_TACHANUN_FOR_BRIS, False))
        except Exception:
            link_enabled = False

        if link_enabled and self.mode_key == "bris":
            nt_eid = "switch.no_tachnun"
            nt_state = self.hass.states.get(nt_eid)

            if active:
                # Ensure No Tachanun is ON during a Bris event
                if not nt_state or nt_state.state != "on":
                    await self.hass.services.async_call("switch", "turn_on", {"entity_id": nt_eid}, blocking=False)
                    self._linked_nt_on = True
            else:
                # Bris not active: if we turned NT on, turn it back off
                if self._linked_nt_on:
                    if nt_state and nt_state.state == "on":
                        await self.hass.services.async_call("switch", "turn_off", {"entity_id": nt_eid}, blocking=False)
                    self._linked_nt_on = False
        # --- END NEW ---

        self.async_write_ha_state()

# ——— Jewish Dates (one all-day per civil day; holiday included) ———

def _hebrew_month_name(month: int, year: int) -> str:
    if month == 12:
        return "אדר א׳" if PYear(year).leap else "אדר"
    if month == 13:
        return "אדר ב׳"
    return {1:"ניסן",2:"אייר",3:"סיון",4:"תמוז",5:"אב",6:"אלול",7:"תשרי",8:"חשון",9:"כסלו",10:"טבת",11:"שבט"}.get(month, "")

def _int_to_hebrew(n: int) -> str:
    letters = [(400,"ת"),(300,"ש"),(200,"ר"),(100,"ק"),(90,"צ"),(80,"פ"),(70,"ע"),(60,"ס"),(50,"נ"),(40,"מ"),(30,"ל"),(20,"כ"),(10,"י"),(9,"ט"),(8,"ח"),(7,"ז"),(6,"ו"),(5,"ה"),(4,"ד"),(3,"ג"),(2,"ב"),(1,"א")]
    out=""; 
    for val,ch in letters:
        while n>=val:
            out+=ch; n-=val
    if len(out)>=2: out=out[:-1]+"״"+out[-1]
    elif len(out)==1: out+="׳"
    return out

class JewishDatesCalendar(CalendarEntity):
    """Informational calendar: exactly one ALL-DAY event per civil day (midnight→midnight).
       Label = Hebrew date chosen at 12:00 noon local + holiday (diaspora) if any."""
    _attr_name = "Jewish Dates"
    _attr_supported_features = 0

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry):
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_calendar_jewish_dates"
        self._events: List[CalendarEvent] = []
        set_language("he")

    @property
    def device_info(self) -> DeviceInfo:
        return device_info_main(self.entry.entry_id)

    @property
    def unique_id(self) -> str:
        return self._attr_unique_id

    @property
    def event(self) -> Optional[CalendarEvent]:
        now = dt_util.now()
        cur, nxt = None, None
        for ev in self._events:
            if ev.start <= now < ev.end:
                cur = ev; break
            if ev.start > now and (nxt is None or ev.start < nxt.start):
                nxt = ev
        return cur or nxt

    async def async_added_to_hass(self):
        await self._rebuild_window(dt_util.now().date(), (dt_util.now()+timedelta(days=180)).date())
        async_track_time_interval(self.hass, self._refresh_tick, timedelta(hours=12))

    async def _refresh_tick(self, _):
        await self._rebuild_window(dt_util.now().date(), (dt_util.now()+timedelta(days=180)).date())

    async def async_get_events(self, hass, start_date: datetime, end_date: datetime) -> list[CalendarEvent]:
        await self._rebuild_window(start_date.date(), end_date.date())
        return [e for e in self._events if e.start <= end_date and e.end >= start_date]

    async def _rebuild_window(self, start_day: date, end_day: date):
        tz = ZoneInfo(self.hass.config.time_zone)
        loc = LocationInfo(latitude=self.hass.config.latitude, longitude=self.hass.config.longitude, timezone=self.hass.config.time_zone)
        events: List[CalendarEvent] = []
        day = start_day
        while day <= end_day:
            noon = datetime.combine(day, time(12,0)).replace(tzinfo=tz)
            s = sun(loc.observer, date=day, tzinfo=tz)
            switch_time = (s["sunset"] + timedelta(minutes=42)).replace(second=0, microsecond=0)
            py_for_hebrew = (day + timedelta(days=1)) if noon >= switch_time else day

            h = PHebrewDate.from_pydate(py_for_hebrew)
            heb_day = _int_to_hebrew(h.day)
            heb_mon = _hebrew_month_name(h.month, h.year)
            heb_year = _int_to_hebrew(h.year % 1000)

            # Diaspora names so Y"T shows properly outside Israel
            try:
                hi = HDateInfo(py_for_hebrew, diaspora=True)
                hol = hi.holiday_description() or ""
            except Exception:
                hol = ""

            title = f"{heb_day} {heb_mon} {heb_year}"
            if hol:
                title = f"{title} — {hol}"

            # All-day by returning midnight→midnight datetimes
            start_dt = datetime.combine(day, time(0,0,0)).replace(tzinfo=tz)
            end_dt   = start_dt + timedelta(days=1)
            events.append(CalendarEvent(start=start_dt, end=end_dt, summary=title))
            day += timedelta(days=1)

        self._events = events
        self.async_write_ha_state()
