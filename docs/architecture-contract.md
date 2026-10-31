# Architecture contract

🇮🇹 [Italiano](it/architecture-contract.md)

This document defines the behaviour each component must preserve when its implementation changes. It also records the
design constraints and the debt to check during a refactor. For the current module and symbol index see
[codebase-map.md](codebase-map.md).

## 1. The invariants

Every component obeys these. A design that breaks one is wrong even if it passes its tests.

1. **One canonical source.** Policy, MCP configuration, skills, memory and private identity each exist once. Per-CLI
   and per-machine files are generated, and nothing is hand-edited downstream of its source.
2. **Everything is lazy by default.** A new entry earns eager loading with an argument; it does not receive it by
   default. The user knows a capability exists and takes it when they want it.
3. **Naming follows what a command acts on.** Memory is the Vault, so memory commands keep `vault-`. The engine and its
   tooling take `nexgen-`.
4. **The engine distributes only its own commands.** Everything else stays in private data. A list of recommended
   extras is documentation, not files.
5. **What is chosen propagates.** Every CLI in scope, every machine declared, every platform targeted. Working on one
   machine is not done.
6. **Repair in silence, speak only about what cannot be repaired.** Routine maintenance is the job, not news.
   Notifying about routine work is how people learn to dismiss notifications.
7. **One megaphone.** Exactly one alert surface exists. Adding a trigger to it is allowed; adding a second notifier is
   not.
8. **Configuration and code arrive on different clocks.** Data reaches every machine in minutes; code arrives with a
   release. Every consumer of a declarative file must therefore tolerate a key or value it does not understand by
   skipping that entry loudly, never by rejecting the document.
9. **One writer, one definition of a secret, one reader of the host.** A file the engine owns is written through
   `nexgen_core.files` (atomic, mode-preserving, backed up where it matters), and a scan test fails the build on a
   direct `write_text`. `nexgen_core.secret_shapes` defines what a credential looks like, and one corpus of synthetic
   credentials runs through every consumer of it. Everything that changes the machine takes the same host lock, and a
   diagnostic that asks "would apply change this?" asks the renderer itself in preview instead of re-deriving the
   answer. Where an MCP server lives (direct, behind the gateway, or absent) is also one function, called by the
   renderer, the gateway and `nexgen mcp plan`.

## 2. Components and their contracts

### The clock

Fires the recurring cycle and nothing else. Survives reboots and missed runs. Never publishes. Does not hold work that
another component can do.

### The guard cycle

One transaction: acquire a host-wide lock, fetch the authoritative remote, classify the data state, and apply only when
that state is safe. Regenerates every derived runtime file from its canonical source. Never pushes. Refuses to touch a
working tree with uncommitted user work, and names the files that blocked it without modifying them.

### The judge

Decides whether the layer is aligned, and is the only component allowed to form that verdict. Reports what needs
attention; a passing check is counted, not narrated. It distinguishes three cases and never confuses them:

- something is broken that the user did not choose: a **failure**;
- something is off that the user did choose: **not reported at all**;
- something cannot be determined right now: **stated as undetermined**.

### The megaphone

The single delivery path for anything a human must read. Owns the message shape, the debounce and the transport
fallbacks. Any number of triggers may wake it; none of them may format or deliver on their own. A trigger must be able
to reach it even when whatever it watches has died, because an alarm hosted inside the thing it monitors does not ring
when that thing fails to start.

It delivers through these transports in this order, and only the desktop waits for all the others to fail:

1. **OpenClaw**, when `openclaw` is on `PATH` and a destination is configured: `NEXGEN_ALERT_CHANNEL` (for example
   `telegram`) plus `NEXGEN_ALERT_TARGET` (the chat id, `@username`, `channel:<id>`, and so on, in the form
   `openclaw message send` documents). When the channel is `telegram` and no target is set, `TELEGRAM_CHAT_ID` is the
   target, so a machine that already knows its chat does not say it twice. The alert travels as plain text, one argv
   element and never a command line assembled by hand, with `stdin` closed and a twenty-second ceiling. On Windows the
   npm `openclaw.cmd` shim is resolved to `node` plus the package's own `node_modules/openclaw/openclaw.mjs`, because
   Windows starts a batch file only through `cmd.exe`, which would re-read `&`, `|`, `^` and `%` inside an alert that
   carries error messages and file names; when that entry or node itself is missing, OpenClaw is not a transport on
   that machine. A non-zero exit, a timeout or a missing binary is not an error: the next transport takes the alert.
