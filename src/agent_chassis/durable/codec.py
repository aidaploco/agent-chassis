import base64
import os
from collections.abc import Sequence

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from temporalio.api.common.v1 import Payload
from temporalio.converter import PayloadCodec

_ENCODING = b"binary/encrypted"
_KEY_ID_METADATA = "encryption-key-id"
_NONCE_SIZE = 12


class EncryptionCodec(PayloadCodec):
    """AES-256-GCM payload codec (D9), ported from a proven earlier
    implementation.

    Conversation content passes through Temporal persistence; this codec
    encrypts client-side so the server only stores ciphertext. Key: base64
    encoded 32 bytes in TEMPORAL_PAYLOAD_CODEC_KEY. Payloads carry an
    encryption-key-id so rotation is a key lookup, not a format change.
    """

    def __init__(self, key_id: str, key_b64: str) -> None:
        try:
            key = base64.b64decode(key_b64, validate=True)
        except Exception as exc:
            raise ValueError("TEMPORAL_PAYLOAD_CODEC_KEY must be valid base64") from exc
        if len(key) != 32:
            raise ValueError(
                "TEMPORAL_PAYLOAD_CODEC_KEY must decode to exactly 32 bytes"
            )
        self._key_id = key_id.encode()
        self._cipher = AESGCM(key)

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        return [self._encrypt(p) for p in payloads]

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        # Pre-codec payloads pass through so old histories stay readable.
        return [
            self._decrypt(p) if p.metadata.get("encoding") == _ENCODING else p
            for p in payloads
        ]

    def _encrypt(self, payload: Payload) -> Payload:
        nonce = os.urandom(_NONCE_SIZE)
        ciphertext = self._cipher.encrypt(nonce, payload.SerializeToString(), None)
        return Payload(
            metadata={"encoding": _ENCODING, _KEY_ID_METADATA: self._key_id},
            data=nonce + ciphertext,
        )

    def _decrypt(self, payload: Payload) -> Payload:
        key_id = payload.metadata.get(_KEY_ID_METADATA, b"")
        if key_id != self._key_id:
            raise ValueError(
                f"unknown payload encryption key id {key_id.decode(errors='replace')!r}"
            )
        nonce, ciphertext = payload.data[:_NONCE_SIZE], payload.data[_NONCE_SIZE:]
        return Payload.FromString(self._cipher.decrypt(nonce, ciphertext, None))
