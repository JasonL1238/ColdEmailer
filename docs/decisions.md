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

## Person finder

Every number below comes from replaying cached SERPs for the 30 saved
contacts that carry a LinkedIn URL, plus 18 companies' email-hunting queries.
Rebuild the capture and re-measure before revisiting any of them. Caveat that
applies to all of them: 28 of the 30 contacts work at one company (Goldman
Sachs), and the ground-truth profiles were themselves produced by this
extractor — so the benchmark is homogeneous *and* optimistic. Treat these as
relative improvements, not as an expected real-world hit rate.

**The stored `role` on these contacts leaks the answer — never quote an
accuracy number measured with it.** 27 of the 30 ground-truth roles are
verbatim substrings of the correct profile's own LinkedIn headline, because
that is where they were scraped from. A scorer handed that field is being told
which profile is right. Any identity measurement must be reported with the
role hint deleted, or it is fiction. (A hint the *user* types is legitimate —
it just is not this good, so the leaked benchmark overstates its value.)

**Candidates are ranked by cross-query corroboration, not by search order.**
Leak-free (role hint removed), fusing the query shapes and counting how many
independently surfaced the same profile moves top-1 from 86.7% to 90.0% and
wrong-person-first from 13.3% to 10.0%, on n=30. The same +3.3/−3.3 delta
appears with the role hint present (93.3% → 96.7%), so the effect is not an
artifact of the leak — but the higher pair must never be quoted as expected
accuracy. The failure it removes: a Goldman managing director losing to a
Dutch construction crew foreman with the same name, because neither snippet
contained the literal token "Goldman", both scored zero, and the tie broke on
arbitrary order. Corroboration feeds `rank_score()` only — never
`score`/`confidence`, because several queries agreeing means the retrieval was
consistent, not that the evidence about the person is stronger. n=30 with
28 people at one company: treat +3.3pt as directional, not as a rate.

**Two ranking signals were tried and are deliberately absent.** A SERP-rank
prior changed nothing (93.3%, unchanged) because the correct and incorrect
profiles are both rank 0 of *some* query. A "title names a different employer"
penalty made it actively worse (93.3% → 90.0%), demoting correct profiles
whose title segment is a school or a parent org. Do not re-add either without
new measurements.

**The website domain is not the mail domain, but DNS — not text frequency —
is what authorizes changing it.** Goldman's mail lives at `gs.com` while its
site is `goldmansachs.com`, so every pattern guess for those 26 contacts used
a domain that does not carry their mail. "Most frequently mentioned domain" is
worse than the bug it fixes: on the same corpus it picks `camping-arize.com`
(a French campsite) for Arize AI, `cranium.eu` for Cranium, and
`exyntechnologies.com` for Exyn. Adversarial review measured an ungated
format-page reader at 72.2% vs 88.9% for simply keeping the website domain —
i.e. worse than doing nothing. `infer_email_domain()` therefore requires all
of: the rival is company-anchored (exact name token, a stem built forward from
one, or the acronym — `gs` ← Goldman Sachs), seen ≥4 times, ≥2x the website
domain, has MX, **and** `shares_mail_tenancy()` proves both domains publish an
identical tenant-specific MX host. Goldman and gs.com both answer
`mx0a-0014b501.pphosted.com`; the embedded customer id is the proof.
Commodity hosts (`aspmx.l.google.com`) are excluded — two domains on Google
Workspace share nothing but a vendor. Measured: 1 override in 18 companies,
the one known correct, and the tenancy check independently rejects
`exyntechnologies.com`, which the frequency rule alone had accepted.
Relatedness is anchored rather than substring precisely because
`camping-arize` contains `arize`.

**The two GitHub routes everyone tries first are dead.** The public events feed
no longer carries `payload.commits` (0 of 350 PushEvents) and the profile
`.email` field is null (0 of 12 users). What still works is `payload.head` plus
`repo.name`, then `https://github.com/<repo>/commit/<sha>.patch` parsed for
`From:`. The commits API agrees on 8/8 but spends quota; the patch costs none,
which is what keeps the unauthenticated 60/hr ceiling from binding.

**GitHub addresses are deliberately NOT name-matched.**
`contact_verify.email_matches_person` would reject `torvalds@linux-foundation.org`,
`me@kennethreitz.org`, `antirez@gmail.com` and `tiangolo@gmail.com` — 4 of the 8
correct answers measured. Precision comes from *attribution* instead: the commit
sits at the head of that login's own push **and** its author is that login. A
merge or rebase commit authored by someone else is rejected on that assertion.
This is why `_accept_found_address` name-checks unattributed addresses and not
attributed ones; the asymmetry is the whole design.

