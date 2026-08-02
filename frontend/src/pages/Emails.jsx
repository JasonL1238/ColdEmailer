/* Emails: review drafts, edit, approve, send, and track replies */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Mail, Send, Trash2, RefreshCw, Check, Clock, Paperclip,
  AlertTriangle, MessageSquare, Sparkles, CornerUpLeft,
} from 'lucide-react'
import { emailsAPI, resumesAPI, sendWindowAPI, errMessage } from '../api'
import {
  Button, Chip, EmptyState, Modal, ProgressBar, Segmented, Spinner,
  timeAgo, useJobPolling, EMAIL_TYPE_META,
} from '../ui'
import { useApp } from '../App'

// Gmail already delivered this one. Legacy rows carry a message id while
// still labelled draft/approved/trashed; showing them in Drafts puts a live
// Send button on an email the recipient already has.
export const isDelivered = (e) => !!(e.gmail_message_id || e.sent_at)

// A message that reached Gmail without a confirmation coming back. Gmail may
// have queued it, so this is not a safe retry — never present it as one.
export const isUnconfirmed = (e) => !!e.send_attempted_at && !isDelivered(e)

/* Whether this email can still be changed at all — the single definition
   behind both the detail pane's buttons and the keyboard shortcuts.
   `isDelivered` alone is not it: a legacy row imported as status='sent'
   carries no Gmail id and no timestamp, and the detail pane has always hidden
   its Trash button. A shortcut gated on the narrower test offered to trash
   exactly those rows, and the backend then refused — leaving a "trashed"
   toast and an Undo button for something that never happened. */
export const isEditable = (e) => !(e.status === 'sent' || isDelivered(e))

// A reply flag the current checker has confirmed vs. one inherited from the old
// checker (which counted bounces, auto-replies and our own messages). Only the
// first may be stated as fact — including its date.
/* Why a draft came out of the plain template. "AI was unavailable" was the
   only thing the app could ever say, which tells you nothing about whether to
   wait, swap a key, or check your connection. */
const FALLBACK_REASONS = {
  llm_quota: 'Your AI quota is exhausted — this used the plain template. Wait for the reset, add billing, or switch provider in Settings.',
  llm_auth: 'Your AI provider rejected the API key — this used the plain template. Check the key in your .env.',
  llm_no_key: 'No AI provider is configured — this used the plain template. Add a key in your .env.',
  llm_no_model: 'None of the configured AI models are available — this used the plain template.',
  llm_network: 'Could not reach the AI provider — this used the plain template.',
  llm_empty: 'The AI returned nothing usable — this used the plain template.',
}
export const fallbackExplanation = (reason) =>
  FALLBACK_REASONS[reason] || 'AI was unavailable — this used the plain template.'

/* Warnings about who a draft is actually addressed to.

   'generic' is a shared inbox (info@, careers@) rather than a person.
   'named_unmatched' is worse and easy to miss: the address does not match the
   contact's name, so the body opens "Hi Jane," and arrives in bob.smith@'s
   mailbox. Every other layer treats that as unsendable — contact_ingest
   rejects it, discovery and enrich blank the address — so the only rows that
   still carry it are legacy ones, which is exactly where a silent chip would
   read as an all-clear. */
export const addressWarning = (e) => {
  if (e.bounced_at || e.contact_bounced_at) {
    return {
      label: 'bounced',
      title: 'The mail server rejected this address as undeliverable. Sending again cannot work, and repeated hard bounces cost the sending reputation your deliverable addresses rely on.',
    }
  }
  if (e.contact_email_kind === 'generic') {
    return { label: 'role inbox', title: 'A shared company inbox, not a person. These reply far less often.' }
  }
  if (e.contact_email_kind === 'named_unmatched') {
    return {
      label: "address doesn't match",
      title: `This address does not look like ${e.contact_name || 'this contact'}. The email greets them by name — check it is the right person.`,
    }
  }
  return null
}

/* "Tue 8:00am" — short enough for a button, specific enough that the user
   knows what they are agreeing to before mail leaves without them. */
export const whenLabel = (iso) => {
  if (!iso) return ''
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return ''
  const today = new Date()
  const sameDay = at.toDateString() === today.toDateString()
  const time = at.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  return sameDay ? time : `${at.toLocaleDateString([], { weekday: 'short' })} ${time}`
}

export const hasVerifiedReply = (e) => !!e.has_response && !e.reply_unverified
export const hasUnverifiedReply = (e) => !!(e.reply_unverified || e.contact_reply_unverified)

/* Where this person sits in the follow-up cadence, and therefore what the
   detail pane should offer.

   `has_follow_up` alone cannot answer it. That flag goes true the moment a
   follow-up exists and stays true after it is sent, so with a two-step cadence
   the second nudge could never be drafted: the button it lives behind had
   already been replaced by a "follow-up drafted" chip, permanently. Sent and
   pending are different facts here — one advances the cadence, the other is
   the user's own unsent work. */
export const DEFAULT_CADENCE = { enabled: true, steps: [7] }

export const followUpState = (email, cadence) => {
  // No cadence at all means "/api/settings has not come back yet", not
  // "switched off". Reading the two the same way blanked the follow-up control
  // on every sent email until the settings request resolved — and left it
  // blank for good against a backend too old to send the field.
  // A dead address outranks everything: the send path and both drafting
  // routes refuse it, so an enabled button here is one that can only ever
  // produce the same 409.
  if (email.bounced_at || email.contact_bounced_at) return { kind: 'bounced' }
  const c = cadence || DEFAULT_CADENCE
  const steps = (c.enabled && c.steps?.length) ? c.steps.length : 0
  if (!steps) return { kind: 'off' }
  // A row carrying neither new field came from a backend that predates them;
  // there, `has_follow_up` is the only thing that exists and "a follow-up
  // exists" is the safe reading. Keyed on both fields being absent, because a
  // row that has them and simply says "one sent, none pending" is the ordinary
  // case for rung 2 — treating that as pending is what blocked the whole
  // feature.
  const legacyRow = email.follow_up_pending === undefined
    && email.follow_ups_sent === undefined
  if (email.follow_up_pending || (legacyRow && email.has_follow_up)) {
    return { kind: 'pending' }
  }
  const sent = Number(email.follow_ups_sent || 0)
  if (sent >= steps) return { kind: 'done', sent, total: steps }
  return { kind: 'ready', step: sent + 1, total: steps }
}

/* Keyboard review.

   Reviewing forty drafts is forty round trips to the mouse, and the thing
   that actually takes the time is moving between them, not deciding.

   Two rules decide what is on this list and what is not.

   **Nothing irreversible gets a key.** There is no send shortcut and there
   never should be: every other action here can be undone from the UI, and a
   single keystroke that mails a stranger cannot. Trash is the closest call,
   and it is a status change that Trash → Restore reverses — plus the toast
   carries an Undo, because a key you can hold down needs one.

   **The editor owns the keyboard whenever it has focus.** Otherwise typing
   "x" into a body trashes the draft you were writing. */
