"""Who may message whom, and who reports to whom: OpenClaw's agents as one roster.

OpenClaw's own default is open: `tools.agentToAgent.allow` omitted means every
pair may talk, and a per-agent `send` narrows one agent on purpose. This file
pins the engine to that default, the migration that takes an earlier engine's
closed star out of a file without touching a person's edit, the `reportsTo`
hierarchy a squad may declare, and the manual section rendered from it.
"""
from __future__ import annotations

import copy

from nexgen_core.openclaw import setup_write
from nexgen_core.openclaw.detect import Detection, Installation, LocalModels, Login
from nexgen_core.openclaw.hierarchy import (
    Place,
    declared_leads,
    derive,
    render_org_block,
    render_org_section,
)
from nexgen_core.openclaw.plan import build_plan
from nexgen_core.openclaw.profile import Choices, build_config
from nexgen_core.openclaw.setup_agents import prepare
from nexgen_core.openclaw.squad import apply_squad
from nexgen_core.openclaw.templates import render_workspace

STATE = "/state"


def machine(*, claude: bool = True, go: bool = True, n8n: bool = True) -> Detection:
    return Detection(
        openclaw=Installation(installed=True, version="2026.10.7", config_path=f"{STATE}/openclaw.json"),
        claude=Login(binary=claude, login=True if claude else None),
        codex=Login(binary=False, login=None),
        opencode=True,
        opencode_go=go,
        local=LocalModels(),
        n8n=n8n,
    )


def _config() -> dict:
    """The default roster the engine proposes: main, capo, automazioni, sviluppo."""
    return build_config(machine(), Choices())


# --------------------------------------------------- the engine writes no cage


def test_the_default_roster_writes_no_agent_to_agent_restriction():
    """OpenClaw's default is every pair allowed: the engine no longer narrows it."""
    config = _config()

    assert list(config["agents"]["entries"]) == ["main", "capo", "automazioni", "sviluppo"]
    assert "agentToAgent" not in config["tools"]
    for agent_id, entry in config["agents"]["entries"].items():
        assert "agentToAgent" not in entry.get("tools", {}), agent_id


def test_a_squad_send_stays_on_its_agent_and_no_global_allow_is_written():
    """The person's narrowing is theirs; it must not cut off the agents it omits."""
    squad = {
        "agents": {
            "main": {"tools": {"agentToAgent": {"send": ["capo"]}}},
            "capo": {},
            "automazioni": {},
        },
    }

    applied = apply_squad(_config(), squad)

    assert applied["agents"]["entries"]["main"]["tools"]["agentToAgent"] == {"send": ["capo"]}
    assert "agentToAgent" not in applied["tools"]
    assert "agentToAgent" not in applied["agents"]["entries"]["capo"].get("tools", {})
    assert "agentToAgent" not in applied["agents"]["entries"]["automazioni"].get("tools", {})


# ------------------------------------------------ the migration of the old star


def _legacy_document() -> dict:
    """A file an earlier engine wrote: the star in `allow` and one `send` per agent."""
    return {
        "agents": {"entries": {
            "main": {"tools": {"agentToAgent": {"send": ["capo", "sviluppo"]}}},
            "capo": {"tools": {"agentToAgent": {"send": ["main", "sviluppo"]}}},
            "sviluppo": {"tools": {"agentToAgent": {"send": ["capo", "main"]}}},
        }},
        "tools": {"agentToAgent": {"enabled": True, "allow": ["capo", "main", "sviluppo"]}},
    }


def _proposal() -> dict:
    return {"agents": {"entries": {"main": {}, "capo": {}, "sviluppo": {}}}}


def test_the_star_allow_and_send_lists_of_an_earlier_engine_are_removed():
    merged = setup_write.merged_document(_legacy_document(), _proposal())

    assert merged["tools"]["agentToAgent"] == {"enabled": True}
    for agent_id in ("main", "capo", "sviluppo"):
        assert "tools" not in merged["agents"]["entries"][agent_id], agent_id
    assert setup_write.merged_document(merged, _proposal()) == merged, "the next run has nothing left to remove"


def test_a_person_who_added_an_agent_to_allow_keeps_it():
    current = {"tools": {"agentToAgent": {"enabled": True, "allow": ["capo", "main", "personale"]}}}

    allow = setup_write.merged_document(current, _proposal())["tools"]["agentToAgent"]["allow"]

    assert allow == ["capo", "main", "personale"]


