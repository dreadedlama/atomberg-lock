"""Constants for Atomberg."""
from homeassistant.const import Platform

DOMAIN = "atomberg_lock"
NAME = "Atomberg Lock"
MANUFACTURER = "Atomberg"

PLATFORMS = [
    Platform.LOCK,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.TEXT,
]

WRITE_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"

AUTO_LOCK_SECONDS = 5
CONNECT_TIMEOUT = 15
COMMAND_TIMEOUT = 6

CONF_LOCK_MAC = "LOCK_MAC"
CONF_STATIC_MASTER_KEY = "STATIC_MASTER_KEY"
CONF_LOCK_SALT = "LOCK_SALT"

ATTR_LAST_UNLOCK_METHOD = "last_unlock_method"
ATTR_LAST_UNLOCK = "last_unlock"
