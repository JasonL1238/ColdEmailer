import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Building2, Users, Send, MessageSquare, ChevronRight, Compass,
  Sparkles, RefreshCw, Clock, CheckCircle2, XCircle, Search, FileUp, Zap,
  AlertTriangle,
} from 'lucide-react'
import { dashboardAPI, emailsAPI, errMessage } from '../api'
import { Button, Chip, EmptyState, timeAgo, EMAIL_TYPE_META } from '../ui'
import { useApp } from '../App'

const EVENT_ICONS = {
  sent: { icon: Send, bg: 'var(--accent-soft)', color: 'var(--accent)' },
  replied: { icon: MessageSquare, bg: 'var(--green-soft)', color: 'var(--green)' },
  generated: { icon: Sparkles, bg: 'var(--violet-soft)', color: 'var(--violet)' },
  regenerated: { icon: RefreshCw, bg: 'var(--violet-soft)', color: 'var(--violet)' },
  follow_up_generated: { icon: Clock, bg: 'var(--amber-soft)', color: 'var(--amber)' },
  finished: { icon: CheckCircle2, bg: 'var(--sky-soft)', color: 'var(--sky)' },
  started: { icon: Compass, bg: 'var(--sky-soft)', color: 'var(--sky)' },
  send_failed: { icon: XCircle, bg: 'var(--red-soft)', color: 'var(--red)' },
  uploaded: { icon: FileUp, bg: 'var(--green-soft)', color: 'var(--green)' },
}

function eventText(ev) {
  const labels = {
    sent: 'Email sent', replied: 'Got a reply', generated: 'Email drafted',
    regenerated: 'Email regenerated', follow_up_generated: 'Follow-up drafted',
    started: 'Discovery started', finished: 'Discovery finished',
    send_failed: 'Send failed', uploaded: 'Resume uploaded',
    imported: 'Contacts imported', enriched: 'Company researched',
    migration: 'Data imported', deleted: 'Resume removed', repair: 'Data check',
  }
  return labels[ev.event] || ev.event
}

