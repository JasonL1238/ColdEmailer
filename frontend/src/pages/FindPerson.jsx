import { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Building2, ExternalLink, History, UserSearch, X,
} from 'lucide-react'
import { personFinderAPI, errMessage } from '../api'
import {
  Button, Chip, EmptyState, ProgressBar, Spinner, timeAgo,
} from '../ui'
import { useApp } from '../App'

const CONFIDENCE_META = {
  strong: { tone: 'green', label: 'Strong match' },
  possible: { tone: 'amber', label: 'Possible match' },
  weak: { tone: 'gray', label: 'Weak match' },
}

const ORIGIN_META = {
  found: { tone: 'green', label: 'Found' },
  hunter: { tone: 'sky', label: 'Hunter' },
  guessed: { tone: 'amber', label: 'Guessed — note only' },
}

// Where a found address was actually published. The tooltip carries the
// attribution string, which is the evidence behind the claim.
const SOURCE_META = {
  github_commit: { tone: 'violet', label: 'GitHub commit' },
  arxiv:         { tone: 'sky',    label: 'arXiv paper' },
  edgar:         { tone: 'sky',    label: 'SEC filing' },
  personal_site: { tone: 'gray',   label: 'Personal site' },
  team_page:     { tone: 'gray',   label: 'Company page' },
  serp:          { tone: 'gray',   label: 'Search result' },
}

// A mailbox check answers "does mail land here", never "is this the person".
const SMTP_META = {
  deliverable: { tone: 'green', label: 'Mailbox exists',
    title: "A mail server accepted this address and rejected a random one on the same domain, so the mailbox is real. It does not prove the mailbox belongs to this person." },
  undeliverable: { tone: 'red', label: 'Rejected by server',
    title: 'The mail server refused this address.' },
  accept_all: { tone: 'gray', label: 'Domain accepts everything',
    title: 'This domain accepts mail for addresses that do not exist, so the check cannot tell you anything about this one.' },
  unknown: { tone: 'gray', label: 'Check inconclusive',
    title: 'No usable answer — greylisting, a timeout, or no verification transport available.' },
}

const DOMAIN_META = {
  company: { tone: 'gray', label: 'Company' },
  personal: { tone: 'violet', label: 'Personal' },
  other: { tone: 'gray', label: 'Other domain' },
}

function EmailRow({ entry, selected, disabled, onSelect }) {
  const origin = ORIGIN_META[entry.origin] || ORIGIN_META.found
  const domain = DOMAIN_META[entry.domain_kind] || DOMAIN_META.other
  const guessed = entry.origin === 'guessed'
  const smtpOk = entry.smtp_status === 'deliverable'
  return (
    <label
      className="row"
      style={{
        gap: 8, flexWrap: 'wrap', padding: '6px 8px', borderRadius: 8,
        background: selected ? 'var(--surface-2)' : 'transparent',
        cursor: (guessed && !smtpOk) || disabled ? 'default' : 'pointer',
        opacity: guessed && !smtpOk ? 0.75 : 1,
      }}
    >
      <input
        type="radio"
        checked={selected}
        disabled={(guessed && !smtpOk) || disabled}
        onChange={() => onSelect(entry.email)}
      />
      <span className="tiny mono">{entry.email}</span>
      <Chip tone={origin.tone}>{origin.label}</Chip>
      {entry.origin === 'found' && SOURCE_META[entry.source_kind] && (
        <Chip tone={SOURCE_META[entry.source_kind].tone}
          title={entry.attribution || undefined}>
          {SOURCE_META[entry.source_kind].label}
        </Chip>
      )}
      {SMTP_META[entry.smtp_status] && (
        <Chip tone={SMTP_META[entry.smtp_status].tone}
          title={SMTP_META[entry.smtp_status].title}>
          {SMTP_META[entry.smtp_status].label}
        </Chip>
      )}
      <Chip tone={domain.tone}>{domain.label}</Chip>
      {entry.email_verified && <Chip tone="green">Verified</Chip>}
      {!entry.email_verified && !guessed && !entry.email_person_match && (
        entry.attribution
          ? <Chip tone="amber" title={entry.attribution}>
              Name not in address — attributed by source
            </Chip>
          : <Chip tone="amber" title="The address does not contain this person's name — you must confirm it's really theirs before saving.">
              Doesn't match name
            </Chip>
      )}
      {entry.source_url && (
        <a href={entry.source_url} target="_blank" rel="noreferrer"
          className="tiny row" style={{ gap: 4 }}>
          Source <ExternalLink size={10} />
        </a>
      )}
    </label>
  )
}

