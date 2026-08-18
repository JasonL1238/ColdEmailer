"""Sources that publish a person's real email address.

The person finder can always *guess* an address. This module is about the
other thing: addresses people actually published, which is what makes an
address usable rather than plausible.

Measured on 30 saved contacts, the shipped crawler found 0 self-published
addresses — but the GitHub route below returned 8/8 on public technologists.
The difference is the population, not the technique: investment bankers do not
publish addresses and open-source maintainers sign every commit with one. So
each adapter here targets a population that demonstrably publishes, and the
router only spends budget on the ones a given person might belong to.

Everything is a pure function over an injectable HTTP getter — no Database, no
FastAPI, no WebScraper — so the whole module is testable without a network.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import quote, urlparse

from contact_verify import name_appears_in, name_tokens, normalize_email
from enrichment import extract_emails_from_html, is_valid_outreach_email
from web_scraper import is_safe_public_url

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

DEFAULT_TIMEOUT = 8.0
MAX_TEXT_BYTES = 64 * 1024      # a .patch From: header is in the first KB
MAX_JSON_BYTES = 512 * 1024
MAX_HOPS = 4

GITHUB_API = "https://api.github.com"
ARXIV_API = "https://export.arxiv.org/api/query"
EDGAR_SEARCH = "https://efts.sec.gov/LATEST/search-index"
# arXiv renders HTML only for papers from roughly this date onward. Older
# entries are dropped without spending a fetch: measured 1/5 yield on 2020
# papers versus 2/4 on recent ones, because the HTML simply does not exist.
ARXIV_HTML_EPOCH = "2023-12-01"

SOURCE_PRECEDENCE = {
    "github_commit": 0,
    "arxiv": 1,
    "edgar": 2,
    "personal_site": 3,
    "team_page": 4,
    "serp": 5,
}


# --------------------------------------------------------------------------
# HTTP seam
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Fetched:
    status_code: int
    headers: Dict[str, str]
    text: str
    final_url: str
    data: Any = None


class GetBudget:
    """Cheap text/JSON GETs, counted apart from expensive fetch_html.

    A `.patch` is ~4KB over plain HTTP; `WebScraper.fetch_html` can launch
    Chromium. Sharing one counter would let twenty of the former crowd out one
    of the latter, so the budgets stay separate and this one carries its own
    per-service sub-caps.
    """

    def __init__(self, *, max_gets: int = 24, max_github_api: int = 12,
                 max_github_search: int = 3,
                 should_stop: Optional[Callable[[], bool]] = None):
        self.max_gets = max_gets
        self.max_github_api = max_github_api
        self.max_github_search = max_github_search
        self._should_stop = should_stop
        self.counts: Dict[str, int] = {
            "plain": 0, "github_api": 0, "github_search": 0}
        self.limited: Set[str] = set()

    @staticmethod
    def classify(url: str) -> str:
        host = (urlparse(url).hostname or "").lower()
        if host == "api.github.com":
            return ("github_search" if "/search/" in url else "github_api")
        return "plain"

    def _cap(self, kind: str) -> int:
        return {"github_api": self.max_github_api,
                "github_search": self.max_github_search}.get(
                    kind, self.max_gets)

    def allow(self, url: str) -> bool:
        if self._should_stop is not None and self._should_stop():
            return False
        kind = self.classify(url)
        if kind.startswith("github") and "github" in self.limited:
            return False
        if self.counts["plain"] + sum(
                v for k, v in self.counts.items() if k != "plain"
        ) >= self.max_gets and kind == "plain":
            return False
        return self.counts[kind] < self._cap(kind)

    def spend(self, url: str) -> None:
        self.counts[self.classify(url)] += 1

    def note_rate_limited(self, service: str) -> None:
        self.limited.add(service)

    def is_rate_limited(self, service: str) -> bool:
        return service in self.limited


def _read_capped(resp, max_bytes: int) -> str:
    """Body text, truncated. Works with a streaming response or a plain fake."""
    iter_content = getattr(resp, "iter_content", None)
    if callable(iter_content):
        chunks, total = [], 0
        try:
            for chunk in iter_content(8192):
                if not chunk:
                    continue
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", "replace")
                chunks.append(chunk)
                total += len(chunk)
                if total >= max_bytes:
                    break
            return "".join(chunks)[:max_bytes]
        except Exception:
            pass
    return (getattr(resp, "text", "") or "")[:max_bytes]


def _guarded_get(url: str, *, headers=None, timeout=DEFAULT_TIMEOUT,
                 max_bytes=MAX_TEXT_BYTES, http_get=None,
                 budget: Optional[GetBudget] = None) -> Optional[Fetched]:
    """GET with the SSRF guard re-applied to every redirect hop.

    Redirects are followed manually rather than by the client, because the
    guard is worthless if a 302 can walk it to localhost — the same reason
    `web_scraper._safe_get` does it this way.
    """
    getter = http_get
    if getter is None:
        if requests is None:  # pragma: no cover
            return None
        getter = requests.get
    current = url
    for _ in range(MAX_HOPS):
        if not is_safe_public_url(current):
            return None
        if budget is not None:
            if not budget.allow(current):
                return None
            budget.spend(current)
        try:
            resp = getter(current, headers=headers or {}, timeout=timeout,
                          allow_redirects=False)
        except Exception:
            return None
        status = getattr(resp, "status_code", 0)
        resp_headers = {str(k).lower(): str(v) for k, v in
                        (getattr(resp, "headers", {}) or {}).items()}
        if status in (301, 302, 303, 307, 308):
            location = resp_headers.get("location")
            if not location:
                return None
            current = location
            continue
        return Fetched(status_code=status, headers=resp_headers,
                       text=_read_capped(resp, max_bytes), final_url=current)
    return None


def guarded_get_text(url: str, **kwargs) -> Optional[Fetched]:
    return _guarded_get(url, **kwargs)


def guarded_get_json(url: str, **kwargs) -> Optional[Fetched]:
    kwargs.setdefault("max_bytes", MAX_JSON_BYTES)
    fetched = _guarded_get(url, **kwargs)
    if not fetched or fetched.status_code != 200:
        return fetched
    try:
        import json
        return Fetched(fetched.status_code, fetched.headers, fetched.text,
                       fetched.final_url, data=json.loads(fetched.text))
    except Exception:
        return Fetched(fetched.status_code, fetched.headers, fetched.text,
                       fetched.final_url, data=None)


# --------------------------------------------------------------------------
# Address acceptance
# --------------------------------------------------------------------------

# Locals that are documentation, not people. Measured: 24 of 34 (71%) of the
# email-shaped strings in cached search results were these, and they attach to
# real people — two different contacts each "had" john.smith@gs.com scraped
# off an email-format page.
PLACEHOLDER_LOCALS = frozenset({
    "john.smith", "johnsmith", "john", "jane.doe", "janedoe", "jane",
    "john.doe", "johndoe", "jane.smith", "janesmith",
    "first.last", "firstlast", "first", "flast", "f.last", "firstinitial",
    "firstname.lastname", "firstname", "lastname", "name.surname",
    "email", "example", "user", "username", "yourname", "your.name",
    "last", "surname", "initial",
})

_NOREPLY_HOSTS = ("users.noreply.github.com", "noreply.github.com")
_BOT_RE = re.compile(
    r"\[bot\]|\bdependabot\b|\bgithub-actions\b|\brenovate\b|\bgreenkeeper\b|"
    r"\bsemantic-release\b|\bactions@github\.com\b|\bcodecov\b|-ci\b|"
    # "Balloob Bot" slipped a marker-only rule: the bare word at the end of
    # an author name, or tagged into the local part, is the real signal.
    r"\bbot\b|\+bot@",
    re.I)


def is_placeholder_address(email: str) -> bool:
    """True for template addresses like first.last@ or john.smith@.

    Domain-independent on purpose: the measured contamination sits on the
    company's *real* domain (john.smith@gs.com), so a domain rule cannot see it.
    """
    local = normalize_email(email).split("@", 1)[0]
    if not local:
        return False
    local = local.split("+", 1)[0]
    normalized = re.sub(r"[_-]", ".", local)
    return normalized in PLACEHOLDER_LOCALS


def is_github_noreply(email: str) -> bool:
    host = normalize_email(email).split("@", 1)[-1]
    return any(host == h or host.endswith("." + h) for h in _NOREPLY_HOSTS)


def is_bot_identity(text: str) -> bool:
    return bool(_BOT_RE.search(text or ""))


def looks_like_real_domain(email: str) -> bool:
    host = normalize_email(email).split("@", 1)[-1]
    if "." not in host:
        return False
    return not any(host == bad or host.endswith("." + bad)
                   for bad in ("localhost", "local", "invalid", "test",
                               "example", "internal"))


def _acceptable(email: str, *, author_text: str = "",
                attributed: bool = False) -> bool:
    """Filters every adapter applies before returning an address.

    The placeholder check is skipped for attributed sources. A template like
    `jane@acme.com` is indistinguishable by string alone from a real Jane's
    address — but an address printed in a commit header or an author block was
    written by a person, not by a documentation example, so provenance settles
    what the string cannot. Unattributed text (search snippets, team pages) has
    no such guarantee and keeps the filter.
    """
    email = normalize_email(email)
    if not email or "@" not in email:
        return False
    if is_github_noreply(email):
        return False
    if not attributed and is_placeholder_address(email):
        return False
    if not looks_like_real_domain(email) or not is_valid_outreach_email(email):
        return False
    return not is_bot_identity(author_text or email)


def _found(email: str, url: str, kind: str, why: str) -> Dict[str, Any]:
    return {"email": normalize_email(email), "source_url": url,
            "source_kind": kind, "attribution": why, "attributed": True}


# --------------------------------------------------------------------------
# GitHub
# --------------------------------------------------------------------------

_GITHUB_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
_GITHUB_RESERVED = {
    "orgs", "features", "about", "topics", "search", "settings", "marketplace",
    "sponsors", "collections", "pricing", "enterprise", "login", "join",
    "explore", "notifications", "apps", "readme", "blog", "security", "site",
    "contact", "issues", "pulls", "trending", "new", "organizations",
    "codespaces", "events", "stars", "watching", "dashboard", "home",
}
_PATCH_FROM_RE = re.compile(r"^From:\s*(.*?)\s*<([^>]+)>\s*$", re.M)


def github_login_from_url(url: str) -> Optional[Tuple[str, bool]]:
    """(login, weak) parsed from a github.com URL, or None.

    `weak` marks an /owner/repo owner, which is very often an organisation
    (github.com/facebook/react) rather than the person — those must be
    verified against the profile before they are trusted.
    """
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in ("github.com", "www.github.com", "gist.github.com"):
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return None
    login = parts[0]
    if login.lower() in _GITHUB_RESERVED or not _GITHUB_LOGIN_RE.match(login):
        return None
    return login, len(parts) > 1


def _github_headers(token: Optional[str]) -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Reach-person-finder",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def github_json(url: str, *, token, http_get, budget) -> Optional[Any]:
    fetched = guarded_get_json(url, headers=_github_headers(token),
                               http_get=http_get, budget=budget)
    if fetched is None:
        return None
    if fetched.status_code in (403, 429):
        # Only a genuine quota exhaustion disables the source; a 403 on one
        # private repo must not.
        if fetched.headers.get("x-ratelimit-remaining") == "0" and budget:
            budget.note_rate_limited("github")
        return None
    if fetched.status_code != 200:
        return None
    return fetched.data


def name_parts(name: str) -> List[str]:
    """Name tokens, keeping a leading single-letter initial.

    `name_tokens` drops tokens under two characters, which silently erases the
    "G" in "G Johansson" and leaves one token — so candidate generation and the
    name check both gave up on a perfectly findable person.
    """
    tokens = name_tokens(name)
    raw = [t for t in re.split(r"[^A-Za-z0-9]+", name or "") if t]
    if raw and len(raw[0]) == 1 and raw[0].isalpha():
        initial = raw[0].lower()
        if not tokens or tokens[0] != initial:
            tokens = [initial] + tokens
    return tokens


def _login_candidates(name: str) -> List[str]:
    """Plausible logins for a human name, cheapest-first.

    With a token a profile lookup costs one of 5,000 hourly calls, so guessing
    a handful of shapes and *verifying each against the profile's real name* is
    far cheaper than the search API — which measured 1/6 on real people. An
    unverified guess is never returned; this only generates candidates.
    """
    tokens = name_parts(name)
    if len(tokens) < 2:
        return []
    first, last = tokens[0], tokens[-1]
    shapes = [
        f"{first}{last}", f"{first}-{last}", f"{first}_{last}",
        f"{first[0]}{last}", f"{first}{last[0]}", f"{last}{first}",
        first, last, f"{first}.{last}",
    ]
    # Middle names carry the login as often as the surname does: Lucas Mindêllo
    # de Andrade is `lucasmindello`, not `lucasandrade`.
    for middle in tokens[1:-1]:
        if len(middle) < 3:
            continue
        shapes += [f"{first}{middle}", f"{first}-{middle}", middle]
    seen, out = set(), []
    for shape in shapes:
        if shape not in seen and _GITHUB_LOGIN_RE.match(shape):
            seen.add(shape)
            out.append(shape)
    return out


def profile_name_matches(target: str, profile_name: Optional[str]) -> bool:
    """Does a GitHub profile's display name denote the person we want?

    Stricter than a substring check, looser than demanding every token. People
    shorten themselves — "Lucas Mindêllo de Andrade" commits as "Lucas
    Mindêllo" — and abbreviate — "G Johansson", "J. Nick Koston". So the
    surname must match exactly, and the given name must match either in full or
    as an initial. Surname alone is never enough; that is how you reach a
    different Johansson.
    """
    want, got = name_parts(target), name_parts(profile_name)
    if len(want) < 2 or len(got) < 2:
        return False
    if want[-1] != got[-1]:
        # A dropped trailing surname, which is routine in Portuguese and
        # Spanish names: "Lucas Mindêllo de Andrade" commits as "Lucas
        # Mindêllo". Only accepted when the shorter name is wholly contained in
        # the longer one and the given names agree, so a stranger sharing one
        # surname still fails.
        return (set(got) <= set(want) and got[0] == want[0])
    heads_want, heads_got = set(want[:-1]), set(got[:-1])
    if heads_want & heads_got:
        return True
    return bool({h[0] for h in heads_want} & {h[0] for h in heads_got})


def _verify_login(login: str, name: str, company: Optional[str], *, token,
                  http_get, budget) -> Optional[Dict]:
    """A login is only accepted when the profile itself corroborates it."""
    profile = github_json(f"{GITHUB_API}/users/{login}",
                           token=token, http_get=http_get, budget=budget)
    if not isinstance(profile, dict) or profile.get("type") != "User":
        return None
    if profile_name_matches(name, profile.get("name")):
        return profile
    if (company and profile.get("company")
            and str(company).lower().split()[0]
            in str(profile["company"]).lower()
            and name_appears_in(name, profile.get("login", "").replace("-", " "))):
        return profile
    return None


def github_search_logins(name: str, *, token=None, http_get=None,
                         budget=None) -> List[str]:
    """Search shapes beyond `in:name`, which alone measured 1/6."""
    # name_tokens, not name_parts: a bare initial makes a *worse* query.
    # Searching "j koston" loses the person that "nick koston" finds, measured
    # as a regression when this used name_parts.
    tokens = name_tokens(name)
    if len(tokens) < 2:
        tokens = name_parts(name)
    if len(tokens) < 2:
        return []
    queries = [
        f'"{name}" in:name',
        f"{tokens[0]} {tokens[-1]} in:name",   # accent-folded tokens
        f"{tokens[0]}{tokens[-1]} in:login",
        f"{tokens[0]}-{tokens[-1]} in:login",
    ]
    out: List[str] = []
    for query in queries:
        data = github_json(
            f"{GITHUB_API}/search/users?q={quote(query)}&per_page=5",
            token=token, http_get=http_get, budget=budget)
        for item in (data or {}).get("items", []):
            login = item.get("login")
            if login and login not in out:
                out.append(login)
        if len(out) >= 8:
            break
    return out


def github_resolve_login(name: str, *, urls: Sequence[str] = (),
                         company: Optional[str] = None, token=None,
                         http_get=None, budget=None) -> Optional[Dict]:
    """Find the person's GitHub login, cheapest evidence first.

    The search API alone resolved only 1 of 6 real people, so it is the last
    resort. A github.com URL already sitting in the candidate's evidence costs
    nothing and is far more reliable.
    """
    strong: List[str] = []
    weak: List[str] = []
    for url in urls or ():
        parsed = github_login_from_url(url or "")
        if not parsed:
            continue
        login, is_weak = parsed
        (weak if is_weak else strong).append(login)

    for login in strong:
        if name_appears_in(name, login.replace("-", " ")):
            return {"login": login, "how": "url",
                    "source_url": f"https://github.com/{login}"}

    def _accept(login, how):
        profile = _verify_login(login, name, company, token=token,
                                http_get=http_get, budget=budget)
        if profile is None:
            return None
        return {"login": profile.get("login") or login, "how": how,
                "profile": profile,
                "source_url": f"https://github.com/{login}"}

    for login in list(strong) + list(weak):
        got = _accept(login, "url_verified")
        if got:
            return got

    # Search before guessing, and the ordering is load-bearing. A guessed
    # login is the weakest evidence in the ladder: `jkoston` exists, its
    # profile name passes the check, and letting it answer first hijacked
    # "J. Nick Koston" from `bdraco` — whom search had ranked first and
    # verified. Guesses only get to speak when nothing better did.
    for login in github_search_logins(name, token=token, http_get=http_get,
                                      budget=budget):
        got = _accept(login, "search")
        if got:
            return got

    for login in _login_candidates(name):
        got = _accept(login, "login_guess")
        if got:
            return got
    return None


def github_repo_commits(login: str, *, limit: int = 8, token=None,
                        http_get=None, budget=None) -> List[Tuple[str, str]]:
    """Fallback for people with no recent public pushes.

    The events feed only covers ~90 days, so an occasional contributor looks
    silent there while their commits sit in their own repositories.
    """
    repos = github_json(
        f"{GITHUB_API}/users/{login}/repos?sort=pushed&per_page=10",
        token=token, http_get=http_get, budget=budget)
    out: List[Tuple[str, str]] = []
    for repo in (repos or [])[:6]:
        full = repo.get("full_name")
        if not full or repo.get("fork"):
            continue
        commits = github_json(
            f"{GITHUB_API}/repos/{full}/commits?author={login}&per_page=5",
            token=token, http_get=http_get, budget=budget)
        for commit in commits or []:
            sha = commit.get("sha")
            if sha:
                out.append((full, sha))
            if len(out) >= limit:
                return out
    return out


def github_push_heads(login: str, *, limit: int = 20, token=None,
                      http_get=None, budget=None) -> List[Tuple[str, str]]:
    """[(repo_full_name, head_sha)] from the person's own public pushes.

    `payload.commits` is deliberately not read — it no longer exists (measured
    0 of 350 PushEvents carry it), and a stale fixture that still has it must
    not silently become the answer.
    """
    events = github_json(
        f"{GITHUB_API}/users/{login}/events/public?per_page=100",
        token=token, http_get=http_get, budget=budget)
    heads: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()
    for event in events or []:
        if not isinstance(event, dict) or event.get("type") != "PushEvent":
            continue
        repo = (event.get("repo") or {}).get("name")
        sha = (event.get("payload") or {}).get("head")
        if not repo or not sha or (repo, sha) in seen:
            continue
        seen.add((repo, sha))
        heads.append((repo, sha))
        if len(heads) >= limit:
            break
    return heads


def github_commit_author(repo: str, sha: str, *, token=None, http_get=None,
                         budget=None) -> Optional[Dict]:
    """The commit's author identity, preferring the quota-free .patch route."""
    patch = guarded_get_text(
        f"https://github.com/{repo}/commit/{sha}.patch",
        headers={"User-Agent": "Reach-person-finder"},
        http_get=http_get, budget=budget)
    if patch is not None and patch.status_code == 200:
        matches = _PATCH_FROM_RE.findall(patch.text)
        addresses = {m[1].strip().lower() for m in matches}
        if len(addresses) == 1:
            author_name, email = matches[0]
            return {"email": email.strip(), "author_name": author_name.strip(),
                    "author_login": None, "via": "patch",
                    "source_url": f"https://github.com/{repo}/commit/{sha}"}
        # Several disagreeing From: headers means a merge carrying other
        # people's commits. Picking the first would be a guess, so ask the API
        # which identity GitHub itself attributes the commit to.

    data = github_json(f"{GITHUB_API}/repos/{repo}/commits/{sha}",
                        token=token, http_get=http_get, budget=budget)
    if not isinstance(data, dict):
        return None
    author = ((data.get("commit") or {}).get("author") or {})
    email = (author.get("email") or "").strip()
    if not email:
        return None
    return {"email": email, "author_name": (author.get("name") or "").strip(),
            "author_login": ((data.get("author") or {}) or {}).get("login"),
            "via": "api",
            "source_url": f"https://github.com/{repo}/commit/{sha}"}


