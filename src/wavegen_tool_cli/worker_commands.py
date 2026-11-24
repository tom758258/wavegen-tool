"""Pure Wavegen Worker command request admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from wavegen_tool_core.action_arguments import (
    ACTION_COMMANDS,
    CONFIGURE_COMMANDS,
    DRY_RUN_COMMANDS,
    MODULATION_FIELDS,
    ActionArgumentError,
    canonicalize_voltage_arguments,
    configuration_options,
    validate_action_arguments,
)
from wavegen_tool_core.errors import WavegenError, WaveformParameterError
from wavegen_tool_core.dual_channel import is_dual_action, plan_dual_channel, uses_other_channel_modulation
from wavegen_tool_core.basic_workflows import BASIC_WORKFLOW_COMMANDS, validate_basic_workflow
from wavegen_tool_core.software_sequence import (
    SOFTWARE_SEQUENCE_ACTION,
    SoftwareSequenceError,
    validate_software_sequence,
)
from wavegen_tool_core.identity import model_info_for_model_id
from wavegen_tool_cli.worker_protocol import WORKER_SCHEMA_VERSION
from wavegen_tool_core.visa import (
    OutputConfig,
    SyncConfig,
    dry_run_dc,
    dry_run_output,
    dry_run_sync,
    dry_run_ramp_list_sweep,
    dry_run_sine_list_sweep,
    dry_run_square_list_sweep,
    dry_run_triangle_list_sweep,
    dry_run_noise,
    dry_run_prbs,
    dry_run_pulse,
    dry_run_ramp,
    dry_run_ramp_sweep,
    dry_run_sine,
    dry_run_sine_sweep,
    dry_run_square,
    dry_run_square_sweep,
    dry_run_triangle,
    dry_run_triangle_sweep,
)

__all__ = [
    "SUPPORTED_WORKER_COMMANDS",
    "ValidatedWorkerCommand",
    "WorkerRequestValidationError",
    "validate_worker_command_request",
]

class WorkerRequestValidationError(ValueError):
    """A Worker command request failed admission validation."""

    def __init__(
        self, code: str, message: str, *, details: dict[str, object] | None = None
    ) -> None:
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


@dataclass(frozen=True)
class ValidatedWorkerCommand:
    """A validated command envelope ready for a later command runner."""

    schema_version: int
    command: str
    arguments: dict[str, object]
    job_id: str | None
    context: dict[str, object]


_ALLOWED_TOP_LEVEL_FIELDS = frozenset(
    {"schema_version", "command", "arguments", "job_id", "context"}
)
_ALLOWED_CONTEXT_FIELDS = frozenset(
    {"mode", "expected_model_id", "planning_model_id"}
)
_REQUEST_MODES = frozenset({"live", "simulate", "dry_run"})
_WORKER_MODES = frozenset({"live", "simulate"})
_SEQUENCE_COMMANDS = BASIC_WORKFLOW_COMMANDS | {SOFTWARE_SEQUENCE_ACTION}
SUPPORTED_WORKER_COMMANDS = ACTION_COMMANDS | _SEQUENCE_COMMANDS
_DRY_RUN_COMMANDS = DRY_RUN_COMMANDS | _SEQUENCE_COMMANDS
_LIVE_WRITE_COMMANDS = DRY_RUN_COMMANDS - {"list-arbitrary-memory", "dual-channel-status"} | {"trigger"}


def validate_worker_command_request(
    payload: object,
    *,
    worker_mode: str,
    allow_output_writes: bool,
) -> ValidatedWorkerCommand:
    """Validate a Worker command without performing device or simulator I/O."""

    if not isinstance(payload, Mapping):
        raise WorkerRequestValidationError(
            "invalid_request", "Worker command payload must be an object."
        )
    if type(allow_output_writes) is not bool:
        raise WorkerRequestValidationError(
            "invalid_request", "allow_output_writes must be a boolean."
        )

    unknown_fields = _unknown_fields(payload, _ALLOWED_TOP_LEVEL_FIELDS)
    if unknown_fields:
        raise WorkerRequestValidationError(
            "invalid_request",
            f"Unknown top-level field(s): {_format_fields(unknown_fields)}.",
        )

    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != WORKER_SCHEMA_VERSION:
        raise WorkerRequestValidationError(
            "invalid_request",
            f"schema_version must be the exact integer {WORKER_SCHEMA_VERSION}.",
        )

    command = payload.get("command")
    if not isinstance(command, str) or not command.strip():
        raise WorkerRequestValidationError(
            "invalid_request", "command must be a non-empty string."
        )

    raw_arguments = payload.get("arguments", {})
    if not isinstance(raw_arguments, Mapping):
        raise WorkerRequestValidationError(
            "invalid_request", "arguments must be an object."
        )
    arguments = dict(raw_arguments)

    if "job_id" in payload and not isinstance(payload["job_id"], str):
        raise WorkerRequestValidationError(
            "invalid_request", "job_id must be a string when provided."
        )
    job_id = payload.get("job_id")

    raw_context = payload.get("context")
    if not isinstance(raw_context, Mapping):
        raise WorkerRequestValidationError(
            "invalid_context", "context must be an object."
        )
    context, request_mode = _validate_context(raw_context, worker_mode)

    if command not in SUPPORTED_WORKER_COMMANDS:
        raise WorkerRequestValidationError(
            "unsupported_command", f"Unsupported Worker command: {command}."
        )
    if request_mode == "dry_run" and command not in _DRY_RUN_COMMANDS and not is_dual_action(command, arguments):
        raise WorkerRequestValidationError(
            "unsupported_command",
            "dry_run context only supports configure-*, Arbitrary, and software sequence commands.",
        )

    if command in _SEQUENCE_COMMANDS:
        _validate_sequence_arguments(
            command, request_mode, arguments, allow_output_writes=allow_output_writes
        )
    else:
        try:
            validate_action_arguments(command, arguments, require_absolute_path=True)
        except ActionArgumentError as exc:
            raise WorkerRequestValidationError(exc.code, str(exc)) from exc
        _validate_live_write_safety(
            request_mode, command, arguments, allow_output_writes=allow_output_writes
        )

    if request_mode == "live" and (is_dual_action(command, arguments) or uses_other_channel_modulation(arguments)):
        raise WorkerRequestValidationError("unsupported_command", "Dual channel operations are outside Product Live feature scope.")
    if is_dual_action(command, arguments):
        model_id = context.get("planning_model_id") or context.get("expected_model_id")
        if model_id is not None:
            try:
                plan_dual_channel(command, arguments, model_id)
            except WavegenError as exc:
                raise WorkerRequestValidationError("invalid_arguments", str(exc)) from exc
    if command in CONFIGURE_COMMANDS:
        try:
            canonicalize_voltage_arguments(command, arguments)
        except ActionArgumentError as exc:
            raise WorkerRequestValidationError(exc.code, str(exc)) from exc
        validation_model_id = (
            context.get("expected_model_id")
            if request_mode == "live"
            else context["planning_model_id"]
        )
        if validation_model_id is not None:
            _validate_waveform_arguments(
                command,
                arguments,
                validation_model_id,
                channel=arguments.get("channel", 1),
            )

    return ValidatedWorkerCommand(
        schema_version=schema_version,
        command=command,
        arguments=arguments,
        job_id=job_id,
        context=context,
    )


def _validate_context(
    raw_context: Mapping[object, object],
    worker_mode: str,
) -> tuple[dict[str, object], str]:
    context = dict(raw_context)
    unknown_fields = _unknown_fields(context, _ALLOWED_CONTEXT_FIELDS)
    if unknown_fields:
        raise WorkerRequestValidationError(
            "invalid_context",
            f"Unknown context field(s): {_format_fields(unknown_fields)}.",
        )

    request_mode = context.get("mode")
    if not isinstance(request_mode, str) or request_mode not in _REQUEST_MODES:
        raise WorkerRequestValidationError(
            "invalid_context",
            "context.mode must be live, simulate, or dry_run.",
        )

    if request_mode == "live":
        if "planning_model_id" in context:
            raise WorkerRequestValidationError(
                "invalid_context",
                "live context must not include planning_model_id.",
            )
        if "expected_model_id" in context:
            _validate_registered_model_id(
                context["expected_model_id"],
                field_name="expected_model_id",
            )
    else:
        if "expected_model_id" in context:
            raise WorkerRequestValidationError(
                "invalid_context",
                f"{request_mode} context must not include expected_model_id.",
            )
        if "planning_model_id" not in context:
            raise WorkerRequestValidationError(
                "invalid_context",
                f"{request_mode} context requires planning_model_id.",
            )
        _validate_registered_model_id(
            context["planning_model_id"],
            field_name="planning_model_id",
        )

    if not isinstance(worker_mode, str) or worker_mode not in _WORKER_MODES:
        raise WorkerRequestValidationError(
            "invalid_context", "worker_mode must be live or simulate."
        )
    allowed_request_modes = (
        frozenset({"live", "dry_run"})
        if worker_mode == "live"
        else frozenset({"simulate", "dry_run"})
    )
    if request_mode not in allowed_request_modes:
        raise WorkerRequestValidationError(
            "invalid_context",
            f"{worker_mode} Worker cannot accept {request_mode} requests.",
        )

    return context, request_mode


def _validate_sequence_arguments(
    command: str, request_mode: str, arguments: dict[str, object], *, allow_output_writes: bool
) -> None:
    if command == SOFTWARE_SEQUENCE_ACTION and (
        "document" not in arguments or arguments.keys() - {"document", "confirm_output"}
    ):
        raise WorkerRequestValidationError(
            "invalid_arguments", "run-software-sequence requires document and accepts optional confirm_output."
        )
    if "confirm_output" in arguments and type(arguments["confirm_output"]) is not bool:
        raise WorkerRequestValidationError("invalid_arguments", "confirm_output must be a boolean.")
    live = request_mode == "live"
    try:
        if command == SOFTWARE_SEQUENCE_ACTION:
            sequence = validate_software_sequence(
                arguments["document"], require_absolute_path=True, product_live=live
            )
        else:
            sequence = validate_basic_workflow(
                command, {key: value for key, value in arguments.items() if key != "confirm_output"},
                product_live=live,
            )
    except WavegenError as exc:
        details = (
            {"domain_error": {"code": exc.code, "message": str(exc), "sequence": exc.details}}
            if isinstance(exc, SoftwareSequenceError) else None
        )
        raise WorkerRequestValidationError("invalid_arguments", str(exc), details=details) from exc
    if live and sequence.writes and not allow_output_writes:
        raise WorkerRequestValidationError(
            "live_write_not_allowed", "Live software sequence writes require allow_output_writes=True."
        )
    if live and sequence.enables_output and arguments.get("confirm_output") is not True:
        raise WorkerRequestValidationError(
            "output_confirmation_required", "Live software sequence output ON requires confirm_output=True."
        )


def _validate_live_write_safety(
    request_mode: str,
    command: str,
    arguments: Mapping[str, object],
    *,
    allow_output_writes: bool,
) -> None:
    if request_mode != "live":
        return

    if command in _LIVE_WRITE_COMMANDS and not allow_output_writes:
        raise WorkerRequestValidationError(
            "live_write_not_allowed",
            f"{command} requires allow_output_writes=True in live context.",
        )

    enabling_output = (
        (command == "output" and arguments["enabled"])
        or (command == "sync-output" and arguments["state"] == "on")
    )
    if not enabling_output:
        return
    if not allow_output_writes:
        raise WorkerRequestValidationError(
            "live_write_not_allowed",
            f"live {command} enable requires allow_output_writes=True.",
        )
    if arguments.get("confirm_output") is not True:
        raise WorkerRequestValidationError(
            "output_confirmation_required",
            f"live {command} enable requires confirm_output=True.",
        )


def _validate_waveform_arguments(
    command: str,
    arguments: Mapping[str, object],
    model_id: object,
    *,
    channel: int,
) -> None:
    try:
        if command == "configure-sync":
            dry_run_sync(model_id, SyncConfig(**arguments))
        elif command == "configure-output":
            config = OutputConfig(**{key: value for key, value in arguments.items() if key != "channel"})
            dry_run_output(model_id, config, channel=channel)
        elif command == "configure-sine":
            dry_run_sine(
                model_id,
                arguments["frequency_hz"],
                arguments["amplitude_vpp"],
                arguments["offset_v"],
                arguments["load"],
                arguments["phase_deg"],
                channel=channel,
                **configuration_options(command, arguments),
            )
        else:
            operation = {
                "configure-sine-sweep": dry_run_sine_sweep,
                "configure-square-sweep": dry_run_square_sweep,
                "configure-ramp-sweep": dry_run_ramp_sweep,
                "configure-triangle-sweep": dry_run_triangle_sweep,
                "configure-sine-list-sweep": dry_run_sine_list_sweep,
                "configure-square-list-sweep": dry_run_square_list_sweep,
                "configure-ramp-list-sweep": dry_run_ramp_list_sweep,
                "configure-triangle-list-sweep": dry_run_triangle_list_sweep,
                "configure-square": dry_run_square,
                "configure-ramp": dry_run_ramp,
                "configure-triangle": dry_run_triangle,
                "configure-pulse": dry_run_pulse,
                "configure-dc": dry_run_dc,
                "configure-noise": dry_run_noise,
                "configure-prbs": dry_run_prbs,
            }[command]
            options = configuration_options(command, arguments)
            values = {key: value for key, value in arguments.items()
                      if key not in options and key not in {"verify_readback", *MODULATION_FIELDS, "burst"}}
            operation(model_id, **values, **options)
    except WaveformParameterError as exc:
        raise WorkerRequestValidationError("invalid_arguments", str(exc)) from exc


def _validate_registered_model_id(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or model_info_for_model_id(value) is None:
        raise WorkerRequestValidationError(
            "invalid_context",
            f"{field_name} must be an exact registered model ID.",
        )


def _unknown_fields(
    value: Mapping[object, object],
    allowed_fields: frozenset[str],
) -> list[object]:
    return [field for field in value if field not in allowed_fields]


def _format_fields(fields: list[object]) -> str:
    return ", ".join(repr(field) for field in fields)
