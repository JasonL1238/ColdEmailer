/* Emails: review drafts, edit, approve, send, and track replies */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Mail, Send, Trash2, RefreshCw, Check, Clock, Paperclip,
  AlertTriangle, MessageSquare, Sparkles, CornerUpLeft,
} from 'lucide-react'
import { emailsAPI, resumesAPI, errMessage } from '../api'
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

// A reply flag the current checker has confirmed vs. one inherited from the old
// checker (which counted bounces, auto-replies and our own messages). Only the
// first may be stated as fact — including its date.
export const hasVerifiedReply = (e) => !!e.has_response && !e.reply_unverified
export const hasUnverifiedReply = (e) => !!(e.reply_unverified || e.contact_reply_unverified)

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
  // Set by the open editor whenever it holds unsaved changes, so navigating
  // away can warn instead of silently discarding the user's writing.
  const pendingEdits = useRef(false)

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

  const setStatus = async (ids, status, keepSelection = false) => {
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
      if (updated < ids.length) {
        toast(`${updated} of ${ids.length} updated — sent emails can't be changed`, { icon: 'ℹ️' })
      } else if (status === 'trashed') {
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
        <div className="card card-pad row-between mb-16" style={{ background: 'var(--amber-soft)', borderColor: '#f3dcb6' }}>
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

      {followUps.length > 0 && tab !== 'trashed' && (
        <div className="card card-pad row-between mb-16" style={{ background: 'var(--amber-soft)', borderColor: '#f3dcb6' }}>
          <div className="row">
            <Clock size={16} style={{ color: 'var(--amber)' }} />
            <span className="small">
              <b>{followUps.length} {followUps.length === 1 ? 'contact' : 'contacts'}</b> went quiet for 7+ days.
              Draft follow-ups with one click below (look for the <CornerUpLeft size={11} style={{ verticalAlign: -1 }} /> icon in Sent).
            </span>
          </div>
          <Button size="sm" onClick={() => setTab('sent')}>View sent</Button>
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
                <div key={e.id} className={`email-row ${active?.id === e.id ? 'active' : ''}`}
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
              busy={busy}
              llmReady={!!settings?.llm_provider}
              dirtyRef={pendingEdits}
            />
          ) : (
            <div className="card"><EmptyState icon={Mail} title="Select an email" /></div>
          )}
        </div>
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

function EmailDetail({ email, onPatch, onSend, onTrash, onRestore, onDelete, onRegenerate, onFollowUp, followUpDue, busy, llmReady, dirtyRef }) {
  const [subject, setSubject] = useState(email.subject)
  const [body, setBody] = useState(email.body)
  const [saving, setSaving] = useState(false)
  const dirty = subject !== email.subject || body !== email.body
  const delivered = email.status === 'sent' || isDelivered(email)
  const editable = !delivered

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
      toast.success('Approved — ready to send')
    } catch (e) { toast.error(errMessage(e)) }
  }

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
                  {email.has_follow_up ? (
                    // Clicking again just creates a duplicate follow-up to the
                    // same person, so say one exists instead of offering another.
                    <Chip tone="sky"><CornerUpLeft size={11} /> follow-up drafted</Chip>
                  ) : (
                    <Button size="sm" icon={CornerUpLeft} onClick={onFollowUp} disabled={busy}
                      variant={followUpDue ? 'primary' : 'secondary'}>
                      {followUpDue ? 'Follow up (due)' : 'Draft follow-up'}
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
            : 'AI was unavailable — this used the plain template. Hit Rewrite to try AI again.'}
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
      <textarea className="email-body-input" value={body}
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
  }, [])

  const send = async () => {
    setPhase('sending')
    try {
      const { data } = await emailsAPI.send({
        email_ids: emailIds,
        attach_resume: attach,
        resume_id: resumeId || null,
        confirm_resend: confirmResend,
        confirm_attachment_change: confirmAttachment,
      })
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
          <Button variant="primary" icon={Send} onClick={send}
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
                <span className="small" style={{ fontWeight: 600 }}>{e.contact_name || e.contact_email}</span>
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
            <div className="card card-pad stack" style={{ gap: 6, background: 'var(--amber-soft)', borderColor: '#f3dcb6' }}>
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
            <div className="card card-pad stack" style={{ gap: 6, background: 'var(--amber-soft)', borderColor: '#f3dcb6' }}>
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
                  <div className="card card-pad" style={{ background: 'var(--amber-soft)', borderColor: '#f3dcb6' }}>
                    <div className="small" style={{ fontWeight: 650, marginBottom: 6 }}>
                      We could not confirm whether these were delivered — check your
                      Gmail Sent folder before retrying:
                    </div>
                    {unknownOut.map(line)}
                  </div>
                )}
                {refused.length > 0 && (
                  <div className="card card-pad" style={{ background: 'var(--red-soft)', borderColor: '#f5c6c6' }}>
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
