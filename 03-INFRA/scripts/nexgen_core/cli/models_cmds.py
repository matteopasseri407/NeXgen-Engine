# nexgen: one-job reason="models command: tier listing, candidate picking, coding trial, promotion and discovery"
"""The `models` verb group: supervisor tier routing and discovery."""
from __future__ import annotations

import argparse
import sys
import time

from nexgen_core.i18n import t


def _format_time(ts: float) -> str:
    """Format epoch timestamp to human time string."""
    return time.strftime("%H:%M", time.localtime(ts))


def cmd_models_list(args: argparse.Namespace) -> int:
    """List tiers, candidates, volume and availability status."""
    from nexgen_local import routing_tiers

    data = routing_tiers.load_tiers()
    if not data:
        print(t("Nessuna configurazione routing-tiers.yaml trovata."))
        return 1
    st = routing_tiers.load_state()
    routing_tiers.clean_expired_exhausted(st)
    tiers = data.get("tiers", {})
    vol_min = data.get("volume_min_5h", routing_tiers.DEFAULT_VOLUME_MIN_5H)
    print(t(f"Soglia minima volume 5h: {vol_min} richieste\n"))

    for tier_name in ("worker", "limited", "orchestrator"):
        candidates = tiers.get(tier_name, [])
        print(f"Tier {tier_name}:")
        if not candidates:
            print("  (nessun candidato)")
            continue
        if tier_name == "worker" and st.get("promoted_order"):
            candidates = routing_tiers._order_candidates(candidates, st.get("promoted_order", []))
        for c in candidates:
            m = routing_tiers._candidate_model(c)
            h = routing_tiers._candidate_harness(c)
            vol = c.get("requests_5h") if isinstance(c, dict) and c.get("requests_5h") is not None \
                else data.get("channels_volume", {}).get(m, 0)
            ex_rec = st.get("exhausted", {}).get(m)
            if ex_rec and time.time() < float(ex_rec.get("expires_at", 0)):
                exp_str = _format_time(float(ex_rec["expires_at"]))
                reason = ex_rec.get("reason", "quota esaurita")
                status = f"esaurito fino alle {exp_str} ({reason})"
            else:
                status = "disponibile"
            vol_str = f" · volume: {vol}" if tier_name == "worker" else ""
            print(f"  - {m} (harness: {h}{vol_str}) · {status}")
        print()
    return 0


def cmd_models_pick(args: argparse.Namespace) -> int:
    """Select the best candidate for tier."""
    from nexgen_local import routing_tiers

    try:
        hit = routing_tiers.pick(tier=args.tier, allow_limited=args.limited)
        print(f"modello: {hit['model']} (harness: {hit['harness']})")
        return 0
    except routing_tiers.RoutingTiersError as exc:
        print(f"nexgen models: {exc}", file=sys.stderr)
        return 1


def cmd_models_trial(args: argparse.Namespace) -> int:
    """Run coding trial for a model."""
    from nexgen_local import routing_discovery

    passed, reason = routing_discovery.trial(args.model, args.harness)
    if passed:
        print(f"trial superato: {args.model} su {args.harness}")
        return 0
    print(f"trial fallito: {args.model} su {args.harness} ({reason})", file=sys.stderr)
    return 1


def cmd_models_promote(args: argparse.Namespace) -> int:
    """Promote model to head of worker tier."""
    from nexgen_local import routing_tiers

    routing_tiers.promote(args.model)
    print(f"modello «{args.model}» promosso in testa al tier worker")
    return 0


def cmd_models_rollback(args: argparse.Namespace) -> int:
    """Roll back to previous model candidate order."""
    from nexgen_local import routing_tiers

    if routing_tiers.rollback():
        print("ordine precedente dei modelli ripristinato")
        return 0
    print("nessun ordine precedente da ripristinare", file=sys.stderr)
    return 1


def cmd_models_discover(args: argparse.Namespace) -> int:
    """Discover new Flash models in inventory, run trial and promote."""
    from nexgen_local import routing_discovery

    results = routing_discovery.discover()
    if not results:
        print("nessun nuovo modello da promuovere")
        return 0
    for r in results:
        print(f"{r['model']}: {r['status']}")
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    """Register `nexgen models` and subcommands."""
    parser = sub.add_parser("models", help=t("Scelta, verifica e disponibilità dei modelli per tier"))
    msub = parser.add_subparsers(dest="models_command", metavar="subcommand")

    p_list = msub.add_parser("list", help="Elenca i tier, i candidati e il loro stato")
    p_list.set_defaults(func=cmd_models_list)

    p_pick = msub.add_parser("pick", help="Sceglie il miglior modello per il tier indicato")
    p_pick.add_argument("tier", choices=["worker", "orchestrator", "limited"], help="Tier richiesto")
    p_pick.add_argument(
        "--limited", action="store_true",
        help="consente modelli del tier limited (mai finanze, posta, identità, segreti)",
    )
    p_pick.set_defaults(func=cmd_models_pick)

    p_trial = msub.add_parser("trial", help="Esegue il test di codice sul modello")
    p_trial.add_argument("model", help="Nome del modello da testare")
    p_trial.add_argument("--harness", required=True, help="Harness da usare (es. opencode, agy)")
    p_trial.set_defaults(func=cmd_models_trial)

    p_promote = msub.add_parser("promote", help="Promuove un modello in testa al tier worker")
    p_promote.add_argument("model", help="Nome del modello da promuovere")
    p_promote.set_defaults(func=cmd_models_promote)

    p_rollback = msub.add_parser("rollback", help="Ripristina l'ordine precedente dei modelli")
    p_rollback.set_defaults(func=cmd_models_rollback)

    p_discover = msub.add_parser("discover", help="Scopre nuovi modelli Flash con volume sufficiente")
    p_discover.set_defaults(func=cmd_models_discover)

    parser.set_defaults(func=lambda _: parser.print_help())
