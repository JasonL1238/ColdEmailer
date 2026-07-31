import { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  AlertCircle, Building2, ExternalLink, History, Microscope, Search, Sparkles, Users, X,
} from 'lucide-react'
import { companiesAPI, deepResearchAPI, errMessage } from '../api'
import { Button, Chip, EmptyState, ProgressBar, Spinner, timeAgo } from '../ui'
import { useApp } from '../App'
import CompanyDrawer from '../components/CompanyDrawer'
import ComposeModal from './ComposeModal'

const CRITERIA_SUGGESTIONS = [
  'VP Engineering, Engineering Manager, hiring manager',
  'Founders, CTO, Head of Product',
  'Talent / recruiting leaders, Head of People',
  'Penn alumni, Wharton alumni',
  'Directors in AI / ML, research leads',
]

export default function DeepDive() {
  const { navigate } = useApp()
  const [companyName, setCompanyName] = useState('')
  const [url, setUrl] = useState('')
  const [criteria, setCriteria] = useState('')
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
              const msg = `Deep dive ready — ${saved} contacts `
                + `(${r.criteria_matches ?? 0} match your criteria)`
              if (r.identity_verified === false) {
                toast.error('Deep dive finished, but the site did not match this company.')
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

  const start = async () => {
    const name = companyName.trim()
    if (name.length < 2) {
      toast.error('Enter a company name')
      return
    }
    setStarting(true)
    try {
      const { data } = await deepResearchAPI.start({
        company_name: name,
        url: url.trim() || null,
        contact_criteria: criteria.trim(),
        min_contacts: 5,
      })
      setRun(data)
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

  const running = run?.status === 'running'
  const result = run?.status === 'done' ? (run.result || {}) : null
  const company = run?.company
  // Prefer the job snapshot so history does not show a later run's intel.
  const intel = result?.deep_intel || company?.deep_intel || {}
  const hasRunContactFilter = Array.isArray(result?.contact_ids)
  const runContactIds = new Set(result?.contact_ids || [])
  const runContacts = hasRunContactFilter
    ? (company?.contacts || []).filter((c) => runContactIds.has(c.id))
    : []
  const contactCountLabel = result
    ? (result.contacts_saved ?? runContacts.length)
    : runContacts.length

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
          that match your criteria. Aims for at least 5 people with LinkedIn or email
          when the company has 5+ employees — prioritizing your criteria.
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
              <Button variant="primary" icon={Microscope} onClick={start} disabled={starting}>
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
            <Chip tone="accent">Deep dive</Chip>
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
                {result.employee_estimate != null
                  ? ` · ~${result.employee_estimate} employees estimated`
                  : ''}
                {result.floor_required && !result.floor_met ? ' · contact floor not fully met' : ''}
              </div>
            </div>
            <div className="row" style={{ gap: 8 }}>
              {result.company_id && (
                <Button size="sm" onClick={() => openCompany(result.company_id)}>
                  Open company
                </Button>
              )}
              {runContacts.filter((c) => c.email).length > 0 && (
                <Button
                  size="sm"
                  variant="primary"
                  icon={Sparkles}
                  onClick={() => setComposeIds(
                    runContacts.filter((c) => c.email).map((c) => c.id),
                  )}
                >
                  Generate emails
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
            <div className="row-between" style={{ marginBottom: 10 }}>
              <div className="row" style={{ gap: 8 }}>
                <Users size={15} />
                <div style={{ fontWeight: 650 }}>
                  Contacts ({contactCountLabel})
                </div>
              </div>
              <Chip tone={result.criteria_ratio_met ? 'green' : 'amber'}>
                {result.criteria_matches}/{result.contacts_saved ?? 0} criteria
              </Chip>
            </div>
            {runContacts.length === 0 ? (
              <div className="small muted">No contacts saved from this run.</div>
            ) : (
              <div className="stack" style={{ gap: 8 }}>
                {runContacts.map((c) => {
                  const criteriaHit = (c.notes || '').toLowerCase().includes('criteria match')
                  return (
                    <div key={c.id} className="card card-pad row-between" style={{ padding: '10px 14px' }}>
                      <div style={{ minWidth: 0 }}>
                        <div className="row" style={{ gap: 8 }}>
                          <div style={{ fontWeight: 600, fontSize: 13 }}>{c.name || 'Unnamed'}</div>
                          {criteriaHit && <Chip tone="violet">Criteria</Chip>}
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
                        </div>
                      </div>
                      {c.email && (
                        <button
                          className="icon-btn"
                          title="Generate email"
                          onClick={() => setComposeIds([c.id])}
                        >
                          <Sparkles size={14} />
                        </button>
                      )}
                    </div>
                  )
                })}
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
                    if (j.status === 'running' || (j.status === 'done' && res.company_id)) {
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
