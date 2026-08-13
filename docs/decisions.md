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

## Scraping

**`is_safe_public_url` reads an IP literal instead of resolving it.** Handing
`10.0.0.1` to `getaddrinfo` looks harmless and is not: on a DNS64/NAT64 network
the resolver synthesizes a *globally routable* IPv6 address for it (measured on
the developer's own machine: `10.0.0.1` → `2607:7700:0:2:0:2:a00:1`), which
passes `ip.is_global` and defeats the guard completely. Resolved addresses are
additionally unwrapped for IPv4-mapped, 6to4, Teredo and RFC 6052 NAT64 forms
before the check. Residual limitation: a hostname whose A record points at a
private address can still be synthesized into a *provider-specific* NAT64
prefix, which no static rule can enumerate.

Every claim below is a number from replaying a 30-site / 1,169-page capture
through the real extractor. Rebuild it with `scripts/scrape_corpus.py capture`
and re-measure with `scripts/measure_shipped.py` before revisiting any of them.

**An address whose local part is 16+ hex characters is thrown away.** It is a
Sentry DSN public key, not a person. These were **270 of the 444 contacts** the
crawler produced corpus-wide — 60% — and they filled `result["emails"]` (9 of
Stripe's 12) and drove hundreds of MX lookups. `_BAD_DOMAINS` already listed
`sentry.io`, but matched it exactly while every real ingest host is a subdomain
(`o415358.ingest.us.sentry.io`), so the check never fired; suffix matching lives
in `_BAD_DOMAIN_SUFFIXES` and is separate because `errors.stripe.com` is junk
while `stripe.com` is a real employer.

**`extract_emails_from_html` decodes `\uXXXX` before matching.** Sites that
embed markup inside JSON write `>` as `>`. The backslash is outside
`EMAIL_RE`'s class but `u003e` is alphanumeric, so matching began at the `u` and
produced `u003ekbrooks@wsgr.com` — which passed validation *and* the MX check,
giving a sendable address that duplicated a real person.

**Link discovery deliberately does not know the words `attorneys`,
`professionals`, `staff`, or `partners`.** This was proposed and refuted:
adding them found **one** real page (`themarkup.org/board-of-directors`) against
four pieces of noise across 30 sites, because firms already use `/people`, which
the existing vocabulary matches. Matched loosely instead, they pulled in 371
links — `/industries/professional-services`,
`/partners/solution-partners/10up`, `practice-areas/.../board-and-internal-
investigations.html`. The sitemap ranker carries this vocabulary instead, where
it pays.

**Sitemap ranking matches whole path segments; link discovery matches
substrings.** The rules differ because the inputs differ by four orders of
magnitude. Substring matching against a 16,000-URL sitemap selects
`val.town/u/fuckyouscratchteam` and `linear.app/changelog/team-documents`;
segment-exact matching rejects both and still keeps
`wsgr.com/en/people/holly-hafford.html`. `SITEMAP_MAX_URLS` must stay above the
number of URLs a 2MB sitemap holds: Zscaler's people pages sit at raw index
3,853 and Val Town's at 16,427, so an earlier cap of 2,000 discarded exactly
what the feature exists to find.

**Bot-wall retry cost was measured and is not worth optimising.** Across 62
blocked corpus pages, every one returned 403 and **none** sent `Retry-After`.
An earlier estimate of 15.5s/page came from a fixture that supplied the header
itself; the real cost is ~0.55s/page.

**JSON-LD person data is not parsed.** Proposed to repair `name_from_email`
stubs such as `cbal@wsgr.com` → "Cbal"; measured, it repairs 12 contacts on
**2 of 30 sites**, and no site in the corpus publishes a JSON-LD LinkedIn URL.
It remains a real gap on professional-services sites specifically — 26 of
Wilson Sonsini's 28 contacts are stubs — but not a general one.

**`lxml` is not used for parsing.** Measured at 1.4x on a realistic page, and it
builds a different DOM shape (it auto-closes `<p>`) than the parent/sibling
walk in `extract_contact_candidates` expects.

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