export default function Dashboard() {
  const { navigate, settings } = useApp()
  const [data, setData] = useState(null)
  const [checking, setChecking] = useState(false)

  const load = useCallback(async () => {
    try {
      const { data } = await dashboardAPI.get()
      setData(data)
    } catch (e) {
      toast.error(errMessage(e, 'Failed to load dashboard'))
    }
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 30000)
    return () => clearInterval(t)
  }, [load])

  const checkReplies = async (recheck = false) => {
    setChecking(true)
    try {
      const { data } = await emailsAPI.checkReplies(recheck)
      const notes = []
      if (data.cleared) notes.push(`${data.cleared} earlier "reply" was a bounce, an auto-reply, or your own message`)
      if (data.confirmed) notes.push(`${data.confirmed} earlier "reply" confirmed as real`)
      if (data.unverified_remaining) notes.push(`${data.unverified_remaining} still unverified`)
      if (data.failed_checks) notes.push(`${data.failed_checks} couldn't be checked — try again shortly`)
      const suffix = notes.length ? ` (${notes.join('; ')})` : ''
      toast.success(
        data.new_replies > 0
          ? `${data.new_replies} new ${data.new_replies === 1 ? 'reply' : 'replies'} found!${suffix}`
          : `Checked ${data.checked} emails — no new replies yet${suffix}`,
        { duration: notes.length ? 6000 : 4000 }
      )
      load()
    } catch (e) {
      toast.error(errMessage(e, 'Reply check failed'))
    } finally {
      setChecking(false)
    }
  }

  if (!data) {
    return (
      <div className="page">
        <div className="page-head"><div><div className="page-title">Dashboard</div></div></div>
        <div className="stat-grid">
          {[...Array(5)].map((_, i) => <div key={i} className="skeleton" style={{ height: 92 }} />)}
        </div>
        <div className="skeleton mt-16" style={{ height: 220 }} />
      </div>
    )
  }

  const { counts, timeline, by_type, follow_ups_due, recent_events, usage } = data
  const maxDay = Math.max(1, ...timeline.map((d) => Math.max(d.sent, d.replies)))
  const firstRun = counts.companies === 0 && counts.contacts === 0

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <div className="page-title">Dashboard</div>
          <div className="page-desc">Your outreach pipeline at a glance</div>
        </div>
        <div className="page-actions">
          <Button icon={RefreshCw} onClick={() => checkReplies(false)} disabled={checking}>
            {checking ? 'Checking…' : 'Check for replies'}
          </Button>
          <Button variant="primary" icon={Compass} onClick={() => navigate('discover')}>
            Discover companies
          </Button>
        </div>
      </div>

      {firstRun ? (
        <div className="card">
          <EmptyState
            icon={Zap}
            title="Welcome to Reach"
            desc="Find companies with AI search, scrape their info, and send tailored cold emails — all from one place. Start by discovering companies you want to reach."
            action={
              <Button variant="primary" icon={Search} onClick={() => navigate('discover')}>
                Find your first companies
              </Button>
            }
          />
        </div>
      ) : (
        <>
          {/* pipeline counts */}
          <div className="stat-grid">
            <StatCard icon={Building2} label="Companies" value={counts.companies}
              onClick={() => navigate('database')} />
            <StatCard icon={Users} label="Contacts" value={counts.contacts}
              onClick={() => navigate('database')} />
            <StatCard icon={Sparkles} label="Drafts ready" value={counts.drafts}
              onClick={() => navigate('emails')} />
            <StatCard icon={Send} label="Sent" value={counts.sent}
              sub={`${usage.emails_sent_today} today`} onClick={() => navigate('emails')} />
            <StatCard icon={MessageSquare} label="Reply rate" value={`${counts.reply_rate}%`}
              sub={counts.replied_unverified > 0
                ? `${counts.replied} verified · ${counts.replied_unverified} unverified`
                : `${counts.replied} replies`}
              accent={counts.replied > 0} />
          </div>

          {/* unverified legacy reply flags */}
          {counts.replied_unverified > 0 && (
            <div className="card card-pad mt-16 row-between" style={{ background: 'var(--amber-soft)', borderColor: '#f3dcb6' }}>
              <div className="row">
                <AlertTriangle size={17} style={{ color: 'var(--amber)' }} />
                <div>
                  <b>{counts.replied_unverified} earlier {counts.replied_unverified === 1 ? 'reply is' : 'replies are'} unverified.</b>{' '}
                  <span className="muted">
                    They were flagged by an older check that also counted bounces,
                    auto-replies and your own messages, so they are left out of the
                    reply rate — and out of the follow-ups-due count — until
                    re-checked against Gmail.
                  </span>
                </div>
              </div>
              <Button size="sm" icon={RefreshCw} onClick={() => checkReplies(true)} disabled={checking}>
                {checking ? 'Checking…' : 'Re-verify replies'}
              </Button>
            </div>
          )}

          {/* follow-ups banner */}
          {follow_ups_due > 0 && (
            <div className="card card-pad mt-16 row-between" style={{ background: 'var(--amber-soft)', borderColor: '#f3dcb6' }}>
              <div className="row">
                <Clock size={17} style={{ color: 'var(--amber)' }} />
                <div>
                  <b>{follow_ups_due} {follow_ups_due === 1 ? 'contact hasn’t' : 'contacts haven’t'} replied in over a week.</b>{' '}
                  <span className="muted">Following up doubles response rates.</span>
                </div>
              </div>
              <Button size="sm" onClick={() => navigate('emails')}>
                Review follow-ups <ChevronRight size={13} />
              </Button>
            </div>
          )}

          <div className="mt-16" style={{ display: 'grid', gridTemplateColumns: '1.6fr 1fr', gap: 14, alignItems: 'start' }}>
            {/* activity chart */}
            <div className="card">
              <div className="card-head">
                <div className="card-title">Last 30 days</div>
                <div className="row small muted">
                  <span className="row" style={{ gap: 5 }}>
                    <span style={{ width: 9, height: 9, borderRadius: 3, background: 'var(--accent)' }} /> sent
                  </span>
                  <span className="row" style={{ gap: 5 }}>
                    <span style={{ width: 9, height: 9, borderRadius: 3, background: '#22c55e' }} /> replies
                  </span>
                </div>
              </div>
              <div className="card-pad">
                <div className="chart-bars">
                  {timeline.map((d) => (
                    <div key={d.day} className="chart-col">
                      <div className="chart-tip">
                        {new Date(d.day + 'T00:00').toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                        {' · '}{d.sent} sent{d.replies > 0 ? ` · ${d.replies} replies` : ''}
                      </div>
                      {d.replies > 0 && (
                        <div className="bar-reply" style={{ height: `${(d.replies / maxDay) * 100}%` }} />
                      )}
                      <div className="bar-sent" style={{ height: `${Math.max(2, (d.sent / maxDay) * 100)}%`, opacity: d.sent === 0 ? 0.15 : undefined }} />
                    </div>
                  ))}
                </div>
                {/* by-type breakdown */}
                {by_type.length > 0 && (
                  <div className="row mt-16" style={{ flexWrap: 'wrap', gap: 8 }}>
                    {by_type.map((t) => {
                      const meta = EMAIL_TYPE_META[t.email_type] || { label: t.email_type, tone: 'gray' }
                      return (
                        <Chip key={t.email_type} tone={meta.tone}>
                          {meta.label}: {t.sent}/{t.total} sent
                          {t.replied > 0 ? ` · ${t.replied} replied` : ''}
                          {t.replied_unverified > 0 ? ` · ${t.replied_unverified} unverified` : ''}
                        </Chip>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* activity feed */}
            <div className="card">
              <div className="card-head"><div className="card-title">Recent activity</div></div>
              <div className="card-pad" style={{ paddingTop: 6, paddingBottom: 6 }}>
                {recent_events.length === 0 ? (
                  <div className="muted small" style={{ padding: '18px 0' }}>No activity yet.</div>
                ) : (
                  <div className="feed">
                    {recent_events.slice(0, 10).map((ev) => {
                      const meta = EVENT_ICONS[ev.event] || { icon: Zap, bg: '#f1f1ef', color: 'var(--text-2)' }
                      return (
                        <div key={ev.id} className="feed-item">
                          <div className="feed-dot" style={{ background: meta.bg, color: meta.color }}>
                            <meta.icon size={13} />
                          </div>
                          <div>
                            <div className="feed-text">
                              <b>{eventText(ev)}</b>
                              {ev.detail ? <span className="muted"> — {ev.detail.slice(0, 70)}</span> : null}
                            </div>
                            <div className="feed-time">{timeAgo(ev.created_at)}</div>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* usage */}
          <div className="card card-pad mt-16 row" style={{ gap: 26, flexWrap: 'wrap' }}>
            <UsageMeter label="Sends today" used={usage.emails_sent_today} limit={usage.daily_limit} />
            <UsageMeter label="Generations today" used={usage.emails_generated_today} limit={usage.generations_daily_limit} />
            <div className="tiny grow" style={{ textAlign: 'right' }}>
              AI provider: <b>{settings?.llm_provider || 'none — template mode'}</b>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function StatCard({ icon: Icon, label, value, sub, accent, onClick }) {
  return (
    <div
      className="card stat-card"
      onClick={onClick}
      style={onClick ? { cursor: 'pointer' } : undefined}
    >
      <div className="stat-label">
        <Icon size={14} style={{ color: accent ? 'var(--green)' : 'var(--text-3)' }} />
        {label}
      </div>
      <div className="stat-value" style={accent ? { color: 'var(--green)' } : undefined}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

function UsageMeter({ label, used, limit }) {
  const pct = limit > 0 ? Math.min(100, (used / limit) * 100) : 0
  return (
    <div style={{ minWidth: 180 }}>
      <div className="row-between tiny" style={{ marginBottom: 5 }}>
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span>{used} / {limit}</span>
      </div>
      <div className="progress" style={{ height: 5 }}>
        <div className="progress-fill" style={{ width: `${pct}%`, background: pct > 85 ? 'var(--amber)' : undefined }} />
      </div>
    </div>
  )
}
