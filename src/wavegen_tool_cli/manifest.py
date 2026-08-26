"""Static Wavegen tool manifest for offline orchestrator introspection."""

from __future__ import annotations

import argparse
import json
from importlib.metadata import version

from wavegen_tool_cli.worker_protocol import (
    WORKER_COMPATIBILITY_POLICY,
    WORKER_SCHEMA_VERSION,
)

TOOL_ID = "wavegen"
MACHINE_SCHEMA_VERSION = 2


def _package_version() -> str:
    return version("wavegen-tool")


def build_tool_manifest(*, tool_version: str | None = None) -> dict[str, object]:
    """Build the static tool manifest without VISA, Worker, or filesystem work."""

    resolved_version = _package_version() if tool_version is None else tool_version
    return {
        "event": "tool_manifest",
        "schema_version": MACHINE_SCHEMA_VERSION,
        "tool_id": TOOL_ID,
        "tool_version": resolved_version,
        "worker_protocol": {
            "compatibility_policy": WORKER_COMPATIBILITY_POLICY,
            "schema_versions": [WORKER_SCHEMA_VERSION],
        },
    }


def run_manifest(args: argparse.Namespace) -> int:
    """Run the manifest command and return a stable process exit code."""

    payload = build_tool_manifest()
    if args.json_output:
        print(json.dumps(payload, separators=(",", ":")))
        return 0
    worker_protocol = payload["worker_protocol"]
    print(f"{payload['tool_id']} {payload['tool_version']}")
    schema_versions = ", ".join(
        str(item) for item in worker_protocol["schema_versions"]
    )
    print(
        f"Worker protocol: {worker_protocol['compatibility_policy']} "
        f"(schema versions: {schema_versions})"
    )
    return 0
