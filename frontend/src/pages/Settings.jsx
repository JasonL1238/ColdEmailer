/* Settings: sender profile, Gmail connection, AI provider, limits */
import { useEffect, useState } from 'react'
import toast from 'react-hot-toast'
import { Save, Unplug, CheckCircle2, XCircle, Sparkles, Mail } from 'lucide-react'
import { settingsAPI, gmailAPI, errMessage } from '../api'
import { Button, Chip } from '../ui'
import { useApp } from '../App'

const FIELDS = [
  { key: 'full_name', label: 'Full name', placeholder: 'Jane Smith' },
  { key: 'email', label: 'Email', placeholder: 'you@school.edu', hint: 'Used in your signature and as the From address' },
  { key: 'phone', label: 'Phone', placeholder: '555-000-0000' },
  { key: 'school', label: 'School / affiliation', placeholder: 'University of…' },
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
        <div className="card card-pad mb-16 row" style={{ background: 'var(--amber-soft)', borderColor: '#f3dcb6', gap: 10 }}>
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
    </div>
  )
}
