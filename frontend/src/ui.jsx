/* Shared UI primitives + hooks */
import { useCallback, useEffect, useRef, useState } from 'react'
import { X } from 'lucide-react'
import toast from 'react-hot-toast'
import { companiesAPI, errMessage, jobsAPI } from './api'

export function Button({ variant = 'secondary', size, icon: Icon, children, className = '', ...props }) {
  return (
    <button
      className={`btn btn-${variant} ${size ? `btn-${size}` : ''} ${className}`}
      {...props}
    >
      {Icon && <Icon size={size === 'sm' ? 13 : 15} strokeWidth={2.2} />}
      {children}
    </button>
  )
}

export function Chip({ tone = 'gray', title, children }) {
  return <span className={`chip chip-${tone}`} title={title}>{children}</span>
}

export function Spinner() {
  return <div className="spinner" />
}

export function ProgressBar({ current, total, indeterminate }) {
  const pct = total > 0 ? Math.min(100, Math.round((current / total) * 100)) : 0
  return (
    <div className="progress">
      <div
        className={`progress-fill ${indeterminate ? 'indeterminate' : ''}`}
        style={{ width: `${indeterminate ? 30 : pct}%` }}
      />
    </div>
  )
}

export function Modal({ title, onClose, footer, children, large }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose?.()}>
      <div className={`modal ${large ? 'modal-lg' : ''}`}>
        <div className="modal-head">
          <div className="modal-title">{title}</div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X size={17} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

export function Drawer({ title, subtitle, onClose, children, actions }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <>
      <div className="drawer-overlay" onMouseDown={onClose} />
      <div className="drawer">
        <div className="drawer-head">
          <div>
            <div style={{ fontSize: 17, fontWeight: 700, letterSpacing: '-0.01em' }}>{title}</div>
            {subtitle && <div className="small muted mt-8" style={{ marginTop: 3 }}>{subtitle}</div>}
          </div>
          <div className="row">
            {actions}
            <button className="icon-btn" onClick={onClose} aria-label="Close">
              <X size={17} />
            </button>
          </div>
        </div>
        <div className="drawer-body">{children}</div>
      </div>
    </>
  )
}

export function EmptyState({ icon: Icon, title, desc, action }) {
  return (
    <div className="empty">
      {Icon && (
        <div className="empty-icon">
          <Icon size={21} />
        </div>
      )}
      <div className="empty-title">{title}</div>
      {desc && <div className="empty-desc">{desc}</div>}
      {action}
    </div>
  )
}

export function Segmented({ options, value, onChange }) {
  return (
    <div className="segmented">
      {options.map((o) => (
        <button key={o.value} className={value === o.value ? 'active' : ''} onClick={() => onChange(o.value)}>
          {o.label}
          {o.count != null && <span className="count">{o.count}</span>}
        </button>
      ))}
    </div>
  )
}

/* ---------- hooks ---------- */

/** Poll a background job until it reaches a terminal state.
 *  Gives up after `maxFailures` consecutive errors and reports a synthetic
 *  failed job, so a backend blip cannot leave the caller polling forever with
 *  a modal stuck open. */
export function useJobPolling(onUpdate, interval = 1500, maxFailures = 8) {
  const [job, setJob] = useState(null)
  const timer = useRef(null)
  const cbRef = useRef(onUpdate)
  cbRef.current = onUpdate

  const stop = useCallback(() => {
    if (timer.current) clearInterval(timer.current)
    timer.current = null
  }, [])

  const track = useCallback((initialJob) => {
    stop()
    setJob(initialJob)
    if (!initialJob?.id) return
    let failures = 0
    timer.current = setInterval(async () => {
      try {
        const { data } = await jobsAPI.get(initialJob.id)
        failures = 0
        setJob(data)
        cbRef.current?.(data)
        if (['done', 'failed', 'cancelled'].includes(data.status)) stop()
      } catch {
        if (++failures >= maxFailures) {
          stop()
          const lost = {
            ...initialJob,
            status: 'failed',
            error: 'Lost contact with the backend while tracking this job. '
              + 'It may still be running — reload to see the result.',
          }
          setJob(lost)
          cbRef.current?.(lost)
        }
      }
    }, interval)
  }, [interval, maxFailures, stop])

  useEffect(() => stop, [stop])
  return { job, track }
}

