/* Shared company detail drawer used by Discover and Database. */
import { useEffect, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  AlertTriangle, ExternalLink, Globe, RefreshCw, Sparkles, Trash2,
} from 'lucide-react'
import { companiesAPI, errMessage } from '../api'
import {
  Button, Chip, Drawer, Segmented, contactStatusMeta, EMAIL_TYPE_META, timeAgo,
} from '../ui'

export const SCRAPE_STATUS = {
  scraped: { label: 'Contacts found', tone: 'green' },
  pending: { label: 'Not researched', tone: 'gray' },
  scraping: { label: 'Researching…', tone: 'accent' },
  no_emails_found: { label: 'No contacts found', tone: 'amber' },
  no_website: { label: 'No website', tone: 'gray' },
  wrong_site: { label: 'Wrong site found', tone: 'amber' },
  scrape_failed: { label: 'Research failed', tone: 'red' },
}

export default function CompanyDrawer({
  company, onClose, onChanged, onDeleted, onCompose,
}) {
  const [enriching, setEnriching] = useState(false)
  const [mode, setMode] = useState('full') // full (default) | fast
  const refreshTimer = useRef(null)

  // Cancel the pending refresh if the drawer closes, or it would re-open the
  // drawer on top of whatever the user moved on to.
  useEffect(() => () => clearTimeout(refreshTimer.current), [])

  const enrich = async () => {
    setEnriching(true)
    try {
      await companiesAPI.enrich(company.id, mode)
      toast.success(
        mode === 'fast'
          ? 'Research started — fast mode (~1 min)'
          : 'Research started — full crawl (deeper, slower)',
      )
      clearTimeout(refreshTimer.current)
      refreshTimer.current = setTimeout(onChanged, mode === 'fast' ? 20000 : 45000)
    } catch (e) { toast.error(errMessage(e)) }
    finally { setEnriching(false) }
  }

  const del = async () => {
    if (!window.confirm(`Delete ${company.name} and all its contacts + emails?`)) return
    try { await companiesAPI.delete(company.id); toast.success('Company deleted'); onDeleted() }
    catch (e) {
      // 409 = the cascade would erase delivered emails (and the reply history
      // that stops those people being emailed twice). Name the count and make
      // the user confirm, the way the contact delete does.
      if (e?.response?.status === 409 && window.confirm(`${errMessage(e)}\n\nDelete anyway?`)) {
        try {
          await companiesAPI.delete(company.id, true)
          toast.success('Company and its email history deleted')
          onDeleted()
        } catch (e2) { toast.error(errMessage(e2)) }
      } else if (e?.response?.status !== 409) {
        toast.error(errMessage(e))
      }
    }
  }

  const st = SCRAPE_STATUS[company.scrape_status] || SCRAPE_STATUS.pending
  const emailContactIds = (company.contacts || []).filter((c) => c.email).map((c) => c.id)

  return (
    <Drawer
      title={company.name}
      subtitle={
        <span className="row" style={{ gap: 8 }}>
          {company.url && (
            <a href={company.url} target="_blank" rel="noreferrer" className="row" style={{ gap: 4 }}>
              <Globe size={11} /> {company.domain || company.url} <ExternalLink size={10} />
            </a>
          )}
          <Chip tone={st.tone}>{st.label}</Chip>
        </span>
      }
      onClose={onClose}
      actions={
        <>
          <Segmented
            value={mode}
            onChange={setMode}
            options={[
              { value: 'full', label: 'Full' },
              { value: 'fast', label: 'Fast' },
            ]}
          />
          <Button size="sm" icon={RefreshCw} onClick={enrich} disabled={enriching}>
            {company.scrape_status === 'scraped' ? 'Re-research' : 'Research'}
          </Button>
          {onDeleted && (
            <button className="icon-btn danger" title="Delete company" onClick={del}>
              <Trash2 size={15} />
            </button>
          )}
        </>
      }
    >
      {Array.isArray(company.scrape_warnings) && company.scrape_warnings.length > 0 && (
        <div className="card card-pad" style={{ borderColor: 'var(--amber)' }}>
          <div className="row" style={{ gap: 6, marginBottom: 6 }}>
            <AlertTriangle size={14} style={{ color: 'var(--amber)' }} />
            <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Contact conflicts
            </div>
          </div>
          <div className="stack" style={{ gap: 4 }}>
            {company.scrape_warnings.map((w) => (
              <div key={w} className="small">{w}</div>
            ))}
          </div>
        </div>
      )}
      {(company.deep_intel?.error || company.deep_intel?.last_error) && (
        <div className="card card-pad" style={{ borderColor: 'var(--amber)' }}>
          <div className="row" style={{ gap: 6, marginBottom: 4 }}>
            <AlertTriangle size={14} style={{ color: 'var(--amber)' }} />
            <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Deep dive issue
            </div>
          </div>
          <div className="small">
            {company.deep_intel.error || company.deep_intel.last_error}
          </div>
        </div>
      )}
      {company.summary ? (
        <div className="stack" style={{ gap: 10 }}>
          <Info label="What they do" value={company.summary} />
          <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
            {company.industry && <Chip tone="violet">{company.industry}</Chip>}
            {company.location && <Chip tone="gray">{company.location}</Chip>}
          </div>
          {company.product && <Info label="Product" value={company.product} />}
          {company.hook && <Info label="Personalization hook" value={company.hook} />}
          {company.recent_news && <Info label="Recent news" value={company.recent_news} />}
          {company.deep_intel && !company.deep_intel.error && (
            <div className="stack" style={{ gap: 10 }}>
              <DeepIntelList label="Key changes" items={company.deep_intel.key_changes} />
              <DeepIntelList label="Improvements" items={company.deep_intel.improvements} />
              <DeepIntelList label="Policy & culture" items={company.deep_intel.policy_highlights} />
              <DeepIntelList label="Talking points" items={company.deep_intel.talking_points} />
              {company.deep_intel.contact_criteria && (
                <Info label="Contact criteria used" value={company.deep_intel.contact_criteria} />
              )}
            </div>
          )}
          <div className="card card-pad" style={{ background: 'var(--surface-hover)' }}>
            <div className="row-between" style={{ gap: 8 }}>
              <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Research coverage
              </div>
              <Chip tone={company.research_quality === 'high' ? 'green'
                : company.research_quality === 'medium' ? 'sky' : 'amber'}>
                {company.research_quality || 'legacy'}
              </Chip>
            </div>
            <div className="small" style={{ marginTop: 6 }}>
              {company.pages_scraped || 0} page{company.pages_scraped === 1 ? '' : 's'} read
              {company.pages_attempted
                ? ` from ${company.pages_attempted} attempted`
                : ''}
            </div>
            {Array.isArray(company.research_sources) && company.research_sources.length > 0 && (
              <div className="stack" style={{ gap: 3, marginTop: 7 }}>
                {company.research_sources.slice(0, 8).map((source) => (
                  <a key={source} href={source} target="_blank" rel="noreferrer"
                    className="tiny row" style={{ gap: 4, overflowWrap: 'anywhere' }}>
                    <ExternalLink size={10} /> {source}
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="card card-pad small muted">
          Not researched yet. Hit <b>Research</b> to scrape their site for a description, hooks, and contacts.
        </div>
      )}

      {/* The model's guess at why this matched the search. Deliberately kept
          out of the research block and out of emails — it is not evidence. */}
      {company.discovery_note && (
        <div className="card card-pad" style={{ background: 'var(--surface-hover)' }}>
          <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>
            Why the search matched
          </div>
          <div className="small" style={{ lineHeight: 1.55 }}>{company.discovery_note}</div>
          <div className="tiny" style={{ marginTop: 6, color: 'var(--text-3)' }}>
            Unverified — from the AI that suggested this company, not from their
            site. Never quoted in your emails.
          </div>
        </div>
      )}

      <div>
        <div className="row-between mb-16" style={{ marginBottom: 8 }}>
          <div style={{ fontWeight: 650, fontSize: 13.5 }}>
            Contacts ({(company.contacts || []).length})
          </div>
          {onCompose && emailContactIds.length > 0 && (
            <Button size="sm" variant="primary" icon={Sparkles} onClick={() => onCompose(emailContactIds)}>
              Generate for all
            </Button>
          )}
        </div>
        {(company.contacts || []).length === 0 ? (
          <div className="small muted">No contacts yet. Research the company or wait for discovery to finish scraping.</div>
        ) : (
          <div className="stack" style={{ gap: 8 }}>
            {company.contacts.map((c) => {
              const cst = contactStatusMeta(c)
              return (
                <div key={c.id} className="card card-pad row-between" style={{ padding: '10px 14px' }}>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{c.name || 'Unnamed'}</div>
                    {c.email ? (
                      <div className="tiny mono">{c.email}</div>
                    ) : c.linkedin_url ? (
                      <a href={c.linkedin_url} target="_blank" rel="noreferrer"
                        className="tiny row" style={{ gap: 4 }}
                        onClick={(e) => e.stopPropagation()}>
                        LinkedIn profile <ExternalLink size={10} />
                      </a>
                    ) : null}
                    <div className="row" style={{ gap: 5, marginTop: 2 }}>
                      {c.role && <div className="tiny">{c.role}</div>}
                      {c.notes?.startsWith('Same-school match:') && (
                        <Chip tone="violet">Penn affinity</Chip>
                      )}
                      {c.linkedin_url && c.email && (
                        <a href={c.linkedin_url} target="_blank" rel="noreferrer"
                          className="tiny" onClick={(e) => e.stopPropagation()}>
                          LinkedIn
                        </a>
                      )}
                    </div>
                    {c.notes && (
                      <div className="tiny row" style={{ gap: 4, color: 'var(--amber)', marginTop: 2 }}>
                        <AlertTriangle size={11} /> {c.notes}
                      </div>
                    )}
                  </div>
                  <div className="row">
                    <Chip tone={cst.tone} title={cst.title}>{cst.label}</Chip>
                    {onCompose && c.email && (
                      <button className="icon-btn" title="Generate email" onClick={() => onCompose([c.id])}>
                        <Sparkles size={14} />
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {(company.emails || []).length > 0 && (
        <div>
          <div style={{ fontWeight: 650, fontSize: 13.5, marginBottom: 8 }}>
            Email history ({company.emails.length})
          </div>
          <div className="stack" style={{ gap: 8 }}>
            {company.emails.slice(0, 6).map((e) => {
              const tm = EMAIL_TYPE_META[e.email_type] || EMAIL_TYPE_META.custom
              return (
                <div key={e.id} className="card card-pad" style={{ padding: '10px 14px' }}>
                  <div className="row-between">
                    <div style={{ fontWeight: 600, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {e.subject}
                    </div>
                    <Chip tone={tm.tone}>{tm.label}</Chip>
                  </div>
                  <div className="tiny mt-8" style={{ marginTop: 4 }}>
                    to {e.contact_email} · {e.status}
                    {e.has_response
                      ? (e.response_verified_at ? ' · replied ✓' : ' · replied (unverified)')
                      : ''} · {timeAgo(e.created_at)}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </Drawer>
  )
}

function Info({ label, value }) {
  return (
    <div>
      <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>
        {label}
      </div>
      <div className="small" style={{ lineHeight: 1.55 }}>{value}</div>
    </div>
  )
}

function DeepIntelList({ label, items }) {
  if (!Array.isArray(items) || items.length === 0) return null
  return (
    <div>
      <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>
        {label}
      </div>
      <ul className="deep-list" style={{ margin: 0 }}>
        {items.map((item) => <li key={item}>{item}</li>)}
      </ul>
    </div>
  )
}
