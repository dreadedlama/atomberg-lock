"""Atomberg Lock buttons."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, MANUFACTURER


class AtombergFetchBatteryButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Get Battery"
    _attr_icon = "mdi:battery-sync"
    _attr_entity_category = None

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address.lower()}_get_battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name="Atomberg SL1 Pro",
            manufacturer=MANUFACTURER,
            model="SL1 Pro",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_fetch_battery()


class AtombergFetchLogsButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_name = "Fetch Logs"
    _attr_icon = "mdi:database-search"
    _attr_entity_category = None

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address.lower()}_fetch_logs"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name="Atomberg SL1 Pro",
            manufacturer=MANUFACTURER,
            model="SL1 Pro",
        )

    async def async_press(self) -> None:
        await self.coordinator.async_fetch_logs()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        AtombergFetchBatteryButton(coordinator),
        AtombergFetchLogsButton(coordinator),
    ])