def _attributed_to(author: Dict, *, login: str, target_name: str) -> bool:
    """Is this commit really by our person?

    The head of someone's push can be a merge or rebase of another person's
    work, which is how a stranger's address leaks in. One of these must hold.
    """
    author_login = (author.get("author_login") or "").lower()
    if author_login:
        return author_login == login.lower()
    author_name = author.get("author_name") or ""
    if name_appears_in(target_name, author_name):
        return True
    compact = re.sub(r"[^a-z0-9]", "", author_name.lower())
    return bool(compact) and compact == login.lower()


def github_emails_for(name: str, *, urls: Sequence[str] = (), company=None,
                      token=None, http_get=None, budget=None,
                      max_commits: int = 20) -> List[Dict]:
    """Self-published addresses from the person's own public commits."""
    token = token if token is not None else os.getenv("GITHUB_TOKEN", "").strip()
    token = token or None
    resolved = github_resolve_login(name, urls=urls, company=company,
                                    token=token, http_get=http_get,
                                    budget=budget)
    if not resolved:
        return []
    login = resolved["login"]
    out: List[Dict] = []
    seen: Set[str] = set()
    commits = github_push_heads(login, limit=max_commits, token=token,
                                http_get=http_get, budget=budget)
    if not commits:
        commits = github_repo_commits(login, token=token, http_get=http_get,
                                      budget=budget)
    for repo, sha in commits:
        author = github_commit_author(repo, sha, token=token,
                                      http_get=http_get, budget=budget)
        if not author:
            continue
        if not _attributed_to(author, login=login, target_name=name):
            continue
        email = normalize_email(author["email"])
        if email in seen:
            continue
        # Deliberately NOT name-matched. torvalds@linux-foundation.org and
        # me@kennethreitz.org are correct answers that a local-part rule would
        # throw away — 4 of 8 in measurement. Attribution is the evidence.
        if not _acceptable(email, author_text=author.get("author_name") or "",
                           attributed=True):
            continue
        seen.add(email)
        out.append(_found(
            email, author["source_url"], "github_commit",
            f"Commit {sha[:7]} in {repo}, pushed by {login} and authored by "
            f"{author.get('author_name') or login}"))

    if not out:
        # Their profile links a site they declared as their own, so an address
        # published there is theirs. Only consulted when commits gave nothing,
        # since a commit header is the stronger evidence.
        out.extend(profile_site_emails(
            resolved.get("profile") or {}, name,
            http_get=http_get, budget=budget))
    return out


