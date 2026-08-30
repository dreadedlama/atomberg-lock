"""Atomberg editable slot mapping text entity."""
from __future__ import annotations

import json

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, MANUFACTURER


class AtombergSlotMappingsText(TextEntity):
    _attr_has_entity_name = True
    _attr_name = "Slot Mappings JSON"
    _attr_icon = "mdi:code-json"
    _attr_mode = TextMode.TEXT
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_max = 4096

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address.lower()}_slot_mappings_json"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name="Atomberg SL1 Pro",
            manufacturer=MANUFACTURER,
            model="SL1 Pro",
        )
        self._attr_native_value = coordinator.slot_mappings_json()
        self._remove_listener = coordinator.add_listener(self._refresh)

    def _refresh(self):
        self._attr_native_value = self.coordinator.slot_mappings_json()
        self.async_write_ha_state()

    async def async_set_value(self, value: str) -> None:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as err:
            raise HomeAssistantError(f"Invalid slot mapping JSON: {err.msg}") from err

        if not isinstance(parsed, dict):
            raise HomeAssistantError("Slot mappings must be a JSON object")

        mappings: dict[int, str] = {}
        for key, name in parsed.items():
            try:
                slot = int(key)
            except (TypeError, ValueError) as err:
                raise HomeAssistantError(f"Invalid slot number: {key}") from err
            if slot < 0 or slot > 65535:
                raise HomeAssistantError(f"Slot must be between 0 and 65535: {slot}")
            if not isinstance(name, str) or not name.strip():
                raise HomeAssistantError(f"Slot {slot} must have a non-empty name")
            mappings[slot] = name.strip()

        await self.coordinator.async_set_slot_mappings(mappings)
        self._attr_native_value = self.coordinator.slot_mappings_json()
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        if self._remove_listener:
            self._remove_listener()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AtombergSlotMappingsText(coordinator)])
