"""Config flow for Atomberg Lock."""
from __future__ import annotations

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.core import callback

from .const import CONF_LOCK_MAC, CONF_LOCK_SALT, CONF_STATIC_MASTER_KEY, DOMAIN, NAME
from .protocol import AtombergProtocol

_LOGGER = logging.getLogger(__name__)

EXAMPLE_MAC = "AA:BB:CC:11:22:33"
EXAMPLE_KEY = "AbCdEfGhIjKlMnOp"
EXAMPLE_SALT = "1a2b3c4d"


def _parse_master_key(raw_key: str) -> bytes | None:
    raw_key = raw_key.strip()
    if len(raw_key) == 32:
        try:
            return bytes.fromhex(raw_key)
        except ValueError:
            pass
    key_bytes = raw_key.encode("utf-8")
    if len(key_bytes) == 16:
        return key_bytes
    return None


def _parse_lock_salt(raw_salt: str) -> bytes | None:
    raw_salt = raw_salt.strip().replace(" ", "").replace("0x", "")
    try:
        salt_bytes = bytes.fromhex(raw_salt)
        if len(salt_bytes) == 4:
            return salt_bytes
    except ValueError:
        pass
    return None


async def _async_validate_lock_credentials(
    hass, address: str, master_key_bytes: bytes, lock_salt_bytes: bytes
) -> int | None:
    ble_target = bluetooth.async_ble_device_from_address(hass, address, connectable=True) or address

    protocol = AtombergProtocol(ble_target, master_key_bytes, lock_salt_bytes)
    try:
        await protocol.connect()
        await protocol.authenticate()
        try:
            return await protocol.get_battery_status()
        except Exception:
            return None
    finally:
        await protocol.disconnect()


class AtombergConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> AtombergOptionsFlowHandler:
        return AtombergOptionsFlowHandler()

    async def async_step_user(self, user_input=None):
        """Direct input of LOCK_MAC, STATIC_MASTER_KEY, and LOCK_SALT."""
        errors = {}

        if user_input is not None:
            address = user_input[CONF_LOCK_MAC].strip().upper()
            raw_key = user_input[CONF_STATIC_MASTER_KEY].strip()
            raw_salt = user_input[CONF_LOCK_SALT].strip()

            key_bytes = _parse_master_key(raw_key)
            salt_bytes = _parse_lock_salt(raw_salt)

            if key_bytes is None:
                errors["base"] = "invalid_master_key"
            elif salt_bytes is None:
                errors["base"] = "invalid_lock_salt"
            else:
                try:
                    battery = await _async_validate_lock_credentials(self.hass, address, key_bytes, salt_bytes)
                    await self.async_set_unique_id(address.replace(":", ""))
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=f"{NAME} ({address})",
                        data={
                            CONF_LOCK_MAC: address,
                            CONF_STATIC_MASTER_KEY: raw_key,
                            CONF_LOCK_SALT: raw_salt,
                            "initial_battery": battery,
                        },
                    )
                except ConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.error("Lock authentication failed: %s", err)
                    errors["base"] = "auth_failed"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCK_MAC, default=EXAMPLE_MAC): str,
                vol.Required(CONF_STATIC_MASTER_KEY, default=EXAMPLE_KEY): str,
                vol.Required(CONF_LOCK_SALT, default=EXAMPLE_SALT): str,
            }),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if not entry:
            return self.async_abort(reason="reconfigure_failed")

        errors = {}
        if user_input is not None:
            address = user_input[CONF_LOCK_MAC].strip().upper()
            raw_key = user_input[CONF_STATIC_MASTER_KEY].strip()
            raw_salt = user_input[CONF_LOCK_SALT].strip()

            key_bytes = _parse_master_key(raw_key)
            salt_bytes = _parse_lock_salt(raw_salt)

            if key_bytes is None:
                errors["base"] = "invalid_master_key"
            elif salt_bytes is None:
                errors["base"] = "invalid_lock_salt"
            else:
                try:
                    battery = await _async_validate_lock_credentials(self.hass, address, key_bytes, salt_bytes)
                    return self.async_update_reload_and_abort(
                        entry,
                        data={
                            **entry.data,
                            CONF_LOCK_MAC: address,
                            CONF_STATIC_MASTER_KEY: raw_key,
                            CONF_LOCK_SALT: raw_salt,
                            "initial_battery": battery,
                        },
                    )
                except ConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.error("Lock reconfigure failed: %s", err)
                    errors["base"] = "auth_failed"

        current_address = entry.data.get(CONF_LOCK_MAC, entry.data.get("address", ""))
        current_key = entry.data.get(CONF_STATIC_MASTER_KEY, entry.data.get("master_key", entry.data.get("static_key", "")))
        current_salt = entry.data.get(CONF_LOCK_SALT, "")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCK_MAC, default=current_address): str,
                vol.Required(CONF_STATIC_MASTER_KEY, default=current_key): str,
                vol.Required(CONF_LOCK_SALT, default=current_salt): str,
            }),
            errors=errors,
        )


class AtombergOptionsFlowHandler(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        entry = self.config_entry

        if user_input is not None:
            address = user_input[CONF_LOCK_MAC].strip().upper()
            raw_key = user_input[CONF_STATIC_MASTER_KEY].strip()
            raw_salt = user_input[CONF_LOCK_SALT].strip()

            key_bytes = _parse_master_key(raw_key)
            salt_bytes = _parse_lock_salt(raw_salt)

            if key_bytes is None:
                errors["base"] = "invalid_master_key"
            elif salt_bytes is None:
                errors["base"] = "invalid_lock_salt"
            else:
                try:
                    battery = await _async_validate_lock_credentials(self.hass, address, key_bytes, salt_bytes)
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_LOCK_MAC: address,
                            CONF_STATIC_MASTER_KEY: raw_key,
                            CONF_LOCK_SALT: raw_salt,
                            "initial_battery": battery,
                        },
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_create_entry(title="", data={})
                except ConnectionError:
                    errors["base"] = "cannot_connect"
                except Exception as err:
                    _LOGGER.error("Lock options validation failed: %s", err)
                    errors["base"] = "auth_failed"

        current_address = entry.data.get(CONF_LOCK_MAC, entry.data.get("address", ""))
        current_key = entry.data.get(CONF_STATIC_MASTER_KEY, entry.data.get("master_key", entry.data.get("static_key", "")))
        current_salt = entry.data.get(CONF_LOCK_SALT, "")

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_LOCK_MAC, default=current_address): str,
                vol.Required(CONF_STATIC_MASTER_KEY, default=current_key): str,
                vol.Required(CONF_LOCK_SALT, default=current_salt): str,
            }),
            errors=errors,
        )