def test_a_person_who_narrowed_a_send_list_keeps_it():
    """`main` may reach one agent and no more: that is not the old star, so it stays."""
    current = _legacy_document()
    current["agents"]["entries"]["main"]["tools"]["agentToAgent"]["send"] = ["capo"]

    send = setup_write.merged_document(current, _proposal())["agents"]["entries"]["main"]["tools"]["agentToAgent"]["send"]

    assert send == ["capo"]


def test_a_send_the_squad_declares_is_kept_even_when_it_looks_legacy():
    """The value alone cannot tell a narrowing from the old star: the squad is the tie-break."""
    proposed = {"agents": {"entries": {"main": {"tools": {"agentToAgent": {"send": ["capo"]}}},
                                      "capo": {}, "sviluppo": {}}}}

    merged = setup_write.merged_document(_legacy_document(), proposed)

    assert merged["agents"]["entries"]["main"]["tools"]["agentToAgent"]["send"] == ["capo", "sviluppo"]


def test_the_migration_leaves_the_caller_documents_untouched():
    current = _legacy_document()
    proposed = _proposal()
    before = (copy.deepcopy(current), copy.deepcopy(proposed))

    setup_write.merged_document(current, proposed)

    assert (current, proposed) == before


# ------------------------------------------------------------- the hierarchy


def test_the_supervisor_reports_to_the_assistant_and_the_others_to_the_supervisor():
    places, errors = derive(("main", "capo", "automazioni", "sviluppo"))

    assert errors == ()
    assert places["main"].lead is None
    assert places["capo"].lead == "main"
    assert places["automazioni"].lead == "capo"
    assert places["sviluppo"].lead == "capo"
    assert places["main"].reports == ("capo",)
    assert places["capo"].reports == ("automazioni", "sviluppo")


def test_the_agents_that_share_a_lead_are_peers():
    places, _errors = derive(("main", "capo", "automazioni", "sviluppo"))

    assert places["automazioni"].peers == ("sviluppo",)
    assert places["sviluppo"].peers == ("automazioni",)
    assert places["capo"].peers == ()
    assert places["main"].peers == ()


def test_without_a_supervisor_every_agent_reports_to_the_assistant():
    places, errors = derive(("main", "automazioni"))

    assert errors == ()
    assert places["automazioni"].lead == "main"
    assert places["main"].reports == ("automazioni",)


def test_a_lone_agent_reports_to_nobody():
    places, _errors = derive(("main",))

    assert places["main"] == Place(lead=None, reports=(), peers=())


def test_an_explicit_reports_to_moves_an_agent_and_its_peers():
    places, errors = derive(("main", "capo", "automazioni", "sviluppo"), {"sviluppo": "main"})

    assert errors == ()
    assert places["sviluppo"].lead == "main"
    assert places["capo"].reports == ("automazioni",)
    assert places["capo"].peers == ("sviluppo",)
    assert places["main"].reports == ("capo", "sviluppo")


def test_an_unknown_lead_is_reported_and_the_agent_falls_back():
    places, errors = derive(("main", "capo", "sviluppo"), {"sviluppo": "personale"})

    assert places["sviluppo"].lead == "capo"
    assert len(errors) == 1
    assert "sviluppo" in errors[0] and "personale" in errors[0]


def test_a_cycle_is_reported_and_the_agent_falls_back():
    places, errors = derive(("main", "capo", "automazioni"), {"capo": "automazioni"})

    assert places["capo"].lead == "main"
    assert places["automazioni"].lead == "capo"
    assert len(errors) == 1
    assert "capo" in errors[0] and "automazioni" in errors[0]


def test_an_agent_that_reports_to_itself_is_a_cycle():
    places, errors = derive(("main", "capo"), {"capo": "capo"})

    assert places["capo"].lead == "main"
    assert len(errors) == 1


def test_the_squad_declares_who_reports_to_whom():
    squad = {
        "agents": {
            "main": {"name": "assistente"},
            "capo": {"name": "capo", "reportsTo": "main"},
            "sviluppo": {"name": "sviluppo", "reportsTo": "capo"},
            "sviluppo-extra": {"reportsTo": "  "},
        },
    }

    assert declared_leads(squad) == {"capo": "main", "sviluppo": "capo"}
    assert declared_leads(None) == {}


# --------------------------------------------------------- the manual section


def _manual(agent: str, language: str = "it") -> str:
    return render_workspace(agent, language)["AGENTS.md"]


