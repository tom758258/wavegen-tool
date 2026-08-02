from copy import deepcopy

import pytest

import wavegen_tool_cli.worker_commands as worker_commands
from wavegen_tool_cli.worker_commands import (
    ValidatedWorkerCommand,
    WorkerRequestValidationError,
    validate_worker_command_request,
)


def _payload(
    command: str,
    arguments: dict[str, object] | None = None,
    *,
    context: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "command": command,
        "arguments": {} if arguments is None else arguments,
        "job_id": "job-1",
        "context": {"mode": "live"} if context is None else context,
    }


def _sine_arguments() -> dict[str, object]:
    return {"frequency_hz": 1000, "amplitude_vpp": 0.1}


@pytest.mark.parametrize(
    ("command", "arguments", "expected_arguments"),
    [
        ("identify", {}, {}),
        ("status", {}, {}),
        ("read-errors", {}, {"max_reads": 20}),
        (
            "configure-sine",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1},
            {"frequency_hz": 1000, "amplitude_vpp": 0.1, "offset_v": 0, "load": "50"},
        ),
        (
            "configure-square",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1},
            {
                "frequency_hz": 1000,
                "amplitude_vpp": 0.1,
                "offset_v": 0,
                "duty_cycle_percent": 50,
                "load": "50",
            },
        ),
        (
            "configure-ramp",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1},
            {
                "frequency_hz": 1000,
                "amplitude_vpp": 0.1,
                "offset_v": 0,
                "symmetry_percent": 100,
                "load": "50",
            },
        ),
        (
            "configure-triangle",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1},
            {
                "frequency_hz": 1000,
                "amplitude_vpp": 0.1,
                "offset_v": 0,
                "load": "50",
            },
        ),
        (
            "configure-pulse",
            {"frequency_hz": 1000, "amplitude_vpp": 0.1, "pulse_width_s": 0.0001},
            {
                "frequency_hz": 1000,
                "amplitude_vpp": 0.1,
                "pulse_width_s": 0.0001,
                "offset_v": 0,
                "edge_time_s": 1e-8,
                "load": "50",
            },
        ),
        (
            "configure-dc",
            {"voltage_v": 1.5},
            {"voltage_v": 1.5, "load": "50"},
        ),
        (
            "configure-noise",
            {"amplitude_vpp": 0.1, "bandwidth_hz": 1_000_000},
            {
                "amplitude_vpp": 0.1,
                "bandwidth_hz": 1_000_000,
                "offset_v": 0,
                "load": "50",
            },
        ),
        (
            "configure-prbs",
            {"bit_rate_bps": 1_000_000, "amplitude_vpp": 0.1},
            {
                "bit_rate_bps": 1_000_000,
                "amplitude_vpp": 0.1,
                "pattern": "PN7",
                "offset_v": 0,
                "edge_time_s": 8.4e-9,
                "load": "50",
            },
        ),
        ("output", {"enabled": False}, {"enabled": False}),
    ],
)
def test_supported_command_matrix_preserves_envelope_and_adds_defaults(
    command, arguments, expected_arguments
):
    result = validate_worker_command_request(
        _payload(command, arguments),
        worker_mode="live",
        allow_output_writes=True,
    )

    assert isinstance(result, ValidatedWorkerCommand)
    assert result.schema_version == 2
    assert result.command == command
    assert result.arguments == expected_arguments
    assert result.job_id == "job-1"
    assert result.context == {"mode": "live"}


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (None, "invalid_request"),
        ([], "invalid_request"),
        ({"command": "status", "context": {"mode": "live"}}, "invalid_request"),
        (_payload("status") | {"schema_version": True}, "invalid_request"),
        (_payload("status") | {"schema_version": "2"}, "invalid_request"),
        (_payload("status") | {"schema_version": 2.0}, "invalid_request"),
        (_payload("status") | {"schema_version": 1}, "invalid_request"),
        (_payload("status") | {"extra": True}, "invalid_request"),
        (_payload("status") | {"arguments": []}, "invalid_request"),
        (_payload("status") | {"job_id": 7}, "invalid_request"),
        (_payload("status") | {"context": []}, "invalid_context"),
    ],
)
def test_envelope_validation_rejects_malformed_requests(payload, expected_code):
    with pytest.raises(WorkerRequestValidationError) as error:
        validate_worker_command_request(
            payload,
            worker_mode="live",
            allow_output_writes=True,
        )

    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("context", "worker_mode"),
    [
        ({"mode": "live", "planning_model_id": "keysight-33521b"}, "live"),
        ({"mode": "simulate"}, "simulate"),
        (
            {
                "mode": "simulate",
                "expected_model_id": "keysight-33521b",
                "planning_model_id": "keysight-33521b",
            },
            "simulate",
        ),
        ({"mode": "dry_run"}, "live"),
        ({"mode": "live", "expected_model_id": "keysight-33522b"}, "live"),
        ({"mode": "simulate", "planning_model_id": "keysight-33521b"}, "live"),
        ({"mode": "live"}, "simulate"),
        ({"mode": "live", "unknown": True}, "live"),
    ],
    ids=[
        "live-planning-model",
        "simulate-missing-planning-model",
        "simulate-expected-model",
        "dry-run-missing-planning-model",
        "wrong-model-id",
        "simulate-worker-live-request",
        "live-worker-valid-request",
        "unknown-context-field",
    ],
)
def test_context_validation_rejects_invalid_contexts(context, worker_mode):
    with pytest.raises(WorkerRequestValidationError) as error:
        validate_worker_command_request(
            _payload("status", context=context),
            worker_mode=worker_mode,
            allow_output_writes=True,
        )

    assert error.value.code == "invalid_context"


