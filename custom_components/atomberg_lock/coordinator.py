class AtombergCoordinator:
    def __init__(self, hass: HomeAssistant, entry):
        # ... keep previous fields ...
        self.locked = True
        self.is_unlocking = False
        self._relock_task = None
        # ... rest of init ...

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

            # Cancel existing timer if triggered consecutively
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
