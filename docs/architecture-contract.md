# Architecture contract

This document defines the behaviour each component must preserve when its implementation changes.
It also records design constraints and debt to check during a refactor.

See [codebase-map.md](codebase-map.md) for the current module and symbol index.

---

## 1. The invariants

Every component obeys these. A design that breaks one is wrong even if it
passes its tests.

1. **One canonical source.** Policy, MCP configuration, skills, memory and
   private identity each exist once. Per-CLI and per-machine files are
   generated. Nothing is hand-edited downstream of its source.
2. **Everything is lazy by default.** A new entry earns eager loading with an
   argument; it does not receive it by default. The user knows a capability
   exists and takes it when they want it.
3. **Naming follows what a command acts on.** Memory is the Vault, so memory
   commands keep `vault-`. The engine and its tooling take `nexgen-`.
4. **The engine distributes only its own commands.** Everything else stays in
   private data. A list of recommended extras is documentation, not files.
5. **What is chosen propagates.** Every CLI in scope, every machine declared,
   every platform targeted. Working on one machine is not done.
6. **Repair in silence, speak only about what cannot be repaired.** Routine
   maintenance is the job, not news. Notifying about routine work is how people
   learn to dismiss notifications.
7. **One megaphone.** Exactly one alert surface exists. Adding a trigger to it
   is allowed; adding a second notifier is not.
8. **Configuration and code arrive on different clocks.** Data reaches every
   machine in minutes; code arrives with a release. Therefore every consumer of
   a declarative file must tolerate a key or value it does not understand by
   skipping that entry loudly, never by rejecting the document.
9. **One writer, one definition of a secret, one reader of the host.** A file the
   engine owns is written through `nexgen_core.files` (atomic, mode-preserving,
   backed up where it matters); a scan test fails the build on a direct
   `write_text`. What a credential looks like is defined in
   `nexgen_core.secret_shapes`, and one corpus of synthetic credentials is
   run through every consumer of it. Everything that changes the machine takes
   the same host lock, and a diagnostic that asks "would apply change this?"
   asks the renderer itself in preview rather than re-deriving the answer. Where an MCP server
   lives (mounted directly, behind the gateway, or absent) is likewise one function that the
   renderer, the gateway and `nexgen mcp plan` all call.

---

## 2. Components and their contracts

### The clock
Fires the recurring cycle and nothing else. Must survive reboots and missed
runs. Must never publish. Must not hold work that another component can do.

### The guard cycle
One transaction: acquire a host-wide lock, fetch the authoritative remote,
classify the data state, and apply only when that state is safe. Regenerates
every derived runtime file from its canonical source. Never pushes. Refuses to
touch a working tree with uncommitted user work, and says which files blocked
it without modifying them.

### The judge
Decides whether the layer is aligned. The only component allowed to form that
verdict. Reports what needs attention; a passing check is counted, not
narrated. Distinguishes three cases and never confuses them:
- something is broken that the user did not choose: **failure**;
- something is off that the user did choose: **not reported at all**;
- something cannot be determined right now: **stated as undetermined**.

### The megaphone
The single delivery path for anything a human must read. Owns the message
shape, the debounce and the transport fallbacks. Any number of triggers may
wake it; none of them may format or deliver on their own. Must be reachable by
a trigger that survives the death of whatever it is watching, because an alarm
hosted inside the thing it monitors does not ring when that thing fails to
start.

### The liveness beat
Independent of the guard, with its own schedule and no dependency on it.
Answers one question: did the guard reach the end recently. This exists because
a job cancelled by a failed dependency never enters a failed state, so
failure-triggered alerting alone cannot see it. Elapsed time since the last
completed run covers that case and every other cause without knowing which.
Also carries the two maintenance duties below, because it is the one place that
runs regularly without holding the guard's lock.

### The self-upgrader
Takes a released upgrade without asking and says nothing about it. Refuses on a
dirty tree, and only considers a tag that exists as a published release. Has a
ceiling on how large a jump it may take unattended, defaulting to the smallest,
because a machine that changes its own behaviour overnight changed it without
anyone choosing that. Speaks only when it cannot do the work, and a failed
attempt must name the recovery, not the check.

