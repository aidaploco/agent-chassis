import dataclasses

from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.converter import DataConverter

from agent_chassis.config import ChassisSettings
from agent_chassis.durable.codec import EncryptionCodec


class CodecKeyMissingError(RuntimeError):
    """Raised when the worker starts without a payload codec key (D9)."""


def build_data_converter(settings: ChassisSettings) -> DataConverter:
    """Pydantic converter wrapped with mandatory payload encryption.

    Fails closed (D9): there is no plaintext mode. Local dev sets an explicit
    key in docker-compose / .env.
    """
    if not settings.temporal_payload_codec_key:
        raise CodecKeyMissingError(
            "TEMPORAL_PAYLOAD_CODEC_KEY is not set — the worker will not start"
            " without payload encryption (D9). Generate a dev key with:"
            " openssl rand -base64 32"
        )
    return dataclasses.replace(
        pydantic_data_converter,
        payload_codec=EncryptionCodec(
            key_id=settings.temporal_payload_codec_key_id,
            key_b64=settings.temporal_payload_codec_key,
        ),
    )
