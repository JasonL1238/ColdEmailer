import { useCallback, useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Search, Sparkles, X, ChevronRight, Globe, Users, AlertCircle, History, Compass,
} from 'lucide-react'
import { companiesAPI, discoveryAPI, errMessage } from '../api'
import { Button, Chip, EmptyState, ProgressBar, Segmented, Spinner, initials, timeAgo } from '../ui'
import { useApp } from '../App'
import CompanyDrawer, { SCRAPE_STATUS } from '../components/CompanyDrawer'
import ComposeModal from './ComposeModal'

const SUGGESTIONS = [
  'AI infrastructure startups in San Francisco',
  'Seed-stage fintech startups in New York',
  'Biotech companies working on gene therapy',
  'Developer tools companies hiring interns',
  'Climate tech startups with under 50 employees',
  'Robotics companies in Boston',
]

export default function Discover() {
  const { navigate } = useApp()
  const [query, setQuery] = useState('')
  const [count, setCount] = useState(10)
  const [mode, setMode] = useState('full')    // full (default) | fast
  const [run, setRun] = useState(null)         // active/last-viewed run (full detail)
  const [history, setHistory] = useState([])
  const [starting, setStarting] = useState(false)
  const [drawerCompany, setDrawerCompany] = useState(null)
  const [composeIds, setComposeIds] = useState(null)
  const pollRef = useRef(null)

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

  /** Watch a run. announce=false when merely opening a past search, so an
   *  old completed run doesn't fire a "search complete" toast on click. */
  const pollRun = useCallback((id, { announce = true } = {}) => {
    stopPolling()
    let failures = 0
    let announced = false
    const tick = async () => {
      try {
        const { data } = await discoveryAPI.get(id)
        failures = 0
        setRun(data)
        if (['done', 'failed', 'cancelled'].includes(data.status)) {
          stopPolling()
          loadHistoryRef.current?.()
          if (announce && !announced) {
            announced = true
            if (data.status === 'done') {
              const r = data.result || {}
              toast.success(`Found ${r.companies_added ?? 0} companies with ${r.contacts_added ?? 0} contacts`)
            } else if (data.status === 'failed') {
              toast.error(data.error || 'Discovery failed')
            }
          }
        }
      } catch {
        // Give up rather than polling a dead backend forever.
        if (++failures >= 10) {
          stopPolling()
          toast.error("Lost contact with the backend while tracking this search.")
        }
      }
    }
    tick()
    pollRef.current = setInterval(tick, 2000)
  }, [stopPolling])

  const [historyLoaded, setHistoryLoaded] = useState(false)

  const loadHistory = useCallback(async () => {
    try {
      const { data } = await discoveryAPI.list()
      setHistory(data)
      // resume watching a run that's still going (e.g. after page refresh)
      const running = data.find((j) => j.status === 'running')
      if (running) pollRun(running.id)
    } catch {
      /* backend down; the page keeps whatever it already showed */
    } finally {
      setHistoryLoaded(true)
    }
  }, [pollRun])

  // pollRun needs loadHistory, and loadHistory needs pollRun — break the cycle
  // with a ref instead of recreating both callbacks on every render.
  const loadHistoryRef = useRef(loadHistory)
  useEffect(() => { loadHistoryRef.current = loadHistory }, [loadHistory])

  useEffect(() => {
    loadHistory()
    return stopPolling
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const start = async (q) => {
    const searchQuery = (q ?? query).trim()
    if (!searchQuery) return
    const n = Math.min(100, Math.max(1, Math.round(Number(count) || 10)))
    setCount(n)
    setStarting(true)
    try {
      const { data } = await discoveryAPI.start(searchQuery, n, mode)
      setQuery(searchQuery)
      setRun(data)
      pollRun(data.id)
    } catch (e) {
      toast.error(errMessage(e, 'Could not start discovery'))
    } finally {
      setStarting(false)
    }
  }

  const cancel = async () => {
    if (!run) return
    try {
      await discoveryAPI.cancel(run.id)
      toast('Cancelling…', { icon: '✋' })
    } catch (e) {
      // 409 just means it finished on its own between render and click
      if (e?.response?.status !== 409) toast.error(errMessage(e))
    }
  }

  const isRunning = run?.status === 'running'
  const companies = run?.companies || []

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Discover</div>
          <div className="page-desc">Describe who you want to reach — AI finds real companies and scrapes their contact info</div>
        </div>
      </div>

      {/* hero search */}
      <div className="card discover-hero">
        <div className="hero-title" style={{ fontSize: 22 }}>
          <Sparkles size={16} style={{ color: 'var(--accent)', verticalAlign: -2, marginRight: 7 }} />
          What kind of companies are you looking for?
        </div>
        <div className="discover-input-row">
          <div className="discover-search-wrap">
            <Search size={17} />
            <input
              className="discover-input"
              placeholder="e.g. AI startups in NYC working on healthcare…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !isRunning && !starting && start()}
              disabled={isRunning}
            />
          </div>
          <label className="discover-count" title="How many companies to scrape (1–100)">
            <input
              className="discover-count-input"
              type="number"
              min={1}
              max={100}
              value={count}
              onChange={(e) => {
                const n = Number(e.target.value)
                if (!Number.isFinite(n)) return
                setCount(Math.min(100, Math.max(1, Math.round(n))))
              }}
              disabled={isRunning}
            />
            <span>companies</span>
          </label>
          <div className="discover-mode" title="Full crawls deeper; Fast stops once it finds a contact">
            <Segmented
              value={mode}
              onChange={(v) => !isRunning && setMode(v)}
              options={[
                { value: 'full', label: 'Full' },
                { value: 'fast', label: 'Fast' },
              ]}
            />
          </div>
          {isRunning ? (
            <Button variant="danger" icon={X} onClick={cancel}>Cancel</Button>
          ) : (
            <Button variant="primary" size="lg" icon={Search} onClick={() => start()} disabled={starting || !query.trim()}>
              {starting ? 'Starting…' : 'Search'}
            </Button>
          )}
        </div>
        {!isRunning && !companies.length && (
          <div className="suggestion-row">
            {SUGGESTIONS.map((s) => (
              <button key={s} className="suggestion" onClick={() => { setQuery(s); start(s) }}>{s}</button>
            ))}
          </div>
        )}
      </div>

      {/* live progress */}
      {run && (
        <div className="card card-pad mt-16">
          <div className="row-between">
            <div className="row">
              {isRunning && <Spinner />}
              <div>
                <div style={{ fontWeight: 650 }}>
                  {isRunning ? (run.stage || 'Working…') : run.status === 'done' ? 'Search complete'
                    : run.status === 'cancelled' ? 'Search cancelled' : 'Search failed'}
                </div>
                <div className="tiny">“{run.payload?.query}”</div>
              </div>
            </div>
            <div className="small muted">
              {run.progress_current}/{run.progress_total || '…'} companies
            </div>
          </div>
          <div className="mt-8">
            <ProgressBar
              current={run.progress_current}
              total={run.progress_total}
              indeterminate={isRunning && !run.progress_current}
            />
          </div>
          {run.status === 'failed' && (
            <div className="row mt-8 small" style={{ color: 'var(--red)' }}>
              <AlertCircle size={14} /> {run.error}
            </div>
          )}
        </div>
      )}

      {/* results grid */}
      {companies.length > 0 && (
        <>
          <div className="row-between mt-24 mb-16">
            <div>
              <div style={{ fontWeight: 700, fontSize: 15 }}>
                {companies.length} {companies.length === 1 ? 'company' : 'companies'} found
              </div>
              <div className="tiny muted" style={{ marginTop: 2 }}>
                Click a company to view research and contacts
              </div>
            </div>
            <Button variant="secondary" onClick={() => navigate('database')}>
              Open full Database <ChevronRight size={14} />
            </Button>
          </div>
          <div className="company-grid">
            {companies.map((c) => {
              const st = SCRAPE_STATUS[c.scrape_status] || SCRAPE_STATUS.pending
              return (
                <button
                  key={c.id}
                  type="button"
                  className="card company-card"
                  onClick={() => openCompany(c.id)}
                  style={{ textAlign: 'left', cursor: 'pointer', width: '100%' }}
                >
                  <div className="company-card-head">
                    <div className="company-favicon">{initials(c.name)}</div>
                    <div style={{ minWidth: 0 }}>
                      <div className="company-name">{c.name}</div>
                      <div className="company-domain row" style={{ gap: 4 }}>
                        {c.domain ? <><Globe size={10} /> {c.domain}</> : 'no website found'}
                      </div>
                    </div>
                  </div>
                  <div className="company-summary">{c.summary || <span className="tiny">No description scraped yet</span>}</div>
                  <div className="company-card-foot">
                    {c.industry && <Chip tone="violet">{c.industry}</Chip>}
                    <Chip tone={st.tone}>{st.label}</Chip>
                    <Chip tone={c.contact_count > 0 ? 'green' : 'gray'}>
                      <Users size={10} /> {c.contact_count} {c.contact_count === 1 ? 'contact' : 'contacts'}
                    </Chip>
                  </div>
                </button>
              )
            })}
          </div>
        </>
      )}

      {/* history + empty state */}
      {!run && historyLoaded && history.length === 0 && (
        <div className="card mt-16">
          <EmptyState
            icon={Compass}
            title="No searches yet"
            desc="Try a search like “fintech startups in NYC” — Reach will find real companies, scrape their sites, and pull out contact emails automatically."
          />
        </div>
      )}
      {history.length > 0 && (
        <div className="card mt-24">
          <div className="card-head">
            <div className="card-title row" style={{ gap: 7 }}><History size={14} /> Past searches</div>
          </div>
          <div>
            {history.slice(0, 8).map((j) => {
              const r = j.result || {}
              return (
                <div
                  key={j.id}
                  className="email-row"
                  onClick={() => { setRun(null); pollRun(j.id, { announce: false }) }}
                >
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="email-row-title">“{j.payload?.query}”</div>
                    <div className="email-row-sub">
                      {j.status === 'done'
                        ? `${r.companies_added ?? 0} companies · ${r.contacts_added ?? 0} contacts`
                        : j.status}
                      {' · '}{timeAgo(j.created_at)}
                    </div>
                  </div>
                  <Chip tone={j.status === 'done' ? 'green' : j.status === 'running' ? 'accent' : j.status === 'failed' ? 'red' : 'gray'}>
                    {j.status}
                  </Chip>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {drawerCompany && (
        <CompanyDrawer
          company={drawerCompany}
          onClose={() => setDrawerCompany(null)}
          onChanged={async () => {
            try { setDrawerCompany((await companiesAPI.get(drawerCompany.id)).data) }
            catch { setDrawerCompany(null) }
            if (run?.id) pollRun(run.id, { announce: false })
          }}
          onDeleted={() => {
            setDrawerCompany(null)
            if (run?.id) pollRun(run.id, { announce: false })
          }}
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
