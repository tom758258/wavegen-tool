"""Shared Wavegen Worker protocol identity constants."""

WORKER_SCHEMA_VERSION = 3
WORKER_SERVICE_NAME = "wavegen-tool"
WORKER_COMPATIBILITY_POLICY = "v3-only"
CANCELLABLE_COMMANDS = ("run-software-sequence", "run-amplitude-ramp", "run-frequency-step")


def worker_features() -> dict[str, object]:
    """Return the fixed features supported by every Wavegen Worker runtime."""

    return {
        "common.async_jobs": {"contract_version": 1},
        "common.job_cancel": {"contract_version": 1, "commands": list(CANCELLABLE_COMMANDS)},
    }


def common_error(
    category: str, code: str, message: str, *, details: dict[str, object] | None = None
) -> dict[str, object]:
    return {
        "category": category,
        "code": code,
        "message": message,
        "details": {} if details is None else details,
    }