export function shouldIgnoreShortcut(event, { modalOpen } = {}) {
  if (modalOpen) return true
  // Cmd/Ctrl/Alt combinations belong to the browser and the OS — swallowing
  // Cmd+A or Cmd+E to mean "approve" or "edit" would be theft.
  if (event.metaKey || event.ctrlKey || event.altKey) return true
  const el = event.target
  const tag = el?.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
    || !!el?.isContentEditable
}

export const SHORTCUTS = [
  ['j  /  ↓', 'Next draft'],
  ['k  /  ↑', 'Previous draft'],
  ['e', 'Edit the body'],
  ['Esc', 'Leave the editor'],
  ['a', 'Approve'],
  ['x', 'Trash (undoable)'],
  ['?', 'This list'],
]

export default function Emails() {
  const { navigate, settings } = useApp()
  const [emails, setEmails] = useState(null)
  const [followUps, setFollowUps] = useState([])
  const [tab, setTab] = useState('drafts')
  const [activeId, setActiveId] = useState(null)
  const [selected, setSelected] = useState(new Set())
  const [sendModal, setSendModal] = useState(null)   // list of email ids to send
  const [busy, setBusy] = useState(false)
  const [editorRev, setEditorRev] = useState(0)      // forces the editor to reload its text
  const [showKeys, setShowKeys] = useState(false)
  // Set by the open editor whenever it holds unsaved changes, so navigating
  // away can warn instead of silently discarding the user's writing.
  const pendingEdits = useRef(false)
  const bodyRef = useRef(null)
  // The detail pane publishes its own approve here, so the key and the button
  // are one code path rather than two that can drift apart.
  const approveRef = useRef(null)
  const trashing = useRef(new Set())

  const confirmDiscard = useCallback(() => {
    if (!pendingEdits.current) return true
    const ok = window.confirm('You have unsaved changes to this email. Discard them?')
    if (ok) pendingEdits.current = false
    return ok
  }, [])

  const load = useCallback(async () => {
    try {
      const [em, fu] = await Promise.all([emailsAPI.list(), emailsAPI.followUps()])
      setEmails(em.data)
      setFollowUps(fu.data)
    } catch (e) {
      toast.error(errMessage(e, 'Failed to load emails'))
      setEmails([])
    }
  }, [])

  useEffect(() => { load() }, [load])

  const groups = useMemo(() => {
    const all = emails || []
    return {
      drafts: all.filter((e) => (e.status === 'draft' || e.status === 'approved')
        && !isDelivered(e)),
      sent: all.filter((e) => e.status === 'sent'
        || (isDelivered(e) && e.status !== 'trashed')),
      trashed: all.filter((e) => e.status === 'trashed'),
    }
  }, [emails])

  const queued = useMemo(
    () => (emails || [])
      .filter((e) => e.scheduled_at && !isDelivered(e) && e.status !== 'trashed')
      .sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at)),
    [emails])

  const visible = groups[tab] || []
  const active = visible.find((e) => e.id === activeId) || visible[0] || null

  useEffect(() => {
    // keep selection within the visible tab
    setSelected((prev) => new Set([...prev].filter((id) => visible.some((e) => e.id === id))))
    if (active) setActiveId(active.id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, emails])

  const toggle = (id, e) => {
    e.stopPropagation()
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const patchLocal = (id, updates) =>
    setEmails((prev) => prev.map((e) => (e.id === id ? { ...e, ...updates } : e)))

  const setStatus = async (ids, status, keepSelection = false, quiet = false) => {
    try {
      const { data } = await emailsAPI.bulkStatus(ids, status)
      // The backend refuses to change already-sent emails, so trust its count
      // rather than claiming every selected email changed.
      const updated = data?.updated ?? ids.length
      if (updated > 0) load()
      // Acting on one email from the detail pane must not wipe a multi-select
      // the user built up in the list.
      if (!keepSelection) setSelected(new Set())
      else setSelected((prev) => new Set([...prev].filter((id) => !ids.includes(id))))
      // `quiet` is for callers that say it better themselves — the keyboard
      // trash replaces this with a toast carrying an Undo, and two toasts for
      // one keystroke is noise.
      if (updated < ids.length) {
        toast(`${updated} of ${ids.length} updated — sent emails can't be changed`, { icon: 'ℹ️' })
      } else if (status === 'trashed' && !quiet) {
        toast.success(`${updated === 1 ? 'Email' : `${updated} emails`} trashed`)
      }
    } catch (e) { toast.error(errMessage(e)) }
  }

  const deleteEmails = async (ids) => {
    if (!window.confirm(`Permanently delete ${ids.length} email(s)?`)) return
    try {
      const { data } = await emailsAPI.bulkDelete(ids)
      const deleted = data?.deleted ?? ids.length
      const protectedSent = data?.protected_sent ?? 0
      // Reload rather than filtering locally: sent emails are kept on purpose.
      load()
      setSelected(new Set())
      if (protectedSent > 0) {
        toast(`Deleted ${deleted}. Kept ${protectedSent} already-delivered email${protectedSent === 1 ? '' : 's'} — that record prevents double-emailing.`,
          { icon: 'ℹ️', duration: 6000 })
      } else {
        toast.success(`Deleted ${deleted} email${deleted === 1 ? '' : 's'}`)
      }
    } catch (e) { toast.error(errMessage(e)) }
  }

  const regenerate = async (id) => {
    setBusy(true)
    try {
      const { data } = await emailsAPI.regenerate(id)
      patchLocal(id, data)
      // EmailDetail holds the subject/body in its own state, seeded on mount.
      // Bump the key so it remounts on the new text — otherwise the editor
      // keeps showing the old draft and Save would overwrite the rewrite.
      setEditorRev((r) => r + 1)
      toast.success('Rewritten with fresh AI output')
    } catch (e) { toast.error(errMessage(e)) }
    finally { setBusy(false) }
  }

  const cancelSchedule = async (ids) => {
    try {
      const { data } = await emailsAPI.unschedule(ids)
      load()
      toast.success(`${data.cleared} taken out of the send queue`)
    } catch (e) { toast.error(errMessage(e)) }
  }

  /* Trash, with a way back.

     A key you can hold down needs an undo that is not "switch tabs and go
     find it". The restore is the same status change the Trash tab's own
     button makes, so there is one code path for coming back. */
  const trashWithUndo = async (email) => {
    // One trash per press, even if a second keystroke lands before the reload.
    // Without this, repeats fired duplicate requests against the same id and
    // stacked an Undo toast for each — all but one a no-op to dismiss.
    if (trashing.current.has(email.id)) return
    trashing.current.add(email.id)
    // What it was before, so Undo is an undo. Restoring everything to 'draft'
    // quietly demoted an approved email that had already been reviewed, which
    // is a second, invisible change the user never asked for.
    const was = email.status === 'approved' ? 'approved' : 'draft'
    try {
      await setStatus([email.id], 'trashed', true, true)
    } finally {
      trashing.current.delete(email.id)
    }
    const who = email.contact_name || email.contact_email || 'Draft'
    toast((t) => (
      <span className="row" style={{ gap: 10 }}>
        {who} trashed
        <button type="button" className="linkish tiny" onClick={() => {
          toast.dismiss(t.id)
          setStatus([email.id], was, true, true)
        }}>Undo</button>
      </span>
    ), { duration: 6000 })
  }

  const makeFollowUp = async (emailId) => {
    setBusy(true)
    try {
      await emailsAPI.generateFollowUp(emailId)
      toast.success('Follow-up drafted — find it in Drafts')
      load()
      setTab('drafts')
    } catch (e) { toast.error(errMessage(e)) }
    finally { setBusy(false) }
  }

  /* The keyboard.

     Held in a ref and refreshed every render, so the handler always sees
     current state without the window listener being torn down and re-added on
     each keystroke — and without the [] deps that would have frozen `active`
     at the first draft forever. */
  const onKeyRef = useRef(null)
  onKeyRef.current = (event) => {
    if (event.key === '?' && !shouldIgnoreShortcut(event, { modalOpen: !!sendModal })) {
      event.preventDefault()
      setShowKeys((open) => !open)
      return
    }
    // Escape belongs to whatever is in front: the editor first, then the help.
    if (event.key === 'Escape') {
      if (document.activeElement === bodyRef.current) {
        bodyRef.current?.blur()
        return
      }
      if (showKeys) setShowKeys(false)
      return
    }
    if (showKeys || shouldIgnoreShortcut(event, { modalOpen: !!sendModal })) return
    if (!active) return

    const index = visible.findIndex((e) => e.id === active.id)
    const move = (delta) => {
      const next = visible[index + delta]
      // Stop at the ends rather than wrapping: on a review list, wrapping
      // silently starts you over and you re-read what you just cleared.
      if (!next || !confirmDiscard()) return
      setActiveId(next.id)
      // Optional call: scrolling is a nicety, and an environment without it
      // must not turn "move to the next draft" into a thrown error.
      document.querySelector(`[data-email-row="${next.id}"]`)
        ?.scrollIntoView?.({ block: 'nearest' })
    }

    // Caps Lock changes event.key to 'X', not the letter the user thinks they
    // pressed, and every shortcut silently stopped working with no way to
    // tell why. Only single characters are folded — 'ArrowDown' and 'Escape'
    // must keep their case.
    const pressed = event.key.length === 1 ? event.key.toLowerCase() : event.key

    switch (pressed) {
      case 'j': case 'ArrowDown': event.preventDefault(); move(1); break
      case 'k': case 'ArrowUp': event.preventDefault(); move(-1); break
      case 'e':
        if (bodyRef.current) { event.preventDefault(); bodyRef.current.focus() }
        break
      case 'a':
        // Only where the button exists. Approving a sent email is meaningless
        // and the backend refuses it, so the key must not offer it either.
        //
        // Routed through the detail pane's own approve, not through a second
        // status call: that function folds unsaved edits into the approval,
        // and the shortcut used to skip it — marking a draft ready to send
        // while the sentence the user had just typed stayed unsaved.
        if (!event.repeat && isEditable(active) && active.status === 'draft') {
          event.preventDefault()
          approveRef.current?.()
        }
        break
      case 'x':
        // `event.repeat` is the difference between one trash and forty. A held
        // key fires at ~30Hz, and because the list reloads after each one the
        // cursor lands on whatever is now at the top — so the repeats chew
        // back through drafts the user had already reviewed and kept.
        if (!event.repeat && isEditable(active) && active.status !== 'trashed') {
          event.preventDefault()
          // Pin the successor from the pre-trash list. Otherwise `active`
          // falls back to `visible[0]` once the row disappears, which sends
          // the cursor to the top of the list rather than forward.
          const after = visible[index + 1] || visible[index - 1] || null
          setActiveId(after ? after.id : null)
          trashWithUndo(active)
        }
        break
      default: break
    }
  }

  useEffect(() => {
    const onKey = (event) => onKeyRef.current?.(event)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // "3 on step 1, 2 on step 2" — which rung the due contacts are on. Worth
  // saying out loud: a step-3 batch is the last thing these people will hear
  // from you, which is not a thing to press a button on inattentively.
  const dueStepSummary = useMemo(() => {
    const counts = new Map()
    for (const f of followUps) {
      const step = f.next_follow_up_step
      if (step) counts.set(step, (counts.get(step) || 0) + 1)
    }
    if (counts.size === 0) return ''
    if (counts.size === 1) {
      const [step] = [...counts.keys()]
      const total = followUps[0]?.follow_up_steps_total
      return total > 1 ? `step ${step} of ${total}` : ''
    }
    return [...counts.entries()].sort((a, b) => a[0] - b[0])
      .map(([step, n]) => `${n} on step ${step}`).join(', ')
  }, [followUps])

  const { job: draftJob, track: trackDraftJob } = useJobPolling((j) => {
    if (j.status === 'done' || j.status === 'cancelled') {
      const r = j.result || {}
      const skipped = r.skipped ?? 0
      if (skipped > 0) {
        // Say what was skipped rather than reporting a clean run: the reasons
        // are things the user needs to know about (someone replied, an address
        // bounced, the generation quota ran out mid-batch).
        toast(`Drafted ${r.drafted ?? 0}. Skipped ${skipped}: ${
          [...new Set((r.notes || []).map((n) => n.error))].slice(0, 2).join('; ')}`,
        { icon: 'ℹ️', duration: 8000 })
      } else {
        toast.success(`${r.drafted ?? 0} follow-up${r.drafted === 1 ? '' : 's'} drafted`)
      }
      setBusy(false)
      load()
      setTab('drafts')
    } else if (j.status === 'failed') {
      toast.error(j.error || 'Drafting follow-ups failed')
      setBusy(false)
    }
  })

  const draftAllDue = async () => {
    setBusy(true)
    try {
      const { data } = await emailsAPI.draftAllFollowUps()
      trackDraftJob(data)
    } catch (e) {
      toast.error(errMessage(e))
      setBusy(false)
    }
  }

  const stopDrafting = async () => {
    if (!draftJob?.id) return
    try {
      await emailsAPI.cancelDraftFollowUps(draftJob.id)
      // Whatever was already written stays in Drafts, and the job records the
      // real count on the cancelled row, so let the poller deliver it.
      toast('Stopping after the current contact…', { icon: '✋' })
    } catch (e) { toast.error(errMessage(e)) }
  }

  const checkReplies = async (recheck = false) => {
    setBusy(true)
    try {
      const { data } = await emailsAPI.checkReplies(recheck)
      const notes = []
      if (data.cleared) notes.push(`${data.cleared} earlier "reply" was a bounce, an auto-reply, or your own message`)
      if (data.confirmed) notes.push(`${data.confirmed} earlier "reply" confirmed as real`)
      if (data.unverified_remaining) notes.push(`${data.unverified_remaining} still unverified`)
      if (data.failed_checks) notes.push(`${data.failed_checks} couldn't be checked — try again shortly`)
      const suffix = notes.length ? ` (${notes.join('; ')})` : ''
      toast.success(data.new_replies > 0
        ? `${data.new_replies} new ${data.new_replies === 1 ? 'reply' : 'replies'}!${suffix}`
        : `No new replies yet${suffix}`,
        { duration: notes.length ? 6000 : 4000 })
      load()
    } catch (e) { toast.error(errMessage(e)) }
    finally { setBusy(false) }
  }

  const unverifiedCount = (emails || []).filter((e) => e.reply_unverified).length

  if (emails === null) {
    return <div className="page"><div className="skeleton" style={{ height: 400, marginTop: 40 }} /></div>
  }

  return (
    <div className="page wide">
      <div className="page-head">
        <div>
          <div className="page-title">Emails</div>
          <div className="page-desc">Review AI drafts, approve, send, and track replies</div>
        </div>
        <div className="page-actions">
          <Button icon={MessageSquare} onClick={() => checkReplies(false)} disabled={busy}>
            Check replies
          </Button>
          {unverifiedCount > 0 && (
            <Button icon={RefreshCw} onClick={() => checkReplies(true)} disabled={busy}
              title="Re-check every reply flag against Gmail, including the ones marked before the current checker existed">
              Re-verify replies
            </Button>
          )}
          {tab === 'drafts' && selected.size > 0 && (
            <Button variant="primary" icon={Send} onClick={() => setSendModal([...selected])}>
              Send {selected.size} selected
            </Button>
          )}
        </div>
      </div>

      {unverifiedCount > 0 && (
        <div className="card card-pad row-between mb-16" style={{ background: 'var(--amber-soft)', borderColor: 'var(--amber-border)' }}>
          <div className="row">
            <AlertTriangle size={16} style={{ color: 'var(--amber)' }} />
            <span className="small">
              <b>{unverifiedCount} {unverifiedCount === 1 ? 'email is' : 'emails are'} marked
              as replied by an older reply check</b> that also counted bounces, auto-replies
              and your own messages in the thread. Those flags — and their dates — are shown
              as unverified until re-checked against Gmail, and they are left out of the
              follow-ups-due list (you can still draft a follow-up from any sent email).
            </span>
          </div>
          <Button size="sm" icon={RefreshCw} onClick={() => checkReplies(true)} disabled={busy}>
            Re-verify replies
          </Button>
        </div>
      )}

      {/* Mail that will leave on its own. The one thing in this app that
          happens without a click deserves to be visible and stoppable from
          the screen where the user would look for it. */}
      {queued.length > 0 && tab !== 'trashed' && (
        <div className="card card-pad row-between mb-16" style={{ background: 'var(--sky-soft, var(--amber-soft))', borderColor: 'var(--border)' }}>
          <div className="row">
            <Clock size={16} style={{ color: 'var(--text-2)' }} />
            <span className="small">
              <b>{queued.length} email{queued.length === 1 ? '' : 's'}</b> queued to
              send {whenLabel(queued[0].scheduled_at)} without further confirmation.
            </span>
          </div>
          <Button size="sm" onClick={() => cancelSchedule(queued.map((e) => e.id))}>
            Cancel {queued.length === 1 ? 'it' : 'them all'}
          </Button>
        </div>
      )}

      {followUps.length > 0 && tab !== 'trashed' && (
        <div className="card card-pad row-between mb-16" style={{ background: 'var(--amber-soft)', borderColor: 'var(--amber-border)' }}>
          <div className="row">
            <Clock size={16} style={{ color: 'var(--amber)' }} />
            <span className="small">
              <b>{followUps.length} {followUps.length === 1 ? 'contact is' : 'contacts are'}</b> due
              for their next follow-up{dueStepSummary ? ` (${dueStepSummary})` : ''}.
              Drafting writes them all; nothing is sent until you send it.
            </span>
          </div>
          <div className="row">
            <Button size="sm" onClick={() => setTab('sent')}>View sent</Button>
            {draftJob?.status === 'running' ? (
              <>
                <span className="tiny muted">
                  Drafting {draftJob.progress_current ?? 0}/{draftJob.progress_total ?? followUps.length}…
                </span>
                <Button size="sm" onClick={stopDrafting}>Stop</Button>
              </>
            ) : (
              <Button size="sm" variant="primary" icon={CornerUpLeft} disabled={busy}
                onClick={draftAllDue}>
                Draft {followUps.length} follow-up{followUps.length === 1 ? '' : 's'}
              </Button>
            )}
          </div>
        </div>
      )}

      <div className="row-between mb-16">
        <Segmented
          value={tab}
          onChange={(t) => { if (confirmDiscard()) setTab(t) }}
          options={[
            { value: 'drafts', label: 'Drafts', count: groups.drafts.length },
            { value: 'sent', label: 'Sent', count: groups.sent.length },
            { value: 'trashed', label: 'Trash', count: groups.trashed.length },
          ]}
        />
        {tab === 'drafts' && visible.length > 0 && (
          <div className="row small muted">
            {/* A shortcut nobody knows about is not a feature. */}
            <button type="button" className="linkish tiny" onClick={() => setShowKeys(true)}
              title="Keyboard shortcuts">
              <kbd className="kbd">?</kbd> keys
            </button>
            <label className="row" style={{ gap: 6, cursor: 'pointer' }}>
              <input type="checkbox" className="checkbox"
                checked={visible.length > 0 && visible.every((e) => selected.has(e.id))}
                onChange={() => setSelected(
                  visible.every((e) => selected.has(e.id)) ? new Set() : new Set(visible.map((e) => e.id))
                )} />
              Select all
            </label>
            {selected.size > 0 && (
              <Button size="sm" variant="danger" icon={Trash2} onClick={() => setStatus([...selected], 'trashed')}>
                Trash
              </Button>
            )}
          </div>
        )}
      </div>

      {visible.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={Mail}
            title={tab === 'drafts' ? 'No drafts' : tab === 'sent' ? 'Nothing sent yet' : 'Trash is empty'}
            desc={tab === 'drafts'
              ? 'Select contacts in the Database and hit "Generate emails" to create drafts.'
              : tab === 'sent' ? 'Approved drafts you send will show up here with reply tracking.' : ''}
            action={tab === 'drafts' && (
              <Button variant="primary" icon={Sparkles} onClick={() => navigate('database')}>
                Pick contacts
              </Button>
            )}
          />
        </div>
      ) : (
        <div className="email-split">
          {/* list */}
          <div className="card email-list">
            {visible.map((e) => {
              const tm = EMAIL_TYPE_META[e.email_type] || EMAIL_TYPE_META.custom
              return (
                <div key={e.id} data-email-row={e.id}
                  className={`email-row ${active?.id === e.id ? 'active' : ''}`}
                  onClick={() => { if (confirmDiscard()) setActiveId(e.id) }}>
                  {tab === 'drafts' && (
                    <input type="checkbox" className="checkbox" style={{ marginTop: 3 }}
                      checked={selected.has(e.id)} onChange={(ev) => toggle(e.id, ev)}
                      onClick={(ev) => ev.stopPropagation()} />
                  )}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    <div className="email-row-title">{e.contact_name || e.contact_email || 'Unknown'}</div>
                    <div className="email-row-sub">{e.company_name || '—'} · {e.subject}</div>
                    <div className="email-row-meta">
                      <Chip tone={tm.tone}>{tm.label}</Chip>
                      {e.status === 'approved' && !isDelivered(e) &&
                        <Chip tone="green"><Check size={10} /> approved</Chip>}
                      {hasVerifiedReply(e) || e.contact_has_replied
                        ? <Chip tone="green"><MessageSquare size={10} /> replied</Chip>
                        : hasUnverifiedReply(e)
                          ? <Chip tone="amber"><MessageSquare size={10} /> reply unverified</Chip>
                          : null}
                      {isUnconfirmed(e) && <Chip tone="amber">delivery unconfirmed</Chip>}
                      {e.scheduled_at && !isDelivered(e) && (
                        <Chip tone="sky" title={`Queued to send at ${e.scheduled_at} without further confirmation.`}>
                          <Clock size={10} /> queued {whenLabel(e.scheduled_at)}
                        </Chip>
                      )}
                      {addressWarning(e) && (
                        <Chip tone="amber" title={addressWarning(e).title}>
                          {addressWarning(e).label}
                        </Chip>
                      )}
                      {!!e.used_template_fallback && <Chip tone="amber">template</Chip>}
                      <span className="tiny">{timeAgo(e.sent_at || e.created_at)}</span>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* detail */}
          {active ? (
            <EmailDetail
              key={`${active.id}:${editorRev}`}
              email={active}
              onPatch={(updates) => patchLocal(active.id, updates)}
              onSend={() => setSendModal([active.id])}
              onTrash={() => setStatus([active.id], 'trashed', true)}
              onRestore={() => setStatus([active.id], 'draft', true)}
              onDelete={() => deleteEmails([active.id])}
              onRegenerate={() => regenerate(active.id)}
              onFollowUp={() => makeFollowUp(active.id)}
              followUpDue={followUps.some((f) => f.id === active.id)}
              cadence={settings?.follow_up_cadence}
              busy={busy}
              llmReady={!!settings?.llm_provider}
              dirtyRef={pendingEdits}
              bodyRef={bodyRef}
              approveRef={approveRef}
            />
          ) : (
            <div className="card"><EmptyState icon={Mail} title="Select an email" /></div>
          )}
        </div>
      )}

      {showKeys && (
        <Modal title="Keyboard review" onClose={() => setShowKeys(false)}>
          <div className="stack" style={{ gap: 8 }}>
            {SHORTCUTS.map(([key, what]) => (
              <div key={key} className="row-between small">
                <span className="muted">{what}</span>
                <kbd className="kbd">{key}</kbd>
              </div>
            ))}
            {/* Said out loud, because its absence is the design and someone
                will otherwise go looking for the key. */}
            <div className="tiny muted" style={{ marginTop: 6 }}>
              There is no shortcut for sending. Everything above can be undone;
              a sent email cannot, so it stays a deliberate click.
            </div>
          </div>
        </Modal>
      )}

      {sendModal && (
        <SendModal
          emailIds={sendModal}
          emails={emails.filter((e) => sendModal.includes(e.id))}
          onClose={() => setSendModal(null)}
          // Refresh behind the modal but leave it open: the results screen is
          // where per-recipient failures are shown. The user closes it.
          onSent={() => { setSelected(new Set()); load() }}
        />
      )}
    </div>
  )
}

/* ---------- detail pane ---------- */

function EmailDetail({ email, onPatch, onSend, onTrash, onRestore, onDelete, onRegenerate, onFollowUp, followUpDue, cadence, busy, llmReady, dirtyRef, bodyRef, approveRef }) {
  const [subject, setSubject] = useState(email.subject)
  const [body, setBody] = useState(email.body)
  const [saving, setSaving] = useState(false)
  const dirty = subject !== email.subject || body !== email.body
  const delivered = email.status === 'sent' || isDelivered(email)
  const editable = !delivered
  const fuState = followUpState(email, cadence)

  // Publish dirtiness so the list can warn before discarding unsaved writing.
  useEffect(() => {
    if (dirtyRef) dirtyRef.current = dirty && editable
    return () => { if (dirtyRef) dirtyRef.current = false }
  }, [dirty, editable, dirtyRef])

  const save = async () => {
    setSaving(true)
    try {
      await emailsAPI.update(email.id, { subject, body })
      onPatch({ subject, body })
      if (dirtyRef) dirtyRef.current = false
      toast.success('Saved')
    } catch (e) { toast.error(errMessage(e)) }
    finally { setSaving(false) }
  }

  const approve = async () => {
    try {
      const updates = dirty ? { subject, body, status: 'approved' } : { status: 'approved' }
      await emailsAPI.update(email.id, updates)
      onPatch(updates)
      if (dirtyRef) dirtyRef.current = false
      toast.success('Approved — ready to send')
    } catch (e) { toast.error(errMessage(e)) }
  }

  // Published so the `a` shortcut approves through this exact function.
  // A second implementation would eventually forget the `dirty` branch above,
  // which is what marks the text on screen — rather than the text last saved —
  // as the thing being approved.
  useEffect(() => {
    if (!approveRef) return undefined
    approveRef.current = approve
    return () => { approveRef.current = null }
  })

  // Sending must never ship the pre-edit text: persist pending edits first.
  const sendWithPendingEdits = async () => {
    if (dirty) {
      setSaving(true)
      try {
        await emailsAPI.update(email.id, { subject, body })
        onPatch({ subject, body })
      } catch (e) {
        toast.error(`${errMessage(e)} — your edits were not saved, so nothing was sent.`)
        return
      } finally { setSaving(false) }
    }
    onSend()
  }

  return (
    <div className="card card-pad email-detail stack" style={{ gap: 12 }}>
      <div className="row-between">
        <div>
          <div style={{ fontWeight: 700, fontSize: 15 }}>
            {email.contact_name || email.contact_email}
          </div>
          <div className="tiny mono">{email.contact_email}{email.company_name ? ` · ${email.company_name}` : ''}</div>
        </div>
        <div className="row" style={{ gap: 6 }}>
          {editable && email.status !== 'trashed' && (
            <>
              <Button size="sm" icon={RefreshCw} onClick={onRegenerate}
                disabled={busy || !llmReady}
                title={llmReady ? 'Rewrite with AI' : 'No AI provider configured'}>
                Rewrite
              </Button>
              {email.status !== 'approved' && (
                <Button size="sm" icon={Check} onClick={approve}>Approve</Button>
              )}
              <Button size="sm" variant="primary" icon={Send} onClick={sendWithPendingEdits}
                disabled={saving}>
                Send
              </Button>
              <button className="icon-btn danger" title="Trash" onClick={onTrash}><Trash2 size={15} /></button>
            </>
          )}
          {email.status === 'trashed' && (
            <>
              {/* A delivered email must not be restorable into Drafts — that
                  is two clicks from sending the recipient a second copy. */}
              {!delivered && <Button size="sm" onClick={onRestore}>Restore to drafts</Button>}
              <Button size="sm" variant="danger" icon={Trash2} onClick={onDelete}>Delete forever</Button>
            </>
          )}
          {delivered && email.status !== 'trashed' && (
            <>
              {hasVerifiedReply(email) ? (
                <Chip tone="green"><MessageSquare size={11} /> replied {timeAgo(email.response_at)}</Chip>
              ) : email.contact_has_replied ? (
                // The reply landed on another email to this person. A follow-up
                // is written on the premise of silence, so offering one here
                // would send "still very interested?" to someone who answered.
                <Chip tone="green"><MessageSquare size={11} /> this person already replied</Chip>
              ) : (
                <>
                  {/* An unverified flag is not a reply date, and it must not
                      switch the follow-up pipeline off either — say what is
                      actually known and leave the decision with the user. */}
                  {hasUnverifiedReply(email) && (
                    <Chip tone="amber" title="Flagged by an older reply check that also counted bounces, auto-replies and your own messages. Hit Re-verify replies to confirm.">
                      <MessageSquare size={11} /> reply unverified
                    </Chip>
                  )}
                  {fuState.kind === 'bounced' ? (
                    <Chip tone="amber" title="The mail server rejected this address. Add a different address for this person and follow-ups resume.">
                      <CornerUpLeft size={11} /> no follow-up — address bounced
                    </Chip>
                  ) : fuState.kind === 'pending' ? (
                    // Clicking again just creates a duplicate follow-up to the
                    // same person, so say one exists instead of offering another.
                    <Chip tone="sky"><CornerUpLeft size={11} /> follow-up drafted</Chip>
                  ) : fuState.kind === 'done' ? (
                    <Chip tone="slate" title={`Your cadence is ${fuState.total} follow-up${fuState.total === 1 ? '' : 's'}. Add a step in Settings to chase again.`}>
                      <CornerUpLeft size={11} /> {fuState.sent} of {fuState.total} follow-ups sent
                    </Chip>
                  ) : fuState.kind === 'off' ? null : (
                    <Button size="sm" icon={CornerUpLeft} onClick={onFollowUp} disabled={busy}
                      variant={followUpDue ? 'primary' : 'secondary'}>
                      {fuState.total > 1
                        ? `${followUpDue ? 'Follow up (due)' : 'Draft follow-up'} · ${fuState.step} of ${fuState.total}`
                        : (followUpDue ? 'Follow up (due)' : 'Draft follow-up')}
                    </Button>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>

      {!!email.used_template_fallback && !delivered && (
        <div className="row small" style={{ color: 'var(--amber)', gap: 6 }}>
          <AlertTriangle size={13} />
          {email.fallback_reason === 'user_requested'
            ? 'Written from the plain template (AI was skipped).'
            : `${fallbackExplanation(email.fallback_reason)} Hit Rewrite to try AI again.`}
          {/* The template ignores instructions entirely — say so rather than
              letting the user assume they were applied. */}
          {email.custom_instructions ? ' Your extra instructions were not applied.' : ''}
        </div>
      )}

      {delivered && !!email.reply_unverified && (
        <div className="row small" style={{ color: 'var(--amber)', gap: 6 }}>
          <AlertTriangle size={13} />
          An older reply check marked this replied
          {email.response_at ? ` (stamped ${timeAgo(email.response_at)})` : ''} — that check
          also counted bounces, auto-replies and your own messages, so neither the reply nor
          the date is confirmed. Use “Re-verify replies” above.
        </div>
      )}

      <input className="email-subject-input" value={subject}
        onChange={(e) => setSubject(e.target.value)} readOnly={!editable} />
      <textarea ref={bodyRef} className="email-body-input" value={body}
        onChange={(e) => setBody(e.target.value)} readOnly={!editable} />

      <div className="row-between">
        <div className="tiny">
          {delivered
            ? `Sent ${timeAgo(email.sent_at)}`
            : `Drafted ${timeAgo(email.created_at)} · ${body.trim().split(/\s+/).filter(Boolean).length} words`}
        </div>
        {editable && dirty && (
          <Button size="sm" variant="primary" onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
        )}
      </div>

      {/* Gated on the message id, not on `delivered`. A legacy row imported
          with status='sent' and no Gmail id is delivered as far as the app is
          concerned, but the endpoint has nothing to fetch — so the button
          could only ever produce a 409. */}
      {!!email.gmail_message_id && <ThreadPane email={email} />}
    </div>
  )
}

/* ---------- the conversation ---------- */

const KIND_META = {
  reply: { tone: 'green', label: 'Reply' },
  bounce: { tone: 'red', label: 'Bounce' },
  auto: { tone: 'gray', label: 'Auto-reply' },
  own: { tone: 'accent', label: 'You' },
}

/* What came back, read from Gmail when asked for.

   Fetched on open rather than with the email list: a thread costs a Gmail
   round-trip, and loading one for every row in a 200-email list would spend
   the quota that reply-checking needs. Nothing here is stored — see the
   backend module for why a reply body stays out of the database. */
export function ThreadPane({ email }) {
  const [state, setState] = useState({ status: 'idle' })
  const [expanded, setExpanded] = useState({})

  const load = useCallback(() => {
    setState({ status: 'loading' })
    emailsAPI.thread(email.id)
      .then(({ data }) => setState({ status: 'ready', data }))
      .catch((e) => setState({
        status: 'error',
        message: errMessage(e, 'Could not read the thread'),
        // 409 means there is no thread to fetch, ever. Offering "Try again"
        // for it invites the user to keep clicking at a wall.
        retryable: e?.response?.status !== 409,
      }))
  }, [email.id])

  // Belt and braces: the list keys EmailDetail on the email id, so this pane
  // is remounted rather than reused when the selection moves. The reset keeps
  // that true if it is ever rendered somewhere without that key.
  useEffect(() => { setState({ status: 'idle' }); setExpanded({}) }, [email.id])

  if (state.status === 'idle') {
    return (
      <div className="row" style={{ gap: 8 }}>
        <Button size="sm" icon={MessageSquare} onClick={load}>Read the thread</Button>
        <span className="tiny muted">Fetched from Gmail when you ask — nothing is stored.</span>
      </div>
    )
  }
  if (state.status === 'loading') {
    return <div className="row tiny muted" style={{ gap: 8 }}><Spinner /> Reading from Gmail…</div>
  }
  if (state.status === 'error') {
    return (
      <div className="row small" style={{ gap: 8, color: 'var(--amber)' }}>
        <AlertTriangle size={13} /> {state.message}
        {state.retryable && <Button size="sm" onClick={load}>Try again</Button>}
      </div>
    )
  }

  const { messages = [], older_omitted: omitted = 0 } = state.data
  const incoming = messages.filter((m) => !m.outgoing)

  return (
    <div className="stack thread-pane" style={{ gap: 10 }}>
      <div className="row-between">
        <b className="small">Conversation</b>
        <button className="linkish tiny" type="button" onClick={() => setState({ status: 'idle' })}>Hide</button>
      </div>

      {/* Both banners are re-checked against the live row, not just against
          the payload they were fetched with. The payload is a snapshot; once
          Check replies records the reply, the row above shows a green
          "replied" chip and an unrevalidated banner sat underneath it still
          insisting the app had not recorded one. */}
      {state.data.unrecorded_reply && !hasVerifiedReply(email)
        && !email.contact_has_replied && (
        <div className="row small" style={{ gap: 6, color: 'var(--amber)' }}>
          <AlertTriangle size={13} />
          There is a reply here that the app has not recorded. Hit “Re-verify replies”
          above to count it — reading a thread deliberately changes nothing.
        </div>
      )}
      {state.data.unrecorded_bounce && !email.bounced_at
        && !email.contact_bounced_at && (
        <div className="row small" style={{ gap: 6, color: 'var(--amber)' }}>
          <AlertTriangle size={13} />
          This thread contains a delivery failure the app has not recorded.
          “Re-verify replies” will stop follow-ups to this address.
        </div>
      )}

      {omitted > 0 && (
        <div className="tiny muted">
          Showing the most recent {messages.length}; {omitted} older message
          {omitted === 1 ? '' : 's'} not shown.
        </div>
      )}

      {incoming.length === 0 && (
        <div className="tiny muted">
          No one has written back in this thread yet.
        </div>
      )}

      {messages.map((m) => {
        const meta = KIND_META[m.kind] || KIND_META.auto
        const open = !!expanded[m.id]
        return (
          <div key={m.id} className={`thread-msg${m.outgoing ? ' outgoing' : ''}`}>
            <div className="row-between" style={{ gap: 8 }}>
              <span className="tiny" style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                <b>{m.from_name || m.from_email || 'Unknown sender'}</b>
                {m.from_name && m.from_email ? <span className="muted mono"> {m.from_email}</span> : null}
              </span>
              <span className="row" style={{ gap: 6, flexShrink: 0 }}>
                <Chip tone={meta.tone}>{meta.label}</Chip>
                {/* No date beats 1970: a message whose timestamp Gmail did not
                    give us should not claim to be 54 years old. */}
                <span className="tiny muted">{m.sent_at ? timeAgo(m.sent_at) : 'no date'}</span>
              </span>
            </div>
            {/* Rendered as text, never as markup — this is the one place in the
                app where a stranger's writing reaches the DOM. */}
            <div className="thread-body">{m.text || <span className="muted">(no text content)</span>}</div>
            {m.attachments?.length > 0 && (
              <div className="tiny muted row" style={{ gap: 5, flexWrap: 'wrap' }}>
                <Paperclip size={11} />
                {m.attachments.join(', ')}
              </div>
            )}
            {m.truncated && (
              <div className="tiny muted">Long message — shown up to the first 40,000 characters.</div>
            )}
            {m.quoted && (
              <>
                <button className="linkish tiny" type="button"
                  onClick={() => setExpanded((x) => ({ ...x, [m.id]: !x[m.id] }))}>
                  {open ? 'Hide quoted text' : 'Show quoted text'}
                </button>
                {open && <div className="thread-body quoted">{m.quoted}</div>}
              </>
            )}
          </div>
        )
      })}
    </div>
  )
}

/* ---------- send modal ---------- */

function SendModal({ emailIds, emails, onClose, onSent }) {
  const [resumeList, setResumeList] = useState(null)   // null = still loading
  const [attach, setAttach] = useState(true)
  const [resumeId, setResumeId] = useState('')   // '' = per-email/default
  const [phase, setPhase] = useState('confirm')  // confirm | sending | done
  const [confirmResend, setConfirmResend] = useState(false)
  const [confirmAttachment, setConfirmAttachment] = useState(false)
  // null until it loads. Absent or disabled means the schedule button never
  // renders — the same first gate the endpoint enforces, so the UI cannot
  // offer something the server will refuse.
  const [sendWindow, setSendWindow] = useState(null)
  const unconfirmed = emails.filter(isUnconfirmed)

  // The body's "my resume is attached" was decided once, when the draft was
  // written, around one specific PDF. Unticking "Attach resume" or picking
  // "Attach X to all" can contradict it, so compare the two before sending.
  const resumes = resumeList || []
  const defaultResumeId = resumes.find((r) => r.is_default)?.id || ''
  const labelFor = (id) => resumes.find((r) => r.id === id)?.label
  const boundResumeId = (e) => e.resume_id || defaultResumeId
  const attachedResumeId = (e) => (attach ? (resumeId || boundResumeId(e)) : '')
  // Only once the resume list is in: mid-load every id looks unresolvable.
  const claimConflicts = resumeList === null ? [] : emails.filter(
    (e) => e.claims_attachment
      && (!attachedResumeId(e) || attachedResumeId(e) !== boundResumeId(e)))
  const needsAttachmentAck = claimConflicts.length > 0 && !confirmAttachment
  const { job, track } = useJobPolling((j) => {
    if (j.status === 'done' || j.status === 'cancelled') {
      setPhase('done')
      const r = j.result || {}
      if (j.status === 'cancelled') {
        toast(`Stopped after ${r.sent ?? 0} sent`, { icon: '✋' })
      } else if ((r.failed ?? 0) > 0) {
        toast.error(`${r.sent} sent, ${r.failed} failed`)
      } else {
        toast.success(`${r.sent} ${r.sent === 1 ? 'email' : 'emails'} sent 🎉`)
      }
      onSent()
    } else if (j.status === 'failed') {
      setPhase('confirm')
      toast.error(j.error || 'Send failed')
    }
  })

  const stopSending = async () => {
    if (!job?.id) return
    try {
      await emailsAPI.cancelSend(job.id)
      toast('Stopping after the current email…', { icon: '✋' })
    } catch (e) {
      // 409 just means the batch finished on its own before the click landed
      if (e?.response?.status !== 409) toast.error(errMessage(e))
    }
  }

  useEffect(() => {
    resumesAPI.list().then(({ data }) => setResumeList(data || []))
      .catch(() => setResumeList([]))
    // Guarded, not just catch()ed: against a backend that predates this
    // endpoint the whole send dialog must still open. Losing the schedule
    // button is a missing convenience; losing the dialog is losing the ability
    // to send at all.
    try {
      sendWindowAPI.get().then(({ data }) => setSendWindow(data)).catch(() => {})
    } catch { /* no sending window available — send-now only */ }
  }, [])

  const send = async (schedule = null) => {
    setPhase('sending')
    try {
      const { data } = await emailsAPI.send({
        email_ids: emailIds,
        attach_resume: attach,
        resume_id: resumeId || null,
        confirm_resend: confirmResend,
        confirm_attachment_change: confirmAttachment,
        // Omitted entirely unless the user picked "Send at…". This is the
        // per-batch half of the opt-in that keeps mail from going out
        // unattended, so it must never be implied by anything else.
        ...(schedule ? { schedule } : {}),
      })
      if (schedule) {
        const when = data?.result?.scheduled_at
        toast.success(`Queued — sending ${when ? whenLabel(when) : 'at the next window'}`)
        setPhase('done')
        onSent()
        return
      }
      track(data)
    } catch (e) {
      setPhase('confirm')
      toast.error(errMessage(e, 'Send failed'))
    }
  }

  const n = emailIds.length

  return (
    <Modal
      title={phase === 'sending' ? 'Sending…' : `Send ${n} ${n === 1 ? 'email' : 'emails'}`}
      // Always closeable: sending runs server-side, so dismissing just stops
      // watching. A stuck poll must never trap the user in a modal.
      onClose={() => {
        if (phase === 'sending') {
          toast('Still sending in the background — check the Sent tab', { icon: '📤' })
        }
        onClose()
      }}
      footer={phase === 'confirm' ? (
        <>
          <Button variant="ghost" onClick={onClose}>Cancel</Button>
          {/* Only offered when the sending window is switched on. Without that
              first gate this button would queue mail nothing will ever pick
              up, and the endpoint rightly refuses it. */}
          {sendWindow?.enabled && !sendWindow.open_now && (
            <Button icon={Clock} onClick={() => send('next_window')}
              disabled={needsAttachmentAck}
              title={`Hold until business hours: ${sendWindow.description}`}>
              Send at {whenLabel(sendWindow.next_opening) || 'next window'}
            </Button>
          )}
          <Button variant="primary" icon={Send} onClick={() => send()}
            disabled={needsAttachmentAck}
            title={needsAttachmentAck
              ? 'These drafts promise an attachment these options would change'
              : undefined}>
            Send now
          </Button>
        </>
      ) : phase === 'done' ? (
        <Button variant="primary" onClick={onClose}>Done</Button>
      ) : (
        <Button variant="danger" onClick={stopSending}>Stop sending</Button>
      )}
    >
      {phase === 'confirm' && (
        <>
          <div className="small muted">
            Sending via your connected Gmail account. Recipients:
          </div>
          <div className="card" style={{ maxHeight: 160, overflowY: 'auto' }}>
            {emails.map((e) => (
              <div key={e.id} className="row-between" style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)' }}>
                <span className="small" style={{ fontWeight: 600 }}>
                  {e.contact_name || e.contact_email}
                  {addressWarning(e) && (
                    <span className="tiny" style={{ color: 'var(--amber)' }}>
                      {' '}· {addressWarning(e).label}
                    </span>
                  )}
                </span>
                <span className="tiny mono">
                  {e.contact_email}
                  {/* Which PDF this draft was written around — without it a
                      swap or a strip is invisible to the user. */}
                  {e.claims_attachment && (
                    <span className="muted">
                      {' · '}
                      {attachedResumeId(e)
                        ? `attaching ${labelFor(attachedResumeId(e)) || 'resume'}`
                        : 'no attachment'}
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
          {claimConflicts.length > 0 && (
            <div className="card card-pad stack" style={{ gap: 6, background: 'var(--amber-soft)', borderColor: 'var(--amber-border)' }}>
              <div className="row small" style={{ gap: 6, fontWeight: 650 }}>
                <AlertTriangle size={13} style={{ color: 'var(--amber)' }} />
                {claimConflicts.length === 1 ? 'One of these emails says' : `${claimConflicts.length} of these emails say`}
                {' '}a resume is attached
              </div>
              <div className="small">
                {attach
                  ? 'These send options would attach a different PDF than the email was written around:'
                  : 'With "Attach resume" off, the recipient gets an email promising an attachment that is not there:'}
              </div>
              {claimConflicts.map((e) => (
                <div key={e.id} className="tiny mono">
                  {e.contact_email}
                  {' — written around '}{labelFor(boundResumeId(e)) || 'your default resume'}
                  {', sending '}{labelFor(attachedResumeId(e)) || 'nothing'}
                </div>
              ))}
              <label className="row small" style={{ gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" className="checkbox" checked={confirmAttachment}
                  onChange={(e) => setConfirmAttachment(e.target.checked)} />
                Send anyway — I know the email text won&apos;t match the attachment
              </label>
            </div>
          )}
          {unconfirmed.length > 0 && (
            <div className="card card-pad stack" style={{ gap: 6, background: 'var(--amber-soft)', borderColor: 'var(--amber-border)' }}>
              <div className="row small" style={{ gap: 6, fontWeight: 650 }}>
                <AlertTriangle size={13} style={{ color: 'var(--amber)' }} />
                {unconfirmed.length === 1 ? 'One of these was' : `${unconfirmed.length} of these were`} already handed to Gmail
              </div>
              <div className="small">
                We never got a confirmation back, so we cannot tell whether Gmail
                delivered {unconfirmed.length === 1 ? 'it' : 'them'}. We'll check your Sent
                folder first, but if the message isn't there yet, sending again could put a
                second copy in {unconfirmed.length === 1 ? 'a real inbox' : 'real inboxes'}:
              </div>
              {unconfirmed.map((e) => (
                <div key={e.id} className="tiny mono">{e.contact_email}</div>
              ))}
              <label className="row small" style={{ gap: 8, cursor: 'pointer' }}>
                <input type="checkbox" className="checkbox" checked={confirmResend}
                  onChange={(e) => setConfirmResend(e.target.checked)} />
                I checked Gmail — send {unconfirmed.length === 1 ? 'it' : 'them'} anyway
              </label>
            </div>
          )}
          <label className="row small" style={{ gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" className="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} />
            <Paperclip size={13} /> Attach resume
          </label>
          {attach && (
            <select className="select" value={resumeId} onChange={(e) => setResumeId(e.target.value)}>
              <option value="">Use each email's resume (or your default)</option>
              {resumes.map((r) => (
                <option key={r.id} value={r.id}>Attach {r.label} to all</option>
              ))}
            </select>
          )}
        </>
      )}
      {phase !== 'confirm' && job && (
        <div className="stack">
          <div className="row">
            {phase === 'sending' && <Spinner />}
            <div style={{ fontWeight: 600 }}>{phase === 'done' ? 'All done' : (job.stage || 'Sending…')}</div>
          </div>
          <ProgressBar current={job.progress_current} total={job.progress_total} />
          <div className="small muted">{job.progress_current}/{job.progress_total} processed</div>
          {phase === 'done' && (() => {
            const failures = (job.result?.results || []).filter((r) => !r.success)
            const sentCount = job.result?.sent ?? 0
            // "We don't know" and "Gmail refused" are different verdicts, and
            // only the second one makes a retry safe. Skipped rows (already
            // sent, not sendable) are neither.
            const unknownOut = failures.filter((r) => r.delivery_unknown)
            const skipped = failures.filter((r) => !r.delivery_unknown && r.retryable === false)
            const refused = failures.filter((r) => !r.delivery_unknown && r.retryable !== false)
            const line = (r, i) => {
              const email = emails.find((e) => e.id === r.email_id)
              return (
                <div key={i} className="small" style={{ marginTop: 3 }}>
                  • <b>{email?.contact_email || email?.contact_name || 'Unknown recipient'}</b>
                  {' — '}{r.error}
                </div>
              )
            }
            return (
              <>
                <div className="small">
                  <b>{sentCount}</b> sent
                  {failures.length > 0 && <> · <b style={{ color: 'var(--red)' }}>{failures.length} failed</b></>}
                </div>
                {unknownOut.length > 0 && (
                  <div className="card card-pad" style={{ background: 'var(--amber-soft)', borderColor: 'var(--amber-border)' }}>
                    <div className="small" style={{ fontWeight: 650, marginBottom: 6 }}>
                      We could not confirm whether these were delivered — check your
                      Gmail Sent folder before retrying:
                    </div>
                    {unknownOut.map(line)}
                  </div>
                )}
                {refused.length > 0 && (
                  <div className="card card-pad" style={{ background: 'var(--red-soft)', borderColor: 'var(--red-border)' }}>
                    <div className="small" style={{ fontWeight: 650, marginBottom: 6 }}>
                      These did not go out — they stay in Drafts so you can retry:
                    </div>
                    {refused.map(line)}
                  </div>
                )}
                {skipped.length > 0 && (
                  <div className="card card-pad">
                    <div className="small" style={{ fontWeight: 650, marginBottom: 6 }}>
                      Skipped — nothing was sent for these:
                    </div>
                    {skipped.map(line)}
                  </div>
                )}
              </>
            )
          })()}
        </div>
      )}
    </Modal>
  )
}