2. **Telegram directly**, with `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, for a machine without OpenClaw. It never
   runs behind a delivered OpenClaw alert: where OpenClaw owns the Telegram channel, its delivery and the Bot API are
   the same destination, and sending both is the same message twice.
3. **A webhook**, with `AGENT_ALERT_WEBHOOK_URL`, unless OpenClaw already carried the alert: one alert reaches one
   surface, and a webhook repeating what the harness delivered is the same alarm twice.
4. **The desktop**, only when nothing before it delivered.

### The liveness beat

Independent of the guard, with its own schedule and no dependency on it. Answers one question: did the guard reach the
end recently. A job cancelled by a failed dependency never enters a failed state, so failure-triggered alerting alone
cannot see it; elapsed time since the last completed run covers that case and every other cause without knowing which.
It also carries the two maintenance duties below, because it is the one place that runs regularly without holding the
guard's lock.

### The self-upgrader

Takes a released upgrade without asking and says nothing about it. Refuses on a dirty tree, and considers only a tag
that exists as a published release. It has a ceiling on how large a jump it may take unattended, defaulting to the
smallest, because a machine that changes its own behaviour overnight changed it without anyone choosing that. It
speaks only when it cannot do the work, and a failed attempt must name the recovery, not the check.

It verifies the release tag against the signers pinned in the copy that is already installed ([SECURITY.md](../SECURITY.md),
"What an installed copy verifies"). A wrong signature, or one by a key that is not pinned, is refused in every mode. A
release that cannot be verified at all is refused when nobody is there to read the warning. The merge happens under
the host lock, and an unattended update that fails after the engine moved is undone on the spot and remembered as
rejected, so it is neither left half-applied nor retried every hour. The interactive command never undoes anything by
itself: a person is there to look.

### The dependency watch

Looks upstream for every pinned third-party thing the layer declares: code fetched at a commit, packages fixed at a
version, tools invoked by name and version. Produces a list and stops there, because applying an upstream change alters
behaviour nobody chose. Never notifies. Being offline writes nothing and reports nothing: a workstation is offline all
the time and that is not an incident.

It also reports when the publisher has withdrawn support for the exact version pinned (npm `deprecated`), because
"nothing newer exists" is not the same as "all good". It watches what a module declares it carries (`upstream:` in the
module catalog): a program shipped as a Docker image (n8n, the Vault OCR API), or a version pinned inside a launcher
script (Playwright). A module says which file holds the pin and where the newest release is read, so the watch does not
need to know any of that. These are only reported, and the guardian holds every one: a program is replaced where it
runs, that host may pin something else, and the Playwright launcher patches the files of one exact version, so even a
patch jump is not a small touch-up.

### The skill materializer

Turns one declaration into the views each runtime can see. Four origins, and the distinction is about who owns the
bytes:

- **owned by the user**: carried in their data;
- **owned by the product**: read from the installed engine, never copied into user data, so an upgrade upgrades the
  command and no second copy can go stale;
- **third-party, fetchable**: pinned to an immutable commit and restored from it;
- **third-party, only its own installer can render it**: pinned to a version, installed by that installer when the
  local copy does not match.

It materializes into a non-discovered library, then creates only the views declared. An installer that drops its copy
into a discovery root has that copy moved; remembering to move it is not a mechanism.

### The runtime renderer

Generates each CLI's configuration from the connector manifest. Omits a connector whose declared precondition is
unmet, without treating that omission as a fault. Preserves local, machine-specific settings it does not own.

### The credential distributor

Fetches operational keys on demand from the private backend and writes them with restrictive permissions. Secrets never
transit version control, never appear in logs and never appear in a summary.

### The identity surface

Keeps runtime presentation from becoming the private identity: neutralises supported personality controls, disables
parallel native memories, quarantines existing ones without deleting them. It grades what it finds: a boundary
violation blocks, a metadata defect warns. A guard that blocks on a formatting problem takes the whole layer down for a
missing line, which is what happened.

### The publisher

Commits and pushes durable work in one operation. Stages only what it was given. Never invents a commit from a dirty
tree.

## 3. The data contracts

Two declarative files are the whole configuration surface. Both must be forward-compatible per invariant 8.

**Connector manifest.** One entry per server: how to start it, which runtimes mount it, the precondition that gates it,
and whether it is fundamental or optional. Optional is the default when unstated, so nothing promotes itself.

**Skill manifest.** One entry per skill: who owns the bytes (above), which runtimes get a native view, and whether it
is eager or lazy. Lazy is the default.

**State files** are machine-local, never synced, and each answers exactly one question. Sharing one file between two
questions is what froze liveness behind an alert debounce.

## 4. The command surface

Grouped by what the user is actually asking for.

- **Align this machine now** and **align it on a schedule**: the same transaction, one manual and one recurring.
- **Publish my work.**
- **Tell me if anything is wrong**, with a default report that shows only that, and a verbose form that lists
  everything checked. `--summary` is the form a git hook and a collection run use: it asks the independent checks at
  the same time, stops waiting after seven seconds, and reports any check that did not answer as undetermined with the
  action that leads to it, rather than dropping it. The full doctor waits for every check, and `--timings` says which
  one spent the time.
- **Update the engine**, interactively with a confirmation gate.
- **Show me what upstream has moved.**
- **Memory commands**: save a fact, close a session into notes, consolidate notes, map the note structure.
- **Find and show a skill on demand**, which is what makes lazy loading usable.
- **Convene a cross-vendor review.**

## 5. Lessons from earlier implementations

These failures shaped the contracts above. They describe earlier implementations; check the current code and backlog
before treating one as an open defect.

1. **Duplicated platform logic:** separate Linux and Windows doctor implementations drifted. The shared Python core now
   owns diagnostics, and platform launchers forward arguments.
2. **Logic embedded in shell:** a large doctor script mixed shell and inline interpreters. Keep checks in modules that
   can be tested independently.
3. **Reporting controlled by globals:** the same call printed or silently counted depending on flags. Return structured
   outcomes and choose formatting at the command boundary.
4. **Tests tied to implementation details:** fixed task counts and exact output strings blocked valid changes. Test
   observable rules, such as task paths and failure reporting.
5. **Changed defaults without compatibility:** consumers depended on a command's old output. Treat output changes as
   contract changes.
6. **Unsupported security claims:** documentation claimed client-side signature enforcement while the client warned and
   continued. Verify security claims against both the release workflow and the updater.
7. **Readers that rejected new configuration:** an unknown key could stop an older consumer. Preserve the
   forward-compatibility rule in invariant 8.
