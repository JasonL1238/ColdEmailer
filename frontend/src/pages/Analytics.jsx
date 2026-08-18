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
import { Button, Chip, EmptyState, Segmented, rateLabel } from '../ui'

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

/* What the "Typical wait" tile says underneath itself.

   "no replies yet" beside "Replied 5" is a flat contradiction, and it happens
   whenever a verified reply carries a timestamp that cannot produce a wait —
   clock skew, a Gmail message with no internalDate, a legacy import. Those
   replies are real; only their clocks are not. */
export function waitSub(wait, replied) {
  if (wait.count) {
    return `${wait.count} ${wait.count === 1 ? 'reply' : 'replies'} · `
      + `${humanHours(wait.p25)}–${humanHours(wait.p75)}`
  }
  if (replied > 0) {
    return `${replied} ${replied === 1 ? 'reply' : 'replies'}, none with a usable timestamp`
  }
  return 'no replies yet'
}

export default function Analytics() {
  const [days, setDays] = useState(90)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let live = true
    setData(null)
    setError(null)
    analyticsAPI.get(days)
      .then(({ data: d }) => { if (live) setData(d) })
      .catch((e) => { if (live) setError(errMessage(e, 'Could not load analytics')) })
    return () => { live = false }
  }, [days, reload])

  if (error) {
    // The window control stays: returning early removed the only thing that
    // triggers a refetch, so a single failed request stranded the page until
    // the user navigated away and back.
    return (
      <div className="page">
        <div className="page-head">
          <div>
            <div className="page-title">Analytics</div>
            <div className="page-desc">What earns replies — and what only looks like it does</div>
          </div>
          <Segmented value={days} onChange={setDays} options={WINDOWS} />
        </div>
        <div className="card">
          <EmptyState icon={AlertTriangle} title="Analytics unavailable" desc={error}
            action={<Button onClick={() => setReload((n) => n + 1)}>Try again</Button>} />
        </div>
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
              // Rendered, not recomputed. JS rounds halves up and Python
              // rounds them to even, so the same rows read 6.3% here and 6.2%
              // in the card directly below.
              sub={data.rate === null || data.rate === undefined
                ? 'too few to rate' : `${data.rate}%`}
              icon={MessageSquare} />
            <Headline label="Typical wait" value={humanHours(wait.median)}
              sub={waitSub(wait, data.replied)}
              icon={Clock} />
          </div>

          {data.unverified > 0 && (
            <div className="card card-pad card-warn row mb-16" style={{ gap: 10 }}>
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
                minSample={data.min_sample}
                dropped={(data.segments[key] || [])[0]?.dropped
                  ?? (data.segments[key]?.length === 0 ? data.sent : 0)} />
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
  const qualified = [
    ['email type', headline.email_type],
    ['who you write to', headline.email_kind],
  ].filter(([, v]) => v && v.best && v.worst)
  const picks = qualified.filter(([, v]) => v.spread > 0)
  // A tie between two well-sampled segments is a finding — "these are level" —
  // not an absence of evidence. Collapsing it into the sample-size sentence
  // told the user to go and collect data they already had.
  const level = qualified.filter(([, v]) => !(v.spread > 0))

  if (picks.length === 0 && level.length === 0) {
    return (
      <div className="card card-pad small muted mb-16">
        No comparison is solid yet. Two segments each need about {minSample} sent
        emails before a difference between them means anything.
      </div>
    )
  }
  if (picks.length === 0) {
    return (
      <div className="card card-pad stack mb-16" style={{ gap: 8 }}>
        {level.map(([what, v]) => (
          <div key={what} className="small">
            No measurable difference by {what} — {v.best.label} and{' '}
            {v.worst.label} both reply at <b>{v.best.rate}%</b>.
          </div>
        ))}
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

function SegmentCard({ title, rows, minSample, dropped = 0 }) {
  const shown = rows.slice(0, 8)
  // Scaled to what is drawn. Computing the ceiling over every rated row let an
  // off-screen 100% segment squash all eight visible bars into slivers, so the
  // card looked like nothing worked.
  const rated = shown.filter((r) => r.enough_data)
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
        <div className="tiny muted">
          {rows.length === 0 && dropped > 0
            ? `None of your ${dropped} sent emails could be attributed here.`
            : 'Nothing sent in this segment yet.'}
        </div>
      ) : shown.map((seg) => {
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
