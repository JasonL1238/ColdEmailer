/* Campaigns: which run of outreach these numbers came from.

   Without this every figure in the app is a lifetime total. "18% reply rate"
   across nine months of different searches, framings and seniorities is not
   something you can act on — the question is always *which batch worked*.

   A campaign is created by a discovery run and named from its query, so it is
   anchored to something that happened rather than to a label the user has to
   remember to apply. An opt-in tag would be blank on exactly the runs worth
   comparing later.

   Two things this page must not do. It must not rate a campaign the sample
   cannot support — a new campaign is always small, so this is the most
   tempting place in the app to render one reply out of three as 33%. And it
   must not let the campaign totals read as the whole database: everything
   from before campaigns existed is permanently unassigned, and it is shown
   rather than quietly left out. */
import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Layers, AlertTriangle, Archive, ArchiveRestore, Pencil, Check, X,
} from 'lucide-react'
import { campaignsAPI, errMessage } from '../api'
import { Button, Chip, EmptyState, timeAgo } from '../ui'
import { useApp } from '../App'

/* What a campaign is allowed to claim. Below the floor the backend sends
   `rate: null`, and this must not fall back to `rate ?? 0` — that renders a
   three-send campaign as a confident 0% beside a real one. */
export function rateLabel(c) {
  if (!c.enough_data || c.rate === null || c.rate === undefined) {
    return { text: `${c.replied}/${c.sent}`, muted: true }
  }
  return { text: `${c.rate}%`, muted: false }
}