It verifies the release tag against the signers pinned in the copy that is
already installed (SECURITY.md, "What an installed copy verifies"). A wrong
signature, or one by a key that is not pinned, is refused in every mode; a
release that cannot be verified at all is refused when nobody is there to read
the warning. The merge happens under the host lock, and an unattended update
that fails after the engine moved is undone on the spot and remembered as
rejected, so it is neither left half-applied nor retried every hour. The
interactive command never undoes anything by itself: a person is there to look.

### The dependency watch
Looks upstream for every pinned third-party thing the layer declares: code
fetched at a commit, packages fixed at a version, tools invoked by name and
version. Produces a list and stops there, because applying an upstream change
alters behaviour nobody chose. Never notifies. Being offline writes nothing and
reports nothing: a workstation is offline all the time and that is not an
incident.

### The skill materializer
Turns one declaration into the views each runtime can actually see. Four
origins, and the distinction is about *who owns the bytes*:
- **owned by the user**: carried in their data;
- **owned by the product**: read from the installed engine, never copied into
  user data, so an upgrade upgrades the command and no second copy can go
  stale;
- **third-party, fetchable**: pinned to an immutable commit and restored from
  it;
- **third-party, only its own installer can render it**: pinned to a version,
  installed by that installer when the local copy does not match.

Materializes into a non-discovered library, then creates only the views
declared. An installer that drops its copy into a discovery root has that copy
moved; remembering to move it is not a mechanism.

### The runtime renderer
Generates each CLI's configuration from the connector manifest. Omits a
connector whose declared precondition is unmet, without treating that omission
as a fault. Preserves local, machine-specific settings it does not own.

### The credential distributor
Fetches operational keys on demand from the private backend and writes them
with restrictive permissions. Secrets never transit version control, never
appear in logs, and never appear in a summary.

### The identity surface
Keeps runtime presentation from becoming the private identity: neutralises
supported personality controls, disables parallel native memories, quarantines
existing ones without deleting them. Grades what it finds: a boundary
violation blocks, a metadata defect warns. A guard that blocks on a formatting
problem takes the whole layer down for a missing line, which is what happened.

### The publisher
Commits and pushes durable work in one operation. Stages only what was given
to it. Never invents a commit from a dirty tree.

---

## 3. The data contracts

Two declarative files are the whole configuration surface. Both must be
forward-compatible per invariant 8.

**Connector manifest.** One entry per server: how to start it, which runtimes
mount it, the precondition that gates it, and whether it is fundamental or
optional. Optional is the default when unstated, so nothing promotes itself.

**Skill manifest.** One entry per skill: who owns the bytes (above), which
runtimes get a native view, and whether it is eager or lazy. Lazy is the
default.

**State files** are machine-local, never synced, and each answers exactly one
question. Sharing one file between two questions is what froze liveness behind
an alert debounce.

---

## 4. The command surface

Grouped by what the user is actually asking for.

- **Align this machine now** and **align it on a schedule**: the same
  transaction, one manual and one recurring.
- **Publish my work.**
- **Tell me if anything is wrong**, with a default report that shows only that,
  and a verbose form that lists everything checked.
- **Update the engine**, interactively with a confirmation gate.
- **Show me what upstream has moved.**
- **Memory commands**: save a fact, close a session into notes, consolidate
  notes, map the note structure.
- **Find and show a skill on demand**, which is what makes lazy loading usable.
- **Convene a cross-vendor review.**

---

## 5. Lessons from earlier implementations

These failures shaped the contracts above.
They describe earlier implementations; check the current code and backlog before treating one as an open defect.

1. **Duplicated platform logic:** separate Linux and Windows doctor implementations drifted.
   The shared Python core now owns diagnostics; platform launchers forward arguments.
2. **Logic embedded in shell:** a large doctor script mixed shell and inline interpreters.
   Keep checks in modules that can be tested independently.
3. **Reporting controlled by globals:** the same call printed or silently counted depending on flags.
   Return structured outcomes and choose formatting at the command boundary.
4. **Tests tied to implementation details:** fixed task counts and exact output strings blocked valid changes.
   Test observable rules, such as task paths and failure reporting.
5. **Changed defaults without compatibility:** consumers depended on a command's old output.
   Treat output changes as contract changes.
6. **Unsupported security claims:** documentation claimed client-side signature enforcement while the client warned and continued.
   Verify security claims against both the release workflow and the updater.
7. **Readers that rejected new configuration:** an unknown key could stop an older consumer.
   Preserve the forward-compatibility rule in invariant 8.