def profile_site_emails(profile: Dict, name: str, *, http_get=None,
                        budget=None) -> List[Dict]:
    """Addresses on the personal site a GitHub profile points at."""
    site = (profile.get("blog") or "").strip()
    if not site:
        return []
    if not site.startswith(("http://", "https://")):
        site = f"https://{site}"
    fetched = guarded_get_text(site, http_get=http_get, budget=budget)
    if not fetched or fetched.status_code != 200:
        return []
    addresses = extract_emails_from_html(fetched.text)
    surname = (name_tokens(name) or [""])[-1]
    if len(addresses) > 1:
        addresses = _scoped_addresses(fetched.text, surname) or addresses[:1]
    out: List[Dict] = []
    for address in addresses[:2]:
        email = normalize_email(address)
        if not _acceptable(email, attributed=True):
            continue
        out.append(_found(email, site, "personal_site",
                          f"Published on {site}, the site linked from "
                          f"{profile.get('login')}'s GitHub profile"))
    return out


# --------------------------------------------------------------------------
# arXiv
# --------------------------------------------------------------------------

def _scoped_addresses(html: str, surname: str, *, window: int = 1500) -> List[str]:
    """Addresses printed near the target's surname.

    A paper lists every co-author's address; proximity is what keeps us from
    crediting the first one to whoever we were searching for. A page with
    exactly one address needs no scoping.
    """
    addresses = extract_emails_from_html(html)
    if not addresses:
        return []
    if not surname:
        return addresses
    text = re.sub(r"<[^>]+>", " ", html)
    lowered, target = text.lower(), (surname or "").lower()
    spots = [m.start() for m in re.finditer(re.escape(target), lowered)] \
        if target else []
    if not spots:
        return []
    out = []
    for address in addresses:
        local = address.split("@", 1)[0].lower()
        for at in [m.start() for m in re.finditer(re.escape(local), lowered)]:
            if any(abs(at - spot) <= window for spot in spots):
                out.append(address)
                break
    return out


