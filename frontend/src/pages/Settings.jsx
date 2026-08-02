/* Settings: sender profile, Gmail connection, AI provider, limits */
import { useCallback, useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { Save, Unplug, CheckCircle2, XCircle, Sparkles, Mail, CornerUpLeft, Plus, Clock, Trash2 } from 'lucide-react'
import { settingsAPI, gmailAPI, cadenceAPI, sendWindowAPI, suppressionsAPI, errMessage } from '../api'
import { Button, Chip } from '../ui'
import { useApp } from '../App'

const FIELDS = [
  { key: 'full_name', label: 'Full name', placeholder: 'Jane Smith' },
  { key: 'email', label: 'Email', placeholder: 'you@school.edu', hint: 'The From address your emails send from. Kept out of the signature — the recipient already sees it' },
  { key: 'phone', label: 'Phone', placeholder: '555-000-0000' },
  { key: 'school', label: 'School / affiliation', placeholder: 'University of…' },
  {
    key: 'affiliations', label: 'Past employers / communities', span2: true,
    textarea: true, placeholder: 'One per line, e.g. Penn Labs, Microsoft, Stripe',
    hint: 'Research flags leaders whose public company bio mentions one of these.',
  },
  { key: 'website', label: 'Website', placeholder: 'https://yoursite.com', span2: true },
  {
    key: 'background', label: 'Background one-liner', span2: true, textarea: true,
    placeholder: "e.g. I'm a CS + Math student who built computer-vision pipelines for research labs…",
    hint: 'The AI uses this everywhere. Make it concrete — projects, tools, outcomes.',
  },
  {
    key: 'signature', label: 'Extra signature lines', span2: true, textarea: true,
    placeholder: 'Optional extra lines under your signature (LinkedIn, GitHub…)',
  },
]

export default function Settings() {
  const { settings, refreshSettings } = useApp()
  const [profile, setProfile] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (settings?.profile && profile === null) setProfile({ ...settings.profile })
  }, [settings, profile])

  const save = async () => {
    setSaving(true)
    try {
      await settingsAPI.update(profile)
      await refreshSettings()
      toast.success('Profile saved')
    } catch (e) { toast.error(errMessage(e)) }
    finally { setSaving(false) }
  }

  const disconnectGmail = async () => {
    if (!window.confirm('Disconnect Gmail? You will need to sign in again on your next send.')) return
    try {
      await gmailAPI.disconnect()
      await refreshSettings()
      toast.success('Gmail disconnected')
    } catch (e) { toast.error(errMessage(e)) }
  }

  if (!settings || profile === null) {
    return <div className="page"><div className="skeleton" style={{ height: 400, marginTop: 40 }} /></div>
  }

  return (
    <div className="page" style={{ maxWidth: 860 }}>
      <div className="page-head">
        <div>
          <div className="page-title">Settings</div>
          <div className="page-desc">Who you are — the AI writes every email as you</div>
        </div>
        <Button variant="primary" icon={Save} onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save profile'}
        </Button>
      </div>

      {settings.profile_incomplete?.length > 0 && (
        <div className="card card-pad mb-16 row" style={{ background: 'var(--amber-soft)', borderColor: 'var(--amber-border)', gap: 10 }}>
          <XCircle size={16} style={{ color: 'var(--amber)', flexShrink: 0 }} />
          <div className="small">
            <b>Your emails are missing {settings.profile_incomplete.join(', ')}.</b>{' '}
            The AI writes every email as you, so filling these in makes a big difference.
          </div>
        </div>
      )}

      <div className="card card-pad">
        <div className="settings-grid">
          {FIELDS.map((f) => (
            <div key={f.key} className={`field ${f.span2 ? 'span-2' : ''}`}>
              <div className="field-label">{f.label}</div>
              {f.textarea ? (
                <textarea className="textarea" style={{ minHeight: 66 }}
                  value={profile[f.key] || ''} placeholder={f.placeholder}
                  onChange={(e) => setProfile((p) => ({ ...p, [f.key]: e.target.value }))} />
              ) : (
                <input className="input" value={profile[f.key] || ''} placeholder={f.placeholder}
                  onChange={(e) => setProfile((p) => ({ ...p, [f.key]: e.target.value }))} />
              )}
              {f.hint && <div className="field-hint">{f.hint}</div>}
            </div>
          ))}
        </div>
      </div>

      <div className="mt-16" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        {/* Gmail */}
        <div className="card card-pad stack" style={{ gap: 10 }}>
          <div className="row-between">
            <div className="row" style={{ gap: 8 }}>
              <Mail size={16} style={{ color: 'var(--text-2)' }} />
              <b>Gmail sending</b>
            </div>
            {settings.gmail_connected
              ? <Chip tone="green"><CheckCircle2 size={11} /> connected</Chip>
              : <Chip tone="amber"><XCircle size={11} /> not connected</Chip>}
          </div>
          {settings.gmail_connected ? (
            <>
              <div className="small muted">Emails send from your Google account via the Gmail API.</div>
              <div><Button size="sm" icon={Unplug} onClick={disconnectGmail}>Disconnect</Button></div>
            </>
          ) : settings.gmail_credentials_present ? (
            <div className="small muted">
              Credentials found. The first time you hit <b>Send</b>, a Google sign-in window opens on the machine running the backend.
            </div>
          ) : (
            <div className="small muted">
              Add <span className="mono">credentials.json</span> from Google Cloud Console (Gmail API → OAuth client → Desktop app) to the project root, then send your first email to sign in.
            </div>
          )}
        </div>

        {/* AI */}
        <div className="card card-pad stack" style={{ gap: 10 }}>
          <div className="row-between">
            <div className="row" style={{ gap: 8 }}>
              <Sparkles size={16} style={{ color: 'var(--text-2)' }} />
              <b>AI writing</b>
            </div>
            {settings.llm_provider
              ? <Chip tone="green"><CheckCircle2 size={11} /> {settings.llm_provider}</Chip>
              : <Chip tone="amber"><XCircle size={11} /> template mode</Chip>}
          </div>
          <div className="small muted">
            {settings.llm_provider
              ? <>Emails and company research are written by <b>{settings.llm_provider}</b>. Configured via <span className="mono">.env</span>.</>
              : <>No API key found. Add <span className="mono">GOOGLE_AI_API_KEY</span> (free tier works) to <span className="mono">.env</span> and restart the backend — until then emails use plain templates.</>}
          </div>
          <div className="tiny">
            Daily limits: {settings.limits?.emails_per_day} sends · {settings.limits?.generations_per_day} generations
          </div>
        </div>
      </div>

      <FollowUpCadence />
      <SendWindow />
      <DoNotContact />
    </div>
  )
}

