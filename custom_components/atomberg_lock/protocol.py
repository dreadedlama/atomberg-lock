"""Atomberg SL1 Pro protocol implementation."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from .const import COMMAND_TIMEOUT, NOTIFY_UUID, WRITE_UUID
from .crypto import crc16_modbus_be, decrypt_ecb, encrypt_ecb

_LOGGER = logging.getLogger(__name__)

DEFAULT_SLOT_MAPPINGS = {
    13: "NFC Card 1",
    14: "NFC Card 2",
    17: "Left Thumb",
    20: "Right Thumb",
}


def _slot_name(slot: int, slot_mappings: dict[int, str] | None) -> str:
    mappings = slot_mappings or DEFAULT_SLOT_MAPPINGS
    return mappings.get(slot, f"Slot {slot}")


def _format_ist_time(ts: int) -> str | None:
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, timezone.utc).astimezone(
            ZoneInfo("Asia/Kolkata")
        ).strftime("%d-%m-%Y %I:%M:%S %p")
    except Exception:
        return None


def parse_log_record(rec: bytes, slot_mappings: dict[int, str] | None = None) -> dict:
    if len(rec) != 32:
        raise ValueError(f"Expected 32-byte record, got {len(rec)}")

    ts = int.from_bytes(rec[0:4], "big")
    event_cat = rec[4]
    battery = rec[5]
    user_id = int.from_bytes(rec[7:9], "big")
    cred_type = rec[9]
    slot_id = int.from_bytes(rec[10:12], "big")

    event = "Hardware Event"
    cred_type_name = ""
    detail = ""
    pin_str = ""
    slot = slot_id

    if event_cat == 0x36:
        event = "False / Denied Attempt"
        if cred_type == 0x01:
            cred_type_name = "Unregistered Fingerprint"
            detail = "Unregistered Fingerprint"
        elif cred_type == 0x02:
            cred_type_name = "Wrong Keypad PIN"
            pin_len = rec[26] if len(rec) > 26 else 0
            if 0 < pin_len <= 12 and len(rec) >= (27 + pin_len):
                pin_str = rec[27:27 + pin_len].decode("latin1", errors="ignore")
            else:
                pin_str = ""
            detail = f"Wrong Keypad PIN: {pin_str}" if pin_str else "Wrong Keypad PIN"
        elif cred_type == 0x04:
            cred_type_name = "Unregistered NFC Card"
            detail = "Unregistered NFC Card"
        else:
            cred_type_name = "Rejected Credential"
            detail = f"Rejected Credential Type 0x{cred_type:02x}"

    elif event_cat == 0x04:
        event = "Unlocked Successfully"
        if b"MX" in rec[18:]:
            cred_type_name = "Atomberg App"
            detail = "Atomberg Mobile App (BLE)"
        elif cred_type == 0x01:
            cred_type_name = "Fingerprint"
            detail = f"Fingerprint: {_slot_name(slot, slot_mappings)} (Slot {slot})"
        elif cred_type == 0x04:
            cred_type_name = "NFC Card"
            card_uid = rec[18:22].hex()
            detail = f"NFC Card: {_slot_name(slot, slot_mappings)} (Slot {slot}, UID {card_uid})"
        elif cred_type == 0x02:
            cred_type_name = "PIN Code"
            code_str = rec[18:24].decode("latin1", errors="ignore").rstrip("\x00")
            if code_str:
                pin_str = code_str
                detail = f"PIN Code: {code_str} (User {user_id}, Slot {slot})"
            else:
                detail = f"PIN Code (User {user_id}, Slot {slot})"
        elif cred_type == 0x00 or slot == 0:
            cred_type_name = "Atomberg App"
            detail = "Atomberg Mobile App (BLE)"
        else:
            cred_type_name = f"Credential Type 0x{cred_type:02x}"
            detail = f"Credential Type 0x{cred_type:02x} (Slot {slot})"

    elif event_cat == 0x37:
        event = "Unlocked (Manual)"
        cred_type_name = "Physical Thumbturn"
        detail = "Physical Thumbturn"
        slot = 0

    elif event_cat == 0x08:
        event = "Credential Enrolled"
        cred_type_name = "Credential Enrollment"
        slot = int.from_bytes(rec[11:13], "big")
        detail = f"Enrolled: {_slot_name(slot, slot_mappings)} (Slot {slot})"

    else:
        event = f"Hardware Event 0x{event_cat:02x}"
        detail = f"Event Code 0x{event_cat:02x}"

    if event_cat == 0x04:
        if cred_type in (0x01, 0x04):
            detail = f"Unlocked by - {_slot_name(slot, slot_mappings)}"
        elif cred_type == 0x02:
            detail = f"Unlocked by - PIN: {pin_str}" if pin_str else "Unlocked by - PIN"
        elif cred_type == 0x00 or slot == 0 or cred_type_name == "Atomberg App":
            detail = "Unlocked by - Atomberg App"
        else:
            detail = f"Unlocked by - {_slot_name(slot, slot_mappings)}"
    elif event_cat == 0x37:
        detail = "Unlocked by - Physical Thumbturn"
    elif event_cat == 0x08:
        detail = f"Enrolled - {_slot_name(slot, slot_mappings)}"

    return {
        "datetime": _format_ist_time(ts),
        "event": event,
        "detail": detail,
        "battery": battery,
        "credential_type": cred_type_name,
        "slot": slot,
        **({"pin": pin_str} if pin_str else {}),
    }


class AtombergProtocol:
    def __init__(self, ble_target, master_key: bytes, lock_salt: bytes):
        self.ble_target = ble_target
        self.master_key = master_key
        self.lock_salt = lock_salt
        self.client = None
        self.session_token = None
        self.session_key = None
        self.seq_counter = 5
        self.recv_queue = asyncio.Queue()
        self.rx_buffer = bytearray()
        self.expected_len = 0

    async def connect(self):
        from bleak import BleakClient
        from bleak_retry_connector import establish_connection

        if isinstance(self.ble_target, str):
            self.client = BleakClient(self.ble_target, timeout=15.0)
            await self.client.connect()
        else:
            self.client = await establish_connection(
                BleakClient,
                self.ble_target,
                self.ble_target.name or "Atomberg SL1 Pro",
            )

        self._clear_buffers()
        await self.client.start_notify(NOTIFY_UUID, self._notification_handler)
        await asyncio.sleep(0.5)

    async def disconnect(self):
        if self.client:
            try:
                await self.client.stop_notify(NOTIFY_UUID)
            except Exception:
                pass
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self._clear_buffers()

    def _clear_buffers(self):
        self.rx_buffer.clear()
        self.expected_len = 0
        while not self.recv_queue.empty():
            try:
                self.recv_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _notification_handler(self, sender, data: bytearray):
        raw = bytes(data)
        if raw.startswith(b"HSJ") and len(raw) >= 5:
            self.expected_len = int.from_bytes(raw[3:5], byteorder="big")
            self.rx_buffer = bytearray(raw)
        else:
            self.rx_buffer.extend(raw)

        if self.expected_len > 0 and len(self.rx_buffer) >= self.expected_len:
            full_frame = bytes(self.rx_buffer[: self.expected_len])
            encrypted_payload = full_frame[7 : self.expected_len - 2]
            self.recv_queue.put_nowait(encrypted_payload)
            self.rx_buffer = bytearray(self.rx_buffer[self.expected_len :])
            self.expected_len = 0

    async def _send_command(self, payload: bytes, key: bytes, frame_seq: int):
        encrypted = encrypt_ecb(key, payload)
        total_len = 3 + 2 + 2 + len(encrypted) + 2

        header = (
            b"HSJ"
            + total_len.to_bytes(2, byteorder="big")
            + frame_seq.to_bytes(2, byteorder="big")
        )
        body = header + encrypted
        packet = body + crc16_modbus_be(body)

        for i in range(0, len(packet), 20):
            chunk = packet[i : i + 20]
            await self.client.write_gatt_char(WRITE_UUID, chunk, response=False)
            await asyncio.sleep(0.03)

    async def _execute_step(
        self,
        payload: bytes,
        key: bytes,
        frame_seq: int,
        expected_op: bytes | None = None,
        timeout: float = COMMAND_TIMEOUT,
    ) -> bytes:
        await self._send_command(payload, key, frame_seq)
        start_t = time.time()

        while (time.time() - start_t) < timeout:
            remaining = timeout - (time.time() - start_t)
            cipher_resp = await asyncio.wait_for(
                self.recv_queue.get(), timeout=max(remaining, 0.1)
            )
            dec = decrypt_ecb(key, cipher_resp)

            if expected_op is not None and len(dec) >= 8:
                if dec[5:7] == expected_op:
                    return dec
                continue
            return dec

        raise TimeoutError("Timed out waiting for opcode response")

    async def authenticate(self):
        # 1. Handshake 0x00F0
        probe_cmd = bytes.fromhex("000000000200f000000000000c")
        resp_f0 = await self._execute_step(
            probe_cmd,
            self.master_key,
            frame_seq=2,
            expected_op=bytes.fromhex("00f0"),
        )
        self.session_token = resp_f0[:4]

        # 2. Key Exchange 0x00F1
        key_req = self.session_token + bytes.fromhex("0300f100000000000c")
        resp_f1 = await self._execute_step(
            key_req,
            self.master_key,
            frame_seq=2,
            expected_op=bytes.fromhex("00f1"),
        )
        if len(resp_f1) < 29:
            raise RuntimeError("Invalid session key response length")
        self.session_key = resp_f1[13:29]

        # 3. Context Synchronization 0x00F2 with dynamic Lock Salt
        sync_cmd = (
            self.session_token
            + bytes.fromhex("0400f2000003e9000c")
            + self.lock_salt
        )
        await self._execute_step(
            sync_cmd,
            self.session_key,
            frame_seq=3,
            expected_op=bytes.fromhex("00f2"),
        )
        self.seq_counter = 5

    async def get_battery_status(self) -> int | None:
        status_cmd = (
            self.session_token
            + bytes([self.seq_counter])
            + bytes.fromhex("000d000003e9000c00000000")
        )
        self.seq_counter = (self.seq_counter + 1) & 0xFF

        try:
            resp_decrypted = await self._execute_step(
                status_cmd,
                self.session_key,
                frame_seq=3,
                expected_op=bytes.fromhex("000d"),
                timeout=4.0,
            )
        except Exception:
            return None

        battery = None
        if b"MX" in resp_decrypted:
            mx_idx = resp_decrypted.index(b"MX")
            if mx_idx + 2 < len(resp_decrypted):
                battery = resp_decrypted[mx_idx + 2]
        elif len(resp_decrypted) >= 30:
            battery = resp_decrypted[29]
        elif len(resp_decrypted) >= 29:
            battery = resp_decrypted[28]

        return battery if (battery is not None and 0 <= battery <= 100) else None

    async def fetch_all_logs(self, slot_mappings: dict[int, str] | None = None) -> list[dict]:
        self.seq_counter = 7
        count_req = (
            self.session_token
            + bytes([self.seq_counter])
            + bytes.fromhex("0008000003e9000c")
        )
        self.seq_counter = (self.seq_counter + 1) & 0xFF

        count_dec = await self._execute_step(
            count_req,
            self.session_key,
            frame_seq=3,
            expected_op=bytes.fromhex("0008"),
        )
        total_records = int.from_bytes(count_dec[-2:], byteorder="big")

        if total_records == 0:
            return []

        all_records = []
        total_pages = (total_records + 4) // 5

        for page in range(total_pages):
            offset = page * 5
            fetch_req = (
                self.session_token
                + bytes([self.seq_counter])
                + bytes.fromhex("0009010003e9001f")
                + offset.to_bytes(2, byteorder="big")
                + bytes.fromhex("0005")
            )
            self.seq_counter = (self.seq_counter + 1) & 0xFF

            log_dec = await self._execute_step(
                fetch_req,
                self.session_key,
                frame_seq=3,
                expected_op=bytes.fromhex("0009"),
            )

            records_data = log_dec[15:]
            num_in_page = len(records_data) // 32
            if num_in_page == 0:
                break

            for i in range(num_in_page):
                rec = records_data[i * 32 : (i + 1) * 32]
                if len(rec) != 32 or rec[4] == 0x3D:
                    continue

                parsed = parse_log_record(rec, slot_mappings)
                parsed = {
                    "count": len(all_records) + 1,
                    **parsed,
                }
                all_records.append(parsed)

        return all_records

    async def unlock(self) -> int:
        ts = int(time.time()).to_bytes(4, byteorder="big")

        # 4. Time calibration 0x0007
        time_cmd = (
            self.session_token
            + bytes.fromhex("010007000003e9000c")
            + ts
            + bytes.fromhex("004d58")
        )
        await self._execute_step(
            time_cmd,
            self.session_key,
            frame_seq=3,
            expected_op=bytes.fromhex("0007"),
        )

        # 5. Remote unlock 0x0001
        unlock_payload = (
            self.session_token
            + bytes.fromhex("070001060103e9001500000006")
            + ts
            + bytes.fromhex("004d5800000000")
        )
        unlock_dec = await self._execute_step(
            unlock_payload,
            self.session_key,
            frame_seq=3,
            expected_op=bytes.fromhex("0001"),
        )

        acknowledged = (
            len(unlock_dec) >= 8
            and unlock_dec[5:7] == b"\x00\x01"
            and unlock_dec[7] == 0x01
        )
        if not acknowledged:
            raise RuntimeError(
                "Atomberg unlock was not acknowledged: " + unlock_dec.hex(" ")
            )

        return int.from_bytes(ts, byteorder="big")