def arxiv_recent_entries(name: str, *, http_get=None, budget=None,
                         max_results: int = 10) -> List[Dict]:
    tokens = name_tokens(name)
    if len(tokens) < 2:
        return []
    query = quote(f'au:"{tokens[-1]}_{tokens[0]}"')
    # sortBy is mandatory: without it arXiv returns old papers, and papers
    # before ARXIV_HTML_EPOCH have no HTML rendering to read at all.
    url = (f"{ARXIV_API}?search_query={query}&start=0"
           f"&max_results={max_results}"
           f"&sortBy=submittedDate&sortOrder=descending")
    fetched = guarded_get_text(url, http_get=http_get, budget=budget)
    if not fetched or fetched.status_code != 200:
        return []
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(fetched.text, "xml")
    except Exception:
        return []
    entries = []
    for entry in soup.find_all("entry"):
        raw_id = (entry.find("id").get_text() if entry.find("id") else "")
        published = (entry.find("published").get_text()[:10]
                     if entry.find("published") else "")
        authors = [a.get_text().strip()
                   for a in entry.find_all("name")]
        arxiv_id = raw_id.rsplit("/abs/", 1)[-1] if raw_id else ""
        if not arxiv_id or published < ARXIV_HTML_EPOCH:
            continue
        if not any(name_appears_in(name, a) for a in authors):
            continue
        entries.append({"arxiv_id": arxiv_id, "published": published,
                        "html_url": f"https://arxiv.org/html/{arxiv_id}"})
    return entries


