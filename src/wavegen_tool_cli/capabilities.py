"""Offline model capability introspection for orchestrators."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from wavegen_tool_core.capabilities import capabilities_for_model_id
from wavegen_tool_core.identity import model_info_for_model_id


TOOL_ID = "wavegen"
MACHINE_SCHEMA_VERSION = 2


def build_capabilities_response(model_id: str) -> dict[str, object]:
    """Build an offline capability response from the Core registries."""

    model_info = model_info_for_model_id(model_id)
    capabilities = capabilities_for_model_id(model_id)
    if model_info is None or capabilities is None:
        return {
            "event": "error",
            "schema_version": MACHINE_SCHEMA_VERSION,
            "tool_id": TOOL_ID,
            "ok": False,
            "error": "invalid_request",
            "message": (
                f"Unsupported model ID {model_id!r}; "
                "expected an exact registered model ID."
            ),
            "exit_code": 2,
            "selection": {"requested_model": model_id},
        }

    return {
        "event": "capabilities",
        "schema_version": MACHINE_SCHEMA_VERSION,
        "tool_id": TOOL_ID,
        "selection": {"requested_model": model_id},
        "model": {
            "model_id": model_info.model_id,
            "canonical_model": model_info.canonical_model,
        },
        "capabilities": asdict(capabilities),
    }


def run_capabilities(args: argparse.Namespace) -> int:
    """Print one machine-readable offline capability response."""

    payload = build_capabilities_response(args.model)
    print(json.dumps(payload, separators=(",", ":")))
    if payload["event"] == "error":
        return 2
    return 0
