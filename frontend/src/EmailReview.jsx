import { useState, useEffect } from 'react'
import toast from 'react-hot-toast'
import { emailAPI, contactsAPI, usageAPI } from './api'
import './EmailReview.css'

function EmailReview() {
  const [emails, setEmails] = useState([])
  const [currentIndex, setCurrentIndex] = useState(0)
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [sending, setSending] = useState(false)
  const [usageStats, setUsageStats] = useState(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showContactSelection, setShowContactSelection] = useState(false)
  const [availableContacts, setAvailableContacts] = useState([])
  const [selectedContactIds, setSelectedContactIds] = useState(new Set())
  const [userName, setUserName] = useState(localStorage.getItem('userName') || 'Jason Li')
  const [userBackground, setUserBackground] = useState(localStorage.getItem('userBackground') || '')
  const [userEmail, setUserEmail] = useState(localStorage.getItem('userEmail') || 'jason.ye.li.7@gmail.com')
  const [resumePath, setResumePath] = useState(localStorage.getItem('resumePath') || '')
  const [attachResume, setAttachResume] = useState('resume28.pdf') // default 2028 resume; 'none' | 'resume28.pdf' | 'resume29.pdf'
  const [useTemplateOnly, setUseTemplateOnly] = useState(false)

  useEffect(() => {
    loadEmails()
    loadUsageStats()
  }, [])

  const loadEmails = async () => {
    try {
      const response = await emailAPI.getAll('pending')
      setEmails(response.data)
      if (response.data.length > 0 && currentIndex >= response.data.length) {
        setCurrentIndex(0)
      }
    } catch (error) {
      console.error('Failed to load emails', error)
    }
  }

  const loadUsageStats = async () => {
    try {
      const response = await usageAPI.getStats()
      setUsageStats(response.data)
    } catch (error) {
      console.error('Failed to load usage stats', error)
    }
  }

  const loadAvailableContacts = async () => {
    try {
      const response = await contactsAPI.getCategorized()
      const noEmailsContacts = response.data.no_emails || []
      setAvailableContacts(noEmailsContacts)
      // Auto-select up to the max allowed by rate limits
      const perMinute = usageStats?.generations_per_minute ?? 15
      const remainingDaily = usageStats?.remaining_generations ?? 500
      const maxToSelect = Math.max(1, Math.min(noEmailsContacts.length, perMinute, remainingDaily))
      setSelectedContactIds(new Set(noEmailsContacts.slice(0, maxToSelect).map(c => c.id)))
      setShowContactSelection(true)
    } catch (error) {
      toast.error('Failed to load contacts')
      console.error(error)
    }
  }

  const handleGenerateClick = () => {
    loadAvailableContacts()
  }

  const handleToggleContact = (contactId) => {
    const newSelected = new Set(selectedContactIds)
    if (newSelected.has(contactId)) {
      newSelected.delete(contactId)
    } else {
      newSelected.add(contactId)
    }
    setSelectedContactIds(newSelected)
  }

  const handleSelectAll = () => {
    setSelectedContactIds(new Set(availableContacts.map(c => c.id)))
  }

  const handleDeselectAll = () => {
    setSelectedContactIds(new Set())
  }

  const handleGenerate = async () => {
    if (selectedContactIds.size === 0) {
      toast.error('Please select at least one contact')
      return
    }

    try {
      setGenerating(true)
      setShowContactSelection(false)
      const contactIdsArray = Array.from(selectedContactIds)
      const response = await emailAPI.generate(
        contactIdsArray,
        userName || undefined,
        userBackground || undefined,
        userEmail || undefined,
        useTemplateOnly
      )
      setEmails(response.data)
      setCurrentIndex(0)
      toast.success(`Generated ${response.data.length} emails`)
      const usedFallback = response.data.some((e) => e.used_template_fallback && e.fallback_reason === 'llm_unavailable')
      if (usedFallback) {
        toast('AI quota exceeded or unavailable. Emails used the template. Use "Use template only" to skip API calls.', { icon: 'ℹ️', duration: 6000 })
      }
      window.dispatchEvent(new CustomEvent('contacts-refresh'))
      await loadUsageStats()
    } catch (error) {
      const message = error.response?.data?.detail || 'Failed to generate emails'
      toast.error(message)
      setShowContactSelection(true) // Show selection again on error
    } finally {
      setGenerating(false)
    }
  }

  const handleSaveSettings = () => {
    localStorage.setItem('userName', userName)
    localStorage.setItem('userBackground', userBackground)
    localStorage.setItem('userEmail', userEmail)
    localStorage.setItem('resumePath', resumePath)
    setShowSettings(false)
    toast.success('Settings saved')
    
    // Update resume path in backend via API if needed
    if (resumePath) {
      // Note: Resume path should be set in .env file for backend
      toast('Note: Make sure RESUME_PATH is set in .env file', { icon: 'ℹ️' })
    }
  }

  const handleDisconnectGmail = async () => {
    try {
      const res = await emailAPI.disconnectGmail()
      toast.success(res.data?.message || 'Gmail disconnected. Next time you send, your browser will open to sign in again.')
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Failed to disconnect Gmail')
    }
  }

  const handleAccept = async (emailId) => {
    try {
      // First accept the email
      await emailAPI.updateStatus(emailId, 'accepted')
      
      // Then automatically send it
      setSending(true)
      const resumeFile = attachResume === 'none' ? null : attachResume
      const response = await emailAPI.send([emailId], resumeFile, userEmail || undefined)
      toast.success('Email accepted and sent!')
      await loadEmails()
      await loadUsageStats()
      setSending(false)
    } catch (error) {
      setSending(false)
      const message = error.response?.data?.detail || 'Failed to send email'
      toast.error(message)
    }
  }

  const handleTrash = async (emailId) => {
    try {
      await emailAPI.updateStatus(emailId, 'trashed')
      await loadEmails()
      toast.success('Email trashed')
    } catch (error) {
      toast.error('Failed to update email')
    }
  }

  const handleSendAll = async () => {
    const sendableEmails = emails.filter((e) => e.status === 'accepted' || e.status === 'pending')
    if (sendableEmails.length === 0) {
      toast.error('No emails to send (accept or send from generated)')
      return
    }

    if (!window.confirm(`Send ${sendableEmails.length} emails?`)) {
      return
    }

    try {
      setSending(true)
      const emailIds = sendableEmails.map((e) => e.id)
      const resumeFile = attachResume === 'none' ? null : attachResume
      const response = await emailAPI.send(emailIds, resumeFile, userEmail || undefined)
      toast.success(`Sent ${response.data.sent} emails`)
      await loadEmails()
      await loadUsageStats()
    } catch (error) {
      const message = error.response?.data?.detail || 'Failed to send emails'
      toast.error(message)
    } finally {
      setSending(false)
    }
  }

  const handleSendOne = async (emailId) => {
    try {
      setSending(true)
      const resumeFile = attachResume === 'none' ? null : attachResume
      const response = await emailAPI.send([emailId], resumeFile, userEmail || undefined)
      toast.success(response.data.sent ? 'Email sent' : 'Failed to send')
      await loadEmails()
      await loadUsageStats()
    } catch (error) {
      const message = error.response?.data?.detail || 'Failed to send email'
      toast.error(message)
    } finally {
      setSending(false)
    }
  }

  const currentEmail = emails[currentIndex]

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyPress = (e) => {
      if (!currentEmail) return
      if (e.key === 'a' || e.key === 'A') {
        handleAccept(currentEmail.id)
      } else if (e.key === 't' || e.key === 'T') {
        handleTrash(currentEmail.id)
      } else if (e.key === 'ArrowRight' && currentIndex < emails.length - 1) {
        setCurrentIndex(currentIndex + 1)
      } else if (e.key === 'ArrowLeft' && currentIndex > 0) {
        setCurrentIndex(currentIndex - 1)
      }
    }

    window.addEventListener('keydown', handleKeyPress)
    return () => window.removeEventListener('keydown', handleKeyPress)
  }, [currentEmail, currentIndex, emails.length])

  if (emails.length === 0 && !showContactSelection) {
    return (
      <div className="email-review">
        <div className="email-review-header">
          <h2>Review Emails</h2>
          <button
            className="btn btn-primary"
            onClick={handleGenerateClick}
            disabled={generating}
          >
            {generating ? 'Generating...' : 'Generate Emails'}
          </button>
        </div>
        <div className="empty-state">
          <p>No emails to review. Click "Generate Emails" to select contacts and create emails.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="email-review">
      <div className="email-review-header">
        <h2>Review Emails</h2>
        <div className="header-actions">
          {usageStats && (
            <div className="usage-stats">
              <span>Sent today: {usageStats.emails_sent_today}/{usageStats.daily_limit}</span>
              <span className="usage-stats-ai" title="Gemini free tier limits (see Google docs for current values)">
                AI: ~{usageStats.generations_per_minute ?? 15}/min, ~{usageStats.remaining_generations ?? '—'}/day left
              </span>
            </div>
          )}
          <button
            className="btn btn-secondary"
            onClick={() => setShowSettings(!showSettings)}
          >
            Settings
          </button>
          <button
            className="btn btn-secondary"
            onClick={handleGenerateClick}
            disabled={generating}
          >
            Generate More
          </button>
          <span className="attach-resume-wrap">
            <label htmlFor="attach-resume-review">Attach resume:</label>
            <select
              id="attach-resume-review"
              value={attachResume}
              onChange={(e) => setAttachResume(e.target.value)}
              className="attach-resume-select"
            >
              <option value="none">None</option>
              <option value="resume28.pdf">2028 resume</option>
              <option value="resume29.pdf">2029 resume</option>
            </select>
          </span>
          <button
            className="btn btn-success"
            onClick={handleSendAll}
            disabled={sending || emails.filter((e) => e.status === 'accepted' || e.status === 'pending').length === 0}
          >
            {sending ? 'Sending...' : 'Send All'}
          </button>
        </div>
      </div>

      {showContactSelection && (
        <div className="settings-modal">
          <div className="settings-content contact-selection-content">
            <h3>Select Contacts to Generate Emails</h3>
            <div className="contact-selection-actions">
              <button type="button" className="btn btn-secondary" onClick={handleSelectAll}>
                Select All
              </button>
              <button type="button" className="btn btn-secondary" onClick={handleDeselectAll}>
                Deselect All
              </button>
              <span className="contact-selection-count">
                {selectedContactIds.size} of {availableContacts.length} selected
              </span>
            </div>
            <div className="contact-list-wrap">
              {availableContacts.length === 0 ? (
                <p className="contact-list-empty">
                  No contacts available. Add contacts in the "No Emails Generated" section.
                </p>
              ) : (
                <table className="contact-table">
                  <thead>
                    <tr>
                      <th className="contact-table-col-select">Select</th>
                      <th>Name</th>
                      <th>Company</th>
                      <th>Email</th>
                    </tr>
                  </thead>
                  <tbody>
                    {availableContacts.map((contact) => (
                      <tr key={contact.id}>
                        <td>
                          <input
                            type="checkbox"
                            checked={selectedContactIds.has(contact.id)}
                            onChange={() => handleToggleContact(contact.id)}
                          />
                        </td>
                        <td>{contact.name || '(No name)'}</td>
                        <td>{contact.company || '(No company)'}</td>
                        <td>{contact.email || '(No email)'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="settings-actions">
              <label className="use-template-only-wrap" style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
                <input
                  type="checkbox"
                  checked={useTemplateOnly}
                  onChange={(e) => setUseTemplateOnly(e.target.checked)}
                />
                <span>Use template only (no AI, no API calls)</span>
              </label>
              <button 
                className="btn btn-primary" 
                onClick={handleGenerate}
                disabled={generating || selectedContactIds.size === 0}
              >
                {generating ? 'Generating...' : `Generate Emails (${selectedContactIds.size})`}
              </button>
              <button 
                className="btn btn-secondary" 
                onClick={() => setShowContactSelection(false)}
                disabled={generating}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {showSettings && (
        <div className="settings-modal">
          <div className="settings-content">
            <h3>Email Generation Settings</h3>
            <div className="settings-form">
              <label className="settings-form-field">
                <span className="input-label">Your Name</span>
                <input
                  type="text"
                  value={userName}
                  onChange={(e) => setUserName(e.target.value)}
                  placeholder="e.g. Jason Li"
                />
              </label>
              <label className="settings-form-field">
                <span className="input-label">Your Email</span>
                <input
                  type="email"
                  value={userEmail}
                  onChange={(e) => setUserEmail(e.target.value)}
                  placeholder="e.g. you@example.com"
                />
              </label>
              <label className="settings-form-field">
                <span className="input-label">Your Background / Qualifications</span>
                <textarea
                  value={userBackground}
                  onChange={(e) => setUserBackground(e.target.value)}
                  placeholder="Computer Science student with experience in Python and React..."
                  rows={4}
                />
              </label>
              <label className="settings-form-field">
                <span className="input-label">Resume File Path (on server)</span>
                <input
                  type="text"
                  value={resumePath}
                  onChange={(e) => setResumePath(e.target.value)}
                  placeholder="resume.pdf (relative to project root or absolute path)"
                />
                <span className="input-helper">
                  Place your resume file in the project directory and enter the path here. Also set RESUME_PATH in your .env file.
                </span>
              </label>
              <div className="settings-actions">
                <button className="btn btn-primary" onClick={handleSaveSettings}>
                  Save
                </button>
                <button className="btn btn-secondary" onClick={() => setShowSettings(false)}>
                  Cancel
                </button>
                <button type="button" className="btn btn-secondary" onClick={handleDisconnectGmail} title="Remove saved sign-in so next send opens browser to sign in again">
                  Reconnect Gmail
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="email-review-content">
        <div className="email-navigation">
          <button
            className="btn-nav"
            onClick={() => setCurrentIndex(Math.max(0, currentIndex - 1))}
            disabled={currentIndex === 0}
          >
            ← Previous
          </button>
          <span className="email-counter">
            {currentIndex + 1} of {emails.length}
          </span>
          <button
            className="btn-nav"
            onClick={() => setCurrentIndex(Math.min(emails.length - 1, currentIndex + 1))}
            disabled={currentIndex === emails.length - 1}
          >
            Next →
          </button>
        </div>

        {currentEmail && (
          <div className="email-card">
            <div className="email-header">
              <div className="email-contact">
                <strong>{currentEmail.contact_name}</strong>
                <span>{currentEmail.contact_email}</span>
                <span className="company-badge">{currentEmail.company}</span>
              </div>
              <div className="email-status">
                Status: <span className={`status-${currentEmail.status}`}>{currentEmail.status}</span>
              </div>
            </div>

            <div className="email-content">
              <div className="email-subject">
                <strong>Subject:</strong> {currentEmail.subject}
              </div>
              <div className="email-body">
                {currentEmail.body.split('\n').map((line, i) => (
                  <p key={i}>{line}</p>
                ))}
              </div>
            </div>

            <div className="email-actions">
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => handleTrash(currentEmail.id)}
              >
                Trash (T)
              </button>
              {(currentEmail.status === 'pending' || currentEmail.status === 'accepted') && (
                <button
                  type="button"
                  className="btn btn-success"
                  onClick={() => handleSendOne(currentEmail.id)}
                  disabled={sending}
                >
                  {sending ? 'Sending...' : 'Send'}
                </button>
              )}
              <button
                type="button"
                className="btn btn-success"
                onClick={() => handleAccept(currentEmail.id)}
              >
                Accept (A)
              </button>
            </div>

            <div className="keyboard-hints">
              <small>Keyboard shortcuts: A = Accept, T = Trash, Arrow keys = Navigate</small>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default EmailReview
