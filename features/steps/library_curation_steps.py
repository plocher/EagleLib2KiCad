"""Executable step definitions for library_curation Behave features."""

from __future__ import annotations

from typing import Any

from behave import given, then, when
try:
    from .common_workspace import state as _state
    from .common_workspace import table_rows as _table_rows
    from .library_curation_harness import ArtifactBytes
    from .library_curation_harness import LibraryCurationRunResult
    from .library_curation_harness import LibraryCurationScenarioInput
    from .library_curation_harness import assert_rows_include as _assert_rows_include
    from .library_curation_harness import normalize_name as _normalize_name
    from .library_curation_harness import parse_audit_options as _parse_audit_options
    from .library_curation_harness import parse_curated_symbols as _parse_curated_symbols
    from .library_curation_harness import parse_eagle_devices as _parse_eagle_devices
    from .library_curation_harness import parse_eagle_symbols as _parse_eagle_symbols
    from .library_curation_harness import parse_kicad_footprints as _parse_kicad_footprints
    from .library_curation_harness import parse_kicad_symbols as _parse_kicad_symbols
    from .library_curation_harness import (
        parse_matching_policy_overrides as _parse_matching_policy_overrides,
    )
    from .library_curation_harness import (
        parse_role_filtering_overrides as _parse_role_filtering_overrides,
    )
    from .library_curation_harness import (
        parse_source_symbol_hashes as _parse_source_symbol_hashes,
    )
    from .library_curation_harness import run_library_curation_capability as _run_capability
    from .library_curation_harness import run_library_curation_command as _run_command
except Exception:
    from common_workspace import state as _state
    from common_workspace import table_rows as _table_rows
    from library_curation_harness import ArtifactBytes
    from library_curation_harness import LibraryCurationRunResult
    from library_curation_harness import LibraryCurationScenarioInput
    from library_curation_harness import assert_rows_include as _assert_rows_include
    from library_curation_harness import normalize_name as _normalize_name
    from library_curation_harness import parse_audit_options as _parse_audit_options
    from library_curation_harness import parse_curated_symbols as _parse_curated_symbols
    from library_curation_harness import parse_eagle_devices as _parse_eagle_devices
    from library_curation_harness import parse_eagle_symbols as _parse_eagle_symbols
    from library_curation_harness import parse_kicad_footprints as _parse_kicad_footprints
    from library_curation_harness import parse_kicad_symbols as _parse_kicad_symbols
    from library_curation_harness import (
        parse_matching_policy_overrides as _parse_matching_policy_overrides,
    )
    from library_curation_harness import (
        parse_role_filtering_overrides as _parse_role_filtering_overrides,
    )
    from library_curation_harness import (
        parse_source_symbol_hashes as _parse_source_symbol_hashes,
    )
    from library_curation_harness import run_library_curation_capability as _run_capability
    from library_curation_harness import run_library_curation_command as _run_command


def _typed_scenario_input(context: Any) -> LibraryCurationScenarioInput:
    """Build typed harness input from scenario state."""
    state = _state(context)
    return LibraryCurationScenarioInput(
        workflow=state.get("workflow", "library_curation"),
        eagle_symbols=tuple(state.get("eagle_symbols", tuple())),
        eagle_devices=tuple(state.get("eagle_devices", tuple())),
        kicad_symbols=tuple(state.get("kicad_symbols", tuple())),
        kicad_footprints=tuple(state.get("kicad_footprints", tuple())),
        role_filtering_overrides=tuple(state.get("role_filtering_overrides", tuple())),
        matching_policy_overrides=tuple(state.get("matching_policy_overrides", tuple())),
        curated_symbols=tuple(state.get("curated_symbols", tuple())),
        source_symbol_hashes=tuple(state.get("source_symbol_hashes", tuple())),
        audit_options=tuple(state.get("audit_options", tuple())),
    )


def _run_results(context: Any) -> tuple[LibraryCurationRunResult, ...]:
    """Return command/capability run results from scenario state."""
    state = _state(context)
    return tuple(state.get("run_results", tuple()))


def _last_run_result(context: Any) -> LibraryCurationRunResult:
    """Return latest run result or raise if no command/capability was run."""
    run_results = _run_results(context)
    if not run_results:
        raise AssertionError("No harness run results are available for assertion.")
    return run_results[-1]