def _chart(agents, language: str = "it", roster=None) -> str:
    return render_org_block(agents, language, roster)


def test_a_specialist_section_names_its_lead_and_its_peer():
    roster = ("main", "capo", "automazioni", "sviluppo")

    section = _chart(("sviluppo",), roster=roster)

    assert "`capo`" in section, "its lead"
    assert "`automazioni`" in section, "its peer, under the same lead"
    assert "`main`" not in section, "not its lead, not a peer, and nobody reports to it"


def test_the_supervisor_section_names_the_agents_reporting_to_it():
    roster = ("main", "capo", "automazioni", "sviluppo")

    section = _chart(("capo",), "en", roster=roster)

    assert "`automazioni`" in section and "`sviluppo`" in section, "its reports"
    assert "`main`" in section, "its lead"


def test_the_assistant_section_names_the_agent_reporting_to_it():
    """`capo` reports to `main`; `automazioni` and `sviluppo` report to `capo`, not to it."""
    roster = ("main", "capo", "automazioni", "sviluppo")

    section = _chart(("main",), roster=roster)

    assert "Riportano a te: `capo`." in section
    assert "`automazioni`" not in section and "`sviluppo`" not in section
    assert "Il tuo responsabile: nessuno" in section


def test_the_chart_follows_the_roster_it_is_rendered_for():
    """The section is data, not a list of names: a roster without `capo` cannot say `capo`."""
    section = _chart(("main",), "en", roster=("main", "automazioni"))

    assert "`automazioni`" in section
    assert "`capo`" not in section


def test_no_manual_lists_its_recipients_by_hand():
    for agent in ("main", "capo", "automazioni", "sviluppo"):
        for language in ("it", "en"):
            text = _manual(agent, language)
            assert "I destinatari possibili sono" not in text, (agent, language)
            assert "The possible recipients are" not in text, (agent, language)
            assert "Organigramma" in text or "Org chart" in text, (agent, language)


def test_a_shared_workspace_gets_one_section_per_agent_in_roster_order():
    """Two agents in one folder are answered together, in the order the roster names them."""
    roster = ("main", "capo", "sviluppo")

    chart = _chart(("sviluppo", "capo"), roster=roster)

    assert chart.index("### `capo`") < chart.index("### `sviluppo`")
    assert chart.count("### `capo`") == 1 and chart.count("### `sviluppo`") == 1


def test_the_org_block_says_it_is_generated_and_replaces_older_lists():
    text = _chart(("main",), "it", roster=("main", "capo"))

    assert "generato dal motore" in text
    assert "sostituisce" in text and "destinatari possibili" in text
    assert text.startswith("## Organigramma")


def test_the_org_section_carries_the_rule_and_the_places():
    text = render_org_section("sviluppo", "en", ("main", "capo", "automazioni", "sviluppo"))

    assert text.startswith("### `sviluppo`")
    assert "Your lead: `capo`." in text
    assert "Your peers: `automazioni`." in text
    assert "Reporting to you: nobody." in text


def test_the_org_block_carries_the_rules_once():
    text = _chart(("capo",), "en", roster=("main", "capo", "sviluppo"))

    assert "`sessions_send`" in text
    assert "delegate" in text.lower()
    assert "`sup` tools" in text and "supervisor" in text


def test_the_org_section_says_the_assistant_answers_to_the_person():
    text = render_org_section("main", "it", ("main", "capo"))

    assert "Il tuo responsabile: nessuno" in text
    assert "la persona" in text


# ------------------------------------------------------------- the setup path


def test_the_setup_writes_the_manual_from_the_roster(tmp_path):
    """The workspace file says what this machine's agents are, not a fixed star."""
    directory = tmp_path / "workspace-automazioni"

    prepare({"automazioni": directory}, set(), "en", lambda line: None, roster=("main", "automazioni"))

    text = (directory / "AGENTS.md").read_text(encoding="utf-8")
    assert "Your lead: `main`." in text
    assert "`capo`" not in text, "no agent of that id exists on this machine"


def test_the_plan_reports_a_reports_to_the_roster_cannot_use(tmp_path):
    home = tmp_path / "home"
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "squad.yaml").write_text(
        "agents:\n  main:\n    name: assistente\n  capo:\n    reportsTo: personale\n",
        encoding="utf-8",
    )

    plan = build_plan(detected=machine(), home=home, env={}, vault_data=vault, plugin_deposited=True)

    assert any("personale" in note for note in plan.notes), plan.notes
