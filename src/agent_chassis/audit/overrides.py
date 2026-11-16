import hashlib
from datetime import UTC, datetime
from pathlib import Path

from agent_chassis.audit.base import AuditEvent, AuditSink
from agent_chassis.packs.registry import ResolvedRole


def manifest_digest(path: Path | str) -> str:
    """SHA-256 of the manifest file: ties an override to the exact manifest that
    set it, and keeps a restart from re-recording the same overrides."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


async def record_overrides(
    sink: AuditSink, resolved: ResolvedRole, *, tenant: str, manifest_sha256: str
) -> int:
    """Write every manifest override (a config value that differs from the pack
    default) to the audit stream, once per manifest version (D11).

    Returns how many were newly written (0 on a restart with the same manifest).
    """
    written = 0
    for override in resolved.overrides:
        event = AuditEvent(
            kind="manifest_override",
            employee=override.role,
            tenant=tenant,
            subject=f"{override.kind}:{override.pack}.{override.setting}",
            actor="manifest",
            action="override",
            detail={
                "kind": override.kind,
                "pack": override.pack,
                "setting": override.setting,
                "value": override.value,
                "default": override.default,
                "manifest_sha256": manifest_sha256,
            },
            dedupe_key=(
                f"{manifest_sha256}:{override.kind}:{override.pack}:{override.setting}"
            ),
            occurred_at=datetime.now(UTC),
        )
        written += await sink.record(event)
    return written
