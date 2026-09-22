"""Atomberg sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    MANUFACTURER,
    SENSOR_LAST_EVENT,
    SENSOR_CRED_TYPE,
    SENSOR_SLOT_ID,
    SENSOR_PIN_CODE,
    SENSOR_LAST_TIMESTAMP,
    SENSOR_USER,
)

MAX_RECENT_LOG_ATTRIBUTES = 10


class AtombergBaseSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key: str, name: str, icon: str | None = None):
        self.coordinator = coordinator
        self._key = key
        self._attr_name = name
        if icon:
            self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.address.lower()}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name="Atomberg SL1 Pro",
            manufacturer=MANUFACTURER,
            model="SL1 Pro",
        )
        self._remove_listener = coordinator.add_listener(self.async_write_ha_state)

    async def async_will_remove_from_hass(self):
        if self._remove_listener:
            self._remove_listener()


class AtombergBatterySensor(AtombergBaseSensor):
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = "battery"
    _attr_state_class = "measurement"

    def __init__(self, coordinator):
        super().__init__(coordinator, "battery", "Battery")

    @property
    def native_value(self):
        return self.coordinator.battery


class AtombergLogSensor(AtombergBaseSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, "logs", "Logs", "mdi:format-list-bulleted")

    @property
    def native_value(self):
        return self.coordinator.log_record_count

    @property
    def extra_state_attributes(self):
        recent_logs = self.coordinator.logs[-MAX_RECENT_LOG_ATTRIBUTES:] if self.coordinator.logs else []
        return {
            "last_fetched": self.coordinator.logs_fetched_at,
            "total_records": self.coordinator.log_record_count,
            "recent_events": recent_logs,
        }


class AtombergEventSensor(AtombergBaseSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, SENSOR_LAST_EVENT, "Last Event", "mdi:lock-alert")

    @property
    def native_value(self):
        return self.coordinator.last_event


class AtombergCredentialTypeSensor(AtombergBaseSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, SENSOR_CRED_TYPE, "Credential Type", "mdi:key-outline")

    @property
    def native_value(self):
        return self.coordinator.cred_type


class AtombergSlotIdSensor(AtombergBaseSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, SENSOR_SLOT_ID, "Slot ID", "mdi:numeric")

    @property
    def native_value(self):
        return self.coordinator.slot_id


class AtombergPinCodeSensor(AtombergBaseSensor):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator):
        super().__init__(coordinator, SENSOR_PIN_CODE, "Keypad PIN", "mdi:form-textbox-password")

    @property
    def native_value(self):
        return self.coordinator.pin_code


class AtombergLastTimestampSensor(AtombergBaseSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, SENSOR_LAST_TIMESTAMP, "Last Timestamp", "mdi:clock-outline")

    @property
    def native_value(self):
        return self.coordinator.last_timestamp


class AtombergUserSensor(AtombergBaseSensor):
    def __init__(self, coordinator):
        super().__init__(coordinator, SENSOR_USER, "User", "mdi:account-lock")

    @property
    def native_value(self):
        return self.coordinator.current_user


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        AtombergBatterySensor(coordinator),
        AtombergLogSensor(coordinator),
        AtombergEventSensor(coordinator),
        AtombergCredentialTypeSensor(coordinator),
        AtombergSlotIdSensor(coordinator),
        AtombergPinCodeSensor(coordinator),
        AtombergLastTimestampSensor(coordinator),
        AtombergUserSensor(coordinator),
    ])
