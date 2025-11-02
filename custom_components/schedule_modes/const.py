from __future__ import annotations
from typing import Dict, List

DOMAIN = "schedule_modes"

# key, friendly_name, group
MODE_DEFS = [
    ("bin_hazmanim",            "Bin Hazmanim", "base"),
    ("guest_room",              "Guest Room", "event"),
    ("bris",                    "Bris", "event"),
    ("home",                    "Home", "event"),
    ("no_tachnun",              "No Tachanun", "base"),
    ("kiddush_mode",            "Kiddush Mode", "event"),
    ("bavarfen_mode",           "Bavarfen Mode", "base"),
    ("rabbi_here",              "Rabbi Here", "presence"),
    ("zucher_mode",             "Zucher Mode", "event"),
    ("chasunah_mode",           "Chasanah Mode", "event"),
    ("yahrtzeit_mode",          "Yahrtzeit Mode", "event"),
    ("rabbi_away",              "Rabbi Away", "presence"),
    ("away_mode",               "Away Mode", "system"),
    ("small_simcha_mode",       "Small Simcha Mode", "event"),
    ("guest_rabbi_mode",        "Guest Rabbi Mode", "event"),
    ("cleaning_mode",           "Cleaning Mode", "system"),
    ("shabbos_sheva_brachos",   "Shabbos Sheva Brachos Mode", "event"),
    ("event_mode",              "Event Mode", "event"),
    ("no_school",               "No School", "base"),
    ("day_camp",                "Day Camp", "base"),
    ("late_school",             "Late School", "base"),
    ("half_day_school",        "Half-Day School", "base"),
]

EVENT_MODE_KEYS = [k for (k, _n, g) in MODE_DEFS if g == "event"]

OPT_ENABLED_MODES       = "enabled_modes"
OPT_DEFAULT_DURATIONS   = "default_durations"
OPT_AUTO_RESET_TIME     = "auto_reset_time"
# NEW: link "No Tachanun" when Bris is active
OPT_LINK_NO_TACHANUN_FOR_BRIS = "link_nt_for_bris"
# Calendar Override - when on, only manual switch can control binary sensor
OPT_CALENDAR_OVERRIDE   = "calendar_override"

STORAGE_VERSION    = 1
STORAGE_EVENTS_KEY = "events"
SIGNAL_EVENTS_UPDATED = f"{DOMAIN}_events_updated"


def ALL_MODE_KEYS() -> List[str]:
    return [k for (k, _n, _g) in MODE_DEFS]


def DEFAULT_DURATIONS() -> Dict[str, int]:
    return {k: 0 for k in ALL_MODE_KEYS()}


DEFAULT_OPTIONS = {
    OPT_ENABLED_MODES: ALL_MODE_KEYS(),
    OPT_DEFAULT_DURATIONS: DEFAULT_DURATIONS(),
    OPT_AUTO_RESET_TIME: "",
    # NEW default
    OPT_LINK_NO_TACHANUN_FOR_BRIS: False,
    OPT_CALENDAR_OVERRIDE: False,
}


def ensure_default_options(opts: Dict) -> Dict:
    if not opts:
        return DEFAULT_OPTIONS
    out = dict(DEFAULT_OPTIONS)
    out.update(opts or {})
    out[OPT_ENABLED_MODES] = [k for k in out.get(OPT_ENABLED_MODES, ALL_MODE_KEYS()) if k in ALL_MODE_KEYS()]
    dd = dict(DEFAULT_DURATIONS())
    dd.update(out.get(OPT_DEFAULT_DURATIONS, {}))
    out[OPT_DEFAULT_DURATIONS] = dd
    # ensure new flags exist
    out.setdefault(OPT_LINK_NO_TACHANUN_FOR_BRIS, False)
    out.setdefault(OPT_CALENDAR_OVERRIDE, False)
    return out


def mode_friendly(key: str) -> str:
    for k, n, _ in MODE_DEFS:
        if k == key:
            return n
    return key


def device_info_main(entry_id: str):
    return {
        "identifiers": {(DOMAIN, entry_id, "main")},
        "manufacturer": "OnOff Automations",
        "name": "Schedule Modes",
        "model": "Modes + Calendars",
    }


def device_info_for_mode(entry_id: str, mode_key: str):
    return {
        "identifiers": {(DOMAIN, entry_id, mode_key)},
        "manufacturer": "OnOff Automations",
        "name": f"{mode_friendly(mode_key)}",
        "model": "Mode",

    }

