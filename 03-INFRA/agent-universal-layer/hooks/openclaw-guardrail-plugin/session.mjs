// NeXgen Engine -- which session a hook event belongs to, in one place.
//
// OpenClaw puts the session identity on both the event and the context, and the two are not
// always the same one: a compaction hands the run to a successor session, and the context is
// the one whose next prompt rebuilds. The briefing marks a session and the events attribute an
// emission to it, so both read it here rather than each deciding for itself.
export function sessionOf(event, ctx) {
  const fromContext = ctx && (ctx.sessionId || ctx.sessionKey);
  if (fromContext) {
    return String(fromContext);
  }
  const fromEvent = event && (event.sessionId || event.sessionKey);
  return fromEvent ? String(fromEvent) : null;
}