/** The company drawer, opened from Discover, Deep dive, and the Database.
 *
 *  Each page decides what else happens when a company is edited — reload a
 *  table, re-poll a run — but opening, re-reading after an edit, and closing
 *  behave the same on all three, so they live here. */
export function useCompanyDrawer() {
  const [company, setCompany] = useState(null)

  const open = useCallback(async (id) => {
    try {
      setCompany((await companiesAPI.get(id)).data)
    } catch (e) {
      toast.error(errMessage(e, 'Could not load company'))
    }
  }, [])

  const close = useCallback(() => setCompany(null), [])

  /** Re-read the open company after an edit inside the drawer. A row that has
   *  since been deleted closes it rather than leaving a stale copy on screen. */
  const refresh = useCallback(async () => {
    if (!company?.id) return
    try {
      setCompany((await companiesAPI.get(company.id)).data)
    } catch {
      setCompany(null)
    }
  }, [company?.id])

  return { company, open, close, refresh }
}

/** The caveats a reply check turned up, as a message suffix.
 *
 *  Cleared, confirmed, still-unverified, and failed checks are the honest part
 *  of the result — the headline count alone reads as "N replies" when some of
 *  those N were bounces the checker just retired. Every surface that runs a
 *  check reports them the same way. */
export function replyCheckNotes(data) {
  const notes = []
  if (data.cleared) notes.push(`${data.cleared} earlier "reply" was a bounce, an auto-reply, or your own message`)
  if (data.confirmed) notes.push(`${data.confirmed} earlier "reply" confirmed as real`)
  if (data.unverified_remaining) notes.push(`${data.unverified_remaining} still unverified`)
  if (data.failed_checks) notes.push(`${data.failed_checks} couldn't be checked — try again shortly`)
  return {
    suffix: notes.length ? ` (${notes.join('; ')})` : '',
    duration: notes.length ? 6000 : 4000,
  }
}

/* ---------- utils ---------- */

export function timeAgo(iso) {
  if (!iso) return ''
  const then = new Date(iso)
  if (isNaN(then)) return ''
  const sec = Math.floor((Date.now() - then.getTime()) / 1000)
  if (sec < 45) return 'just now'
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
  if (sec < 86400 * 30) return `${Math.floor(sec / 86400)}d ago`
  return then.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function fmtDate(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (isNaN(d)) return '—'
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export const EMAIL_TYPE_META = {
  application: { label: 'Application', tone: 'accent' },
  coffee_chat: { label: 'Coffee chat', tone: 'sky' },
  sales: { label: 'Sales', tone: 'green' },
  custom: { label: 'Custom', tone: 'violet' },
  follow_up: { label: 'Follow-up', tone: 'amber' },
}

const CONTACT_STATUS_META = {
  new: { label: 'New', tone: 'gray' },
  drafted: { label: 'Drafted', tone: 'accent' },
  sent: { label: 'Sent', tone: 'sky' },
  replied: { label: 'Replied', tone: 'green' },
  archived: { label: 'Archived', tone: 'gray' },
}

// A green "Replied" chip resting only on flags from the older reply check (which
// also counted bounces, auto-replies and our own messages) states something the
// app cannot back up. Show it as unverified until a real check confirms it.
export function contactStatusMeta(contact) {
  if (contact?.status === 'replied' && !contact.verified_reply_count
      && contact.unverified_reply_count > 0) {
    return {
      label: 'Replied?', tone: 'amber',
      title: 'Flagged by an older reply check that also counted bounces, '
        + 'auto-replies and your own messages — not verified against Gmail yet. '
        + 'Use "Re-verify replies" on the Emails page.',
    }
  }
  return CONTACT_STATUS_META[contact?.status] || CONTACT_STATUS_META.new
}

export function initials(name) {
  return (name || '?')
    .split(/\s+/)
    .map((w) => w[0])
    .filter(Boolean)
    .slice(0, 2)
    .join('')
    .toUpperCase()
}
