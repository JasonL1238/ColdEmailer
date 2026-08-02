/* Analytics: which kind of email actually earns replies.

   The dashboard says how much went out and how much came back. This says
   which kind came back — the only thing that changes what you send next.

   The design constraint that shapes every component here: at this app's
   volumes most segments are a handful of sends, where one reply reads as 33%.
   The backend withholds a rate below its minimum sample; this page has to
   *show* that withholding rather than paper over it with a 0%, because a
   plausible number is more dangerous than a missing one. */
import { useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { BarChart3, MessageSquare, Clock, AlertTriangle } from 'lucide-react'
import { analyticsAPI, errMessage } from '../api'
import { Button, Chip, EmptyState, Segmented } from '../ui'

const WINDOWS = [
  { value: 30, label: '30 days' },
  { value: 90, label: '90 days' },
  { value: 365, label: '1 year' },
]

const SEGMENT_TITLES = {
  email_type: 'By email type',
  email_kind: 'By who you wrote to',
  seniority: 'By seniority',
  follow_up_step: 'By follow-up step',
  written_by: 'AI vs plain template',
  company: 'By company',
}

/* Hours into something a person reads without doing arithmetic. */
export function humanHours(hours) {
  if (hours === null || hours === undefined) return '—'
  if (hours < 1) return `${Math.round(hours * 60)} min`
  if (hours < 48) return `${Math.round(hours)}h`
  return `${Math.round(hours / 24)}d`
}

/* What a segment is allowed to claim.

   Below the sample floor the backend sends `rate: null`, and this must not
   fall back to `rate ?? 0` — that renders a thin segment as a confident 0%
   next to a real one, which is exactly the wrong comparison to invite. */
export function rateLabel(seg) {
  if (!seg.enough_data || seg.rate === null || seg.rate === undefined) {
    return { text: `${seg.replied}/${seg.sent}`, muted: true }
  }
  return { text: `${seg.rate}%`, muted: false }
}

export default function Analytics() {
  const [days, setDays] = useState(90)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    analyticsAPI.get(days)
      .then(({ data: d }) => { if (live) setData(d) })
      .catch((e) => { if (live) setError(errMessage(e, 'Could not load analytics')) })
    return () => { live = false }
  }, [days])

  if (error) {
    return (
      <div className="page">
        <div className="card"><EmptyState icon={AlertTriangle} title="Analytics unavailable" desc={error} /></div>
      </div>
    )
  }
  if (!data) {
    return <div className="page"><div className="skeleton" style={{ height: 420, marginTop: 40 }} /></div>
  }

  const wait = data.time_to_reply_hours

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Analytics</div>
          <div className="page-desc">What earns replies — and what only looks like it does</div>
        </div>
        <Segmented value={days} onChange={setDays} options={WINDOWS} />
      </div>

      {data.sent === 0 ? (
        <div className="card">
          <EmptyState icon={BarChart3} title="Nothing sent in this window"
            desc="Send some emails and this fills in. Reply rates need roughly ten sends per segment before they mean anything." />
        </div>
      ) : (
        <>
          <div className="row" style={{ gap: 14, flexWrap: 'wrap', marginBottom: 16 }}>
            <Headline label="Sent" value={data.sent} icon={BarChart3} />
            <Headline label="Replied" value={data.replied}
              sub={data.sent >= data.min_sample
                ? `${Math.round(data.replied / data.sent * 1000) / 10}%`
                : 'too few to rate'}
              icon={MessageSquare} />
            <Headline label="Typical wait" value={humanHours(wait.median)}
              sub={wait.count ? `${wait.count} replies · ${humanHours(wait.p25)}–${humanHours(wait.p75)}` : 'no replies yet'}
              icon={Clock} />
          </div>

          {data.unverified > 0 && (
            <div className="card card-pad row mb-16" style={{ background: 'var(--amber-soft)', borderColor: 'var(--amber-border)', gap: 10 }}>
              <AlertTriangle size={16} style={{ color: 'var(--amber)', flexShrink: 0 }} />
              <span className="small">
                <b>{data.unverified} reply flag{data.unverified === 1 ? '' : 's'} left out of every number here.</b>{' '}
                They came from an older check that also counted bounces, auto-replies
                and your own messages. Re-verify them on the Emails page and they
                will start counting.
              </span>
            </div>
          )}

          <Verdict headline={data.headline} minSample={data.min_sample} />

          <div className="analytics-grid">
            {Object.entries(SEGMENT_TITLES).map(([key, title]) => (
              <SegmentCard key={key} title={title} rows={data.segments[key] || []}
                minSample={data.min_sample} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function Headline({ label, value, sub, icon: Icon }) {
  return (
    <div className="card card-pad" style={{ flex: '1 1 180px' }}>
      <div className="row" style={{ gap: 8 }}>
        <Icon size={15} style={{ color: 'var(--text-2)' }} />
        <span className="tiny muted">{label}</span>
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4 }}>{value}</div>
      {sub && <div className="tiny muted">{sub}</div>}
    </div>
  )
}

/* The one sentence worth acting on, or nothing at all.

   The backend refuses to name a best/worst unless two segments each cleared
   the sample floor, so an absent verdict here means "not enough evidence yet",
   which is said out loud rather than left as blank space. */
function Verdict({ headline, minSample }) {
  const picks = [
    ['email type', headline.email_type],
    ['who you write to', headline.email_kind],
  ].filter(([, v]) => v && v.best && v.worst && v.spread > 0)

  if (picks.length === 0) {
    return (
      <div className="card card-pad small muted mb-16">
        No comparison is solid yet. Two segments each need about {minSample} sent
        emails before a difference between them means anything.
      </div>
    )
  }
  return (
    <div className="card card-pad stack mb-16" style={{ gap: 8 }}>
      {picks.map(([what, v]) => (
        <div key={what} className="small">
          <b>{v.best.label}</b> replies at <b>{v.best.rate}%</b> versus{' '}
          <b>{v.worst.rate}%</b> for {v.worst.label} — {v.spread} points of
          difference by {what}.
        </div>
      ))}
    </div>
  )
}

function SegmentCard({ title, rows, minSample }) {
  const rated = rows.filter((r) => r.enough_data)
  const ceiling = Math.max(1, ...rated.map((r) => r.rate || 0))

  return (
    <div className="card card-pad stack" style={{ gap: 10 }}>
      <div className="row-between">
        <b className="small">{title}</b>
        {rows.length > 0 && rated.length === 0 && (
          <Chip tone="gray" title={`A rate needs about ${minSample} sent emails. Until then the counts are the honest answer.`}>
            counts only
          </Chip>
        )}
      </div>
      {rows.length === 0 ? (
        <div className="tiny muted">Nothing sent in this segment yet.</div>
      ) : rows.slice(0, 8).map((seg) => {
        const label = rateLabel(seg)
        return (
          <div key={String(seg.key)} className="stack" style={{ gap: 3 }}>
            <div className="row-between">
              <span className="tiny" style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {seg.label}
              </span>
              <span className={`tiny ${label.muted ? 'muted' : ''}`}
                title={label.muted
                  ? `${seg.replied} of ${seg.sent} replied — too few sends to state a rate`
                  : `${seg.replied} of ${seg.sent} replied`}>
                {label.text}
              </span>
            </div>
            {/* Only a rated segment gets a bar. Drawing one from a null rate
                would render "no evidence" as a confident zero. */}
            <div className="seg-track">
              {seg.enough_data && (
                <div className="seg-fill" style={{ width: `${Math.round((seg.rate / ceiling) * 100)}%` }} />
              )}
            </div>
          </div>
        )
      })}
      {rows.length > 8 && (
        <div className="tiny muted">+{rows.length - 8} more</div>
      )}
    </div>
  )
}
