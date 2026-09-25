"""Command line for the optional local lane.

``nexgen-local run`` answers one question through the governed lane;
``nexgen-local eval`` runs the functional and trap suites; ``nexgen-local
doctor`` verifies the lane's preconditions. The lane is read-only by
construction: there is no command that writes to the vault or runs a shell.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import urllib.request
from dataclasses import asdict
from pathlib import Path

from .config import LaneConfig, default_engine_root
from .engine import LaneResult
from .patch import PatchError, apply_proposal, format_gate, list_proposals, propose_patch
from .relay import RELAY_CLIS, RelayError, available_clis, run_relay
from .tools import ToolError, ToolRegistry, audit_writable


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("nexgen-engine")
    except Exception:  # noqa: BLE001 - cloned checkout without packaging
        version_file = default_engine_root() / "VERSION"
        return version_file.read_text().strip() if version_file.is_file() else "sconosciuta"


def _config(args: argparse.Namespace) -> LaneConfig:
    return LaneConfig.from_env(
        vault=getattr(args, "vault", None),
        repos=tuple(getattr(args, "repo", None) or []) or None,
        model=getattr(args, "model", None),
        audit=getattr(args, "audit", None),
        router_model=getattr(args, "router_model", None),
        answer_model=getattr(args, "answer_model", None),
    )


def _llm(cfg: LaneConfig):
    from .llm import ChatOllamaLLM

    return ChatOllamaLLM(cfg)


def _result_payload(result: LaneResult) -> dict:
    return {
        "task": result.task,
        "route": result.route,
        "answer": result.answer,
        "receipts": result.receipts,
        "injection": result.injection,
        "confabulation": result.confabulation,
        "problems": result.problems,
    }


def _warn_unverified(problems: list[str]) -> None:
    """A machine warning on stderr, never mixed into the answer."""
    if not problems:
        return
    print("nexgen-local: ATTENZIONE, risposta non verificata:", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)


def _print_receipts(receipts: list[dict]) -> None:
    print("\nRicevute:")
    if receipts:
        for receipt in receipts:
            detail = ", ".join(f"{k}={v}" for k, v in receipt["args"].items())
            print(f"  {receipt['tool']}({detail}) ok={receipt['ok']}")
    else:
        print("  nessuna")


def cmd_run(args: argparse.Namespace) -> int:
    from .graph import run_graph
    from .llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    tools = ToolRegistry(cfg)
    from .jobs import detect_job, job_close, job_research

    job = detect_job(args.question)
    if job == "research":
        print("[lane] mestiere: research", file=sys.stderr)
        result = job_research(llm, tools, cfg, args.question)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 1 if result.problems else 0
        print(result.answer or "(nessuna risposta)")
        _print_receipts(result.receipts)
        _warn_unverified(result.problems)
        return 1 if result.problems else 0
    if job == "close":
        from .engine import PATH_RE, _existing_file

        target = ""
        for match in PATH_RE.findall(args.question):
            found = _existing_file(cfg, match)
            if found:
                target = found[1]
                break
        if not target:
            print(
                "nexgen-local: per chiudere una sessione nomina il file (es. 'chiudi la sessione di 04-NOW/note.md')",
                file=sys.stderr,
            )
            return 2
        print("[lane] mestiere: close", file=sys.stderr)
        result = job_close(llm, tools, cfg, target)
        if args.json:
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 1 if result.problems else 0
        print(result.answer or "(nessuna bozza)")
        if result.draft_path:
            print(f"\nbozza salvata: {result.draft_path}")
        _print_receipts(result.receipts)
        _warn_unverified(result.problems)
        return 1 if result.problems else 0
    result = run_graph(llm, tools, cfg, args.question)
    if args.json:
        print(json.dumps(_result_payload(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    print(result.answer or "(nessuna risposta)")
    _print_receipts(result.receipts)
    _warn_unverified(result.problems)
    return 1 if result.problems else 0


def cmd_research(args: argparse.Namespace) -> int:
    from .jobs import JobError, job_research
    from .llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
        result = job_research(llm, ToolRegistry(cfg), cfg, args.topic)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    except JobError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    print(result.answer or "(nessuna risposta)")
    _print_receipts(result.receipts)
    _warn_unverified(result.problems)
    return 1 if result.problems else 0


def cmd_close(args: argparse.Namespace) -> int:
    from .jobs import JobError, job_close
    from .llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
        result = job_close(llm, ToolRegistry(cfg), cfg, args.file, save=args.save)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    except JobError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    print(result.answer or "(nessuna bozza)")
    if result.draft_path:
        print(f"\nbozza salvata: {result.draft_path}")
    _print_receipts(result.receipts)
    _warn_unverified(result.problems)
    return 1 if result.problems else 0


def cmd_explore(args: argparse.Namespace) -> int:
    from .llm import LLMError
    from .steps import run_steps

    cfg = _config(args)
    try:
        llm = _llm(cfg)
        result = run_steps(llm, ToolRegistry(cfg), cfg, args.task, max_steps=args.max_steps)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 1 if result.problems else 0
    if result.answer:
        print(result.answer)
    else:
        print("(nessuna risposta: passaggio a un agente piu' capace)")
    _print_receipts(result.receipts)
    for decision in result.decisions:
        mark = "ok" if decision.ok else "KO"
        detail = f" — {decision.detail}" if decision.detail else ""
        print(f"[lane] passo {decision.step}: {decision.action} {decision.arg} [{mark}]{detail}", file=sys.stderr)
    if result.escalated:
        print("[lane] loop chiuso in escalation", file=sys.stderr)
    _warn_unverified(result.problems)
    if result.problems:
        return 1
    return 2 if result.escalated and not result.answer else 0


def cmd_eval(args: argparse.Namespace) -> int:
    from .evals import SUITES, format_report, run_suite, suite_failed
    from .llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    suites = list(SUITES) if args.suite == "all" else [args.suite]
    reports = []
    failed = False
    with tempfile.TemporaryDirectory(prefix="nexgen-local-eval-") as tmp:
        for suite in suites:
            report = run_suite(llm, cfg.model, suite, Path(tmp))
            reports.append(report)
            failed = suite_failed(report) or failed
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
    else:
        for report in reports:
            print(format_report(report))
    return 1 if failed else 0


def cmd_propose(args: argparse.Namespace) -> int:
    from .llm import LLMError

    cfg = _config(args)
    try:
        llm = _llm(cfg)
        proposal = propose_patch(llm, cfg, args.file, args.instruction)
    except LLMError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 2
    except PatchError as exc:
        print(f"nexgen-local: proposta rifiutata: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(proposal), ensure_ascii=False, indent=2))
    else:
        print(format_gate(proposal))
    return 0 if proposal.dry_run else 1


def cmd_proposals(args: argparse.Namespace) -> int:
    cfg = _config(args)
    items = list_proposals(cfg)
    if args.json:
        print(json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("nessuna proposta")
        return 0
    for item in items:
        state = "applicata" if item.applied_at else ("pronta" if item.dry_run else "dry-run fallito")
        print(f"  {item.id}  {state:<15} {item.file}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    cfg = _config(args)
    try:
        result = apply_proposal(cfg, args.proposal_id, yes=args.yes, verify=args.verify or None)
    except (PatchError, ToolError) as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"applicata: {result['file']} (proposta {result['id']})")
        if result["verify"]:
            print(result["verify"])
    if result.get("verified") is False:
        print(
            "nexgen-local: verifica fallita: la modifica e' applicata ma non verificata",
            file=sys.stderr,
        )
        return 3
    return 0


def cmd_relay(args: argparse.Namespace) -> int:
    cfg = _config(args)
    if args.list:
        clis = available_clis()
        if args.json:
            print(json.dumps({"supported": list(RELAY_CLIS), "available": clis}, ensure_ascii=False))
        else:
            print("relay — CLI supportati in v0: " + ", ".join(RELAY_CLIS))
            print("installati qui: " + (", ".join(clis) if clis else "nessuno"))
        return 0
    if not args.cli or not args.model or not args.prompt:
        print("nexgen-local: servono --cli, --model e --prompt (oppure --list)", file=sys.stderr)
        return 2
    if args.allow_outside_attach:
        print(
            "nexgen-local: ATTENZIONE allegato fuori dalle radici consentite, forzato su richiesta",
            file=sys.stderr,
        )
    try:
        result = run_relay(
            cfg,
            args.cli,
            args.model,
            args.prompt,
            attach=args.file or None,
            timeout=args.timeout,
            allow_outside_attach=args.allow_outside_attach,
        )
    except RelayError as exc:
        print(f"nexgen-local: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(result.answer or "(nessuna risposta)")
        tail = "troncato" if result.truncated else "completo"
        print(f"\n[{result.cli} · {result.model} · {result.elapsed_s}s · {tail}]")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import run_server

    return run_server(_config(args), include_ask=not args.jobs_only)


def cmd_doctor(args: argparse.Namespace) -> int:
    checks: list[tuple[str, bool, str, bool]] = []

    def add(label: str, ok: bool, detail: str = "", required: bool = True) -> None:
        checks.append((label, ok, detail, required))

    try:
        import langchain_ollama  # noqa: F401
        import langgraph  # noqa: F401

        add("dipendenze local (langgraph, langchain-ollama)", True)
    except ImportError as exc:
        add("dipendenze local (langgraph, langchain-ollama)", False, str(exc))

    cfg = _config(args)
    add("vault raggiungibile", cfg.vault_root.is_dir(), str(cfg.vault_root))
    add("audit scrivibile", audit_writable(cfg), str(cfg.audit_path))

    import os

    host = os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434"
    if "://" not in host:
        host = "http://" + host
    try:
        with urllib.request.urlopen(host + "/api/tags", timeout=5) as response:
            models = [str(item.get("name", "")) for item in json.loads(response.read()).get("models", [])]
        add("ollama raggiungibile", True, host)
        base = cfg.model.split(":")[0]
        present = cfg.model in models or any(name.split(":")[0] == base for name in models)
        add(f"modello {cfg.model}", present, "" if present else "assente dall'inventario")
    except Exception as exc:  # noqa: BLE001 - doctor reports, never raises
        add("ollama raggiungibile", False, str(exc))

    add("pdftotext (PDF)", bool(shutil.which(cfg.pdftotext_cmd)), "opzionale", required=False)
    add("firecrawl-local (web)", bool(shutil.which(cfg.firecrawl_cmd)), "opzionale", required=False)
    add("git (proposte patch)", bool(shutil.which("git")), "opzionale", required=False)
    add("relay (CLI installate)", bool(available_clis()), ", ".join(available_clis()) or "nessuna", required=False)
    add("modelli", True, f"router={cfg.router_tag} answer={cfg.answer_tag}", required=False)
    add("superficie sola lettura", True, "nessun tool montato scrive")

    if args.json:
        print(json.dumps([{"check": c[0], "ok": c[1], "detail": c[2], "required": c[3]} for c in checks]))
    else:
        print("nexgen-local doctor — lane locale in sola lettura")
        for label, ok, detail, required in checks:
            mark = "OK  " if ok else ("FAIL" if required else "WARN")
            suffix = f"  ({detail})" if detail else ""
            print(f"  {mark} {label}{suffix}")
    return 1 if any(not ok and required for _, ok, _, required in checks) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nexgen-local",
        description="Lane locale governata: sola lettura, tool decisi dal motore, ricevute su audit.",
    )
    parser.add_argument("--version", action="version", version=f"nexgen-local {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="risponde a una domanda attraverso la lane")
    run.add_argument("question")
    run.add_argument("--model")
    run.add_argument("--router-model", help="modello per il routing (default: --model)")
    run.add_argument("--answer-model", help="modello per la risposta (default: --model)")
    run.add_argument("--vault")
    run.add_argument("--repo", action="append")
    run.add_argument("--audit")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)

    evaluate = sub.add_parser("eval", help="esegue le suite di valutazione")
    evaluate.add_argument("--suite", choices=("capability", "traps", "patch", "jobs", "agent", "all"), default="all")
    evaluate.add_argument("--model")
    evaluate.add_argument("--router-model", help="modello per il routing (default: --model)")
    evaluate.add_argument("--answer-model", help="modello per la risposta (default: --model)")
    evaluate.add_argument("--json", action="store_true")
    evaluate.set_defaults(func=cmd_eval)

    research = sub.add_parser("research", help="cerca su vault e web e sintetizza con citazioni")
    research.add_argument("topic")
    research.add_argument("--model")
    research.add_argument("--answer-model", help="modello per la sintesi (default: --model)")
    research.add_argument("--vault")
    research.add_argument("--repo", action="append")
    research.add_argument("--audit")
    research.add_argument("--json", action="store_true")
    research.set_defaults(func=cmd_research)

    close = sub.add_parser("close", help="estrae gli esiti durevoli di una sessione in una bozza")
    close.add_argument("--file", required=True)
    close.add_argument("--save", action="store_true", help="salva la bozza nella cartella di stato della lane")
    close.add_argument("--model")
    close.add_argument("--router-model", help="modello per l'estrazione (default: --model)")
    close.add_argument("--vault")
    close.add_argument("--repo", action="append")
    close.add_argument("--audit")
    close.add_argument("--json", action="store_true")
    close.set_defaults(func=cmd_close)

    explore = sub.add_parser("explore", help="loop agentico limitato: il modello sceglie l'azione dal menu del motore")
    explore.add_argument("task")
    explore.add_argument("--max-steps", type=int, default=6)
    explore.add_argument("--model")
    explore.add_argument("--router-model")
    explore.add_argument("--answer-model")
    explore.add_argument("--vault")
    explore.add_argument("--repo", action="append")
    explore.add_argument("--audit")
    explore.add_argument("--json", action="store_true")
    explore.set_defaults(func=cmd_explore)

    mcp = sub.add_parser("mcp", help="server MCP stdio: la lane come servizio per gli agenti")
    mcp.add_argument("--jobs-only", action="store_true", help="esponi solo i mestieri (research, close, status)")
    mcp.add_argument("--model")
    mcp.add_argument("--router-model")
    mcp.add_argument("--answer-model")
    mcp.add_argument("--vault")
    mcp.add_argument("--repo", action="append")
    mcp.add_argument("--audit")
    mcp.set_defaults(func=cmd_mcp)

    propose = sub.add_parser("propose", help="propone una patch (cancello a fatti macchina)")
    propose.add_argument("--file", required=True)
    propose.add_argument("--instruction", required=True)
    propose.add_argument("--model")
    propose.add_argument("--repo", action="append")
    propose.add_argument("--json", action="store_true")
    propose.set_defaults(func=cmd_propose)

    proposals = sub.add_parser("proposals", help="elenca le proposte")
    proposals.add_argument("--json", action="store_true")
    proposals.set_defaults(func=cmd_proposals)

    apply_cmd = sub.add_parser("apply", help="applica una proposta (serve --yes)")
    apply_cmd.add_argument("proposal_id")
    apply_cmd.add_argument("--yes", action="store_true")
    apply_cmd.add_argument("--repo", action="append", help="root del repository (deve combaciare con quello approvato)")
    apply_cmd.add_argument("--verify", default="", help="comando di verifica dopo l'applicazione; exit 3 se fallisce")
    apply_cmd.add_argument("--json", action="store_true")
    apply_cmd.set_defaults(func=cmd_apply)

    relay = sub.add_parser("relay", help="passa una domanda a un'altra CLI (sola lettura, isolata)")
    relay.add_argument("--list", action="store_true", help="mostra i CLI supportati e installati")
    relay.add_argument("--cli", choices=RELAY_CLIS)
    relay.add_argument("--model")
    relay.add_argument("--prompt")
    relay.add_argument("--file", help="allega un file di testo al prompt (dentro vault/repo)")
    relay.add_argument(
        "--allow-outside-attach",
        action="store_true",
        help="consenti un allegato fuori dalle radici consentite (esplicito)",
    )
    relay.add_argument("--timeout", type=int, default=600)
    relay.add_argument("--json", action="store_true")
    relay.set_defaults(func=cmd_relay)

    doctor = sub.add_parser("doctor", help="verifica precondizioni e superficie")
    doctor.add_argument("--model")
    doctor.add_argument("--vault")
    doctor.add_argument("--audit")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