def _artifact_bytes(
    result: LibraryCurationRunResult,
    artifact_name: str,
) -> ArtifactBytes:
    """Return artifact bytes payload, defaulting to empty deterministic bytes."""
    return result.artifact_bytes.get(
        artifact_name,
        ArtifactBytes(json_bytes=b"", csv_bytes=b""),
    )


def _assert_artifact_identical_across_runs(
    context: Any,
    *,
    artifact_name: str,
    label: str,
) -> None:
    """Assert both canonical JSON and CSV artifact bytes are identical across runs."""
    run_results = _run_results(context)
    if len(run_results) < 2:
        raise AssertionError(
            f"{label} deterministic assertion requires at least two runs, got {len(run_results)}."
        )
    baseline = _artifact_bytes(run_results[0], artifact_name)
    for index, result in enumerate(run_results[1:], start=2):
        current = _artifact_bytes(result, artifact_name)
        if baseline.json_bytes != current.json_bytes:
            raise AssertionError(
                f"{label} JSON output differs between run 1 and run {index}."
            )
        if baseline.csv_bytes != current.csv_bytes:
            raise AssertionError(
                f"{label} CSV output differs between run 1 and run {index}."
            )


@given("an e2k CSV sandbox")
def given_e2k_csv_sandbox(context: Any) -> None:
    """Initialize scenario state and clear prior harness inputs/results."""
    state = _state(context)
    state.clear()
    state["defaults_mode"] = "generic"
    state["default_output"] = "-"
    state["workflow"] = "library_curation"
    state["run_results"] = tuple()
    state["eagle_symbols"] = tuple()
    state["eagle_devices"] = tuple()
    state["kicad_symbols"] = tuple()
    state["kicad_footprints"] = tuple()
    state["role_filtering_overrides"] = tuple()
    state["matching_policy_overrides"] = tuple()
    state["curated_symbols"] = tuple()
    state["source_symbol_hashes"] = tuple()
    state["audit_options"] = tuple()


@given('workflow "{workflow}" is selected')
def given_workflow_selected(context: Any, workflow: str) -> None:
    """Select the workflow under test for this scenario."""
    _state(context)["workflow"] = workflow


@given("an Eagle library contains symbols:")
def given_eagle_library_contains_symbols(context: Any) -> None:
    """Capture typed Eagle symbol rows."""
    _state(context)["eagle_symbols"] = _parse_eagle_symbols(_table_rows(context))


@given("the Eagle library contains devices:")
def given_eagle_library_contains_devices(context: Any) -> None:
    """Capture typed Eagle deviceset/device rows."""
    _state(context)["eagle_devices"] = _parse_eagle_devices(_table_rows(context))


@given("a KiCad symbol corpus contains:")
def given_kicad_symbol_corpus_contains(context: Any) -> None:
    """Capture typed KiCad symbol corpus rows."""
    _state(context)["kicad_symbols"] = _parse_kicad_symbols(_table_rows(context))


@given("a KiCad footprint corpus contains:")
def given_kicad_footprint_corpus_contains(context: Any) -> None:
    """Capture typed KiCad footprint corpus rows."""
    _state(context)["kicad_footprints"] = _parse_kicad_footprints(_table_rows(context))


@given("role filtering overrides are:")
def given_role_filtering_overrides(context: Any) -> None:
    """Capture typed role-filter override rows."""
    _state(context)["role_filtering_overrides"] = _parse_role_filtering_overrides(
        _table_rows(context)
    )


@given("matching policy overrides are:")
def given_matching_policy_overrides(context: Any) -> None:
    """Capture typed matching-policy override rows."""
    _state(context)["matching_policy_overrides"] = _parse_matching_policy_overrides(
        _table_rows(context)
    )


@given("curated symbols contain:")
def given_curated_symbols_contain(context: Any) -> None:
    """Capture typed curated symbol rows for provenance audit scenarios."""
    _state(context)["curated_symbols"] = _parse_curated_symbols(_table_rows(context))


@given("source symbol hashes contain:")
def given_source_symbol_hashes_contain(context: Any) -> None:
    """Capture typed source symbol hash rows for provenance audit scenarios."""
    _state(context)["source_symbol_hashes"] = _parse_source_symbol_hashes(_table_rows(context))