def arxiv_emails_for(name: str, *, http_get=None, budget=None,
                     max_papers: int = 2) -> List[Dict]:
    tokens = name_tokens(name)
    if len(tokens) < 2:
        return []
    out: List[Dict] = []
    seen: Set[str] = set()
    for entry in arxiv_recent_entries(name, http_get=http_get,
                                      budget=budget)[:max_papers]:
        fetched = guarded_get_text(entry["html_url"], http_get=http_get,
                                   budget=budget)
        if not fetched or fetched.status_code != 200:
            continue
        for address in _scoped_addresses(fetched.text, tokens[-1]):
            email = normalize_email(address)
            host = email.split("@", 1)[-1]
            if email in seen or host.endswith("arxiv.org"):
                continue
            if not _acceptable(email, attributed=True):
                continue
            seen.add(email)
            out.append(_found(email, entry["html_url"], "arxiv",
                              f"Author block of arXiv:{entry['arxiv_id']}"))
    return out


# --------------------------------------------------------------------------
# EDGAR
# --------------------------------------------------------------------------

def edgar_contact_ua(contact_email: Optional[str]) -> Optional[str]:
    """SEC requires a declared contact address; without one the source is off.

    sec.gov 403s a browser User-Agent. Inventing a contact address to get past
    that would be misrepresenting who is making the request, so no contact
    configured means EDGAR simply does not run.
    """
    contact = (contact_email or "").strip()
    return f"Reach person-finder {contact}" if "@" in contact else None


