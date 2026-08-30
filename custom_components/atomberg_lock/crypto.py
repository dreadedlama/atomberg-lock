"""Atomberg cryptographic helpers."""
from __future__ import annotations
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad

def encrypt_ecb(key: bytes, data: bytes) -> bytes:
    return AES.new(key, AES.MODE_ECB).encrypt(pad(data, 16))

def decrypt_ecb(key: bytes, data: bytes) -> bytes:
    dec = AES.new(key, AES.MODE_ECB).decrypt(data)
    try:
        return unpad(dec, 16)
    except ValueError:
        return dec

def crc16_modbus_be(data: bytes) -> bytes:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc.to_bytes(2, "big")
