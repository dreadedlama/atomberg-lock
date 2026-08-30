"""Atomberg lock entity."""
from __future__ import annotations

from homeassistant.components.lock import LockEntity
from homeassistant.helpers.entity import DeviceInfo

from .const import ATTR_LAST_UNLOCK, ATTR_LAST_UNLOCK_METHOD, DOMAIN, MANUFACTURER

class AtombergLock(LockEntity):
    _attr_has_entity_name = True
    _attr_name = "Lock"

    def __init__(self, coordinator):
        self.coordinator = coordinator
        self._attr_unique_id = f"{coordinator.address.lower()}_lock"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            name="Atomberg SL1 Pro",
            manufacturer=MANUFACTURER,
            model="SL1 Pro",
        )
        self._remove_listener = coordinator.add_listener(self.async_write_ha_state)

    @property
    def is_locked(self):
        return self.coordinator.locked

    @property
    def extra_state_attributes(self):
        return {
            ATTR_LAST_UNLOCK_METHOD: self.coordinator.last_unlock_method,
            ATTR_LAST_UNLOCK: (
                self.coordinator.last_unlock.isoformat()
                if self.coordinator.last_unlock else None
            ),
        }

    async def async_lock(self, **kwargs):
        self.coordinator.locked = True
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs):
        await self.coordinator.async_unlock()

    async def async_will_remove_from_hass(self):
        if self._remove_listener:
            self._remove_listener()


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([AtombergLock(coordinator)])