def edgar_emails_for(name: str, *, contact_email=None, http_get=None,
                     budget=None) -> List[Dict]:
    user_agent = edgar_contact_ua(contact_email)
    if not user_agent:
        return []
    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    fetched = guarded_get_json(
        f"{EDGAR_SEARCH}?q={quote(chr(34) + name + chr(34))}",
        headers=headers, http_get=http_get, budget=budget)
    hits = (((fetched.data if fetched else None) or {})
            .get("hits", {}).get("hits", []))
    out: List[Dict] = []
    seen: Set[str] = set()
    for hit in hits[:2]:
        source = hit.get("_source") or {}
        if not any(name_appears_in(name, n)
                   for n in (source.get("display_names") or [])):
            continue
        url = source.get("_doc_url") or hit.get("_doc_url")
        if not url:
            continue
        doc = guarded_get_text(url, headers=headers, http_get=http_get,
                               budget=budget)
        if not doc or doc.status_code != 200:
            continue
        for address in _scoped_addresses(doc.text, name_tokens(name)[-1]):
            email = normalize_email(address)
            if email in seen or not _acceptable(email, attributed=True):
                continue
            seen.add(email)
            out.append(_found(email, url, "edgar", "Address in an SEC filing"))
    return out


# --------------------------------------------------------------------------
# Router
# --------------------------------------------------------------------------

