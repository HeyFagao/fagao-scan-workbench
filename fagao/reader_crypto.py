"""Decode the public reader's AES-CBC transport with bundled, pinned pyaes.

This is only a format adapter. The caller must establish that the official
service permits public access before requesting or decoding any chapter.
It is not used for credentials, local storage, or new encryption protocols.
"""

from __future__ import annotations

from hmac import compare_digest

from ._vendor.pyaes import AESModeOfOperationCBC


MAX_CIPHERTEXT_BYTES = 4 * 1024 * 1024


def decrypt_cbc(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """Decrypt AES-CBC and require valid PKCS#7 padding, or raise ValueError.

    No implicit encodings, password derivation, external executables, network
    calls, or package installation occur here. pyaes' permissive unpad helper
    is deliberately unused: every padding byte must match the padding length.
    """
    if not all(isinstance(value, bytes) for value in (ciphertext, key, iv)):
        raise ValueError("AES inputs must be bytes")
    if len(key) not in (16, 24, 32) or len(iv) != 16:
        raise ValueError("Invalid AES key or IV length")
    if not ciphertext or len(ciphertext) % 16 or len(ciphertext) > MAX_CIPHERTEXT_BYTES:
        raise ValueError("Invalid AES ciphertext length")

    mode = AESModeOfOperationCBC(key, iv=iv)
    plaintext = b"".join(
        mode.decrypt(ciphertext[offset:offset + 16])
        for offset in range(0, len(ciphertext), 16)
    )
    pad_length = plaintext[-1]
    if not 1 <= pad_length <= 16 or not compare_digest(
        plaintext[-pad_length:], bytes([pad_length]) * pad_length
    ):
        raise ValueError("Invalid AES padding")
    return plaintext[:-pad_length]