@given("audit options are:")
def given_audit_options_are(context: Any) -> None:
    """Capture typed audit option rows."""
    _state(context)["audit_options"] = _parse_audit_options(_table_rows(context))


@when('I run e2k command "{command}"')
def when_run_e2k_command(context: Any, command: str) -> None:
    """Execute one deterministic harness run for the provided command."""
    state = _state(context)
    scenario_input = _typed_scenario_input(context)
    result = _run_command(scenario_input, command)
    state["command"] = command
    state["run_count"] = 1
    state["run_results"] = (result,)


@when('I run e2k command "{command}" twice')
def when_run_e2k_command_twice(context: Any, command: str) -> None:
    """Execute two deterministic harness runs for repeatability assertions."""
    state = _state(context)
    scenario_input = _typed_scenario_input(context)
    run_results = (
        _run_command(scenario_input, command),
        _run_command(scenario_input, command),
    )
    state["command"] = command
    state["run_count"] = 2
    state["run_results"] = run_results


@when('I run capability "{capability}" for workflow "{workflow}"')
def when_run_capability_for_workflow(
    context: Any,
    capability: str,
    workflow: str,
) -> None:
    """Execute one deterministic harness run for a capability invocation."""
    state = _state(context)
    scenario_input = _typed_scenario_input(context)
    result = _run_capability(scenario_input, capability, workflow)
    state["capability"] = capability
    state["workflow"] = workflow
    state["run_count"] = 1
    state["run_results"] = (result,)


@then("the command should succeed")
def then_command_should_succeed(context: Any) -> None:
    """Assert all harness runs in this scenario succeeded."""
    failures = [
        result.error_message or "unspecified harness error"
        for result in _run_results(context)
        if not result.success
    ]
    if failures:
        raise AssertionError(f"Expected success but run failures were: {failures}")


@then("no curated symbol library should be produced")
def then_no_curated_symbol_library(context: Any) -> None:
    """Assert curated symbol output is absent."""
    result = _last_run_result(context)
    if result.curated_symbol_rows:
        raise AssertionError(
            f"Expected no curated symbol rows, got: {result.curated_symbol_rows}"
        )


@then("no curated footprint library should be produced")
def then_no_curated_footprint_library(context: Any) -> None:
    """Assert curated footprint output is absent."""
    result = _last_run_result(context)
    if result.curated_footprint_rows:
        raise AssertionError(
            f"Expected no curated footprint rows, got: {result.curated_footprint_rows}"
        )


@then("a curated symbol library should be produced")
def then_curated_symbol_library_produced(context: Any) -> None:
    """Assert curated symbol output is present."""
    result = _last_run_result(context)
    if not result.curated_symbol_rows:
        raise AssertionError("Expected curated symbol library rows but none were produced.")


@then("a curated footprint library should be produced")
def then_curated_footprint_library_produced(context: Any) -> None:
    """Assert curated footprint output is present."""
    result = _last_run_result(context)
    if not result.curated_footprint_rows:
        raise AssertionError("Expected curated footprint library rows but none were produced.")


@then("the CSV output has rows where:")
def then_csv_output_has_rows(context: Any) -> None:
    """Assert expected rows are present in CSV output rows."""
    result = _last_run_result(context)
    _assert_rows_include(
        expected_rows=_table_rows(context),
        actual_rows=result.csv_output_rows,
        dataset_name="CSV output",
    )


@then("the curated symbol library contains rows:")
def then_curated_symbol_library_contains_rows(context: Any) -> None:
    """Assert expected rows are present in curated symbol output."""
    result = _last_run_result(context)
    _assert_rows_include(
        expected_rows=_table_rows(context),
        actual_rows=result.curated_symbol_rows,
        dataset_name="curated symbol output",
    )


@then('the curated symbol library should not contain symbol "{symbol_name}"')
def then_curated_symbol_library_not_contains_symbol(
    context: Any,
    symbol_name: str,
) -> None:
    """Assert the curated symbol output excludes the named symbol."""
    result = _last_run_result(context)
    excluded_name = _normalize_name(symbol_name)
    for row in result.curated_symbol_rows:
        if _normalize_name(row.get("SymbolName", "")) == excluded_name:
            raise AssertionError(f"Unexpected symbol present in curated output: {symbol_name}")


