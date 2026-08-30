"""Atomberg coordinator."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store

from .const import (
    AUTO_LOCK_SECONDS,
    CONF_LOCK_MAC,
    CONF_LOCK_SALT,
    CONF_STATIC_MASTER_KEY,
    DOMAIN,
    MANUFACTURER,
)
from .protocol import DEFAULT_SLOT_MAPPINGS, AtombergProtocol

_LOGGER = logging.getLogger(__name__)


class AtombergCoordinator:
    def __init__(self, hass: HomeAssistant, entry):
        self.hass = hass
        self.entry = entry
        self.address = entry.data.get(CONF_LOCK_MAC, entry.data.get("address", ""))

        raw_key = entry.data.get(
            CONF_STATIC_MASTER_KEY,
            entry.data.get("master_key", entry.data.get("static_key", "")),
        ).strip()
        if len(raw_key) == 32:
            try:
                self.master_key = bytes.fromhex(raw_key)
            except ValueError:
                self.master_key = raw_key.encode("utf-8")
        else:
            self.master_key = raw_key.encode("utf-8")

        raw_salt = entry.data.get(CONF_LOCK_SALT, "").strip().replace(" ", "").replace("0x", "")
        self.lock_salt = bytes.fromhex(raw_salt)

        self.battery = entry.data.get("initial_battery")
        self.locked = True
        self.is_unlocking = False
        self.last_unlock_method = None
        self.last_unlock = None
        self.logs = []
        self.logs_fetched_at = None
        self.log_record_count = 0
        self.slot_mappings = dict(DEFAULT_SLOT_MAPPINGS)

        self._log_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}_logs")
        self._state_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}_state")
        self._busy = asyncio.Lock()
        self._relock_task = None
        self._listeners = []

    async def async_setup(self):
        """Load persisted values strictly for this lock instance across restarts."""
        stored_logs = await self._log_store.async_load()
        if isinstance(stored_logs, dict):
            self.logs = stored_logs.get("logs", []) or []
            self.log_record_count = int(stored_logs.get("record_count", len(self.logs)) or 0)
            self.logs_fetched_at = stored_logs.get("fetched_at")

        stored_state = await self._state_store.async_load()
        if isinstance(stored_state, dict):
            battery = stored_state.get("battery")
            if isinstance(battery, (int, float)) and 0 <= battery <= 100:
                self.battery = battery
            stored_unlock = stored_state.get("last_unlock")
            if stored_unlock:
                try:
                    self.last_unlock = datetime.fromisoformat(stored_unlock)
                except (TypeError, ValueError):
                    self.last_unlock = None
            method = stored_state.get("last_unlock_method")
            if isinstance(method, str):
                self.last_unlock_method = method
            stored_mappings = stored_state.get("slot_mappings")
            if isinstance(stored_mappings, dict):
                parsed = {}
                for key, value in stored_mappings.items():
                    try:
                        slot = int(key)
                    except (TypeError, ValueError):
                        continue
                    if isinstance(value, str) and value.strip():
                        parsed[slot] = value.strip()
                if parsed:
                    self.slot_mappings = parsed
        elif self.battery is not None:
            await self._save_state()

        self.device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, self.address)},
            manufacturer=MANUFACTURER,
            name="Atomberg SL1 Pro",
            connections={("bluetooth", self.address)},
        )

    async def async_shutdown(self):
        if self._relock_task and not self._relock_task.done():
            self._relock_task.cancel()

    async def async_remove_stored_data(self):
        """Purge disk cache when this config entry is completely removed."""
        await self._log_store.async_remove()
        await self._state_store.async_remove()

    def _get_ble_target(self):
        return bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        ) or self.address

    async def async_fetch_battery(self) -> int | None:
        """Explicitly fetch battery status from the lock."""
        async with self._busy:
            ble_target = self._get_ble_target()
            protocol = AtombergProtocol(ble_target, self.master_key, self.lock_salt)
            try:
                _LOGGER.debug("BATTERY: connecting to Atomberg lock")
                await protocol.connect()
                await protocol.authenticate()
                battery = await protocol.get_battery_status()

                if battery is not None:
                    self.battery = battery
                    await self._save_state()
                    _LOGGER.info("BATTERY: updated to %d%%", battery)
                else:
                    _LOGGER.warning("BATTERY: lock returned no battery status")
            except Exception as err:
                raise HomeAssistantError(f"Failed to fetch battery: {err}") from err
            finally:
                await protocol.disconnect()

            self._write_state()
            return self.battery

    async def async_fetch_logs(self):
        async with self._busy:
            ble_target = self._get_ble_target()
            protocol = AtombergProtocol(ble_target, self.master_key, self.lock_salt)
            try:
                _LOGGER.debug("LOGS: connecting to Atomberg lock")
                await protocol.connect()
                await protocol.authenticate()
                logs = await protocol.fetch_all_logs(self.slot_mappings)

                now = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                self.logs = logs
                self.log_record_count = len(logs)
                self.logs_fetched_at = now
                await self._log_store.async_save({
                    "logs": self.logs,
                    "record_count": self.log_record_count,
                    "fetched_at": self.logs_fetched_at,
                })
                _LOGGER.info("LOGS: fetched and saved %d Atomberg records", len(logs))
            except Exception as err:
                raise HomeAssistantError(f"Failed to fetch logs: {err}") from err
            finally:
                await protocol.disconnect()

            self._write_state()

    async def async_unlock(self):
        """Fast remote unlock without battery query overhead."""
        async with self._busy:
            self.is_unlocking = True
            self._write_state()

            ble_target = self._get_ble_target()
            protocol = AtombergProtocol(ble_target, self.master_key, self.lock_salt)
            try:
                await protocol.connect()
                await protocol.authenticate()

                unlock_ts = await protocol.unlock()

                now_ist = datetime.fromtimestamp(unlock_ts, ZoneInfo("Asia/Kolkata"))
                self.locked = False
                self.is_unlocking = False
                self.last_unlock_method = "App"
                self.last_unlock = now_ist
                await self._save_state()
                self._write_state()
            except Exception as err:
                self.is_unlocking = False
                self.locked = True
                self._write_state()
                raise HomeAssistantError(f"Failed to unlock: {err}") from err
            finally:
                await protocol.disconnect()

            if self._relock_task and not self._relock_task.done():
                self._relock_task.cancel()
            self._relock_task = asyncio.create_task(self._relock_after_delay())

    async def _relock_after_delay(self):
        try:
            await asyncio.sleep(AUTO_LOCK_SECONDS)
            self.locked = True
            self._write_state()
        except asyncio.CancelledError:
            pass

    async def _save_state(self):
        await self._state_store.async_save({
            "battery": self.battery,
            "last_unlock": self.last_unlock.isoformat() if self.last_unlock else None,
            "last_unlock_method": self.last_unlock_method,
            "slot_mappings": {str(k): v for k, v in self.slot_mappings.items()},
        })

    async def async_set_slot_mappings(self, mappings: dict[int, str]) -> None:
        self.slot_mappings = dict(sorted(mappings.items()))
        await self._save_state()
        self._write_state()

    def slot_mappings_json(self) -> str:
        import json
        return json.dumps(
            {str(k): v for k, v in self.slot_mappings.items()},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _write_state(self):
        for listener in self._listeners:
            listener()

    def add_listener(self, callback):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback) if callback in self._listeners else None
