"""One root for deliberate failures, every builtin contract kept."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from nexgen_core.config import ConfigError, TemplateError  # noqa: E402
from nexgen_core.errors import NexgenError  # noqa: E402
from nexgen_core.lock import LockTimeoutError  # noqa: E402
from nexgen_core.provision import ProvisionError  # noqa: E402
from nexgen_core.runtimes.base import GuardrailError  # noqa: E402
from nexgen_core.stack.runner import StackError  # noqa: E402
from nexgen_core.updater import PostMergeError, UpdateError  # noqa: E402
from nexgen_core.vault.gate import GateError  # noqa: E402
from nexgen_core.vault.git_utils import GitCommandError  # noqa: E402
from nexgen_core.vault.runner import RunnerError  # noqa: E402


def test_every_deliberate_failure_shares_the_root():
    for cls in (
        ConfigError, TemplateError, UpdateError, PostMergeError,
        GuardrailError, ProvisionError, LockTimeoutError, StackError,
        GateError, GitCommandError, RunnerError,
    ):
        assert issubclass(cls, NexgenError), cls


def test_builtin_contracts_survive_the_rebase():
    # Code catching builtins must not notice the new root.
    assert issubclass(ConfigError, ValueError)
    assert issubclass(UpdateError, RuntimeError)
    assert issubclass(StackError, RuntimeError)
    assert issubclass(LockTimeoutError, TimeoutError)
    assert isinstance(ConfigError("probe"), NexgenError)
    assert isinstance(UpdateError("probe"), NexgenError)
    assert isinstance(GuardrailError("probe"), NexgenError)
    assert isinstance(ProvisionError("probe"), NexgenError)
    assert isinstance(StackError("probe"), NexgenError)
    assert isinstance(GateError("probe"), NexgenError)
    assert isinstance(GitCommandError("probe"), NexgenError)
    assert isinstance(RunnerError("probe"), NexgenError)
    from pathlib import Path as _Path

    assert isinstance(LockTimeoutError("probe", _Path("/tmp/x")), NexgenError)
