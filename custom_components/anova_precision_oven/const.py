"""Constants for the Anova Precision Oven integration."""

DOMAIN = "anova_precision_oven"

CONF_TOKEN = "token"

ACCESSORIES_APO = "APO"

WS_ENDPOINT = "wss://devices.anovaculinary.io"
WS_TIMEOUT = 30
WS_RECONNECT_DELAY = 30
WS_MAX_MESSAGE_SIZE = 10 * 1024 * 1024

OVEN_TYPE_V1 = "oven_v1"
OVEN_TYPE_V2 = "oven_v2"

CMD_APO_START = "CMD_APO_START"
CMD_APO_STOP = "CMD_APO_STOP"
CMD_APO_SET_LAMP = "CMD_APO_SET_LAMP"

EVENT_APO_WIFI_LIST = "EVENT_APO_WIFI_LIST"
EVENT_APO_STATE = "EVENT_APO_STATE"

TEMP_MIN_CELSIUS = 25
TEMP_MAX_CELSIUS = 250

DEFAULT_TEMPERATURE_CELSIUS = 175
DEFAULT_FAN_SPEED = 100
DEFAULT_HUMIDITY = 0

# Command responses
RESPONSE = "RESPONSE"
RESPONSE_TIMEOUT = 10

# Cook limits (Celsius), from Anova's v1 developer docs
TEMP_MAX_WET_CELSIUS = 100
TEMP_MAX_BOTTOM_ONLY_CELSIUS = 180
PROBE_MIN_CELSIUS = 1
PROBE_MAX_CELSIUS = 100
RACK_POSITIONS = (1, 2, 3, 4, 5)
HEATING_ELEMENTS = ("top", "bottom", "rear")

# Services
SERVICE_START_COOK = "start_cook"
SERVICE_START_CUSTOM_COOK = "start_custom_cook"

ATTR_MODE = "mode"
ATTR_PROBE_TEMPERATURE = "probe_temperature"
ATTR_DURATION = "duration"
ATTR_HEATING_ELEMENTS = "heating_elements"
ATTR_FAN_SPEED = "fan_speed"
ATTR_STEAM = "steam"
ATTR_VENT_OPEN = "vent_open"
ATTR_RACK_POSITION = "rack_position"
ATTR_PREHEAT = "preheat"
ATTR_WAIT_FOR_START = "wait_for_start"
ATTR_TITLE = "title"
ATTR_STAGES = "stages"

COOK_MODE_DRY = "dry"
COOK_MODE_SOUS_VIDE = "sous_vide"

# Event entity
EVENT_PREHEATED = "preheated"
EVENT_PROBE_TARGET_REACHED = "probe_target_reached"
EVENT_TIMER_FINISHED = "timer_finished"
EVENT_COOK_ENDED = "cook_ended"
EVENT_TYPES = [
    EVENT_PREHEATED,
    EVENT_PROBE_TARGET_REACHED,
    EVENT_TIMER_FINISHED,
    EVENT_COOK_ENDED,
]
# How close (Celsius) the bulb must get to its setpoint to count as preheated
PREHEAT_TOLERANCE_CELSIUS = 1.0
