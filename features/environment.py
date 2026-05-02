"""Behave environment configuration for EagleLib2KiCad feature tests.

Hard rule: scenarios must run inside isolated temporary sandboxes and must not
write into the repository working tree.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

_SANDBOX_PREFIX = "e2k_behave_"
_STEPS_ENV_VAR = "BEHAVE_STEPS_DIR"
_TRACE_ENV_VAR = "E2K_BEHAVE_TRACE"
_KEEP_SANDBOX_ENV_VAR = "E2K_BEHAVE_KEEP_SANDBOX"


def _env_truthy(name: str) -> bool:
    """Return True when an environment variable is set to a truthy value."""
    value = os.environ.get(name, "").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def before_all(context: Any) -> None:
    """Set up global Behave context before running any scenario."""
    repo_root = Path(__file__).resolve().parents[1]
    features_root = repo_root / "features"
    steps_dir = features_root / "steps"
    src_root = repo_root / "src"

    context.repo_root = repo_root
    context.features_root = features_root
    context.steps_dir = steps_dir
    context.src_root = src_root
    context._initial_cwd = Path.cwd()
    context._added_src_to_sys_path = False

    os.environ[_STEPS_ENV_VAR] = str(steps_dir)
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
        context._added_src_to_sys_path = True


def before_scenario(context: Any, scenario: Any) -> None:
    """Initialize per-scenario context and create an isolated sandbox."""
    context.last_command = None
    context.last_output = ""
    context.last_error_output = ""
    context.last_exit_code = None
    context.diagnostics = None
    context.e2k_state = {
        "defaults_mode": "generic",
        "default_output": "-",
    }

    scenario_tags = set(getattr(scenario, "effective_tags", set()))
    context.trace = ("trace" in scenario_tags) or _env_truthy(_TRACE_ENV_VAR)

    sandbox_root = Path(tempfile.mkdtemp(prefix=_SANDBOX_PREFIX))
    context.sandbox_root = sandbox_root
    context.project_root = sandbox_root
    context._cwd_before_scenario = Path.cwd()

    os.chdir(sandbox_root)


def after_scenario(context: Any, scenario: Any) -> None:
    """Restore working directory and clean up per-scenario sandbox."""
    previous_cwd = getattr(context, "_cwd_before_scenario", None)
    if previous_cwd is not None:
        try:
            os.chdir(previous_cwd)
        except OSError:
            fallback_cwd = getattr(context, "_initial_cwd", None)
            if fallback_cwd is not None:
                try:
                    os.chdir(fallback_cwd)
                except OSError:
                    pass

    sandbox_root = getattr(context, "sandbox_root", None)
    if sandbox_root is not None:
        sandbox_path = Path(sandbox_root)
        scenario_tags = set(getattr(scenario, "effective_tags", set()))
        keep_sandbox = (
            ("keep_sandbox" in scenario_tags)
            or _env_truthy(_KEEP_SANDBOX_ENV_VAR)
        )
        if sandbox_path.exists() and sandbox_path.name.startswith(_SANDBOX_PREFIX):
            if keep_sandbox:
                if getattr(context, "trace", False):
                    print(f"[e2k-behave] keeping sandbox: {sandbox_path}")
            else:
                shutil.rmtree(sandbox_path, ignore_errors=True)

    context.sandbox_root = None
    context.project_root = None


def after_all(context: Any) -> None:
    """Restore process-level state changed by before_all."""
    initial_cwd = getattr(context, "_initial_cwd", None)
    if initial_cwd is not None:
        try:
            os.chdir(initial_cwd)
        except OSError:
            pass

    if getattr(context, "_added_src_to_sys_path", False):
        src_root = getattr(context, "src_root", None)
        if src_root is not None:
            try:
                sys.path.remove(str(src_root))
            except ValueError:
                pass