_ENGINEER_RE = re.compile(
    r"\b(engineer|developer|swe|sde|programmer|cto|technical co-?founder|"
    r"infrastructure|backend|frontend|full.?stack|devops|sre|platform|"
    r"machine learning|data scientist|research engineer|open.?source|"
    r"maintainer|staff engineer|principal engineer|architect)\b", re.I)
_ACADEMIC_RE = re.compile(
    r"\b(professor|prof\.|phd|postdoc|research(er| scientist| fellow)?|"
    r"faculty|lecturer|department of|university|laborator(y|ies)|institute)\b",
    re.I)
_FINANCE_RE = re.compile(
    r"\b(goldman|morgan stanley|j\.?p\.? ?morgan|citadel|jane street|"
    r"blackstone|blackrock|evercore|lazard|managing director|"
    r"investment bank|equity research|portfolio manager|hedge fund|"
    r"private equity|trader|wealth management)\b", re.I)
_ACADEMIC_HOSTS = ("arxiv.org", "scholar.google", "semanticscholar.org",
                   "orcid.org")

MAX_SOURCES_PER_CANDIDATE = 2


def route_sources(*, role: Optional[str] = None, evidence_text: str = "",
                  urls: Sequence[str] = (), company_name: str = "",
                  company_domain: Optional[str] = None, notes: str = "",
                  edgar_enabled: bool = False) -> List[str]:
    """Which sources are worth spending budget on for this person.

    Running everything for everyone is what makes a source pipeline
    unaffordable. A managing director has no GitHub commits and a professor is
    not in SEC filings, so each gets only the sources their evidence justifies.
    """
    blob = " ".join([role or "", evidence_text or "", notes or "",
                     company_name or ""])
    urls = list(urls or [])
    chosen: List[str] = []

    if any(github_login_from_url(u) for u in urls) or _ENGINEER_RE.search(blob):
        chosen.append("github")
    academic_url = any(
        h in (urlparse(u or "").hostname or "") for u in urls
        for h in _ACADEMIC_HOSTS) or any(
        (urlparse(u or "").hostname or "").endswith((".edu", ".ac.uk"))
        for u in urls)
    if academic_url or (company_domain or "").endswith(".edu") \
            or _ACADEMIC_RE.search(blob):
        chosen.append("arxiv")
    if edgar_enabled and _FINANCE_RE.search(blob):
        chosen.append("edgar")
    return chosen[:MAX_SOURCES_PER_CANDIDATE]


SOURCE_FUNCS: Dict[str, Callable[..., List[Dict]]] = {
    "github": github_emails_for,
    "arxiv": arxiv_emails_for,
    "edgar": edgar_emails_for,
}
