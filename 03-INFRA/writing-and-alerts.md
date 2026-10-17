# Writing And Alerts

## Alert surfaces (moved from the bootstrap)

**Alert surfaces the user reads directly (desktop notifications, messaging apps) speak plain language, not sysadmin.** Every alert must say: what broke in everyday words, how serious it is, and the ONE action to take — usually a ready-to-paste line for an agent chat. Raw technical detail goes at the tail as `[technical: …]` or in the log, never as the headline. Counters like `FAIL=2` or jargon like "manifest drift" alone are a bug, not a message. Adapt the language to the user's `USER-PROFILE.md` preference.

## Writing voice (moved from the bootstrap)

**Writing voice (humanize every artifact, not chat).** The trigger is not "always", it is three concrete cases. One, you produce an artifact for the user (CV, cover text, post, copy, email, doc, any deliverable they will use, send, or publish). Two, they hand you a text and ask you to fix, edit, or review it. Three, you fill in a form or application on their behalf. In those cases, load whatever editing skill the user has registered for this (`agent-skill find humanize`), then strip the AI tells while keeping meaning, coverage and the user's voice. With no such skill installed, the inline rules below are the whole contract. Plain back-and-forth chat with the user stays lighter.

## Punctuation and orthography (moved from the bootstrap)

**Punctuation and orthography (follow the user's language).** If the user's `USER-PROFILE.md` sets a writing language, follow its orthography and punctuation rules in every output (chat and content). For Italian: use commas to separate clauses and asides, not dashes. Never put `—` or `-` where a comma or a period belongs. End each sentence with a period. Keep proper Italian orthography (accents, apostrophes). For content only (carousels, CVs, social posts, slides, any deliverable), additionally write one sentence per line, with a line break after every period. In chat replies skip the one-per-line breaks, they waste tokens.
