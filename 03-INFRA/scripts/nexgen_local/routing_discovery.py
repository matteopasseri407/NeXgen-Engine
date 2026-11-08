# nexgen: one-job reason="discover new flat-channel Flash models and trial them without promoting, run discovery daily, and preflight worker candidates for quota"
"""Model discovery for the supervisor: baseline, trial, promotion and quota preflight.

One job: remember every model already seen in `known_models`, trial only the models
that appear later on flat channels, alert on the ones that pass (never promote them), run discovery at most
once every 24 hours, and check a worker candidate for spent quota before using it.
Tier selection and the exhaustion state live in `routing_tiers`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from . import routing_tiers as rt

DEFAULT_FLAT_PREFIXES = ("opencode-go/",)
EXCLUDED_NAME_MARKS = ("free", "contributor")
DEFAULT_TRIAL_TIMEOUT_S = 120.0
PREFLIGHT_TIMEOUT_S = 60.0
PREFLIGHT_PROMPT = "Rispondi solo: ok"
DISCOVER_INTERVAL_S = 24 * 3600


def _cli(name: str) -> str:
    """The launcher as Windows finds it (`.cmd` shims included), or the bare name."""
    return shutil.which(name) or name


def _init_git_repo(tmp_path: Path) -> None:
    """Initialize a git repo with a failing unittest."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "trial"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "trial@local"], cwd=tmp_path, capture_output=True, check=True)
    (tmp_path / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
    test_code = (
        "import unittest\nfrom calc import add\n\n"
        "class TestAdd(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n\n"
        "if __name__ == '__main__':\n    unittest.main()\n"
    )
    (tmp_path / "test_calc.py").write_text(test_code, encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)


def _trial_command(model: str, harness: str, prompt: str) -> list[str]:
    """The CLI call for a trial; opencode runs with --auto because the folder is a throwaway."""
    if harness == "opencode":
        return [_cli("opencode"), "run", "-m", model, "--auto", prompt]
    return [_cli("agy"), "--mode", "accept-edits", "--model", model, "--prompt", prompt]


def trial(model: str, harness: str, *, timeout: float = DEFAULT_TRIAL_TIMEOUT_S,
          adapter: Any = None, runner: Any = None) -> tuple[bool, str]:
    """Deterministic coding trial: model must fix a failing test without touching other files."""
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        _init_git_repo(tmp_path)
        prompt = "Correggi il bug in calc.py affinché test_calc.py passi. Non modificare altri file."
        try:
            if runner is not None:
                runner(model, harness, tmp_path, prompt)
            elif adapter is not None and hasattr(adapter, "run_trial"):
                adapter.run_trial(model, tmp_path, prompt)
            else:
                subprocess.run(_trial_command(model, harness, prompt), cwd=tmp_path, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                               timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return False, "tempo limite superato"
        except Exception as exc:
            return False, f"esecuzione fallita: {exc}"
        status = subprocess.run(["git", "status", "--porcelain"], cwd=tmp_path, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", check=False).stdout.strip().splitlines()
        modified = [line.strip().split(None, 1)[1] for line in status if len(line.strip().split(None, 1)) > 1]
        if modified != ["calc.py"]:
            return False, f"file inattesi modificati o creati: {', '.join(modified) or 'nessuno'}"
        verify = subprocess.run([os.sys.executable, "test_calc.py"], cwd=tmp_path, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", check=False)
        if verify.returncode != 0:
            return False, f"il test reale continua a fallire: {verify.stderr.strip()[:200]}"
        return True, "test superato"


def is_flat_model(model: str, prefixes: tuple[str, ...] = DEFAULT_FLAT_PREFIXES) -> bool:
    """A flat-channel model: under a flat prefix, and not a free or contributor channel."""
    if not any(model.startswith(prefix) for prefix in prefixes):
        return False
    low = model.casefold()
    return not any(mark in low for mark in EXCLUDED_NAME_MARKS)


def _register_unseen(items: list[dict[str, str]], st: dict[str, Any]) -> list[dict[str, str]]:
    """Add every inventory model not yet known to `known_models`; return those, before any trial."""
    known = set(st.get("known_models", []))
    fresh = [item for item in items if item["model"] not in known]
    if fresh:
        st["known_models"] = sorted(known | {item["model"] for item in fresh})
    return fresh


def _evaluate_fresh(fresh: list[dict[str, str]], data: dict[str, Any],
                    trial_fn: Callable[[str, str], tuple[bool, str]] | None,
                    megaphone: Any) -> list[dict[str, Any]]:
    """Trial the new flat Flash models with known volume and alert on those that pass. Never promotes."""
    from nexgen_core.megaphone import Megaphone
    phone = megaphone or Megaphone()
    prefixes = tuple(data.get("flat_prefixes") or DEFAULT_FLAT_PREFIXES)
    worker_models = {rt._candidate_model(c) for c in data.get("tiers", {}).get("worker", [])}
    channels_volume = data.get("channels_volume", {})
    min_vol = int(data.get("volume_min_5h", rt.DEFAULT_VOLUME_MIN_5H))
    results: list[dict[str, Any]] = []
    for item in fresh:
        model, harness = item["model"], item.get("harness", "opencode")
        if not is_flat_model(model, prefixes) or model in worker_models or "flash" not in model.lower():
            continue
        if model not in channels_volume:
            phone.send_alert(
                title="Modello scoperto senza volume noto",
                message=f"Il modello «{model}» è presente ma non ha un volume noto in channels_volume.",
                action="Compila channels_volume in routing-tiers.yaml",
                alert_key=f"discover_missing_volume:{model}",
            )
            continue
        if channels_volume[model] < min_vol:
            continue
        passed, reason = (trial_fn or trial)(model, harness)
        if passed:
            phone.send_alert(
                title="Nuovo modello valido trovato",
                message=f"Il modello «{model}» ha superato la prova: per adottarlo nexgen models promote {model}.",
                action=f"nexgen models promote {model}",
                alert_key=f"discover_passed:{model}",
            )
            results.append({"model": model, "status": "passed", "harness": harness})
        else:
            phone.send_alert(
                title="Modello scoperto non idoneo",
                message=f"Il modello «{model}» non ha superato la prova: {reason}.",
                action=None,
                alert_key=f"discover_failed:{model}",
            )
            results.append({"model": model, "status": f"failed: {reason}", "harness": harness})
    return results


def discover(*, path: Path | None = None, state_file: Path | None = None,
             inventory_fn: Callable[[], list[dict[str, str]]] | None = None,
             trial_fn: Callable[[str, str], tuple[bool, str]] | None = None,
             megaphone: Any = None) -> list[dict[str, Any]]:
    """Scan inventory; the first run only records it, later runs trial the models never seen."""
    data = rt.load_tiers(path)
    if not data:
        return []
    items = inventory_fn() if inventory_fn else [
        {"model": m, "harness": "opencode"} for m in rt.probe_cli_inventory("opencode")
    ]
    st = rt.load_state(state_file)
    if not items:
        return []
    if "known_models" not in st:
        st["known_models"] = sorted({item["model"] for item in items})
        rt.save_state(st, state_file)
        return []
    fresh = _register_unseen(items, st)
    if not fresh:
        return []
    rt.save_state(st, state_file)
    return _evaluate_fresh(fresh, data, trial_fn, megaphone)


def check_and_run_daily_discover(*, vault: Path | None = None, home: Path | None = None,
                                 megaphone: Any = None) -> dict[str, Any] | None:
    """Run discover at most once every 24 hours, on any machine that has a routing file."""
    if not rt.routing_tiers_exists(vault):
        return {"ok": True, "skipped": "nessun routing-tiers.yaml su questa macchina"}
    sfile = rt.lane_state_file(home=home)
    st = rt.load_state(sfile)
    now = time.time()
    if now - float(st.get("last_discover_ts", 0)) < DISCOVER_INTERVAL_S:
        return {"ok": True, "skipped": "già eseguito nelle ultime 24 ore"}
    st["last_discover_ts"] = now
    rt.save_state(st, sfile)
    t_path = rt.routing_tiers_path(vault=vault)
    discovered = discover(path=t_path, state_file=sfile, megaphone=megaphone)
    return {"ok": True, "discovered": discovered}


def _run_preflight(model: str, harness: str) -> str:
    """What the harness answers to a one-line check; empty when it cannot be run at all."""
    exe = shutil.which(harness)
    if not exe:
        return ""
    try:
        res = subprocess.run([exe, "run", "-m", model, "--archived", PREFLIGHT_PROMPT],
                             capture_output=True, text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL, timeout=PREFLIGHT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):  # nexgen: allow reason="a check that cannot run does not block the candidate"
        return ""
    return f"{res.stdout}\n{res.stderr}"


def preflight(model: str, harness: str, runner: Callable[[str, str], str] | None = None) -> tuple[bool, str]:
    """Check a worker candidate before use; returns (exhausted, reason).

    Only opencode is checked. A quota message marks the model exhausted; any other
    outcome, or a check that cannot run, lets the candidate through.
    """
    if harness != "opencode":
        return False, ""
    output = (runner or _run_preflight)(model, harness)
    is_quota, reason = rt.is_quota_exhausted_error(output)
    if not is_quota:
        return False, ""
    rt.mark_exhausted(model, reason)
    return True, reason