function CandidateCard({ jobId, candidate, onApproved }) {
  const { navigate } = useApp()
  const conf = CONFIDENCE_META[candidate.confidence] || CONFIDENCE_META.weak
  const emails = candidate.emails || []
  const selectable = emails.filter(
    (e) => e.origin !== 'guessed' || e.smtp_status === 'deliverable')
  const [selectedEmail, setSelectedEmail] = useState(
    selectable.find((e) => e.email_verified)?.email
    || selectable.find((e) => e.origin !== 'guessed')?.email || null,
  )
  const [includeLinkedin, setIncludeLinkedin] = useState(true)
  const [confirmOwnership, setConfirmOwnership] = useState(false)
  const [approving, setApproving] = useState(false)
  const saved = candidate.approved_contact_id
  const selectedEntry = emails.find((e) => e.email === selectedEmail)
  const needsConfirm = Boolean(
    selectedEntry && (!selectedEntry.email_person_match
      || selectedEntry.origin === 'guessed'),
  )

  const approve = async () => {
    setApproving(true)
    try {
      const { data } = await personFinderAPI.approve(jobId, {
        candidate_id: candidate.id,
        email: selectedEmail || null,
        include_linkedin: includeLinkedin,
        confirm_email_ownership: confirmOwnership,
      })
      const name = data.contact?.name || 'Contact'
      toast.success(data.already_existed
        ? `${name} was already in your database`
        : `${name} saved to your database`)
      ;(data.conflicts || []).forEach((c) => toast.error(c))
      onApproved()
    } catch (e) {
      toast.error(errMessage(e, 'Could not save this contact'))
    } finally {
      setApproving(false)
    }
  }

  return (
    <div className="card card-pad" style={{ marginBottom: 10 }}>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 650 }}>{candidate.name || 'Unnamed'}</div>
        <Chip tone={conf.tone}>{conf.label}</Chip>
        {candidate.linkedin_url && (
          <a href={candidate.linkedin_url} target="_blank" rel="noreferrer"
            className="tiny row" style={{ gap: 4 }}>
            LinkedIn <ExternalLink size={10} />
          </a>
        )}
      </div>
      {candidate.role && (
        <div className="tiny muted" style={{ marginTop: 2 }}>{candidate.role}</div>
      )}

      {candidate.llm_summary && (
        <div className="tiny muted" style={{
          marginTop: 8, padding: '6px 10px', borderRadius: 8,
          background: 'var(--surface-2)',
        }}>
          <span style={{ fontWeight: 650 }}>Unverified — from the AI: </span>
          {candidate.llm_summary}
        </div>
      )}

      {(candidate.matched_signals || []).length > 0 && (
        <div className="stack" style={{ gap: 4, marginTop: 8 }}>
          {candidate.matched_signals.map((s, i) => (
            <div key={i} className="tiny row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <Chip tone="accent">{s.signal}</Chip>
              <span className="muted" style={{ minWidth: 0 }}>
                “{s.evidence}”
              </span>
              {s.source_url && (
                <a href={s.source_url} target="_blank" rel="noreferrer"
                  className="row" style={{ gap: 3 }}>
                  source <ExternalLink size={10} />
                </a>
              )}
            </div>
          ))}
        </div>
      )}
      {(candidate.conflicting_signals || []).map((s, i) => (
        <div key={i} className="tiny row" style={{ gap: 6, marginTop: 4 }}>
          <Chip tone="red">conflict: {s.signal}</Chip>
          <span className="muted">“{s.evidence}”</span>
        </div>
      ))}

      {emails.length > 0 && (
        <div className="stack" style={{ gap: 2, marginTop: 10 }}>
          {emails.map((e) => (
            <EmailRow
              key={e.email}
              entry={e}
              selected={selectedEmail === e.email}
              disabled={Boolean(saved)}
              onSelect={setSelectedEmail}
            />
          ))}
          {selectedEmail && !saved && (
            <button
              type="button"
              className="tiny muted"
              style={{ background: 'none', border: 0, cursor: 'pointer',
                textAlign: 'left', padding: '2px 8px' }}
              onClick={() => setSelectedEmail(null)}
            >
              Clear selection (save without an email)
            </button>
          )}
        </div>
      )}

      {saved ? (
        <div className="row" style={{ gap: 8, marginTop: 10 }}>
          <Chip tone="green">Saved to your database</Chip>
          <button
            type="button"
            className="tiny"
            style={{ background: 'none', border: 0, cursor: 'pointer',
              color: 'var(--accent)' }}
            onClick={() => navigate('database')}
          >
            Open Database
          </button>
        </div>
      ) : (
        <div className="row" style={{ gap: 12, marginTop: 10, flexWrap: 'wrap' }}>
          {candidate.linkedin_url && (
            <label className="row tiny" style={{ gap: 6, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={includeLinkedin}
                onChange={(e) => setIncludeLinkedin(e.target.checked)}
              />
              Include LinkedIn
            </label>
          )}
          {needsConfirm && (
            <label className="row tiny" style={{ gap: 6, cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={confirmOwnership}
                onChange={(e) => setConfirmOwnership(e.target.checked)}
              />
              {selectedEntry?.origin === 'guessed'
                ? "I confirmed this mailbox is theirs, not a namesake's"
                : 'I confirmed this address belongs to this person'}
            </label>
          )}
          <Button
            variant="primary"
            size="sm"
            disabled={approving || (needsConfirm && !confirmOwnership)}
            onClick={approve}
          >
            {approving ? 'Saving…' : 'Save as contact'}
          </Button>
        </div>
      )}
    </div>
  )
}

export default function FindPerson() {
  const [form, setForm] = useState({
    name: '', company_name: '', company_url: '', school: '',
    school_dates: '', past_companies: '', role_hint: '', location: '',
    notes: '',
  })
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))
  const [run, setRun] = useState(null)
  const [history, setHistory] = useState([])
  const [starting, setStarting] = useState(false)
  const pollRef = useRef(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = null
  }, [])

  const pollRun = useCallback((id) => {
    stopPolling()
    let failures = 0
    const tick = async () => {
      try {
        const { data } = await personFinderAPI.get(id)
        failures = 0
        setRun(data)
        if (['done', 'failed', 'cancelled'].includes(data.status)) {
          stopPolling()
          loadHistory({ resume: false })
          if (data.status === 'failed') {
            toast.error(data.error || 'Person search failed')
          }
        }
      } catch {
        if (++failures >= 10) {
          stopPolling()
          toast.error('Lost contact with the backend while tracking this search.')
        }
      }
    }
    tick()
    pollRef.current = setInterval(tick, 1500)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopPolling])

  const loadHistory = useCallback(async ({ resume = true } = {}) => {
    try {
      const { data } = await personFinderAPI.list()
      setHistory(data)
      if (resume) {
        const running = data.find((j) => j.status === 'running')
        if (running) pollRun(running.id)
      }
    } catch {
      /* keep prior UI */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pollRun])

  useEffect(() => {
    loadHistory()
    return stopPolling
  }, [loadHistory, stopPolling])

  const running = run?.status === 'running'

  const start = async () => {
    if (form.name.trim().split(/\s+/).length < 2) {
      toast.error("Enter the person's first and last name")
      return
    }
    setStarting(true)
    try {
      const body = {}
      Object.entries(form).forEach(([k, v]) => {
        body[k] = v.trim() || null
      })
      body.name = form.name.trim()
      const { data } = await personFinderAPI.start(body)
      setRun(data)
      pollRun(data.id)
    } catch (e) {
      toast.error(errMessage(e, 'Could not start the person search'))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!run?.id) return
    try {
      await personFinderAPI.cancel(run.id)
      toast('Cancelling…')
    } catch (e) {
      toast.error(errMessage(e, 'Could not cancel'))
    }
  }

  const openRun = async (id) => {
    try {
      const { data } = await personFinderAPI.get(id)
      setRun(data)
      if (data.status === 'running') pollRun(id)
    } catch (e) {
      toast.error(errMessage(e, 'Could not load that search'))
    }
  }

  const result = run?.status === 'done' ? (run.result || {}) : null
  const candidates = result?.candidates || []
  const disabled = running || starting

  return (
    <div className="page">
      <div className="discover-hero card" style={{ marginBottom: 18 }}>
        <div className="row" style={{ gap: 10, marginBottom: 6 }}>
          <UserSearch size={18} strokeWidth={2.2} style={{ color: 'var(--accent)' }} />
          <div className="tiny" style={{ fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            Find person
          </div>
        </div>
        <h1 className="hero-title">Find one specific person</h1>
        <p className="small muted" style={{ marginTop: 8, maxWidth: 640, lineHeight: 1.55 }}>
          Tell it what you know — name, company, school and rough years, past
          jobs — and it searches the public web, picks the right person among
          same-named strangers, and collects their email addresses for your
          review. Nothing is saved until you approve it.
        </p>

        <div className="stack" style={{ gap: 10, marginTop: 18 }}>
          <div className="discover-input-row" style={{ marginTop: 0 }}>
            <div className="discover-search-wrap">
              <UserSearch size={16} />
              <input
                className="discover-input"
                placeholder="Full name (required — e.g. Jane Doe)"
                value={form.name}
                onChange={(e) => set('name', e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !disabled && start()}
                disabled={disabled}
              />
            </div>
            <div className="discover-search-wrap" style={{ flex: 0.85 }}>
              <Building2 size={16} />
              <input
                className="discover-input"
                placeholder="Company (e.g. Stripe)"
                value={form.company_name}
                onChange={(e) => set('company_name', e.target.value)}
                disabled={disabled}
              />
            </div>
          </div>
          <div className="discover-input-row" style={{ marginTop: 0 }}>
            <input
              className="discover-input"
              style={{ paddingLeft: 16 }}
              placeholder="School (e.g. Northwestern)"
              value={form.school}
              onChange={(e) => set('school', e.target.value)}
              disabled={disabled}
            />
            <input
              className="discover-input"
              style={{ flex: 0.5, paddingLeft: 16 }}
              placeholder="Years there (e.g. 2018-2022)"
              value={form.school_dates}
              onChange={(e) => set('school_dates', e.target.value)}
              disabled={disabled}
            />
            <input
              className="discover-input"
              style={{ flex: 0.7, paddingLeft: 16 }}
              placeholder="Role, if known (e.g. VP Engineering)"
              value={form.role_hint}
              onChange={(e) => set('role_hint', e.target.value)}
              disabled={disabled}
            />
          </div>
          <div className="discover-input-row" style={{ marginTop: 0 }}>
            <input
              className="discover-input"
              style={{ paddingLeft: 16 }}
              placeholder="Past companies (comma-separated)"
              value={form.past_companies}
              onChange={(e) => set('past_companies', e.target.value)}
              disabled={disabled}
            />
            <input
              className="discover-input"
              style={{ flex: 0.5, paddingLeft: 16 }}
              placeholder="Location (optional)"
              value={form.location}
              onChange={(e) => set('location', e.target.value)}
              disabled={disabled}
            />
          </div>
          <textarea
            className="textarea"
            placeholder="Anything else you know about them (free text)"
            value={form.notes}
            maxLength={1000}
            onChange={(e) => set('notes', e.target.value)}
            disabled={disabled}
          />
          <div className="row" style={{ gap: 10, justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <Chip tone="gray">Hard stop 10 min</Chip>
            {running ? (
              <Button variant="ghost" icon={X} onClick={cancel}>Cancel</Button>
            ) : (
              <Button
                variant="primary"
                icon={UserSearch}
                onClick={start}
                disabled={starting}
              >
                {starting ? 'Starting…' : 'Find this person'}
              </Button>
            )}
          </div>
        </div>
      </div>

      {running && (
        <div className="card card-pad mb-16">
          <div className="row" style={{ gap: 8, marginBottom: 10 }}>
            <Spinner size={14} />
            <div style={{ fontWeight: 650 }}>{run.stage || 'Searching…'}</div>
          </div>
          <ProgressBar
            current={run.progress_current || 0}
            total={run.progress_total || 1}
          />
        </div>
      )}

      {result && (
        <div className="mb-16">
          <div className="row" style={{ gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
            <h2 style={{ fontSize: 15, fontWeight: 650, margin: 0 }}>
              Candidates for {result.query?.name}
            </h2>
            {result.timed_out && <Chip tone="amber">Stopped at 10-min limit</Chip>}
            {result.found_sources?.github_rate_limited && (
              <Chip tone="amber" title="GitHub throttled this run. Set GITHUB_TOKEN in .env to raise the limit from 60 to 5000 requests/hour.">
                GitHub rate-limited
              </Chip>
            )}
            {result.keyless && (
              <Chip tone="gray" title="No AI key configured — showing raw match signals instead of AI summaries.">
                AI summaries off
              </Chip>
            )}
          </div>
          {candidates.length === 0 ? (
            <EmptyState
              icon={UserSearch}
              title="No candidates found"
              desc="Try adding a company or school, or loosening the name spelling."
            />
          ) : (
            candidates.map((c) => (
              <CandidateCard
                key={c.id}
                jobId={run.id}
                candidate={c}
                onApproved={() => openRun(run.id)}
              />
            ))
          )}
        </div>
      )}

      {history.length > 0 && (
        <div>
          <div className="row" style={{ gap: 8, marginBottom: 8 }}>
            <History size={14} />
            <div className="tiny" style={{ fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
              Past searches
            </div>
          </div>
          <div className="stack" style={{ gap: 6 }}>
            {history.map((j) => (
              <button
                key={j.id}
                type="button"
                className="card card-pad row-between"
                style={{ padding: '8px 14px', cursor: 'pointer',
                  textAlign: 'left', font: 'inherit',
                  border: run?.id === j.id ? '1px solid var(--accent)' : undefined }}
                onClick={() => openRun(j.id)}
              >
                <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                  <span style={{ fontWeight: 600, fontSize: 13 }}>
                    {j.payload?.name || 'Unknown'}
                  </span>
                  {j.payload?.company_name && (
                    <span className="tiny muted">{j.payload.company_name}</span>
                  )}
                  <Chip tone={j.status === 'done' ? 'green'
                    : j.status === 'running' ? 'accent' : 'gray'}>
                    {j.status}
                  </Chip>
                </div>
                <span className="tiny muted">{timeAgo(j.created_at)}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
