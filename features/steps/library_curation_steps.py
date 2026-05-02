"""Step-definition stubs for library_curation Behave features.

These steps intentionally scaffold phrase coverage and scenario state capture
without implementing runtime behavior yet.
"""

from __future__ import annotations

from typing import Any

from behave import given, then, when
try:
    from .common_diagnostic_utils import pending_step as _pending
    from .common_workspace import record_table as _record_table
    from .common_workspace import state as _state
except ImportError:
    from common_diagnostic_utils import pending_step as _pending
    from common_workspace import record_table as _record_table
    from common_workspace import state as _state


@given("an e2k CSV sandbox")
def given_e2k_csv_sandbox(context: Any) -> None:
    """Initialize scenario state with implicit generic defaults."""
    state = _state(context)
    state.clear()
    state["defaults_mode"] = "generic"
    state["default_output"] = "-"


@given('workflow "{workflow}" is selected')
def given_workflow_selected(context: Any, workflow: str) -> None:
    """Select the workflow under test for this scenario."""
    _state(context)["workflow"] = workflow


@given("an Eagle library contains symbols:")
def given_eagle_library_contains_symbols(context: Any) -> None:
    """Capture explicit Eagle symbol test data."""
    _record_table(context, "eagle_symbols")


@given("the Eagle library contains devices:")
def given_eagle_library_contains_devices(context: Any) -> None:
    """Capture explicit Eagle deviceset/device test data."""
    _record_table(context, "eagle_devices")


@given("a KiCad symbol corpus contains:")
def given_kicad_symbol_corpus_contains(context: Any) -> None:
    """Capture explicit KiCad symbol corpus test data."""
    _record_table(context, "kicad_symbols")


@given("a KiCad footprint corpus contains:")
def given_kicad_footprint_corpus_contains(context: Any) -> None:
    """Capture explicit KiCad footprint corpus test data."""
    _record_table(context, "kicad_footprints")


@given("role filtering overrides are:")
def given_role_filtering_overrides(context: Any) -> None:
    """Capture role-filter override rows."""
    _record_table(context, "role_filtering_overrides")


@given("matching policy overrides are:")
def given_matching_policy_overrides(context: Any) -> None:
    """Capture matching policy override rows."""
    _record_table(context, "matching_policy_overrides")


@given("curated symbols contain:")
def given_curated_symbols_contain(context: Any) -> None:
    """Capture curated symbol rows for provenance audit scenarios."""
    _record_table(context, "curated_symbols")


@given("source symbol hashes contain:")
def given_source_symbol_hashes_contain(context: Any) -> None:
    """Capture source symbol hash rows for provenance audit scenarios."""
    _record_table(context, "source_symbol_hashes")


@given("audit options are:")
def given_audit_options_are(context: Any) -> None:
    """Capture audit option rows."""
    _record_table(context, "audit_options")


@when('I run e2k command "{command}"')
def when_run_e2k_command(context: Any, command: str) -> None:
    """Capture a single command invocation for later assertion steps."""
    state = _state(context)
    state["command"] = command
    state["run_count"] = 1


@when('I run e2k command "{command}" twice')
def when_run_e2k_command_twice(context: Any, command: str) -> None:
    """Capture repeated command invocation intent."""
    state = _state(context)
    state["command"] = command
    state["run_count"] = 2


@when('I run capability "{capability}" for workflow "{workflow}"')
def when_run_capability_for_workflow(
    context: Any,
    capability: str,
    workflow: str,
) -> None:
    """Capture capability invocation intent."""
    state = _state(context)
    state["capability"] = capability
    state["workflow"] = workflow


@then("the command should succeed")
def then_command_should_succeed(context: Any) -> None:
    """Placeholder for command-success assertion behavior."""
    del context
    _pending("Then the command should succeed")


@then("no curated symbol library should be produced")
def then_no_curated_symbol_library(context: Any) -> None:
    """Placeholder for curated symbol output absence assertion."""
    del context
    _pending("Then no curated symbol library should be produced")


@then("no curated footprint library should be produced")
def then_no_curated_footprint_library(context: Any) -> None:
    """Placeholder for curated footprint output absence assertion."""
    del context
    _pending("Then no curated footprint library should be produced")


@then("a curated symbol library should be produced")
def then_curated_symbol_library_produced(context: Any) -> None:
    """Placeholder for curated symbol output presence assertion."""
    del context
    _pending("Then a curated symbol library should be produced")


