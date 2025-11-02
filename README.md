# Schedule Modes Integration for Home Assistant

A comprehensive Home Assistant custom integration for managing multiple modes with calendar-based scheduling, manual overrides, and intelligent automation support. Originally designed for Shul (synagogue) scheduling but fully adaptable to any use case requiring mode-based automation.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Features

### Core Capabilities

- **20+ Pre-configured Modes**: Comprehensive set of modes for various scenarios (customizable)
- **Calendar Integration**: Full calendar support with create, update, and delete event capabilities
- **Manual Override System**: Per-mode calendar override switches to prevent automatic activation
- **Binary Sensors**: Multiple status sensors per mode showing active state and event information
- **Event Sensors**: Dynamic sensors created for each calendar event with detailed tracking
- **Jewish Date Calendar**: Built-in Hebrew calendar with holiday information (diaspora support)
- **Automatic Switch Control**: Calendar events automatically control mode switches
- **State Persistence**: All switches maintain their state across Home Assistant restarts

### What You Get Per Mode

Each enabled mode provides:

1. **Mode Switch** (`switch.{mode_name}`)
   - Manual on/off control
   - Automatic activation from calendar events
   - State attributes showing control source

2. **Calendar Override Switch** (`switch.{mode_name}_calendar_override`)
   - Prevents calendar from automatically controlling the mode
   - Allows manual-only operation
   - Persists across restarts

3. **Mode Active Binary Sensor** (`binary_sensor.{mode_name}_active`)
   - Shows if mode is currently active (manual OR calendar)
   - Attributes include:
     - `controlled_by`: "manual" or "calendar"
     - `active_started`: When current event started
     - `active_end`: When current event will end
     - `last_ended`: When last event ended
     - `next_calendar_start`: Next scheduled event start
     - `next_calendar_end`: Next scheduled event end

4. **Mode Event Active Binary Sensor** (`binary_sensor.{mode_name}_event_active`)
   - Shows if there's an active calendar event (regardless of override)
   - Only ON for calendar events, OFF for manual activation
   - Attributes include:
     - `calendar_override_enabled`: Current override status
     - `event_start`: Event start time (if active)
     - `event_end`: Event end time (if active)
     - `event_summary`: Event title (if active)

5. **Mode Calendar** (`calendar.{mode_name}`)
   - Full calendar entity for this mode
   - Create, edit, and delete events
   - View in Home Assistant calendar dashboard
   - Events automatically trigger mode activation

6. **Event Sensors** (Dynamic)
   - One sensor per calendar event
   - Shows state: "upcoming", "running", or "ended"
   - Automatically created/deleted with events
   - Includes full event details in attributes

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click on "Integrations"
3. Click the three dots in the top right and select "Custom repositories"
4. Add this repository URL and select "Integration" as the category
5. Click "Install"
6. Restart Home Assistant

### Manual Installation

1. Download the `schedule_modes` folder from this repository
2. Copy it to your Home Assistant `custom_components` directory
3. Restart Home Assistant

## Configuration

### Initial Setup

1. Go to **Settings** → **Devices & Services**
2. Click **+ Add Integration**
3. Search for "Schedule Modes"
4. Click to add the integration

### Configuration Options

After installation, you can configure:

#### Enabled Modes

Select which modes you want to use from the 20+ available options:

**Available Modes:**
- Bin Hasmanim
- No Tachanun
- Bavarfen
- No School
- Day Camp
- Late School
- Half-Day School
- Bris
- Zucher Mode
- Chasanah Mode
- Kiddush Mode
- Yahrtzeit Mode
- Small Simcha Mode
- Guest Rabbi Mode
- Shabbos Sheva Brachos Mode
- Event Mode
- Guest Room
- Home
- Rabbi Here
- Rabbi Away
- Away Mode
- Cleaning Mode

#### Default Durations

Set the default expiration time (in minutes) for each mode when manually activated:
- `0` = No expiration (stays on until manually turned off)
- `60` = Automatically turns off after 1 hour
- `120` = Automatically turns off after 2 hours
- Custom values as needed

#### Auto-Reset Time

Optionally set a daily time (HH:MM format) when all active modes automatically turn off:
- Example: `23:00` = All modes turn off at 11 PM
- Leave empty to disable auto-reset

#### Advanced Options

**Link No Tachanun for Bris Events**
- When enabled, creating a Bris event automatically creates a linked No Tachanun event
- Editing/deleting the Bris event updates/deletes the linked No Tachanun event
- Prevents accidental deletion of linked events

## Usage

### Creating Calendar Events

#### Via Home Assistant UI

1. Go to **Calendar** in the Home Assistant sidebar
2. Select the mode calendar you want to add an event to
3. Click on a date/time to create a new event
4. Fill in:
   - **Summary**: Event title
   - **Start**: Event start date/time
   - **End**: Event end date/time
   - **Description**: Optional event details
5. Click **Save**

The mode switch will automatically turn ON when the event starts and OFF when it ends.

#### Via Automation