export default function Campaigns() {
  const { navigate } = useApp()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [editing, setEditing] = useState(null)
  const [draftName, setDraftName] = useState('')

  const load = useCallback(() => {
    setError(null)
    campaignsAPI.list()
      .then(({ data: d }) => setData(d))
      .catch((e) => setError(errMessage(e, 'Could not load campaigns')))
  }, [])

  useEffect(() => { load() }, [load])

  const patch = async (id, updates) => {
    try {
      await campaignsAPI.update(id, updates)
      load()
    } catch (e) { toast.error(errMessage(e)) }
  }

  const saveName = async (id) => {
    const name = draftName.trim()
    setEditing(null)
    if (!name) return
    await patch(id, { name })
  }

  if (error) {
    return (
      <div className="page">
        <Head />
        <div className="card">
          <EmptyState icon={AlertTriangle} title="Campaigns unavailable" desc={error}
            action={<Button onClick={load}>Try again</Button>} />
        </div>
      </div>
    )
  }
  if (!data) {
    return <div className="page"><div className="skeleton" style={{ height: 380, marginTop: 40 }} /></div>
  }

  const { campaigns = [], unassigned = {}, headline = {} } = data
  // Companies count too. Discovery routinely creates companies whose scrape
  // finds no addresses, so a database can hold forty unassigned companies and
  // no unassigned contacts — and the block explaining them never appeared,
  // leaving the campaign cards accounting for none of them.
  const hasUnassigned = (unassigned.companies || 0) > 0
    || (unassigned.contacts || 0) > 0 || (unassigned.sent || 0) > 0

  return (
    <div className="page">
      <Head />

      {headline.best && headline.worst && (
        headline.spread > 0 ? (
          <div className="card card-pad small mb-16">
            <b>{headline.best.name}</b> replies at <b>{headline.best.rate}%</b> versus{' '}
            <b>{headline.worst.rate}%</b> for {headline.worst.name} — {headline.spread} points
            of difference.
          </div>
        ) : (
          <div className="card card-pad small mb-16">
            No measurable difference yet — {headline.best.name} and {headline.worst.name} both
            reply at <b>{headline.best.rate}%</b>.
          </div>
        )
      )}
      {!headline.best && campaigns.length > 0 && (
        <div className="card card-pad small muted mb-16">
          No comparison is solid yet. Two campaigns each need about {data.min_sample} sent
          emails before a difference between them means anything.
        </div>
      )}

      {campaigns.length === 0 ? (
        <div className="card">
          <EmptyState icon={Layers} title="No campaigns yet"
            desc="A campaign is created for you each time you run a search on Discover, and everything that search finds belongs to it."
            action={<Button variant="primary" onClick={() => navigate('discover')}>Go to Discover</Button>} />
        </div>
      ) : (
        <div className="stack" style={{ gap: 10 }}>
          {campaigns.map((c) => {
            const label = rateLabel(c)
            return (
              <div key={c.id} className={`card card-pad stack${c.archived_at ? ' campaign-archived' : ''}`}
                style={{ gap: 8 }}>
                <div className="row-between" style={{ gap: 10 }}>
                  {editing === c.id ? (
                    <span className="row" style={{ gap: 6, flex: 1, minWidth: 0 }}>
                      <input className="input" value={draftName} autoFocus
                        onChange={(e) => setDraftName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') saveName(c.id)
                          if (e.key === 'Escape') setEditing(null)
                        }} />
                      <button className="icon-btn" title="Save" onClick={() => saveName(c.id)}>
                        <Check size={14} />
                      </button>
                      <button className="icon-btn" title="Cancel" onClick={() => setEditing(null)}>
                        <X size={14} />
                      </button>
                    </span>
                  ) : (
                    <span style={{ minWidth: 0 }}>
                      <b className="small">{c.name}</b>
                      {c.archived_at && <Chip tone="gray" title="Set aside — left out of the comparison above.">archived</Chip>}
                      {c.query && c.query !== c.name && (
                        <div className="tiny muted" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          searched “{c.query}”
                        </div>
                      )}
                    </span>
                  )}
                  <span className="row" style={{ gap: 6, flexShrink: 0 }}>
                    <span className={`small ${label.muted ? 'muted' : ''}`}
                      title={label.muted
                        ? `${c.replied} of ${c.sent} replied — too few sends to state a rate`
                        : `${c.replied} of ${c.sent} replied`}>
                      {label.text}
                    </span>
                    {editing !== c.id && (
                      <button className="icon-btn" title="Rename"
                        onClick={() => { setEditing(c.id); setDraftName(c.name) }}>
                        <Pencil size={14} />
                      </button>
                    )}
                    <button className="icon-btn"
                      title={c.archived_at
                        ? 'Unarchive'
                        : 'Archive — keeps every number, just stops comparing it'}
                      onClick={() => patch(c.id, { archived: !c.archived_at })}>
                      {c.archived_at ? <ArchiveRestore size={14} /> : <Archive size={14} />}
                    </button>
                  </span>
                </div>

                <div className="row tiny muted" style={{ gap: 12, flexWrap: 'wrap' }}>
                  <span>{c.companies} compan{c.companies === 1 ? 'y' : 'ies'}</span>
                  <span>{c.contacts} contact{c.contacts === 1 ? '' : 's'}</span>
                  <span>{c.drafts} draft{c.drafts === 1 ? '' : 's'}</span>
                  <span>{c.sent} sent</span>
                  {c.bounced > 0 && <span style={{ color: 'var(--red)' }}>{c.bounced} bounced</span>}
                  {/* Named, not counted: an unverified flag came from the checker
                      that also counted bounces and the user's own messages. */}
                  {c.unverified > 0 && (
                    <span style={{ color: 'var(--amber)' }}
                      title="Flagged by an older reply check. Re-verify on the Emails page and they will start counting.">
                      {c.unverified} unverified
                    </span>
                  )}
                  {c.last_sent_at && <span>last sent {timeAgo(c.last_sent_at)}</span>}
                  {!c.enough_data && c.sent > 0 && (
                    <span title={`A rate needs about ${data.min_sample} sent emails.`}>
                      counts only
                    </span>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {hasUnassigned && (
        <div className="card card-pad stack mt-16" style={{ gap: 6 }}>
          <b className="small">Not in a campaign</b>
          <div className="tiny muted">
            {unassigned.companies} compan{unassigned.companies === 1 ? 'y' : 'ies'} ·{' '}
            {unassigned.contacts} contact{unassigned.contacts === 1 ? '' : 's'} ·{' '}
            {unassigned.sent} sent ·{' '}
            {unassigned.enough_data ? `${unassigned.rate}% replied` : `${unassigned.replied}/${unassigned.sent} replied`}
          </div>
          {/* This used to say "these predate campaigns", which is only half
              true and gets less true with use: a campaign is created by a
              Discover search, so anything added by hand, imported from CSV, or
              found by Deep Dive lands here too — today, not in the past.
              Naming both causes is the only version that stays accurate. */}
          <div className="tiny muted">
            Either they predate campaigns, or they came in a way that does not start
            one — added by hand, imported from a CSV, or found by Deep Dive. Nothing is
            assigned retroactively: guessing which campaign they would have belonged to
            would invent the thing this page reports.
          </div>
        </div>
      )}
    </div>
  )
}

function Head() {
  return (
    <div className="page-head">
      <div>
        <div className="page-title">Campaigns</div>
        <div className="page-desc">Which run of outreach these numbers came from</div>
      </div>
    </div>
  )
}