@then("a curated footprint library should be produced")
def then_curated_footprint_library_produced(context: Any) -> None:
    """Placeholder for curated footprint output presence assertion."""
    del context
    _pending("Then a curated footprint library should be produced")


@then("the CSV output has rows where:")
def then_csv_output_has_rows(context: Any) -> None:
    """Placeholder for table-driven CSV row assertions."""
    _record_table(context, "expected_csv_rows")
    _pending("Then the CSV output has rows where:")


@then("the curated symbol library contains rows:")
def then_curated_symbol_library_contains_rows(context: Any) -> None:
    """Placeholder for curated symbol table assertions."""
    _record_table(context, "expected_curated_symbol_rows")
    _pending("Then the curated symbol library contains rows:")


@then('the curated symbol library should not contain symbol "{symbol_name}"')
def then_curated_symbol_library_not_contains_symbol(
    context: Any,
    symbol_name: str,
) -> None:
    """Placeholder for curated symbol exclusion assertions."""
    _state(context)["excluded_symbol_name"] = symbol_name
    _pending('Then the curated symbol library should not contain symbol "{symbol_name}"')


@then("the curated footprint library contains rows:")
def then_curated_footprint_library_contains_rows(context: Any) -> None:
    """Placeholder for curated footprint table assertions."""
    _record_table(context, "expected_curated_footprint_rows")
    _pending("Then the curated footprint library contains rows:")


@then("the converted symbol output should represent all Eagle symbols:")
def then_converted_symbol_output_represents_all_eagle_symbols(context: Any) -> None:
    """Placeholder for fidelity symbol coverage assertions."""
    _record_table(context, "expected_fidelity_symbol_rows")
    _pending("Then the converted symbol output should represent all Eagle symbols:")


@then("the converted footprint output should represent all Eagle packages:")
def then_converted_footprint_output_represents_all_eagle_packages(context: Any) -> None:
    """Placeholder for fidelity footprint coverage assertions."""
    _record_table(context, "expected_fidelity_footprint_rows")
    _pending("Then the converted footprint output should represent all Eagle packages:")


@then("the curated symbol outputs should be byte-identical")
def then_curated_symbol_outputs_byte_identical(context: Any) -> None:
    """Placeholder for deterministic symbol output assertions."""
    del context
    _pending("Then the curated symbol outputs should be byte-identical")


@then("the curated footprint outputs should be byte-identical")
def then_curated_footprint_outputs_byte_identical(context: Any) -> None:
    """Placeholder for deterministic footprint output assertions."""
    del context
    _pending("Then the curated footprint outputs should be byte-identical")


@then("the decision reports should be byte-identical")
def then_decision_reports_byte_identical(context: Any) -> None:
    """Placeholder for deterministic decision report assertions."""
    del context
    _pending("Then the decision reports should be byte-identical")


@then('the mapping summary for deviceset "{deviceset}" should contain:')
def then_mapping_summary_for_deviceset_contains(
    context: Any,
    deviceset: str,
) -> None:
    """Placeholder for deviceset summary metric assertions."""
    _state(context)["deviceset_under_assertion"] = deviceset
    _record_table(context, "expected_mapping_summary_rows")
    _pending('Then the mapping summary for deviceset "{deviceset}" should contain:')


@then("the decision report contains rows:")
def then_decision_report_contains_rows(context: Any) -> None:
    """Placeholder for decision report table assertions."""
    _record_table(context, "expected_decision_report_rows")
    _pending("Then the decision report contains rows:")


@then('no approved curated mapping should exist for device "{device_key}"')
def then_no_approved_curated_mapping_for_device(
    context: Any,
    device_key: str,
) -> None:
    """Placeholder for unresolved mapping assertions."""
    _state(context)["unapproved_device_key"] = device_key
    _pending('Then no approved curated mapping should exist for device "{device_key}"')


@then('the curated symbol for device "{device_key}" should have origin "{origin}"')
def then_curated_symbol_for_device_has_origin(
    context: Any,
    device_key: str,
    origin: str,
) -> None:
    """Placeholder for origin-tracking assertions."""
    _state(context)["origin_assertion"] = {"device_key": device_key, "origin": origin}
    _pending('Then the curated symbol for device "{device_key}" should have origin "{origin}"')


@then("the curated symbol properties contain rows:")
def then_curated_symbol_properties_contain_rows(context: Any) -> None:
    """Placeholder for E2K_PROVENANCE property row assertions."""
    _record_table(context, "expected_curated_symbol_property_rows")
    _pending("Then the curated symbol properties contain rows:")
