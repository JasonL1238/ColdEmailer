/* Compose modal: pick email type + resume + instructions, then watch the
   generation job live. Lands the user in Emails → Drafts when done. */
import { useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { Briefcase, Coffee, DollarSign, PenLine, Sparkles, FileText } from 'lucide-react'
import { emailsAPI, resumesAPI, errMessage } from '../api'
import { Button, Modal, ProgressBar, useJobPolling } from '../ui'
import { useApp } from '../App'

const TYPE_CARDS = [
  {
    id: 'application', icon: Briefcase, title: 'Application',
    desc: 'Internship or job inquiry. Leans on your resume to show why you fit their team.',
  },
  {
    id: 'coffee_chat', icon: Coffee, title: 'Coffee chat',
    desc: 'Networking ask. Warm, curious, explicitly not asking for a job.',
  },
  {
    id: 'sales', icon: DollarSign, title: 'Sales / Pitch',
    desc: 'Pitch your product or service. Focused on their problem, not your bio.',
  },
  {
    id: 'custom', icon: PenLine, title: 'Custom',
    desc: 'You write the instructions; AI follows them using the scraped research.',
  },
]

export default function ComposeModal({ contactIds, onClose, onDone }) {
  const { navigate } = useApp()
  const [emailType, setEmailType] = useState('application')
  const [resumeList, setResumeList] = useState([])
  const [resumeId, setResumeId] = useState('')
  const [instructions, setInstructions] = useState('')
  const [templateOnly, setTemplateOnly] = useState(false)
  const [allowRecontact, setAllowRecontact] = useState(false)
  const [phase, setPhase] = useState('setup') // setup | running | done

  const { job, track } = useJobPolling((j) => {
    if (j.status === 'done') {
      setPhase('done')
      const r = j.result || {}
      const made = r.generated ?? 0
      const skipped = (r.skipped || []).length
      const line = `${made} ${made === 1 ? 'email' : 'emails'} drafted${skipped ? ` · ${skipped} skipped` : ''}`
      // A run that wrote nothing is not a success — saying so with a green
      // check is how "0 emails drafted" got read as "done".
      if (made > 0) toast.success(line)
      else toast(line, { icon: '⚠️' })
      onDone?.()
    } else if (j.status === 'failed') {
      setPhase('setup')
      toast.error(j.error || 'Generation failed')
    }
  })

  useEffect(() => {
    resumesAPI.list().then(({ data }) => {
      setResumeList(data)
      const def = data.find((r) => r.is_default)
      if (def) setResumeId(def.id)
    }).catch(() => {})
  }, [])

  const start = async () => {
    if (emailType === 'custom' && !instructions.trim()) {
      toast.error('Custom emails need instructions — tell the AI what to write')
      return
    }
    setPhase('running')
    try {
      const { data } = await emailsAPI.generate({
        contact_ids: contactIds,
        email_type: emailType,
        // Types that never attach a resume (sales) must not carry one on the
        // draft either — the send step would staple it to an email whose body
        // was written on the premise that nothing is attached.
        resume_id: (showsResume && resumeId) || null,
        custom_instructions: instructions.trim() || null,
        use_template_only: canSkipAi && templateOnly,
        allow_recontact: allowRecontact,
      })
      track(data)
    } catch (e) {
      setPhase('setup')
      toast.error(errMessage(e, 'Could not start generation'))
    }
  }

  const cancel = async () => {
    if (job?.id) {
      try { await emailsAPI.cancelGeneration(job.id) } catch { /* already done */ }
    }
  }

  const n = contactIds.length
  // What the finished run actually produced. A completed job is not the same
  // thing as drafts existing: every contact can be skipped.
  const drafted = job?.result?.generated ?? 0
  const skippedList = job?.result?.skipped || []
  const nothingDrafted = phase === 'done' && drafted === 0
  const showsResume = emailType !== 'sales'
  // The plain template can't follow instructions — it would emit a generic
  // application email instead, so custom always needs AI.
  const canSkipAi = emailType !== 'custom'

  return (
    <Modal
      large
      title={
        phase === 'done' ? (nothingDrafted ? 'Nothing drafted' : 'Drafts ready')
          : phase === 'running' ? 'Writing emails…'
            : `Generate ${n} ${n === 1 ? 'email' : 'emails'}`
      }
      // Dismissing mid-run must not throw away the batch — a stray backdrop
      // click would silently kill it. Generation is a background job, so let
      // it finish; only the explicit Cancel button stops it.
      onClose={() => {
        if (phase === 'running') {
          toast('Still writing in the background — drafts will appear in Emails', { icon: '✍️' })
        }
        onClose()
      }}
      footer={
        phase === 'setup' ? (
          <>
            <Button variant="ghost" onClick={onClose}>Cancel</Button>
            <Button variant="primary" icon={Sparkles} onClick={start}>
              Generate {n} {n === 1 ? 'email' : 'emails'}
            </Button>
          </>
        ) : phase === 'done' ? (
          // Nothing was written, so there is nothing to review — sending the
          // user to Drafts would land them on an unrelated older draft.
          nothingDrafted ? (
            <Button variant="primary" onClick={onClose}>Close</Button>
          ) : (
            <>
              <Button variant="ghost" onClick={onClose}>Close</Button>
              <Button variant="primary" onClick={() => { onClose(); navigate('emails') }}>
                Review drafts
              </Button>
            </>
          )
        ) : (
          <Button variant="danger" onClick={() => { cancel(); onClose() }}>Cancel generation</Button>
        )
      }
    >
      {phase === 'setup' && (
        <>
          <div className="field">
            <div className="field-label">Email type</div>
            <div className="type-grid">
              {TYPE_CARDS.map((t) => (
                <button
                  key={t.id}
                  className={`type-card ${emailType === t.id ? 'selected' : ''}`}
                  onClick={() => setEmailType(t.id)}
                >
                  <div className="type-card-title">
                    <t.icon size={15} style={{ color: emailType === t.id ? 'var(--accent)' : 'var(--text-3)' }} />
                    {t.title}
                  </div>
                  <div className="type-card-desc">{t.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {showsResume && (
            <div className="field">
              <div className="field-label row" style={{ gap: 6 }}><FileText size={13} /> Resume version</div>
              {resumeList.length === 0 ? (
                <div className="field-hint">
                  No resumes uploaded yet — emails will use your profile background only.{' '}
                  <a href="#/resumes">Upload one</a> for stronger personalization.
                </div>
              ) : (
                <select className="select" value={resumeId} onChange={(e) => setResumeId(e.target.value)}>
                  {resumeList.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.label}{r.is_default ? ' (default)' : ''}
                    </option>
                  ))}
                </select>
              )}
              <div className="field-hint">Used both to personalize the writing and as the attachment when you send.</div>
            </div>
          )}

          <div className="field">
            <div className="field-label">
              {emailType === 'custom' ? 'Instructions (required)' : 'Extra instructions (optional)'}
            </div>
            <textarea
              className="textarea"
              placeholder={
                emailType === 'custom'
                  ? 'e.g. Write a 3-sentence intro asking about their summer research program. Mention my robotics background.'
                  : 'e.g. Mention that I saw their launch on Hacker News. Keep it under 100 words.'
              }
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              maxLength={2000}
            />
          </div>

          <label className="row small" style={{ gap: 8, cursor: canSkipAi ? 'pointer' : 'not-allowed', opacity: canSkipAi ? 1 : 0.55 }}>
            <input type="checkbox" className="checkbox" checked={canSkipAi && templateOnly}
              disabled={!canSkipAi}
              onChange={(e) => setTemplateOnly(e.target.checked)} />
            Skip AI — use the plain template (no API credits used)
          </label>
          {!canSkipAi && (
            <div className="field-hint">
              Custom emails need AI: the plain template can&apos;t follow your instructions.
            </div>
          )}

          <label className="row small" style={{ gap: 8, cursor: 'pointer' }}>
            <input type="checkbox" className="checkbox" checked={allowRecontact}
              onChange={(e) => setAllowRecontact(e.target.checked)} />
            Write a new first email even for people I&apos;ve already emailed
          </label>
          <div className="field-hint">
            Off by default: anyone already contacted is skipped, since a second
            first-contact email reads as spam. Use <b>Draft follow-up</b> in Sent instead.
          </div>
        </>
      )}

      {phase !== 'setup' && job && (
        <div className="stack">
          <div className="row">
            {phase === 'running' && <div className="spinner" />}
            <div style={{ fontWeight: 600 }}>{job.stage || 'Working…'}</div>
          </div>
          <ProgressBar current={job.progress_current} total={job.progress_total}
            indeterminate={phase === 'running' && !job.progress_current} />
          <div className="small muted">
            {/* "N/N done" counted skipped contacts as work done. Say how many
                drafts exist, and keep skips as their own number. */}
            {phase === 'done'
              ? `${drafted} of ${job.progress_total} drafted${skippedList.length ? ` · ${skippedList.length} skipped` : ''}`
              : `${job.progress_current}/${job.progress_total} drafted — each email is researched and written individually, this takes a few seconds per contact`}
          </div>
          {phase === 'done' && skippedList.length > 0 && (
            <div className="card card-pad" style={{ background: 'var(--amber-soft)', borderColor: 'var(--amber-border)' }}>
              <div className="small" style={{ fontWeight: 650, marginBottom: 4 }}>Skipped</div>
              {job.result.skipped.map((s, i) => (
                <div key={i} className="small">
                  • <b>{s.name || s.email || s.company_name || 'Unnamed contact'}</b>: {s.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
