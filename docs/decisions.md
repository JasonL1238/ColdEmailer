# Decisions that look like bugs

Each of these has been reported as broken and is not. Read the entry before
"fixing" the behavior; if you still think it is wrong, change the entry in the
same commit so the next reader inherits the new reasoning rather than the old.

## Safety and gating

**The do-not-contact list is the only check that fails closed.** Every other
gate is optimistic — `domain_has_mx` returning "could not check" lets a send
proceed, so a DNS blip cannot block real mail. Suppression is the opposite: if
it cannot be evaluated, nothing goes out.

**Sent mail cannot be deleted, and companies or contacts with sent history need
`?force=true`.** That record is what prevents emailing the same person twice.

**There is no keyboard shortcut for sending, and the help text says so.** Every
other shortcut is reversible; a delivered email is not.

**The `custom` email type refuses the offline template** — it raises
`TemplateUnavailable` rather than emitting an internship email that ignores the
instructions the user actually wrote.

**Emails never claim an attachment they do not have.** The "resume is attached"
line appears only when a real PDF will be attached, and sales emails never
attach one.

## Reporting honestly

**Reply rate shows 0% with "N unverified".** The old checker counted bounces,
auto-replies, and the user's own thread messages as replies, producing a fake
89.8%. Those flags now sit in `emails.response_verified_at IS NULL` and are
reported separately. "Re-verify replies" re-checks against Gmail and promotes
the genuine ones.

**A rate is withheld below `analytics.MIN_SAMPLE` (10 sends)** on every surface —
analytics, campaigns, the unassigned bucket — and counts are shown instead.
`MIN_SAMPLE` and `rate_of` are imported from `analytics`, never re-declared.

**Nothing is backfilled into a campaign.** Rows predating campaigns stay
`campaign_id IS NULL` forever and are reported as their own bucket. Guessing
which campaign an old company belonged to would invent the very fact the page
exists to report.

**The pipeline board derives stages from evidence, not `contacts.status`.** That
column is written from four places and reset from none, so a contact whose draft
was deleted would stay `drafted` for good.

**Some companies show "Wrong site found".** Their scraped profile came from an
unrelated website and was cleared rather than left to be quoted into an email.
Re-researching the company fixes it.

## Runtime

**Startup runs idempotent repairs.** `main.py` calls the `repair_*` functions in
`db.py` on every boot. They are migrations that must stay safe to re-run.

**Scheduled sending ships disabled.** It needs both a Settings toggle and a
per-batch opt-in before any mail leaves unattended — the only path where a
message goes out with nobody watching.

# Known gaps

Real, unresolved, and deliberately not acted on. Do not close one silently.

1. **`resume28.pdf` and `resume29.pdf` are committed to a public repo** and carry
   the owner's phone number and email. They predate the `*.pdf` gitignore rule
   (`bcad104`), so the rule never applied to them. `skills.md` is tracked and
   carries the same details. Untracking leaves all of it in history; a real purge
   needs `git filter-repo` and a force push, or making the repo private.
   **Owner's decision — do not act unilaterally.**
2. **Commit `bcad104` is titled "email gen now with gemni key."** If a real key
   was ever committed it should be rotated. Not audited.
3. **Keyless discovery quality is mediocre.** Web search alone returns VC firms
   and startup directories alongside real companies. `discovery.AGGREGATOR_DOMAINS`
   and `discovery.is_junk_site()` filter these and could both be extended.