@pytest.mark.parametrize(
    ("command", "arguments", "expected_code"),
    [
        ("list-resources", {}, "unsupported_command"),
        ("status", {"extra": True}, "invalid_arguments"),
        ("configure-sine", {"frequency_hz": 1000}, "invalid_arguments"),
        ("read-errors", {"max_reads": 0}, "invalid_arguments"),
        ("read-errors", {"max_reads": 101}, "invalid_arguments"),
        ("read-errors", {"max_reads": True}, "invalid_arguments"),
        ("output", {"enabled": "true"}, "invalid_arguments"),
        ("output", {"enabled": False, "confirm_output": "yes"}, "invalid_arguments"),
    ],
)
def test_command_arguments_validation_rejects_invalid_arguments(
    command, arguments, expected_code
):
    with pytest.raises(WorkerRequestValidationError) as error:
        validate_worker_command_request(
            _payload(command, arguments),
            worker_mode="live",
            allow_output_writes=True,
        )

    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("worker_mode", "context", "command", "arguments", "allow_output_writes", "expected_code"),
    [
        (
            "live",
            {"mode": "live"},
            "configure-sine",
            _sine_arguments(),
            False,
            "live_write_not_allowed",
        ),
        (
            "live",
            {"mode": "live"},
            "configure-sine",
            _sine_arguments(),
            "false",
            "invalid_request",
        ),
        (
            "live",
            {"mode": "live"},
            "configure-sine",
            _sine_arguments(),
            True,
            None,
        ),
        (
            "live",
            {"mode": "live"},
            "output",
            {"enabled": True},
            False,
            "live_write_not_allowed",
        ),
        (
            "live",
            {"mode": "live"},
            "output",
            {"enabled": True},
            True,
            "output_confirmation_required",
        ),
        (
            "live",
            {"mode": "live"},
            "output",
            {"enabled": True, "confirm_output": True},
            True,
            None,
        ),
        (
            "live",
            {"mode": "live"},
            "output",
            {"enabled": False},
            False,
            None,
        ),
        (
            "simulate",
            {"mode": "simulate", "planning_model_id": "keysight-33521b"},
            "output",
            {"enabled": True},
            False,
            None,
        ),
        (
            "live",
            {"mode": "dry_run", "planning_model_id": "keysight-33521b"},
            "configure-sine",
            _sine_arguments(),
            False,
            None,
        ),
        (
            "live",
            {"mode": "dry_run", "planning_model_id": "keysight-33521b"},
            "identify",
            {},
            False,
            "unsupported_command",
        ),
    ],
)
def test_live_write_safety_and_dry_run_scope(
    worker_mode,
    context,
    command,
    arguments,
    allow_output_writes,
    expected_code,
):
    if expected_code is None:
        result = validate_worker_command_request(
            _payload(command, arguments, context=context),
            worker_mode=worker_mode,
            allow_output_writes=allow_output_writes,
        )
        assert isinstance(result, ValidatedWorkerCommand)
    else:
        with pytest.raises(WorkerRequestValidationError) as error:
            validate_worker_command_request(
                _payload(command, arguments, context=context),
                worker_mode=worker_mode,
                allow_output_writes=allow_output_writes,
            )
        assert error.value.code == expected_code
        if allow_output_writes == "false":
            assert str(error.value) == "allow_output_writes must be a boolean."


def test_configure_sine_delegates_to_core_and_converts_parameter_errors(monkeypatch):
    real_dry_run_sine = worker_commands.dry_run_sine
    calls = []

    def spy(*args):
        calls.append(args)
        return real_dry_run_sine(*args)

    monkeypatch.setattr(worker_commands, "dry_run_sine", spy)

    result = validate_worker_command_request(
        _payload("configure-sine", _sine_arguments()),
        worker_mode="live",
        allow_output_writes=True,
    )
    assert isinstance(result, ValidatedWorkerCommand)
    assert calls[0][0] == "keysight-33521b"
    assert calls[0][1:] == (1000, 0.1, 0, "50")

    with pytest.raises(WorkerRequestValidationError) as error:
        validate_worker_command_request(
            _payload("configure-sine", {"frequency_hz": 0, "amplitude_vpp": 0.1}),
            worker_mode="live",
            allow_output_writes=True,
        )

    assert error.value.code == "invalid_arguments"
    assert str(error.value) == (
        "Sine frequency must be between 0.000001 Hz and 30000000 Hz."
    )


def test_validation_does_not_mutate_arguments_or_add_live_identity_guard():
    payload = _payload("configure-sine", _sine_arguments())
    original_payload = deepcopy(payload)

    result = validate_worker_command_request(
        payload,
        worker_mode="live",
        allow_output_writes=True,
    )

    assert payload == original_payload
    assert result.arguments == {
        "frequency_hz": 1000,
        "amplitude_vpp": 0.1,
        "offset_v": 0,
        "load": "50",
    }
    assert result.arguments is not payload["arguments"]
    assert result.context == {"mode": "live"}
    assert "expected_model_id" not in result.context