@then("the curated footprint library contains rows:")
def then_curated_footprint_library_contains_rows(context: Any) -> None:
    """Assert expected rows are present in curated footprint output."""
    result = _last_run_result(context)
    _assert_rows_include(
        expected_rows=_table_rows(context),
        actual_rows=result.curated_footprint_rows,
        dataset_name="curated footprint output",
    )


@then("the converted symbol output should represent all Eagle symbols:")
def then_converted_symbol_output_represents_all_eagle_symbols(context: Any) -> None:
    """Assert fidelity conversion output contains all expected Eagle symbols."""
    result = _last_run_result(context)
    _assert_rows_include(
        expected_rows=_table_rows(context),
        actual_rows=result.converted_symbol_rows,
        dataset_name="converted symbol output",
    )


@then("the converted footprint output should represent all Eagle packages:")
def then_converted_footprint_output_represents_all_eagle_packages(context: Any) -> None:
    """Assert fidelity conversion output contains all expected Eagle packages."""
    result = _last_run_result(context)
    _assert_rows_include(
        expected_rows=_table_rows(context),
        actual_rows=result.converted_footprint_rows,
        dataset_name="converted footprint output",
    )


@then("the curated symbol outputs should be byte-identical")
def then_curated_symbol_outputs_byte_identical(context: Any) -> None:
    """Assert curated symbol output bytes are deterministic across runs."""
    _assert_artifact_identical_across_runs(
        context,
        artifact_name="curated_symbol_output",
        label="Curated symbol output",
    )


@then("the curated footprint outputs should be byte-identical")
def then_curated_footprint_outputs_byte_identical(context: Any) -> None:
    """Assert curated footprint output bytes are deterministic across runs."""
    _assert_artifact_identical_across_runs(
        context,
        artifact_name="curated_footprint_output",
        label="Curated footprint output",
    )


@then("the decision reports should be byte-identical")
def then_decision_reports_byte_identical(context: Any) -> None:
    """Assert decision report bytes are deterministic across runs."""
    _assert_artifact_identical_across_runs(
        context,
        artifact_name="decision_report",
        label="Decision report",
    )


@then('the mapping summary for deviceset "{deviceset}" should contain:')
def then_mapping_summary_for_deviceset_contains(
    context: Any,
    deviceset: str,
) -> None:
    """Assert mapping summary metrics for a specific deviceset."""
    result = _last_run_result(context)
    filtered_rows = tuple(
        row
        for row in result.mapping_summary_rows
        if _normalize_name(row.get("DeviceSet", "")) == _normalize_name(deviceset)
    )
    _assert_rows_include(
        expected_rows=_table_rows(context),
        actual_rows=filtered_rows,
        dataset_name=f"mapping summary for deviceset {deviceset}",
    )


@then("the decision report contains rows:")
def then_decision_report_contains_rows(context: Any) -> None:
    """Assert expected rows are present in decision report output."""
    result = _last_run_result(context)
    _assert_rows_include(
        expected_rows=_table_rows(context),
        actual_rows=result.decision_report_rows,
        dataset_name="decision report",
    )


@then('no approved curated mapping should exist for device "{device_key}"')
def then_no_approved_curated_mapping_for_device(
    context: Any,
    device_key: str,
) -> None:
    """Assert specific device key is not in approved mapping set."""
    result = _last_run_result(context)
    if device_key in set(result.approved_device_keys):
        raise AssertionError(
            f"Expected no approved mapping for {device_key}, but it was approved."
        )


@then('the curated symbol for device "{device_key}" should have origin "{origin}"')
def then_curated_symbol_for_device_has_origin(
    context: Any,
    device_key: str,
    origin: str,
) -> None:
    """Assert origin mapping for a specific device key."""
    result = _last_run_result(context)
    actual_origin = result.origin_by_device_key.get(device_key, "")
    if _normalize_name(actual_origin) != _normalize_name(origin):
        raise AssertionError(
            f"Expected origin '{origin}' for {device_key}, got '{actual_origin}'."
        )


@then("the curated symbol properties contain rows:")
def then_curated_symbol_properties_contain_rows(context: Any) -> None:
    """Assert expected E2K_PROVENANCE property rows are present."""
    result = _last_run_result(context)
    _assert_rows_include(
        expected_rows=_table_rows(context),
        actual_rows=result.curated_symbol_property_rows,
        dataset_name="curated symbol properties",
    )