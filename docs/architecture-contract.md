# Architecture contract

What the agent layer must do, stated as behaviour rather than as the code that
currently does it. Written to be the input to a rewrite: the functions below are
the ones to keep, the implementation is not.

Anything that reads like an apology for the current design is deliberate. The
debt is named at the end so a rewrite does not inherit it by accident.

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
dirty tree, refuses a bad signature, and only considers a tag that exists as a
published release. Has a ceiling on how large a jump it may take unattended,
defaulting to the smallest, because a machine that changes its own behaviour
overnight changed it without anyone choosing that. Speaks only when it cannot
do the work, and a failed attempt must name the recovery, not the check.

*Truth in advertising:* signature enforcement belongs to the release process.
A client that warns and continues on an unverifiable signature is not enforcing
anything, and documentation must not claim otherwise.

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

## 5. The debt a rewrite must not inherit

Named specifically, because each one cost real time this week.

1. **Two hand-maintained twins.** The Linux and Windows implementations of the
   judge are separate files kept in step by hand. They drifted: two checks
   existed on one platform only, in the very component whose job is noticing
   drift. Either generate both from one description, or make one of them thin
   enough that it cannot drift.
2. **A 1300-line shell script with embedded interpreters.** The judge shells
   out to inline programs to read its own configuration. It is not testable in
   pieces and not readable in one pass.
3. **Output functions whose meaning depends on flags.** After adding one flag,
   the same reporting call means "print" or "count silently" depending on two
   globals. Reporting should be data the caller emits, and formatting a
   decision made once at the edge.
4. **Tests that pin the census instead of the invariant.** "Exactly two
   scheduled tasks", "the output contains a checkmark". Every legitimate
   extension broke them, which trains people to loosen tests rather than trust
   them. Assert the rule: every task runs from the state directory; failures
   are never suppressed.
5. **Defaults changed without a compatibility window.** Changing what a command
   prints by default is a contract change and ripples through everything that
   reads it.
6. **Documentation that promises more than the code does.** A public file
   claimed release signing was enforced client-side when it warns and
   continues. A verifiable claim that is false discredits the true ones beside
   it.
7. **No forward compatibility in the config readers.** Fixed for one of the two
   manifests; the other still rejects an unknown key outright, which stops
   every machine on the older release until someone intervenes by hand.
