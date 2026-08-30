#!/usr/bin/env python3

import struct
import sys
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

FACTORY_KEY = b"mQ3cJOATdERLW1a5"
HSJ = b"HSJ"


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def aes_decrypt(key: bytes, ciphertext: bytes) -> bytes:
    cipher = AES.new(key, AES.MODE_ECB)
    dec = cipher.decrypt(ciphertext)
    try:
        return unpad(dec, 16)
    except ValueError:
        return dec


def read_btsnoop(filename: str):
    data = Path(filename).read_bytes()
    if not data.startswith(b"btsnoop"):
        raise RuntimeError("Not a valid BTSnoop file")

    records = []
    offset = 16
    number = 0

    while offset + 24 <= len(data):
        original_len, captured_len, flags, drops, timestamp = struct.unpack(
            ">IIIIQ", data[offset : offset + 24]
        )
        offset += 24
        if offset + captured_len > len(data):
            break

        packet = data[offset : offset + captured_len]
        offset += captured_len
        number += 1

        records.append({
            "number": number,
            "data": packet,
        })
    return records


def parse_att(record):
    p = record["data"]
    if len(p) < 9 or p[0] != 0x02:
        return None

    acl_length = struct.unpack("<H", p[3:5])[0]
    payload = p[5 : 5 + acl_length]
    if len(payload) < 5 or struct.unpack("<H", payload[2:4])[0] != 4:
        return None

    att = payload[4:]
    if not att:
        return None

    if att[0] == 0x52:  # Write
        return {"direction": "TX", "value": att[3:]}
    if att[0] == 0x1B:  # Notify
        return {"direction": "RX", "value": att[3:]}
    return None


def collect_hsj_frames(records):
    frames = []
    current = None

    for record in records:
        att = parse_att(record)
        if not att:
            continue

        value = att["value"]
        if value.startswith(HSJ):
            if len(value) < 5:
                continue
            declared = int.from_bytes(value[3:5], "big")
            current = {
                "direction": att["direction"],
                "record": record["number"],
                "expected": declared,
                "data": bytearray(value),
            }
        elif current is not None:
            if att["direction"] != current["direction"]:
                continue
            current["data"].extend(value)
        else:
            continue

        if current is not None and len(current["data"]) >= current["expected"]:
            frame = bytes(current["data"][: current["expected"]])
            frames.append({
                "direction": current["direction"],
                "record": current["record"],
                "frame": frame,
            })
            current = None

    return frames


def decrypt_frame(frame: bytes, key: bytes):
    if len(frame) < 9 or not frame.startswith(HSJ):
        return None

    declared = int.from_bytes(frame[3:5], "big")
    if declared != len(frame):
        return None

    ciphertext = frame[7:-2]
    received_crc = int.from_bytes(frame[-2:], "big")
    expected_crc = crc16_modbus(frame[:-2])

    if received_crc != expected_crc or len(ciphertext) % 16 != 0:
        return None

    try:
        return aes_decrypt(key, ciphertext)
    except Exception:
        return None


def main():
    if len(sys.argv) != 2:
        print(f"Usage: python3 {sys.argv[0]} btsnoop_hci.log")
        sys.exit(1)

    records = read_btsnoop(sys.argv[1])
    frames = collect_hsj_frames(records)

    lock_mac = None
    master_key = None
    lock_salt = None

    for item in frames:
        candidate_keys = [FACTORY_KEY]
        if master_key:
            candidate_keys.append(master_key)

        plaintext = None
        for key in candidate_keys:
            res = decrypt_frame(item["frame"], key)
            if res is not None and len(res) >= 8:
                opcode = int.from_bytes(res[5:7], "big")
                if opcode in [0x00F0, 0x00F1, 0x00F2, 0x00F3, 0x00C6, 0x02C6, 0x0007, 0x000D, 0x0019]:
                    plaintext = res
                    break

        if plaintext is None:
            continue

        opcode = int.from_bytes(plaintext[5:7], "big")

        # 1. Capture Master Key from F1 response during initial pairing
        if opcode == 0x00F1 and item["direction"] == "RX" and len(plaintext) >= 29:
            if master_key is None:
                master_key = plaintext[13:29]

        # 2. Extract MAC Address and Salt from 0x00C6 TLV payload
        if opcode == 0x00C6 and item["direction"] == "RX":
            idx = 13
            while idx + 2 < len(plaintext):
                tag_len = plaintext[idx]
                tag_type = plaintext[idx + 1]
                val = plaintext[idx + 2 : idx + tag_len + 1]

                # Tag 0x01: MAC Address (6 bytes)
                if tag_type == 0x01 and len(val) == 6:
                    lock_mac = ":".join(f"{b:02X}" for b in val)

                # Tag 0x09: Salt (4 bytes)
                if tag_type == 0x09 and len(val) == 4:
                    lock_salt = val

                idx += tag_len + 1

        # 3. Extract Salt from 0x00F2 request (non-zero fallback)
        if opcode == 0x00F2 and item["direction"] == "TX" and len(plaintext) >= 17:
            candidate_salt = plaintext[13:17]
            if candidate_salt != b"\x00\x00\x00\x00":
                lock_salt = candidate_salt

    print("=" * 60)
    print(" EXTRACTION COMPLETED")
    print("=" * 60)
    if lock_mac:
        print(f'LOCK_MAC = "{lock_mac}"')
    else:
        print("[!] MAC Address not found.")

    if master_key:
        print(f'STATIC_MASTER_KEY = "{master_key.decode("latin1", errors="replace")}"')
    else:
        print("[!] Master Key not found.")

    if lock_salt:
        print(f'LOCK_SALT = "{lock_salt.hex()}"')
    else:
        print("[!] Salt not found.")
    print("=" * 60)


if __name__ == "__main__":
    main()