**The placeholder filter runs on the found path only, and the mail-domain
harvest is fed by a different query pool.** 24 of 34 (71%) email-shaped strings
in cached SERPs are templates — `john.smith@gs.com` ×9, `janedoe@gs.com` ×6 —
and they attach to real people: two different contacts each "had"
`john.smith@gs.com`. But filtering the *harvest* input flips Goldman from
`gs.com` 16 / `goldmansachs.com` 8 to `goldmansachs.com` 4 / `gs.com` 1,
destroying the one domain inference that works. The two mechanisms are mutually
destructive, so they are separated structurally rather than by comment:
`search(..., pool=False)` keeps stage 1's template-dense results out of the
found path entirely.

**A template address and a real one can be the same string, so provenance
decides.** `jane@acme.com` is a documentation example on a format page and a
real mailbox in a commit header. The filter therefore applies only to
unattributed text; an address printed in a commit or an author block skips it.

**Team and leadership pages are identity evidence, not an email source.** 0
emails from 862KB of measured HTML (exyn.com/about, goldmansachs.com
leadership). The fetch budget there went 4 → 1, and `MAX_PAGE_FETCHES` 8 → 4;
GitHub profile pages are never rendered at all, since the address is not there.

**Data brokers are not shipped.** 24 of 30 targets have broker pages, only
ContactOut leaked addresses into snippets (3/30), and **2 of those 3 were
provably wrong** — one domain has no MX at all, one is absent from EDGAR where
known-good controls return 505 and 123 hits. Precision and ToS both fail.

**EDGAR runs only with a declared contact User-Agent.** sec.gov 403s a browser
UA and requires `"<app> <contact email>"`. With no contact configured the source
is silently off; inventing one would misrepresent who is making the request.

**arXiv HTML exists only for papers from ~Dec 2023 onward** (measured 2/4 on
recent papers vs 1/5 on 2020 ones), and the API needs
`sortBy=submittedDate&sortOrder=descending` or it returns old papers with no
HTML to read.

**Outbound TCP/25 is blocked here, so mailbox verification runs over HTTPS.**
Measured 2026-08-16: `smtp.gmail.com:587` opened in 0.26s with a real ESMTP
banner, while port 25 timed out to Google, OVH, Zoho, Fastmail and Proofpoint
alike. Proofpoint *refused* 465/587 in 70ms while 25 hung — proof the route is
healthy and the filter is port-25-specific, i.e. the standard consumer-ISP
anti-spam block. There is no alternate port: MX hosts accept mail only on 25,
and 465/587 are authenticated submission ports on your own provider. So
`mailbox_verify` keeps a direct-SMTP transport for networks where 25 is open and
otherwise asks a provider over 443. Re-check with
`scripts/probe_smtp_feasibility.py` from any new network.

**What a mailbox check can and cannot prove.** A `deliverable` verdict means a
mailbox exists at that address on that server, once, for that sender. It does
**not** mean the mailbox belongs to your target — `john.smith@gs.com` may accept
mail and be a different John Smith — and it does not promise delivery, since
filtering happens after RCPT. On an accept-all domain every answer is 250 and
therefore no answer at all. That is why a result never writes `email_verified`,
why `origin` stays `"guessed"`, and why saving a confirmed guess still requires
the user to assert ownership. Verification is off by default
(`MAILBOX_VERIFY=0`) because its worst case — an IP reputation listing, or a
contact list sent to a vendor — is not something the user can undo, unlike
`EMAIL_PATTERN_INFERENCE`, whose worst case is a sentence in a notes field.

**Email discovery is capped by the population, not the code.** A live probe
across GitHub, arXiv, EDGAR, personal sites, company team pages and open web
found a self-published address for **0 of 30** saved contacts — while the same
GitHub pipeline returned 8/8 on public technologists. Investment bankers do
not publish their addresses; engineers and academics do. So a zero hit rate
here is the honest answer for this contact set, not a bug to re-plumb. Spend
effort on identity and on honest labeling instead, and expect found addresses
only for engineering/research targets.

**Candidates are staged in the job's `result` JSON, not a table.** Jobs are
never pruned (startup only flips orphaned `running` rows to `failed`), and
`db.update_job` re-serializes a mutated result, so review-before-save survives
restarts and approval can be marked in place. A candidates table would add a
second lifecycle for zero durability gain.

**There is no `email_domain_kind` column.** Company/personal/other is
presentation metadata in the candidate JSON, and provenance lands in `notes`
on approval. `email_kind` keeps its person-mailbox meaning; adding a second
kind column would force an audit of every reader for no send-path benefit.

**Past-employer matching lives in `person_finder._past_employer_mentioned`,
and `deep_research._looks_like_employee_snippet` still rejects "ex-"/"former"
snippets.** They look contradictory and are both right: for a current-employee
hunt those shapes are noise, and for a user-declared past employer they are
the very signal. Do not "unify" them.

**A person hunt hard-stops at 10 minutes, not 30.** It runs a bounded query
ladder for one person; promising a deep-dive-length window in the UI would be
an over-claim.

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