```yaml
automation:
  - alias: "Schedule Morning Bris"
    trigger:
      - platform: time
        at: "06:00:00"
    action:
      - service: calendar.create_event
        target:
          entity_id: calendar.bris
        data:
          summary: "Morning Bris - Goldstein Family"
          start: "{{ today_at('08:00') }}"
          end: "{{ today_at('10:00') }}"
          description: "Bris for Goldstein family baby boy"
```

### Manual Mode Activation

#### Via UI

1. Go to the mode's device page
2. Toggle the mode switch ON
3. Optionally set an expiration time

#### Via Service Call

```yaml
service: switch.turn_on
target:
  entity_id: switch.bris
data:
  minutes: 120  # Optional: auto-off after 2 hours
```

### Using Calendar Override

When you want to prevent calendar events from automatically activating a mode:

1. Turn ON the Calendar Override switch for that mode
2. The mode switch can now only be controlled manually
3. Calendar events will still show in the calendar, but won't trigger the switch
4. The "Mode Event Active" sensor will still show ON during calendar events

**Use Cases:**
- Testing mode manually without calendar interference
- Temporary suspension of calendar control
- Override scheduled events for special circumstances

### Differentiating Calendar vs Manual Activation

Use the binary sensors to tell the difference:

| Scenario | Mode Active | Mode Event Active |
|----------|-------------|-------------------|
| Calendar event running | ✅ ON | ✅ ON |
| Calendar event + override enabled | ❌ OFF | ✅ ON |
| Manually turned on (no event) | ✅ ON | ❌ OFF |
| Nothing active | ❌ OFF | ❌ OFF |


## Automation Examples

### Example 1: Lighting Control Based on Mode

```yaml
automation:
  - alias: "Bris Mode Lighting"
    trigger:
      - platform: state
        entity_id: binary_sensor.bris_active
        to: "on"
    action:
      - service: light.turn_on
        target:
          entity_id: light.main_hall
      
```


## Binary Sensor Attributes Reference

### Mode Active Sensor Attributes

```yaml
mode_key: "bris"
controlled_by: "calendar"  # or "manual"
active_started: "2025-01-15T08:00:00+00:00"  # ISO timestamp or null
active_end: "2025-01-15T10:00:00+00:00"       # ISO timestamp or null
last_ended: "2025-01-14T12:00:00+00:00"       # ISO timestamp or null
next_calendar_start: "2025-01-16T09:00:00+00:00"  # ISO timestamp or null
next_calendar_end: "2025-01-16T11:00:00+00:00"    # ISO timestamp or null
```

### Mode Event Active Sensor Attributes

```yaml
mode_key: "bris"
calendar_override_enabled: false  # true/false
event_start: "2025-01-15T08:00:00+00:00"  # Only present if event active
event_end: "2025-01-15T10:00:00+00:00"     # Only present if event active
event_summary: "Goldstein Family Bris"     # Only present if event active
```

## Global Sensors

In addition to per-mode sensors, the integration provides:

### Event Modes Summary
- **Entity**: `binary_sensor.shul_modes_event_modes`
- **State**: ON if any event mode is active
- **Attributes**: List of currently active event modes

### Event Running with Override
- **Entity**: `binary_sensor.event_running_with_override`
- **State**: ON when any mode is running AND its override is enabled
- **Attributes**: List of modes with override enabled

### Jewish Dates Calendar
- **Entity**: `calendar.jewish_dates`
- **Purpose**: Shows Hebrew dates and Jewish holidays
- **Features**:
  - One all-day event per civil day
  - Shows Hebrew date at noon local time
  - Includes diaspora holiday names
  - Automatically generated 180 days ahead

### Old Events Archive
- **Entity**: `sensor.old_events`
- **State**: Count of archived events
- **Attributes**: Details of past events (removed 1 day after ending)


## Logging

The integration provides comprehensive logging at different levels:

### Enable Debug Logging

Add to `configuration.yaml`:

```yaml
logger:
  default: info
  logs:
    custom_components.autoh_modes: debug
```

### Log Messages to Look For

- ✅ `Successfully removed entity {name}` - Sensor deleted properly
- 🔄 `Starting async_remove for {name}` - Deletion in progress
- ⚠️ `DELETING X event sensors` - Events being removed
- ❌ `async_remove failed` - Deletion error (investigate)


## Support

- **Issues**: Report bugs via GitHub Issues
- **Feature Requests**: Submit via GitHub Discussions
- **Documentation**: This README and inline code comments



## Credits

- **Developer**: OnOff Automations
- **Special Thanks**: Home Assistant Community
- **Hebrew Calendar**: HA YidCal Integration

## Changelog

### Version 0.0.1 (Current)

- ✅ Initial release
- ✅ 20+ pre-configured modes
- ✅ Full calendar integration
- ✅ Per-mode calendar override switches
- ✅ Dual binary sensors per mode (Active + Event Active)
- ✅ Event sensors with automatic lifecycle management
- ✅ Jewish dates calendar
- ✅ Bris → No Tachanun event linking
- ✅ Comprehensive logging system

---
## Upcoming Featurs
 - Option to link each mode with a remote calendar
 - Auto delete automatic calendar event sensors after x days
 - In ConfigFlow, add more modes on yourself


**Made with ❤️ for Home Assistant and Jewish community**
