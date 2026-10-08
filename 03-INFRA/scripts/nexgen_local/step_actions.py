"""Execute an admitted loop action and update its working state.

Callers enforce step_policy before execution. This owner reads audited tools
and stages proposals; approval and application stay in their existing gates.
"""
from __future__ import annotations

from .llm import LLM
from .source_selection import existing_file, pinned_path, sanitize_content
from .step_state import LoopState, MAX_CONTINUATIONS, MAX_OBSERVATION_CHARS
from .tools import ToolError, ToolRegistry

def _observe(tool: str, arg: str, output: str) -> str:
    text = " ".join(sanitize_content(output).split())
    if len(text) > MAX_OBSERVATION_CHARS:
        text = text[:MAX_OBSERVATION_CHARS] + "..."
    return f"{tool}({arg}) -> {text or '(vuoto)'}"

#: Which collected store a continued chunk joins, by read action.
_READ_STORES = {
    "read_mail": "mail",
    "read_drive": "drive",
    "read_outlook": "outlook",
    "read_calendar": "calendar",
    "read_vault": "reads",
    "read_repo": "reads",
    "read_pdf": "reads",
}


def execute_action(llm: LLM, tools: ToolRegistry, state: LoopState, action: str, arg: str) -> None:
    if action == "search_vault":
        output = tools.call_result("search_vault", {"query": arg, "require_all": True})
        state.tried_queries.append(arg)
        hits = [] if not output.usable else [line.strip() for line in output.text.splitlines() if line.strip()]
        state.hits = hits
        state.empty_streak = 0 if hits else state.empty_streak + 1
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("search_vault", arg, output.text))
    elif action == "search_web":
        output = tools.call_result("web_search", {"query": arg})
        state.tried_queries.append(arg)
        state.empty_streak = 0 if output.usable else state.empty_streak + 1
        if output.usable:
            state.web.append(f"[web: {arg}]\n{sanitize_content(output.text)}")
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("search_web", arg, output.text))
    elif action == "search_mail":
        output = tools.call_result("search_mail", {"query": arg})
        state.tried_queries.append(arg)
        ids = [] if not output.usable else [line.split("|")[0].strip() for line in output.text.splitlines() if line.strip()]
        state.mail_ids = [mid for mid in ids if mid]
        state.empty_streak = 0 if state.mail_ids else state.empty_streak + 1
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("search_mail", arg, output.text))
    elif action == "search_drive":
        output = tools.call_result("search_drive", {"query": arg})
        state.tried_queries.append(arg)
        ids = [] if not output.usable else [line.split("|")[0].strip() for line in output.text.splitlines() if line.strip()]
        state.drive_ids = [fid for fid in ids if fid]
        state.empty_streak = 0 if state.drive_ids else state.empty_streak + 1
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("search_drive", arg, output.text))
    elif action == "search_outlook":
        output = tools.call_result("search_outlook", {"query": arg})
        state.tried_queries.append(arg)
        ids = [] if not output.usable else [line.split("|")[0].strip() for line in output.text.splitlines() if line.strip()]
        state.outlook_ids = [mid for mid in ids if mid]
        state.empty_streak = 0 if state.outlook_ids else state.empty_streak + 1
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("search_outlook", arg, output.text))
    elif action == "search_calendar":
        output = tools.call_result("search_calendar", {"query": arg})
        state.tried_queries.append(arg)
        ids = [] if not output.usable else [line.split("|")[0].strip() for line in output.text.splitlines() if line.strip()]
        state.calendar_ids = [eid for eid in ids if eid]
        state.empty_streak = 0 if state.calendar_ids else state.empty_streak + 1
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("search_calendar", arg, output.text))
    elif action == "read_file":
        found = existing_file(tools.cfg, arg)
        if not found:
            raise ToolError(f"percorso non piu' raggiungibile: {arg}")
        kind, rel, root = found
        dest = pinned_path(root, rel)
        if kind == "vault":
            output = tools.call_result("read_vault", {"path": dest})
        elif kind == "repo":
            output = tools.call_result("read_repo", {"path": dest})
        else:
            output = tools.call_result("read_pdf", {"path": dest})
        if output.usable:
            state.reads.append(f"[{dest}]\n{sanitize_content(output.text)}")
            state.tried_paths.append(dest)
            state.empty_streak = 0
            state.last_read = dict(tools.last_coverage)
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("read_file", rel, output.text))
    elif action in ("read_mail", "read_drive"):
        output = tools.call_result("read_mail", {"id": arg}) if action == "read_mail" else tools.call_result("read_drive", {"id": arg})
        if output.usable:
            store = state.mail if action == "read_mail" else state.drive
            store.append(sanitize_content(output.text))
            state.empty_streak = 0
            state.last_read = dict(tools.last_coverage)
            if arg not in state.read_ids:
                state.read_ids.append(arg)
            if action == "read_mail":
                state.last_mail_id = arg
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe(action, arg, output.text))
    elif action == "read_outlook":
        output = tools.call_result("read_outlook", {"id": arg})
        if output.usable:
            state.outlook.append(sanitize_content(output.text))
            state.empty_streak = 0
            state.last_read = dict(tools.last_coverage)
            if arg not in state.read_ids:
                state.read_ids.append(arg)
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("read_outlook", arg, output.text))
    elif action == "read_calendar":
        output = tools.call_result("read_calendar", {"id": arg})
        if output.usable:
            state.calendar.append(sanitize_content(output.text))
            state.empty_streak = 0
            state.last_read = dict(tools.last_coverage)
            if arg not in state.read_ids:
                state.read_ids.append(arg)
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("read_calendar", arg, output.text))
    elif action == "continue_read":
        last = state.last_read
        if not last.get("truncated") or state.continuations >= MAX_CONTINUATIONS:
            raise ToolError("niente da continuare: lettura completa o tetto raggiunto")
        tool = str(last.get("tool", ""))
        args = dict(last.get("args", {}))
        args["offset"] = int(last.get("resume_at", int(last.get("offset", 0)) + tools.cfg.read_chars))
        output = tools.call_result(tool, args)
        if not output.usable:
            # The source shrank mid-read: no chunk, no spiral. The loop
            # rebuilds the menu from here (answer is offered again).
            state.last_read = {}
        else:
            store = getattr(state, _READ_STORES.get(tool, "reads"))
            store.append(sanitize_content(output.text))
            state.continuations += 1
            state.empty_streak = 0
            state.last_read = dict(tools.last_coverage)
        state.content_seen.append(sanitize_content(output.text))
        state.observations.append(_observe("continue_read", "", output.text))
    elif action == "draft_mail":
        from .compose import MailError, mail_envelope, propose_mail_from_context

        envelope_src = state.mail[-1] if state.mail else ""
        to, subject = mail_envelope(envelope_src)
        if not to or not state.last_mail_id:
            raise ToolError("bozza rifiutata: nessuna mail letta da cui rispondere")
        # The draft answers with every source the session read, not just the
        # last mail: replying "tenendo conto del contratto" requires the
        # contract in the model context, not only in the persisted receipts.
        blocks = [
            *state.reads,
            *state.web,
            *state.mail,
            *state.drive,
            *state.calendar,
            *state.outlook,
        ]
        context = "\n\n".join(block for block in blocks if block) or envelope_src
        try:
            proposal = propose_mail_from_context(
                llm,
                tools.cfg,
                state.task,
                to=to,
                subject=subject or "Re: ",
                in_reply_to=state.last_mail_id,
                context=context,
                kind="reply",
            )
        except MailError as exc:
            raise ToolError(f"bozza rifiutata: {exc}") from exc
        state.mail_draft = proposal.id
        state.mail_draft_preview = (
            f"[bozza {proposal.id}]\nA: {proposal.to}\nOggetto: {proposal.subject}\n"
            f"Stato: da approvare con nexgen-local mail-send {proposal.id} --yes\n\n"
            f"{proposal.body[:1200]}"
        )
        state.proposal_footers.append(
            f"[motore: bozza {proposal.id} a {proposal.to} ({proposal.subject}) — "
            f"approva con nexgen-local mail-send {proposal.id} --yes]"
        )
        detail = f"bozza {proposal.id} a {proposal.to}: {proposal.subject}"
        state.content_seen.append(sanitize_content(detail))
        state.observations.append(f"draft_mail() -> {detail}")
    elif action == "propose_upload":
        from .drive_mcp import DriveGateError, stage_upload

        try:
            staged = stage_upload(tools.cfg, arg)
        except DriveGateError as exc:
            raise ToolError(f"proposta rifiutata: {exc}") from exc
        state.upload_proposal = staged["id"]
        state.upload_preview = (
            f"[proposta upload {staged['id']}]\nFile: {staged['name']} ({staged['size']} byte)\n"
            f"Stato: da approvare con nexgen-local drive-upload {staged['id']} --yes"
        )
        state.proposal_footers.append(
            f"[motore: proposta upload {staged['id']}: {staged['name']} ({staged['size']} byte) — "
            f"approva con nexgen-local drive-upload {staged['id']} --yes]"
        )
        detail = f"proposta {staged['id']}: {staged['name']} ({staged['size']} byte)"
        state.content_seen.append(sanitize_content(detail))
        state.observations.append(f"propose_upload({arg}) -> {detail}")
    else:
        raise ToolError(f"azione non eseguibile: {action}")
    state.receipts = [call.receipt() for call in tools.calls]