/* The do-not-contact list.

   Unlike everything else on this page it is not a preference — it is a
   promise. So the UI leans on saying what an entry actually covers: a domain
   entry blocks every address there including subdomains, which is the case
   people do not expect, and the count of contacts already matched is shown at
   the moment of adding rather than discovered later by a refused send. */
export function DoNotContact() {
  const [entries, setEntries] = useState(null)
  const [value, setValue] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)

  const [failed, setFailed] = useState(false)

  const load = useCallback(() => {
    setFailed(false)
    suppressionsAPI.list()
      .then(({ data }) => setEntries(data))
      // A failed read must never render as "Nobody on the list." — that is a
      // positive claim that nothing is suppressed, made by the one feature in
      // this app documented as fail-closed. The list is still enforced at send
      // time whatever this card can see.
      .catch(() => { setEntries([]); setFailed(true) })
  }, [])
  useEffect(() => { load() }, [load])

  const add = async (e) => {
    e?.preventDefault?.()
    if (!value.trim()) return
    setBusy(true)
    try {
      const { data } = await suppressionsAPI.add(value.trim(), reason.trim() || null)
      setValue(''); setReason('')
      load()
      toast.success(data.matched_contacts > 0
        ? `Added — this already covers ${data.matched_contacts} contact${data.matched_contacts === 1 ? '' : 's'} in your database.`
        : 'Added to the do-not-contact list.')
    } catch (err) { toast.error(errMessage(err)) }
    finally { setBusy(false) }
  }

  const remove = async (entry) => {
    const what = entry.kind === 'domain' ? `everyone at @${entry.value}` : entry.value
    if (!window.confirm(`Remove ${what} from the do-not-contact list? Mail to ${entry.kind === 'domain' ? 'that domain' : 'that address'} will be allowed again.`)) return
    try {
      await suppressionsAPI.remove(entry.id)
      load()
    } catch (err) { toast.error(errMessage(err)) }
  }

  return (
    <div className="card card-pad stack mt-16" style={{ gap: 12 }}>
      <div>
        <b>Do not contact</b>
        <div className="tiny muted">
          Addresses and domains this app will never write to. Checked when a contact
          is added and again immediately before every send, so drafts written before
          an entry was added are refused too.
        </div>
      </div>

      <form className="row" style={{ gap: 8, flexWrap: 'wrap' }} onSubmit={add}>
        <input className="input" style={{ flex: '2 1 220px' }} value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="dana@acme.com or acme.com" />
        <input className="input" style={{ flex: '3 1 220px' }} value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Why (optional) — shown when a send is refused" />
        <Button type="submit" variant="primary" disabled={busy || !value.trim()}>
          Add
        </Button>
      </form>

      {failed ? (
        <div className="row small" style={{ gap: 8, color: 'var(--amber)' }}>
          <span>
            Could not load the do-not-contact list. It is still enforced at send
            time — this card just cannot show it.
          </span>
          <Button size="sm" onClick={load}>Try again</Button>
        </div>
      ) : entries === null ? null : entries.length === 0 ? (
        <div className="tiny muted">Nobody on the list.</div>
      ) : (
        <div className="stack" style={{ gap: 6 }}>
          {entries.map((entry) => (
            <div key={entry.id} className="row-between suppression-row">
              <span style={{ minWidth: 0 }}>
                <span className="small mono">
                  {entry.kind === 'domain' ? `@${entry.value}` : entry.value}
                </span>
                {entry.kind === 'domain' && (
                  <Chip tone="amber" title="Blocks every address at this domain, including its subdomains.">
                    whole domain
                  </Chip>
                )}
                {entry.reason && <div className="tiny muted">{entry.reason}</div>}
              </span>
              <button className="icon-btn danger" title="Remove from the list"
                onClick={() => remove(entry)}>
                <Trash2 size={14} />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

/* Business hours for sending.

   This is the only setting in the app that lets mail leave without anyone
   watching, so the card says so plainly and ships switched off. Turning it on
   is one half of the permission; the other half is picking "Send at…" on a
   specific batch, which is the only thing that ever queues anything. */
function SendWindow() {
  const [state, setState] = useState(null)      // server truth + derived fields
  const [draft, setDraft] = useState(null)
  const [saving, setSaving] = useState(false)

  const load = () => sendWindowAPI.get()
    .then(({ data }) => {
      setState(data)
      setDraft({
        enabled: !!data.enabled, timezone: data.timezone || '',
        days: [...(data.days || [])],
        start_hour: data.start_hour, end_hour: data.end_hour,
      })
    })
    .catch(() => {})

  useEffect(() => { load() }, [])

  if (!draft) return null

  const toggleDay = (d) => setDraft((s) => ({
    ...s,
    days: s.days.includes(d) ? s.days.filter((x) => x !== d) : [...s.days, d].sort(),
  }))

  const save = async () => {
    setSaving(true)
    try {
      await sendWindowAPI.update(draft)
      await load()
      toast.success('Sending window saved')
    } catch (e) { toast.error(errMessage(e)) }
    finally { setSaving(false) }
  }

  return (
    <div className="card card-pad stack mt-16" style={{ gap: 12 }}>
      <div className="row-between">
        <div className="row" style={{ gap: 8 }}>
          <Clock size={16} style={{ color: 'var(--text-2)' }} />
          <b>Sending window</b>
        </div>
        <label className="row small" style={{ gap: 6, cursor: 'pointer' }}>
          <input type="checkbox" className="checkbox" checked={draft.enabled}
            onChange={(e) => setDraft((s) => ({ ...s, enabled: e.target.checked }))} />
          Allow scheduled sending
        </label>
      </div>

      <div className="small muted">{state?.description}</div>

      {draft.enabled && (
        <>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            {DAY_NAMES.map((name, i) => (
              <button key={name} type="button"
                className={`chip ${draft.days.includes(i) ? 'chip-sky' : ''}`}
                style={{ cursor: 'pointer', border: '1px solid var(--border)' }}
                onClick={() => toggleDay(i)}>
                {name}
              </button>
            ))}
          </div>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
            <span className="tiny muted">Between</span>
            <input className="input" type="number" min={0} max={23} style={{ width: 66 }}
              value={draft.start_hour}
              onChange={(e) => setDraft((s) => ({
                ...s, start_hour: clampHour(e.target.value, s.start_hour) }))} />
            <span className="tiny muted">and</span>
            <input className="input" type="number" min={1} max={23} style={{ width: 66 }}
              value={draft.end_hour}
              onChange={(e) => setDraft((s) => ({
                ...s, end_hour: clampHour(e.target.value, s.end_hour) }))} />
            <span className="tiny muted">o&apos;clock, timezone</span>
            <input className="input" style={{ width: 190 }}
              placeholder={state?.detected_timezone || 'your local time'}
              value={draft.timezone}
              onChange={(e) => setDraft((s) => ({ ...s, timezone: e.target.value }))} />
          </div>
          <div className="tiny muted">
            Leave the timezone blank to use this machine&apos;s clock, or give an
            IANA name like <span className="mono">America/New_York</span>. These
            are <b>your</b> business hours, not the recipient&apos;s — we rarely
            know where they are, and a confident 9am in the wrong zone is 4am
            somewhere real.
          </div>
        </>
      )}

      <div className="tiny muted">
        Switching this on does not schedule anything by itself. It adds a
        <b> Send at…</b> button to the send dialog; a batch only waits if you
        pick it. That is the one path where email leaves without you watching.
      </div>

      {state?.scheduled_count > 0 && (
        <div className="small" style={{ color: 'var(--amber)' }}>
          {state.scheduled_count} email{state.scheduled_count === 1 ? '' : 's'} queued,
          next at {state.next_scheduled_at}.
        </div>
      )}

      <div>
        <Button size="sm" variant="primary" icon={Save} onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save window'}
        </Button>
      </div>
    </div>
  )
}

// Clamped on entry, not on save: a value that decides when real mail leaves
// must never be displayed as one thing and stored as another.
const clampHour = (value, fallback) => {
  const n = Math.round(Number(value))
  return Number.isFinite(n) ? Math.max(0, Math.min(23, n)) : fallback
}

/* How many times one silent contact gets chased, and how long the gaps are.

   Defaults to a single nudge after a week — what this app did before cadences
   existed. Extra rungs mean more mail to real people, so they are added
   deliberately here rather than arriving with an update. */
export const cadenceSummary = (cadence) => {
  const steps = cadence?.steps || []
  if (!cadence?.enabled || steps.length === 0) return 'No follow-ups will be drafted.'
  const days = steps.reduce((sum, gap, i) => [...sum, (sum[i - 1] || 0) + gap], [])
  return `${steps.length} follow-up${steps.length === 1 ? '' : 's'}, on day `
    + days.join(', ') + ' after the first email (if they stay silent).'
}

function FollowUpCadence() {
  const { settings, refreshSettings } = useApp()
  const [draft, setDraft] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (settings?.follow_up_cadence && draft === null) {
      setDraft({
        enabled: !!settings.follow_up_cadence.enabled,
        steps: [...(settings.follow_up_cadence.steps || [])],
      })
    }
  }, [settings, draft])

  if (!draft) return null

  const setStep = (i, value) => setDraft((d) => {
    const steps = [...d.steps]
    // Clamped on entry, not on save: an out-of-range gap decides when real
    // email goes out, so it must never be possible to look at a saved value
    // and a different stored one.
    // Rounded as well as clamped. A number input accepts "7.5"; the summary
    // then promised a 7.5-day gap and the save 422'd on List[int], leaving the
    // editor showing a value that was never stored.
    steps[i] = Math.max(1, Math.min(90, Math.round(Number(value)) || 1))
    return { ...d, steps }
  })

  const save = async () => {
    setSaving(true)
    try {
      const { data } = await cadenceAPI.update(draft)
      setDraft({ enabled: !!data.enabled, steps: [...(data.steps || [])] })
      await refreshSettings()
      toast.success('Follow-up cadence saved')
    } catch (e) { toast.error(errMessage(e)) }
    finally { setSaving(false) }
  }

  return (
    <div className="card card-pad stack mt-16" style={{ gap: 12 }}>
      <div className="row-between">
        <div className="row" style={{ gap: 8 }}>
          <CornerUpLeft size={16} style={{ color: 'var(--text-2)' }} />
          <b>Follow-up cadence</b>
        </div>
        <label className="row small" style={{ gap: 6, cursor: 'pointer' }}>
          <input type="checkbox" className="checkbox" checked={draft.enabled}
            onChange={(e) => setDraft((d) => ({ ...d, enabled: e.target.checked }))} />
          Draft follow-ups
        </label>
      </div>

      <div className="small muted">{cadenceSummary(draft)}</div>

      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        {draft.steps.map((gap, i) => (
          <div key={i} className="row" style={{ gap: 6 }}>
            <span className="tiny muted">
              {i === 0 ? 'First follow-up after' : `then ${ordinal(i + 1)} after`}
            </span>
            <input className="input" type="number" min={1} max={90} value={gap}
              style={{ width: 70 }} disabled={!draft.enabled}
              onChange={(e) => setStep(i, e.target.value)} />
            <span className="tiny muted">days</span>
            <Button size="sm" variant="ghost"
              onClick={() => setDraft((d) => ({ ...d, steps: d.steps.filter((_, j) => j !== i) }))}>
              Remove
            </Button>
          </div>
        ))}
        {draft.steps.length < 4 && (
          <Button size="sm" icon={Plus}
            onClick={() => setDraft((d) => ({ ...d, steps: [...d.steps, 7] }))}>
            Add step
          </Button>
        )}
      </div>

      <div className="tiny muted">
        Each gap counts from the last message that person actually received, not
        from the first. Nothing sends itself — due follow-ups are written as
        drafts for you to read and send. A reply, a bounce, or trashing the
        draft stops the sequence.
      </div>

      <div>
        <Button size="sm" variant="primary" icon={Save} onClick={save} disabled={saving}>
          {saving ? 'Saving…' : 'Save cadence'}
        </Button>
      </div>
    </div>
  )
}

const ordinal = (n) => ({ 2: 'the next', 3: 'the third', 4: 'the fourth' }[n] || `#${n}`)
