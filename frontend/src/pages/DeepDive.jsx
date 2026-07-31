import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  AlertCircle, Building2, ExternalLink, History, Microscope, Search, Sparkles, Users, X,
} from 'lucide-react'
import { companiesAPI, deepResearchAPI, errMessage } from '../api'
import {
  Button, Chip, EmptyState, ProgressBar, Spinner, contactStatusMeta, timeAgo,
} from '../ui'
import { useApp } from '../App'
import CompanyDrawer from '../components/CompanyDrawer'
import ComposeModal from './ComposeModal'

const CRITERIA_SUGGESTIONS = [
  'Northwestern Alum',
  'Penn alumni, Wharton alumni',
  'VP Engineering, Engineering Manager, hiring manager',
  'Founders, CTO, Head of Product',
  'Talent / recruiting leaders, Head of People',
]

const EMAILED_STATUSES = new Set(['sent', 'replied'])

function isEmailed(contact) {
  return EMAILED_STATUSES.has(contact?.status)
    || Boolean(contact?.last_sent_at)
}

export default function DeepDive() {
  const { navigate } = useApp()
  const [companyName, setCompanyName] = useState('')
  const [url, setUrl] = useState('')
  const [criteria, setCriteria] = useState('')
  const [untilEnabled, setUntilEnabled] = useState(false)
  const [targetMatches, setTargetMatches] = useState(5)
  const [contactFilter, setContactFilter] = useState('all') // all | ready | emailed
  const [run, setRun] = useState(null)
  const [history, setHistory] = useState([])
  const [starting, setStarting] = useState(false)
  const [drawerCompany, setDrawerCompany] = useState(null)
  const [composeIds, setComposeIds] = useState(null)
  const [historyLoaded, setHistoryLoaded] = useState(false)
  const pollRef = useRef(null)
  const loadHistoryRef = useRef(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = null
  }, [])

  const openCompany = async (id) => {
    try {
      const { data } = await companiesAPI.get(id)
      setDrawerCompany(data)
    } catch (e) {
      toast.error(errMessage(e, 'Could not load company'))
    }
  }

  const pollRun = useCallback((id, { announce = true } = {}) => {
    stopPolling()
    let failures = 0
    let announced = false
    const tick = async () => {
      try {
        const { data } = await deepResearchAPI.get(id)
        failures = 0
        setRun(data)
        if (['done', 'failed', 'cancelled'].includes(data.status)) {
          stopPolling()
          loadHistoryRef.current?.()
          if (announce && !announced) {
            announced = true
            if (data.status === 'done') {
              const r = data.result || {}
              const saved = r.contacts_saved ?? r.contacts_selected ?? 0
              const matches = r.criteria_matches ?? 0
              const msg = `Deep dive ready — ${saved} contacts `
                + `(${matches} match your criteria)`
              if (r.identity_verified === false) {
                toast.error('Deep dive finished, but the site did not match this company.')
              } else if (
                r.timed_out
                && r.target_criteria_matches
                && r.target_met !== true
              ) {
                toast.error(
                  `Hit 30-minute limit with ${matches}/${r.target_criteria_matches}`
                  + ' criteria matches — partial results saved.',
                )
              } else if (r.timed_out) {
                // Hard-stop saved partial results — prefer that message over
                // floor-not-met, which is expected when time ran out.
                const floorNote = (
                  r.floor_required && !r.floor_met
                ) ? ' Contact floor not fully met.' : ''
                toast.success(
                  `${msg}. Stopped at the 30-minute limit.${floorNote}`,
                )
              } else if (
                r.target_criteria_matches
                && r.target_met === false
              ) {
                toast.error(
                  `${msg}. Target was ${r.target_criteria_matches} criteria matches — try Continue contacts.`,
                )
              } else if (r.alumni_mode && r.floor_required && !r.floor_met) {
                toast.error(
                  `${msg}. Alumni floor not met — try Continue contacts.`,
                )
              } else if (r.floor_required && !r.floor_met) {
                toast.error(`${msg}. Contact floor not met.`)
              } else if (r.floor_required && r.criteria_ratio_met === false) {
                toast.error(`${msg}. Fewer than 4 criteria matches.`)
              } else {
                toast.success(msg)
              }
            } else if (data.status === 'failed') {
              toast.error(data.error || 'Deep research failed')
            }
          }
        }
      } catch {
        if (++failures >= 10) {
          stopPolling()
          toast.error('Lost contact with the backend while tracking this deep dive.')
        }
      }
    }
    tick()
    pollRef.current = setInterval(tick, 2500)
  }, [stopPolling])

  const loadHistory = useCallback(async () => {
    try {
      const { data } = await deepResearchAPI.list()
      setHistory(data)
      const running = data.find((j) => j.status === 'running')
      if (running) pollRun(running.id)
    } catch {
      /* keep prior UI */
    } finally {
      setHistoryLoaded(true)
    }
  }, [pollRun])

  useEffect(() => {
    loadHistoryRef.current = loadHistory
  }, [loadHistory])

  useEffect(() => {
    loadHistory()
    return stopPolling
  }, [loadHistory, stopPolling])

  const running = run?.status === 'running'
  const payload = (() => {
    const p = run?.payload
    if (!p) return {}
    if (typeof p === 'string') {
      try { return JSON.parse(p) } catch { return {} }
    }
    return p
  })()
  // History list items omit `company`; always read ids from parsed result too.
  const result = (() => {
    if (run?.status !== 'done') return null
    const r = run?.result
    if (!r) return null
    if (typeof r === 'string') {
      try { return JSON.parse(r) } catch { return null }
    }
    return r
  })()
  const company = run?.company
  const companyId = result?.company_id || company?.id || payload?.company_id || null
  const resolvedName = companyName.trim()
    || result?.company_name
    || company?.name
    || payload?.company_name
    || ''

  const start = async (continueMode = null) => {
    // onClick={start} passes a click event — only real mode strings count.
    const mode = typeof continueMode === 'string' ? continueMode : null
    const name = resolvedName
    if (!mode && name.length < 2) {
      toast.error('Enter a company name')
      return
    }
    // Continue can use company_id from the last run, or resolve by name on
    // the backend when this company was deep-dived before.
    if (mode && !companyId && name.length < 2) {
      toast.error('Run a full deep dive before continuing')
      return
    }
    if (untilEnabled && !criteria.trim()) {
      toast.error('Add contact criteria before hunting until a target count')
      return
    }
    setStarting(true)
    setContactFilter('all')
    try {
      const target = Math.max(1, Math.min(50, Number(targetMatches) || 5))
      const body = {
        company_name: name || undefined,
        company_id: companyId || undefined,
        url: url.trim() || null,
        contact_criteria: criteria.trim(),
        min_contacts: untilEnabled ? target : 5,
      }
      // Research-only continue does not hunt contacts — don't attach a target.
      if (untilEnabled && mode !== 'research') {
        body.target_criteria_matches = target
      }
      if (mode) body.continue_mode = mode
      const { data } = await deepResearchAPI.start(body)
      setRun(data)
      if (name) setCompanyName(name)
      pollRun(data.id)
    } catch (e) {
      toast.error(errMessage(e, 'Could not start deep research'))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!run?.id) return
    try {
      await deepResearchAPI.cancel(run.id)
      toast.success('Cancelled')
      stopPolling()
      setRun((prev) => (prev ? { ...prev, status: 'cancelled', stage: 'Cancelled' } : prev))
      loadHistory()
    } catch (e) {
      toast.error(errMessage(e))
    }
  }

  // Prefer the job snapshot so history does not show a later run's intel.
  const intel = result?.deep_intel || company?.deep_intel || {}
  const hasRunContactFilter = Array.isArray(result?.contact_ids)
  const runContactIds = new Set(result?.contact_ids || [])
  const matchedIdSet = new Set(result?.matched_contact_ids || [])
  const otherIdSet = new Set(result?.other_contact_ids || [])
  const runContacts = hasRunContactFilter
    ? (company?.contacts || []).filter((c) => runContactIds.has(c.id))
    : []
  const matchedContacts = matchedIdSet.size
    ? runContacts.filter((c) => matchedIdSet.has(c.id))
    : runContacts.filter((c) => /criteria match/i.test(c.notes || ''))
  const otherContacts = otherIdSet.size
    ? runContacts.filter((c) => otherIdSet.has(c.id))
    : runContacts.filter((c) => !matchedContacts.some((m) => m.id === c.id))
  const contactCountLabel = result
    ? (result.contacts_saved ?? runContacts.length)
    : runContacts.length
  const canContinue = Boolean(companyId || resolvedName.length >= 2)

  const emailedCount = useMemo(
    () => runContacts.filter(isEmailed).length,
    [runContacts],
  )
  // "Ready" for compose = has email and not already emailed.
  const readyToEmail = useMemo(
    () => runContacts.filter((c) => c.email && !isEmailed(c)),
    [runContacts],
  )
  const readyCount = readyToEmail.length

  const readyIds = useMemo(
    () => new Set(readyToEmail.map((c) => c.id)),
    [readyToEmail],
  )
  const applyContactFilter = (list) => {
    if (contactFilter === 'emailed') return list.filter(isEmailed)
    if (contactFilter === 'ready') return list.filter((c) => readyIds.has(c.id))
    return list
  }
  const visibleMatched = applyContactFilter(matchedContacts)
  const visibleOther = applyContactFilter(otherContacts)

  const renderContactRow = (c, { criteriaHit } = {}) => {
    const st = contactStatusMeta(c)
    const emailed = isEmailed(c)
    return (
      <div key={c.id} className="card card-pad row-between" style={{ padding: '10px 14px' }}>
        <div style={{ minWidth: 0 }}>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <div style={{ fontWeight: 600, fontSize: 13 }}>{c.name || 'Unnamed'}</div>
            {criteriaHit && <Chip tone="violet">Criteria</Chip>}
            <Chip tone={st.tone} title={st.title}>{st.label}</Chip>
          </div>
          {c.role && <div className="tiny" style={{ marginTop: 2 }}>{c.role}</div>}
          <div className="row" style={{ gap: 10, marginTop: 4, flexWrap: 'wrap' }}>
            {c.email && <span className="tiny mono">{c.email}</span>}
            {c.linkedin_url && (
              <a href={c.linkedin_url} target="_blank" rel="noreferrer"
                className="tiny row" style={{ gap: 4 }}>
                LinkedIn <ExternalLink size={10} />
              </a>
            )}
            {emailed && c.last_sent_at && (
              <span className="tiny muted">Emailed {timeAgo(c.last_sent_at)}</span>
            )}
          </div>
        </div>
        <div className="row" style={{ gap: 4 }}>
          {c.email && (
            <button
              className="icon-btn"
              title={emailed ? 'Generate another email' : 'Generate email'}
              onClick={() => setComposeIds([c.id])}
            >
              <Sparkles size={14} />
            </button>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="page">
      <div className="discover-hero card" style={{ marginBottom: 18 }}>
        <div className="row" style={{ gap: 10, marginBottom: 6 }}>
          <Microscope size={18} strokeWidth={2.2} style={{ color: 'var(--accent)' }} />
          <div className="tiny" style={{ fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
            Deep dive
          </div>
        </div>
        <h1 style={{ fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', margin: 0 }}>
          Interview-grade company research
        </h1>
        <p className="small muted" style={{ marginTop: 8, maxWidth: 640, lineHeight: 1.55 }}>
          Dig into one company for key changes, policy differentiators, and contacts
          that match your criteria. Optionally keep hunting until you hit a target
          count of matches — every run hard-stops at 30 minutes.
        </p>

        <div className="stack" style={{ gap: 10, marginTop: 18 }}>
          <div className="discover-input-row" style={{ marginTop: 0 }}>
            <div className="discover-search-wrap">
              <Building2 size={16} />
              <input
                className="discover-input"
                placeholder="Company name (e.g. Stripe)"
                value={companyName}
                onChange={(e) => setCompanyName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !running && start()}
                disabled={running || starting}
              />
            </div>
            <input
              className="discover-input"
              style={{ flex: 0.85, paddingLeft: 16 }}
              placeholder="Website (optional)"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              disabled={running || starting}
            />
          </div>
          <div className="discover-search-wrap">
            <Search size={16} />
            <input
              className="discover-input"
              placeholder="Contact criteria — roles, schools, teams…"
              value={criteria}
              onChange={(e) => setCriteria(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !running && start()}
              disabled={running || starting}
            />
          </div>
          <div
            className="row"
            style={{
              gap: 12, flexWrap: 'wrap', alignItems: 'center',
              padding: '8px 12px', borderRadius: 10,
              background: 'var(--surface-2, rgba(0,0,0,0.03))',
            }}
          >
            <label className="row" style={{ gap: 8, cursor: running || starting ? 'default' : 'pointer' }}>
              <input
                type="checkbox"
                checked={untilEnabled}
                disabled={running || starting}
                onChange={(e) => setUntilEnabled(e.target.checked)}
              />
              <span className="small">Keep searching until I find</span>
            </label>
            <input
              type="number"
              min={1}
              max={50}
              value={targetMatches}
              disabled={running || starting || !untilEnabled}
              onChange={(e) => setTargetMatches(e.target.value)}
              style={{
                width: 64, padding: '4px 8px', borderRadius: 8,
                border: '1px solid var(--border)', font: 'inherit',
                opacity: untilEnabled ? 1 : 0.5,
              }}
            />
            <span className="small muted">people matching my criteria</span>
            <Chip tone="gray">Hard stop 30 min</Chip>
          </div>
          <div className="row" style={{ gap: 10, justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <div className="suggestion-row" style={{ marginTop: 0, flex: 1 }}>
              {CRITERIA_SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="suggestion"
                  disabled={running || starting}
                  onClick={() => setCriteria(s)}
                >
                  {s}
                </button>
              ))}
            </div>
            {running ? (
              <Button variant="ghost" icon={X} onClick={cancel}>Cancel</Button>
            ) : (
              <Button
                variant="primary"
                icon={Microscope}
                onClick={() => start()}
                disabled={starting}
              >
                {starting ? 'Starting…' : 'Deep research'}
              </Button>
            )}
          </div>
        </div>
      </div>

      {running && (
        <div className="card card-pad mb-16">
          <div className="row-between" style={{ marginBottom: 10 }}>
            <div className="row" style={{ gap: 8 }}>
              <Spinner size={14} />
              <div style={{ fontWeight: 650 }}>{run.stage || 'Researching…'}</div>
            </div>
            <div className="row" style={{ gap: 6 }}>
              {payload.target_criteria_matches && (
                <Chip tone="violet">Until {payload.target_criteria_matches} matches</Chip>
              )}
              <Chip tone="accent">≤30 min</Chip>
            </div>
          </div>
          <ProgressBar
            current={run.progress_current || 0}
            total={run.progress_total || 6}
          />
        </div>
      )}

      {run?.status === 'failed' && (
        <div className="card card-pad mb-16" style={{ borderColor: 'var(--red)' }}>
          <div className="row" style={{ gap: 8 }}>
            <AlertCircle size={16} style={{ color: 'var(--red)' }} />
            <div className="small">{run.error || 'Deep research failed'}</div>
          </div>
        </div>
      )}

      {result && (
        <div className="stack" style={{ gap: 16, marginBottom: 20 }}>
          <div className="row-between" style={{ flexWrap: 'wrap', gap: 10 }}>
            <div>
              <h2 style={{ margin: 0, fontSize: 20 }}>{result.company_name}</h2>
              <div className="small muted" style={{ marginTop: 4 }}>
                {result.contacts_saved ?? 0} contacts saved · {result.criteria_matches ?? 0} criteria matches
                {result.target_criteria_matches
                  ? ` · target ${result.target_criteria_matches}${result.target_met ? ' met' : ' not met'}`
                  : ''}
                {result.timed_out ? ' · stopped at 30 min' : ''}
                {result.alumni_mode ? ' · alumni mode' : ''}
                {result.employee_estimate != null
                  ? ` · ~${result.employee_estimate} employees estimated`
                  : ''}
                {result.floor_required && !result.floor_met ? ' · contact floor not fully met' : ''}
                {runContacts.length > 0
                  ? ` · ${emailedCount} emailed · ${runContacts.length - emailedCount} not emailed`
                  : ''}
              </div>
            </div>
            <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
              {companyId && (
                <Button size="sm" onClick={() => openCompany(companyId)}>
                  Open company
                </Button>
              )}
              <Button
                size="sm"
                disabled={running || starting || !canContinue}
                onClick={() => start('contacts')}
              >
                Continue contacts
              </Button>
              <Button
                size="sm"
                disabled={running || starting || !canContinue}
                onClick={() => start('research')}
              >
                Continue research
              </Button>
              <Button
                size="sm"
                disabled={running || starting || !canContinue}
                onClick={() => start('both')}
              >
                Continue both
              </Button>
              {runContacts.filter((c) => c.email).length > 0 && (
                <Button
                  size="sm"
                  variant="primary"
                  icon={Sparkles}
                  onClick={() => {
                    const ids = (
                      readyToEmail.length
                        ? readyToEmail
                        : runContacts.filter((c) => c.email)
                    ).map((c) => c.id)
                    setComposeIds(ids)
                  }}
                >
                  Generate emails{readyCount > 0 ? ` (${readyCount} ready)` : ''}
                </Button>
              )}
            </div>
          </div>

          {(intel.error || intel.last_error) ? (
            <div className="card card-pad" style={{ borderColor: 'var(--amber)' }}>
              <div className="row" style={{ gap: 8 }}>
                <AlertCircle size={16} style={{ color: 'var(--amber)' }} />
                <div className="small">{intel.error || intel.last_error}</div>
              </div>
            </div>
          ) : (
            <div className="deep-grid">
              <IntelBlock title="Key changes" items={intel.key_changes} />
              <IntelBlock title="Improvements" items={intel.improvements} />
              <IntelBlock title="Policy & culture" items={intel.policy_highlights} />
              <IntelBlock title="Differentiators" items={intel.differentiators} />
            </div>
          )}

          {(intel.talking_points || []).length > 0 && (
            <div className="card card-pad">
              <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
                Interview talking points
              </div>
              <ul className="deep-list">
                {intel.talking_points.map((t) => <li key={t}>{t}</li>)}
              </ul>
            </div>
          )}

          <div className="card card-pad">
            <div className="row-between" style={{ marginBottom: 10, flexWrap: 'wrap', gap: 8 }}>
              <div className="row" style={{ gap: 8 }}>
                <Users size={15} />
                <div style={{ fontWeight: 650 }}>
                  Contacts ({contactCountLabel})
                </div>
              </div>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                {[
                  ['all', `All (${runContacts.length})`],
                  ['ready', `Ready (${readyCount})`],
                  ['emailed', `Emailed (${emailedCount})`],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    className="suggestion"
                    style={{
                      opacity: contactFilter === key ? 1 : 0.65,
                      fontWeight: contactFilter === key ? 700 : 500,
                    }}
                    onClick={() => setContactFilter(key)}
                  >
                    {label}
                  </button>
                ))}
                <Chip tone={result.criteria_ratio_met ? 'green' : 'amber'}>
                  {result.criteria_matches ?? matchedContacts.length} match criteria
                  {' · '}
                  {otherContacts.length} other
                </Chip>
              </div>
            </div>
            {runContacts.length === 0 ? (
              <div className="small muted">No contacts saved from this run.</div>
            ) : visibleMatched.length === 0 && visibleOther.length === 0 ? (
              <div className="small muted">
                No contacts in this filter — try All or Ready.
              </div>
            ) : (
              <div className="stack" style={{ gap: 16 }}>
                <div>
                  <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
                    Criteria matches ({visibleMatched.length}
                    {contactFilter !== 'all' ? ` of ${matchedContacts.length}` : ''})
                  </div>
                  {matchedContacts.length === 0 ? (
                    <div className="small muted">No criteria matches yet — try Continue contacts.</div>
                  ) : visibleMatched.length === 0 ? (
                    <div className="small muted">No criteria matches in this filter.</div>
                  ) : (
                    <div className="stack" style={{ gap: 8 }}>
                      {visibleMatched.map((c) => renderContactRow(c, { criteriaHit: true }))}
                    </div>
                  )}
                </div>
                <div>
                  <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
                    Other contacts ({visibleOther.length}
                    {contactFilter !== 'all' ? ` of ${otherContacts.length}` : ''})
                  </div>
                  {otherContacts.length === 0 ? (
                    <div className="small muted">No additional company contacts in this run.</div>
                  ) : visibleOther.length === 0 ? (
                    <div className="small muted">No other contacts in this filter.</div>
                  ) : (
                    <div className="stack" style={{ gap: 8 }}>
                      {visibleOther.map((c) => renderContactRow(c))}
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {!running && !result && historyLoaded && history.length === 0 && (
        <EmptyState
          icon={Microscope}
          title="No deep dives yet"
          body="Pick a company and the kinds of people you want to reach. Reach will scrape their site, news, and public profiles for interview-ready intel."
        />
      )}

      {history.length > 0 && (
        <div>
          <div className="row" style={{ gap: 8, marginBottom: 10 }}>
            <History size={14} />
            <div style={{ fontWeight: 650, fontSize: 13.5 }}>Recent deep dives</div>
          </div>
          <div className="stack" style={{ gap: 8 }}>
            {history.slice(0, 12).map((j) => {
              const payload = typeof j.payload === 'string'
                ? (() => { try { return JSON.parse(j.payload) } catch { return {} } })()
                : (j.payload || {})
              const res = typeof j.result === 'string'
                ? (() => { try { return JSON.parse(j.result) } catch { return {} } })()
                : (j.result || {})
              return (
                <button
                  key={j.id}
                  type="button"
                  className="card card-pad row-between"
                  style={{ textAlign: 'left', cursor: 'pointer', border: 'none', font: 'inherit', color: 'inherit' }}
                  onClick={() => {
                    setRun(j)
                    setContactFilter('all')
                    const name = payload.company_name || res.company_name || ''
                    if (name) setCompanyName(name)
                    if (payload.contact_criteria) setCriteria(payload.contact_criteria)
                    else setCriteria('')
                    if (payload.target_criteria_matches) {
                      setUntilEnabled(true)
                      setTargetMatches(payload.target_criteria_matches)
                    } else {
                      setUntilEnabled(false)
                    }
                    // Always hydrate — list rows omit the company object that
                    // Continue needs for company_id.
                    if (j.status === 'running' || j.status === 'done') {
                      pollRun(j.id, { announce: false })
                    }
                  }}
                >
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                      {payload.company_name || res.company_name || 'Company'}
                    </div>
                    <div className="tiny muted" style={{ marginTop: 2 }}>
                      {payload.contact_criteria
                        ? `Criteria: ${payload.contact_criteria}`
                        : 'No criteria'}
                      {' · '}{timeAgo(j.created_at)}
                    </div>
                  </div>
                  <Chip tone={
                    j.status === 'done' ? 'green'
                      : j.status === 'running' ? 'accent'
                        : j.status === 'failed' ? 'red' : 'gray'
                  }>
                    {j.status}
                  </Chip>
                </button>
              )
            })}
          </div>
          <div className="tiny muted" style={{ marginTop: 12 }}>
            Results also land in{' '}
            <button type="button" className="linkish" onClick={() => navigate('database')}>
              Database
            </button>
            .
          </div>
        </div>
      )}

      {drawerCompany && (
        <CompanyDrawer
          company={drawerCompany}
          onClose={() => setDrawerCompany(null)}
          onChanged={async () => {
            if (drawerCompany?.id) openCompany(drawerCompany.id)
          }}
          onDeleted={() => setDrawerCompany(null)}
          onCompose={(ids) => setComposeIds(ids)}
        />
      )}
      {composeIds && (
        <ComposeModal
          contactIds={composeIds}
          onClose={() => setComposeIds(null)}
          onDone={() => setComposeIds(null)}
        />
      )}
    </div>
  )
}

function IntelBlock({ title, items }) {
  const list = Array.isArray(items) ? items : []
  return (
    <div className="card card-pad">
      <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 8 }}>
        {title}
      </div>
      {list.length === 0 ? (
        <div className="small muted">No grounded evidence found.</div>
      ) : (
        <ul className="deep-list">
          {list.map((item) => <li key={item}>{item}</li>)}
        </ul>
      )}
    </div>
  )
}
