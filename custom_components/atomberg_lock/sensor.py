"""Atomberg sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, MANUFACTURER

# Maximum number of recent log entries to expose in state attributes to stay under the 16KB limit
MAX_RECENT_LOG_ATTRIBUTES = 10


class AtombergBatterySensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Battery"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = "battery"
    _attr_state_class = "measurement"
    _attr_entity_category = None

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address.lower()}_battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name="Atomberg SL1 Pro",
            manufacturer=MANUFACTURER,
            model="SL1 Pro",
        )
        self._remove_listener = coordinator.add_listener(self.async_write_ha_state)

    @property
    def native_value(self):
        return self.coordinator.battery

    async def async_will_remove_from_hass(self):
        if self._remove_listener:
            self._remove_listener()


class AtombergLogSensor(SensorEntity):
    """Persistent audit-log summary view.

    The state is the total count of fetched records.
    The attributes show metadata and the most recent events (staying under the 16KB limit).
    """
    _attr_has_entity_name = True
    _attr_name = "Logs"
    _attr_icon = "mdi:format-list-bulleted"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address.lower()}_logs"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name="Atomberg SL1 Pro",
            manufacturer=MANUFACTURER,
            model="SL1 Pro",
        )
        self._remove_listener = coordinator.add_listener(self.async_write_ha_state)

    @property
    def native_value(self):
        return self.coordinator.log_record_count

    @property
    def extra_state_attributes(self):
        # Keep only the last N events in recorder attributes to prevent database overflow
        recent_logs = self.coordinator.logs[-MAX_RECENT_LOG_ATTRIBUTES:] if self.coordinator.logs else []
        
        return {
            "last_fetched": self.coordinator.logs_fetched_at,
            "total_records": self.coordinator.log_record_count,
            "recent_events": recent_logs,
        }

    async def async_will_remove_from_hass(self):
        if self._remove_listener:
            self._remove_listener()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        AtombergBatterySensor(coordinator),
        AtombergLogSensor(coordinator),
    ])
