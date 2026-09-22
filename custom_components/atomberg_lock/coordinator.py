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

        # Real-time live event states matching ESPHome
        self.last_event: str | None = None
        self.cred_type: str | None = None
        self.slot_id: int | None = None
        self.pin_code: str | None = None
        self.last_timestamp: str | None = None

        self._log_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}_logs")
        self._state_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}_state")
        self._busy = asyncio.Lock()
        self._relock_task = None
        self._listeners = []

    def resolve_user_name(self, slot_id: int | None, cred_type: str | None) -> str:
        """Resolves slot ID to friendly user name identical to ESPHome lambda."""
        if not slot_id or slot_id == 0:
            if cred_type == "Atomberg App":
                return "Atomberg App"
            if cred_type == "Physical Thumbturn":
                return "Manual Thumbturn"
            if cred_type == "System Auto-Lock":
                return "Auto-Lock"
            if cred_type == "Unregistered Fingerprint":
                return "Unregistered Finger"
            if cred_type == "Wrong Keypad PIN":
                return "Wrong PIN Entered"
            return "None"

        if slot_id in self.slot_mappings:
            return self.slot_mappings[slot_id]

        return f"{cred_type or 'Unknown'} (Slot {slot_id})"

    @property
    def current_user(self) -> str:
        return self.resolve_user_name(self.slot_id, self.cred_type)

    def handle_live_event_packet(self, data: dict):
        """Called immediately when a 0x000A live event is pushed by the lock."""
        self.last_event = data.get("event")
        self.cred_type = data.get("cred_type")
        self.slot_id = data.get("slot_id")
        self.pin_code = data.get("pin_code")
        self.last_timestamp = data.get("timestamp")

        if data.get("battery") is not None:
            self.battery = data["battery"]

        if self.last_event == "Unlocked Successfully":
            self.locked = False
            self.last_unlock_method = self.cred_type
            if self._relock_task and not self._relock_task.done():
                self._relock_task.cancel()
            self._relock_task = asyncio.create_task(self._relock_after_delay())
        elif self.last_event == "Auto-Lock / Latch":
            self.locked = True

        self.hass.async_create_task(self._save_state())
        self._write_state()

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

            # Restore live event sensors
            self.last_event = stored_state.get("last_event")
            self.cred_type = stored_state.get("cred_type")
            self.slot_id = stored_state.get("slot_id")
            self.pin_code = stored_state.get("pin_code")
            self.last_timestamp = stored_state.get("last_timestamp")

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
        await self._log_store.async_remove()
        await self._state_store.async_remove()

    def _get_ble_target(self):
        return bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        ) or self.address

    def _create_protocol(self) -> AtombergProtocol:
        ble_target = self._get_ble_target()
        return AtombergProtocol(
            ble_target,
            self.master_key,
            self.lock_salt,
            on_live_event=self.handle_live_event_packet,
        )

    async def async_fetch_battery(self) -> int | None:
        async with self._busy:
            protocol = self._create_protocol()
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
            protocol = self._create_protocol()
            try:
                _LOGGER.debug("LOGS: connecting to Atomberg lock")
                await protocol.connect()
                await protocol.authenticate()

                # Start reading from the count of currently cached logs
                start_offset = len(self.logs)
                new_entries = await protocol.fetch_incremental_logs(
                    start_offset=start_offset,
                    slot_mappings=self.slot_mappings,
                )

                now = datetime.now(ZoneInfo("Asia/Kolkata")).isoformat()
                self.logs_fetched_at = now

                if new_entries:
                    self.logs.extend(new_entries)
                    self.log_record_count = len(self.logs)
                    _LOGGER.info(
                        "LOGS: Added %d new records (Total stored: %d)",
                        len(new_entries),
                        self.log_record_count,
                    )
                else:
                    _LOGGER.info("LOGS: Already up to date. No new records found.")

                await self._log_store.async_save({
                    "logs": self.logs,
                    "record_count": self.log_record_count,
                    "fetched_at": self.logs_fetched_at,
                })
            except Exception as err:
                raise HomeAssistantError(f"Failed to fetch logs: {err}") from err
            finally:
                await protocol.disconnect()

            self._write_state()

    async def async_unlock(self):
        async with self._busy:
            self.is_unlocking = True
            self._write_state()

            protocol = self._create_protocol()
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
            "last_event": self.last_event,
            "cred_type": self.cred_type,
            "slot_id": self.slot_id,
            "pin_code": self.pin_code,
            "last_timestamp": self.last_timestamp,
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
