import { useState, useEffect, useMemo } from 'react'
import toast from 'react-hot-toast'
import { contactsAPI, emailAPI } from './api'
import { sendTelemetry } from './config'
import './CSVManager.css'

// Get user info for email generation
const getUserInfo = () => ({
  userName: localStorage.getItem('userName') || 'Jason Li',
  userBackground: localStorage.getItem('userBackground') || '',
  userEmail: localStorage.getItem('userEmail') || 'jason.ye.li.7@gmail.com'
})

function CSVManager() {
  const [categorizedContacts, setCategorizedContacts] = useState({
    emailed: [],
    emails_generated: [],
    no_emails: []
  })
  const [emailsByContact, setEmailsByContact] = useState({}) // contact_id -> email
  const [followUpReminders, setFollowUpReminders] = useState([])
  const [loading, setLoading] = useState(true)
  const [searchTerm, setSearchTerm] = useState('')
  const [editingCell, setEditingCell] = useState(null)
  const [hasChanges, setHasChanges] = useState(false)
  const [activeTab, setActiveTab] = useState('emailed') // 'emailed', 'generated', 'no_emails'
  const [viewingEmail, setViewingEmail] = useState(null) // Email being viewed
  const [sendingEmail, setSendingEmail] = useState(false)
  const [sortBy, setSortBy] = useState('sent_at_desc') // 'sent_at_desc', 'sent_at_asc', 'name'
  const [filterFollowUps, setFilterFollowUps] = useState('all') // 'all', 'unsent_followups'
  const [allEmails, setAllEmails] = useState([]) // All emails including follow-ups

  const handleTabChange = (tab) => {
    try {
      sendTelemetry('CSVManager.jsx:handleTabChange:start', 'Tab change initiated', { fromTab: activeTab, toTab: tab })
    } catch (e) {}
    try {
      setActiveTab(tab)
      sendTelemetry('CSVManager.jsx:handleTabChange:success', 'Tab changed successfully', { newTab: tab })
    } catch (error) {
      sendTelemetry('CSVManager.jsx:handleTabChange:error', 'Tab change failed', { error: error.message, stack: error.stack })
      console.error('Tab change error:', error)
      throw error
    }
  }
  
  // User info for follow-up generation
  const [userName] = useState(localStorage.getItem('userName') || 'Jason Li')
  const [userBackground] = useState(localStorage.getItem('userBackground') || '')
  const [userEmail] = useState(localStorage.getItem('userEmail') || 'jason.ye.li.7@gmail.com')

  useEffect(() => {
    loadCategorizedContacts()
    loadFollowUpReminders()
  }, [])

  const loadCategorizedContacts = async () => {
    try {
      setLoading(true)
      const response = await contactsAPI.getCategorized()
      setCategorizedContacts({
        emailed: response.data.emailed,
        emails_generated: response.data.emails_generated,
        no_emails: response.data.no_emails
      })
      
      // Set emails map from response or load separately
      if (response.data.emails) {
        const emailsMap = {}
        Object.entries(response.data.emails).forEach(([contactId, emailData]) => {
          emailsMap[contactId] = emailData
        })
        setEmailsByContact(emailsMap)
      } else {
        // Fallback: load all emails separately
        const emailsResponse = await emailAPI.getAll()
        const emailsMap = {}
        emailsResponse.data.forEach(email => {
          emailsMap[email.contact_id] = email
        })
        setEmailsByContact(emailsMap)
      }
    } catch (error) {
      toast.error('Failed to load contacts')
      console.error(error)
    } finally {
      setLoading(false)
    }
  }

  const loadFollowUpReminders = async () => {
    try {
      const response = await contactsAPI.getFollowUpReminders()
      setFollowUpReminders(response.data)
    } catch (error) {
      console.error('Failed to load follow-up reminders:', error)
    }
  }

  const handleCellEdit = (contactId, field, value) => {
    // Update in the appropriate category
    const updateCategory = (category) => {
      const updated = category.map((c) =>
        c.id === contactId ? { ...c, [field]: value } : c
      )
      return updated
    }

    setCategorizedContacts({
      emailed: updateCategory(categorizedContacts.emailed),
      emails_generated: updateCategory(categorizedContacts.emails_generated),
      no_emails: updateCategory(categorizedContacts.no_emails)
    })
    setHasChanges(true)
  }

  const handleSave = async () => {
    try {
      // Update all changed contacts across all categories
      const allContacts = [
        ...categorizedContacts.emailed,
        ...categorizedContacts.emails_generated,
        ...categorizedContacts.no_emails
      ]
      
      for (const contact of allContacts) {
        await contactsAPI.update(contact.id, {
          name: contact.name,
          company: contact.company,
          email: contact.email,
          status: contact.status,
        })
      }
      await contactsAPI.save()
      setHasChanges(false)
      toast.success('Contacts saved successfully')
      await loadCategorizedContacts()
    } catch (error) {
      toast.error('Failed to save contacts')
      console.error(error)
    }
  }

  const handleAddContact = async () => {
    sendTelemetry('CSVManager.jsx:handleAddContact:start', 'Add contact called', { activeTab })
    
    // Only allow adding contacts in "No Emails Generated" section
    if (activeTab !== 'no_emails') {
      sendTelemetry('CSVManager.jsx:handleAddContact:wrongTab', 'Add contact blocked - wrong tab', { activeTab })
      toast.error('You can only add contacts in the "No Emails Generated" section')
      return
    }
    
    const newContact = {
      name: '',
      company: '',
      email: '',
      status: 'pending',
    }
    
    sendTelemetry('CSVManager.jsx:handleAddContact:beforeAPI', 'About to call API', { newContact })
    
    try {
      const response = await contactsAPI.create(newContact)
      sendTelemetry('CSVManager.jsx:handleAddContact:success', 'API call successful', { contactId: response.data?.id })
      const contactId = response.data.id
      toast.success('Contact added and saved')
      await loadCategorizedContacts()
      setEditingCell({ id: contactId, field: 'name' })
    } catch (error) {
      sendTelemetry('CSVManager.jsx:handleAddContact:error', 'API call failed', { error: error.message, response: error.response?.data, status: error.response?.status })
      toast.error('Failed to save contact')
      console.error(error)
    }
  }

  const handleViewEmail = (email) => {
    setViewingEmail(email)
  }

  const handleSendEmail = async (emailId) => {
    if (!window.confirm('Send this email now?')) {
      return
    }
    
    try {
      setSendingEmail(true)
      const response = await emailAPI.send([emailId])
      toast.success('Email sent!')
      await loadCategorizedContacts()
      setViewingEmail(null) // Close email view
    } catch (error) {
      const message = error.response?.data?.detail || 'Failed to send email'
      toast.error(message)
    } finally {
      setSendingEmail(false)
    }
  }

  const handleDeleteEmail = async (contactId) => {
    const email = getEmailForContact(contactId)
    if (!email) {
      toast.error('No email found for this contact')
      return
    }
    
    if (!window.confirm('Delete the email for this contact? The contact will move to "No Emails Generated" section.')) {
      return
    }
    
    try {
      await emailAPI.delete(email.id)
      await loadCategorizedContacts()
      setViewingEmail(null) // Close email view if open
      toast.success('Email deleted. Contact moved to "No Emails Generated" section.')
    } catch (error) {
      toast.error('Failed to delete email')
      console.error(error)
    }
  }


  const handleDelete = async (id) => {
    if (!window.confirm('Are you sure you want to delete this contact?')) {
      return
    }
    try {
      await contactsAPI.delete(id)
      await loadCategorizedContacts()
      toast.success('Contact deleted')
    } catch (error) {
      toast.error('Failed to delete contact')
      console.error(error)
    }
  }

  const handleFileUpload = async (e) => {
    const file = e.target.files[0]
    if (!file) return

    try {
      await contactsAPI.upload(file)
      await loadCategorizedContacts()
      toast.success('CSV uploaded successfully')
    } catch (error) {
      toast.error('Failed to upload CSV')
      console.error(error)
    }
  }

  const handleExport = async () => {
    try {
      // Export based on active tab
      const section = activeTab === 'emailed' ? 'emailed' : 
                     activeTab === 'generated' ? 'emails_generated' : 
                     activeTab === 'no_emails' ? 'no_emails' : null
      
      const response = await contactsAPI.export(section)
      const url = window.URL.createObjectURL(new Blob([response.data]))
      const link = document.createElement('a')
      link.href = url
      
      // Set filename based on section
      const filename = section === 'emailed' ? 'contacts_emailed.csv' :
                      section === 'emails_generated' ? 'contacts_emails_generated.csv' :
                      section === 'no_emails' ? 'contacts_no_emails.csv' :
                      'contacts.csv'
      
      link.setAttribute('download', filename)
      document.body.appendChild(link)
      link.click()
      link.remove()
      toast.success(`CSV exported: ${filename}`)
    } catch (error) {
      toast.error('Failed to export CSV')
      console.error(error)
    }
  }

  const getEmailForContact = (contactId) => {
    return emailsByContact[contactId]
  }

  const getFollowUpEmail = (contactId) => {
    // Get follow-up email for this contact (if exists)
    return allEmails.find(e => e.contact_id === contactId && e.is_follow_up)
  }

  const needsFollowUp = (contactId) => {
    return followUpReminders.some(reminder => reminder.contact.id === contactId)
  }

  const getFollowUpInfo = (contactId) => {
    return followUpReminders.find(reminder => reminder.contact.id === contactId)
  }

  const formatTimeAgo = (dateString) => {
    if (!dateString) return '—'
    const date = new Date(dateString)
    const now = new Date()
    const diffMs = now - date
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))
    const diffHours = Math.floor(diffMs / (1000 * 60 * 60))
    const diffMinutes = Math.floor(diffMs / (1000 * 60))
    
    if (diffDays > 0) {
      return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`
    } else if (diffHours > 0) {
      return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`
    } else if (diffMinutes > 0) {
      return `${diffMinutes} minute${diffMinutes !== 1 ? 's' : ''} ago`
    } else {
      return 'Just now'
    }
  }

  const handleGenerateFollowUp = async (emailId) => {
    try {
      const userInfo = getUserInfo()
      const response = await contactsAPI.generateFollowUp(emailId, userInfo)
      toast.success('Follow-up email generated!')
      await loadCategorizedContacts()
    } catch (error) {
      const message = error.response?.data?.detail || 'Failed to generate follow-up'
      toast.error(message)
      console.error(error)
    }
  }

  const handleSendFollowUp = async (followUpEmailId) => {
    if (!window.confirm('Send this follow-up email now?')) {
      return
    }
    
    try {
      setSendingEmail(true)
      const response = await emailAPI.send([followUpEmailId])
      toast.success('Follow-up email sent!')
      await loadCategorizedContacts()
    } catch (error) {
      const message = error.response?.data?.detail || 'Failed to send follow-up'
      toast.error(message)
    } finally {
      setSendingEmail(false)
    }
  }

  const renderEmailedSection = () => {
    // #region agent log
    try {
      sendTelemetry('CSVManager.jsx:renderEmailedSection:start', 'Rendering emailed section', { contactsCount: categorizedContacts.emailed?.length, isArray: Array.isArray(categorizedContacts.emailed) })
    } catch (e) {}
    // #endregion
    try {
      // Get all emails for these contacts (including follow-ups)
      const contactsWithEmails = (categorizedContacts.emailed || []).map(contact => {
        const email = getEmailForContact(contact.id)
        const followUp = getFollowUpEmail(contact.id)
        return { contact, email, followUp }
      })

      // Filter for unsent follow-ups if requested
      let filtered = contactsWithEmails
      if (filterFollowUps === 'unsent_followups') {
        filtered = contactsWithEmails.filter(({ followUp }) => 
          followUp && followUp.status !== 'sent' && !followUp.follow_up_sent_at
        )
      }

      // Filter by search term (name, company, email)
      if (searchTerm && searchTerm.trim()) {
        const term = searchTerm.trim().toLowerCase()
        filtered = filtered.filter(({ contact }) =>
          (contact.name || '').toLowerCase().includes(term) ||
          (contact.company || '').toLowerCase().includes(term) ||
          (contact.email || '').toLowerCase().includes(term)
        )
      }

      // Sort contacts
      const sorted = [...filtered].sort((a, b) => {
        if (sortBy === 'sent_at_desc') {
          const aDate = a.email?.sent_at || a.followUp?.sent_at || ''
          const bDate = b.email?.sent_at || b.followUp?.sent_at || ''
          return new Date(bDate) - new Date(aDate)
        } else if (sortBy === 'sent_at_asc') {
          const aDate = a.email?.sent_at || a.followUp?.sent_at || ''
          const bDate = b.email?.sent_at || b.followUp?.sent_at || ''
          return new Date(aDate) - new Date(bDate)
        } else if (sortBy === 'name') {
          return (a.contact.name || '').localeCompare(b.contact.name || '')
        }
        return 0
      })

      return (
        <section className="section-emailed">
          <div className="section-toolbar">
            <h3>Emailed ({filtered.length})</h3>
            <div className="section-filters">
              <label className="filters-row">
                <span>Sort:</span>
                <select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
                  <option value="sent_at_desc">Newest First</option>
                  <option value="sent_at_asc">Oldest First</option>
                  <option value="name">Name</option>
                </select>
              </label>
              <label className="filters-row">
                <span>Filter:</span>
                <select value={filterFollowUps} onChange={(e) => setFilterFollowUps(e.target.value)}>
                  <option value="all">All Emails</option>
                  <option value="unsent_followups">Unsent Follow-ups</option>
                </select>
              </label>
            </div>
          </div>
          <div className="table-container">
            <table className="contacts-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Company</th>
                  <th>Email</th>
                  <th className="table-numeric">Time Ago</th>
                  <th>Response</th>
                  <th>Follow-Up</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(({ contact, email, followUp }) => {
                  const sentEmail = email?.status === 'sent' ? email : null
                  const sentDate = sentEmail?.sent_at || followUp?.sent_at
                  
                  return (
                    <tr key={contact.id}>
                      <td>{contact.name}</td>
                      <td>{contact.company}</td>
                      <td>{contact.email}</td>
                      <td>
                        {sentDate ? (
                          <span className="sent-date" title={new Date(sentDate).toLocaleString()}>
                            {formatTimeAgo(sentDate)}
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td>
                        {sentEmail?.has_response ? (
                          <span className="response-badge response-yes">
                            ✓ Responded {sentEmail.response_date ? formatTimeAgo(sentEmail.response_date) : ''}
                          </span>
                        ) : sentEmail ? (
                          <span className="response-badge response-no">No response</span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td>
                        {followUp ? (
                          <div className="follow-up-section">
                            {followUp.status === 'sent' ? (
                              <span className="follow-up-badge">Follow-up sent</span>
                            ) : (
                              <>
                                <span className="follow-up-badge">Follow-up generated</span>
                                <button
                                  className="btn btn-small btn-success"
                                  onClick={() => handleSendFollowUp(followUp.id)}
                                  disabled={sendingEmail}
                                  title="Send follow-up email"
                                >
                                  {sendingEmail ? 'Sending...' : 'Send Follow-up'}
                                </button>
                              </>
                            )}
                          </div>
                        ) : sentEmail && !sentEmail.has_response ? (
                          <button
                            className="btn btn-small btn-primary"
                            onClick={() => handleGenerateFollowUp(sentEmail.id)}
                            title="Generate follow-up email"
                          >
                            Generate Follow-up
                          </button>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                      <td>
                        <select
                          value={contact.status}
                          onChange={(e) => handleCellEdit(contact.id, 'status', e.target.value)}
                          className="status-select"
                        >
                          <option value="pending">Pending</option>
                          <option value="trashed">Trashed</option>
                          <option value="sent">Sent</option>
                        </select>
                      </td>
                      <td>
                        <div className="action-buttons">
                          {followUp && (
                            <button
                              className="btn btn-small btn-primary"
                              onClick={() => setViewingEmail(followUp)}
                              title="View follow-up email"
                            >
                              View Follow-up
                            </button>
                          )}
                          <button
                            type="button"
                            className="btn btn-small btn-icon"
                            onClick={() => handleDelete(contact.id)}
                            title="Delete contact"
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </section>
      )
    } catch (error) {
      // #region agent log
      sendTelemetry('CSVManager.jsx:renderEmailedSection:error', 'Error rendering emailed section', { error: error.message, stack: error.stack })
      // #endregion
      console.error('Error rendering emailed section:', error)
      return <div>Error loading emailed contacts: {error.message}</div>
    }
  }

  const renderGeneratedSection = () => {
    // #region agent log
    try {
      sendTelemetry('CSVManager.jsx:renderGeneratedSection:start', 'Rendering generated section', { contactsCount: categorizedContacts.emails_generated?.length, isArray: Array.isArray(categorizedContacts.emails_generated) })
    } catch (e) {}
    // #endregion
    try {
      return (
        <section className="section-emails-generated">
          <h3>Emails Generated - Not Sent ({categorizedContacts.emails_generated?.length || 0})</h3>
          <p className="section-description">
            Includes pending, accepted, and trashed emails. Click "View Email" to see the email content, then send or delete it.
          </p>
          <div className="table-container">
            <table className="contacts-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Company</th>
                  <th>Email</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {renderContactTable(categorizedContacts.emails_generated || [], false, true, true)}
              </tbody>
            </table>
          </div>
        </section>
      )
    } catch (error) {
      // #region agent log
      sendTelemetry('CSVManager.jsx:renderGeneratedSection:error', 'Error rendering generated section', { error: error.message, stack: error.stack })
      // #endregion
      console.error('Error rendering generated section:', error)
      return <div>Error loading generated contacts: {error.message}</div>
    }
  }

  const renderNoEmailsSection = () => {
    // #region agent log
    try {
      sendTelemetry('CSVManager.jsx:renderNoEmailsSection:start', 'Rendering no_emails section', { contactsCount: categorizedContacts.no_emails?.length, isArray: Array.isArray(categorizedContacts.no_emails) })
    } catch (e) {}
    // #endregion
    try {
      return (
        <section className="section-no-emails">
          <h3>No Emails Generated ({categorizedContacts.no_emails?.length || 0})</h3>
          <p className="section-description">
            These contacts don't have generated emails yet. 
            Click "Generate Emails" in the Review Emails tab to select and generate emails for contacts.
          </p>
          <div className="table-container">
            <table className="contacts-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Company</th>
                  <th>Email</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {renderContactTable(categorizedContacts.no_emails || [], false, false, false)}
              </tbody>
            </table>
          </div>
        </section>
      )
    } catch (error) {
      // #region agent log
      sendTelemetry('CSVManager.jsx:renderNoEmailsSection:error', 'Error rendering no_emails section', { error: error.message, stack: error.stack })
      // #endregion
      console.error('Error rendering no_emails section:', error)
      return <div>Error loading no_emails contacts: {error.message}</div>
    }
  }

  const renderContactTable = (contacts, showTracking = false, showDeleteEmail = false, showEmailActions = false) => {
    // #region agent log
    try {
      sendTelemetry('CSVManager.jsx:renderContactTable:start', 'Rendering contact table', { contactsCount: contacts?.length, showTracking, showDeleteEmail, showEmailActions })
    } catch (e) {}
    // #endregion
    
    if (!contacts || !Array.isArray(contacts)) {
      // #region agent log
      sendTelemetry('CSVManager.jsx:renderContactTable:invalid', 'Invalid contacts array', { contacts })
      // #endregion
      return (
        <tr>
          <td colSpan={5} className="empty-state">
            Error: Invalid contacts data
          </td>
        </tr>
      )
    }
    
    const filtered = searchTerm
      ? contacts.filter(c => 
          c && (c.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
          c.company?.toLowerCase().includes(searchTerm.toLowerCase()) ||
          c.email?.toLowerCase().includes(searchTerm.toLowerCase()))
        )
      : contacts

    const colSpan = showTracking ? 7 : (showDeleteEmail || showEmailActions ? 6 : 5)
    
    if (filtered.length === 0) {
      return (
        <tr>
          <td colSpan={colSpan} className="empty-state">
            No contacts found.
          </td>
        </tr>
      )
    }

    return filtered.map((contact) => {
      if (!contact || !contact.id) {
        return null // Skip invalid contacts
      }
      
      const email = getEmailForContact(contact.id)
      const followUpInfo = getFollowUpInfo(contact.id)
      const needsFollowUpBadge = needsFollowUp(contact.id)

      return (
        <tr key={contact.id}>
          <td>
            <EditableCell
              value={contact.name}
              onSave={(value) => handleCellEdit(contact.id, 'name', value)}
              editing={editingCell?.id === contact.id && editingCell?.field === 'name'}
              onEdit={() => setEditingCell({ id: contact.id, field: 'name' })}
              onCancel={() => setEditingCell(null)}
            />
          </td>
          <td>
            <EditableCell
              value={contact.company}
              onSave={(value) => handleCellEdit(contact.id, 'company', value)}
              editing={editingCell?.id === contact.id && editingCell?.field === 'company'}
              onEdit={() => setEditingCell({ id: contact.id, field: 'company' })}
              onCancel={() => setEditingCell(null)}
            />
          </td>
          <td>
            <EditableCell
              value={contact.email}
              onSave={(value) => handleCellEdit(contact.id, 'email', value)}
              editing={editingCell?.id === contact.id && editingCell?.field === 'email'}
              onEdit={() => setEditingCell({ id: contact.id, field: 'email' })}
              onCancel={() => setEditingCell(null)}
              type="email"
            />
          </td>
          {showTracking && (
            <>
              <td>
                {email?.sent_at ? (
                  <span className="sent-date">
                    {new Date(email.sent_at).toLocaleDateString()}
                  </span>
                ) : (
                  <span className="text-muted">—</span>
                )}
              </td>
              <td>
                {email?.has_response ? (
                  <span className="response-badge response-yes">
                    ✓ Responded {email.response_date ? new Date(email.response_date).toLocaleDateString() : ''}
                  </span>
                ) : email?.sent_at ? (
                  <span className="response-badge response-no">No response</span>
                ) : (
                  <span className="text-muted">—</span>
                )}
              </td>
              <td>
                {needsFollowUpBadge && email && (
                  <div className="follow-up-section">
                    <span className="follow-up-badge">
                      Follow-up ({followUpInfo?.days_since_sent || 0} days)
                    </span>
                    <button
                      className="btn btn-small btn-primary"
                      onClick={() => handleGenerateFollowUp(email.id)}
                      title="Generate follow-up email"
                    >
                      Generate Follow-Up
                    </button>
                  </div>
                )}
              </td>
            </>
          )}
          <td>
            <select
              value={contact.status}
              onChange={(e) => handleCellEdit(contact.id, 'status', e.target.value)}
              className="status-select"
            >
              <option value="pending">Pending</option>
              <option value="trashed">Trashed</option>
              <option value="sent">Sent</option>
            </select>
          </td>
          <td>
            <div className="action-buttons">
              {showEmailActions && email && (
                <>
                  <button
                    className="btn btn-small btn-primary"
                    onClick={() => {
                      // #region agent log
                      sendTelemetry('CSVManager.jsx:viewEmail:click', 'View email button clicked', { emailId: email?.id, hasEmail: !!email })
                      // #endregion
                      handleViewEmail(email)
                    }}
                    title="View email content"
                  >
                    View Email
                  </button>
                  <button
                    className="btn btn-small btn-success"
                    onClick={() => {
                      // #region agent log
                      sendTelemetry('CSVManager.jsx:sendEmail:click', 'Send email button clicked', { emailId: email?.id })
                      // #endregion
                      handleSendEmail(email.id)
                    }}
                    title="Send this email"
                    disabled={sendingEmail}
                  >
                    Send
                  </button>
                  <button
                    className="btn btn-small btn-danger"
                    onClick={() => handleDeleteEmail(contact.id)}
                    title="Delete email (moves contact to 'No Emails Generated')"
                  >
                    Delete
                  </button>
                </>
              )}
              {showDeleteEmail && email && !showEmailActions && (
                <button
                  className="btn btn-small btn-danger"
                  onClick={() => handleDeleteEmail(contact.id)}
                  title="Delete email (moves contact to 'No Emails Generated')"
                >
                  Delete Email
                </button>
              )}
              <button
                type="button"
                className="btn btn-small btn-icon"
                onClick={() => handleDelete(contact.id)}
                title="Delete contact"
              >
                Delete
              </button>
            </div>
          </td>
        </tr>
      )
    })
  }

  if (loading) {
    return (
      <div className="loading-skeleton">
        <div className="loading-spinner-wrap">
          <div className="spinner" aria-hidden="true" />
          <span>Loading contacts</span>
        </div>
        <div className="loading-skeleton-row">
          <div className="skeleton loading-skeleton-cell loading-skeleton-cell--md" />
          <div className="skeleton loading-skeleton-cell loading-skeleton-cell--md" />
          <div className="skeleton loading-skeleton-cell loading-skeleton-cell--lg" />
        </div>
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="loading-skeleton-row">
            <div className="skeleton loading-skeleton-cell loading-skeleton-cell--md" />
            <div className="skeleton loading-skeleton-cell loading-skeleton-cell--md" />
            <div className="skeleton loading-skeleton-cell loading-skeleton-cell--lg" />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="csv-manager">
      <div className="csv-manager-header">
        <h2>Contacts</h2>
        <div className="csv-manager-actions">
          {activeTab === 'no_emails' && (
            <>
              <input
                type="file"
                accept=".csv"
                onChange={handleFileUpload}
                className="input-file-hidden"
                id="csv-upload"
              />
              <label htmlFor="csv-upload" className="btn btn-secondary">
                Upload CSV
              </label>
            </>
          )}
          <button className="btn btn-secondary" onClick={handleExport}>
            Export CSV ({activeTab === 'emailed' ? 'Emailed' : 
                        activeTab === 'generated' ? 'Not Sent' : 
                        activeTab === 'no_emails' ? 'No Emails' : 'All'})
          </button>
          {activeTab === 'no_emails' && (
            <button className="btn btn-primary" onClick={handleAddContact}>
              + Add Contact
            </button>
          )}
          <button
            className="btn btn-success"
            onClick={handleSave}
            disabled={!hasChanges}
          >
            Save Changes
          </button>
        </div>
      </div>

      <div className="search-bar">
        <label htmlFor="search-contacts" className="input-label">
          Search contacts
        </label>
        <input
          id="search-contacts"
          type="search"
          placeholder="Filter by name, company, or email"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
        />
      </div>

      {/* Tabs for sections */}
      <div className="section-tabs">
        <button
          className={`tab ${activeTab === 'emailed' ? 'active' : ''}`}
          onClick={(e) => {
            // #region agent log
            sendTelemetry('CSVManager.jsx:tab:emailed:click', 'Emailed tab clicked', { currentTab: activeTab })
            // #endregion
            e.preventDefault()
            e.stopPropagation()
            handleTabChange('emailed')
          }}
        >
          Emailed ({categorizedContacts.emailed.length})
        </button>
        <button
          className={`tab ${activeTab === 'generated' ? 'active' : ''}`}
          onClick={(e) => {
            // #region agent log
            sendTelemetry('CSVManager.jsx:tab:generated:click', 'Generated tab clicked', { currentTab: activeTab })
            // #endregion
            e.preventDefault()
            e.stopPropagation()
            handleTabChange('generated')
          }}
        >
          Emails Generated - Not Sent ({categorizedContacts.emails_generated.length})
        </button>
        <button
          className={`tab ${activeTab === 'no_emails' ? 'active' : ''}`}
          onClick={(e) => {
            // #region agent log
            sendTelemetry('CSVManager.jsx:tab:no_emails:click', 'No emails tab clicked', { currentTab: activeTab })
            // #endregion
            e.preventDefault()
            e.stopPropagation()
            handleTabChange('no_emails')
          }}
        >
          No Emails Generated ({categorizedContacts.no_emails.length})
        </button>
      </div>

      {/* Contacts sections */}
      <div className="contacts-sections">
        {(() => {
          // #region agent log
          try {
            sendTelemetry('CSVManager.jsx:render:sections:start', 'Rendering sections container', { activeTab })
          } catch (e) {}
          // #endregion
          try {
            if (activeTab === 'emailed') {
              return renderEmailedSection()
            } else if (activeTab === 'generated') {
              // #region agent log
              sendTelemetry('CSVManager.jsx:render:sections:generated', 'About to render generated section', { activeTab })
              // #endregion
              return renderGeneratedSection()
            } else if (activeTab === 'no_emails') {
              // #region agent log
              sendTelemetry('CSVManager.jsx:render:sections:no_emails', 'About to render no_emails section', { activeTab })
              // #endregion
              return renderNoEmailsSection()
            }
            return null
          } catch (error) {
            // #region agent log
            sendTelemetry('CSVManager.jsx:render:sections:error', 'Error rendering sections', { error: error.message, stack: error.stack, activeTab })
            // #endregion
            console.error('Error rendering section:', error)
            return <div className="section-error">Error rendering section: {error.message}</div>
          }
        })()}
      </div>

      {/* Email View Modal */}
      {viewingEmail && (
        <div className="settings-modal email-preview-modal">
          <div className="settings-content email-preview-content">
            <h3>Email Preview</h3>
            <div className="email-preview-meta">
              <div className="email-preview-row">
                <strong>To:</strong> {viewingEmail.contact_email}
              </div>
              <div className="email-preview-row">
                <strong>Subject:</strong> {viewingEmail.subject}
              </div>
              <div className="email-preview-body">
                {viewingEmail.body}
              </div>
            </div>
            <div className="settings-actions">
              <button 
                className="btn btn-success" 
                onClick={() => handleSendEmail(viewingEmail.id)}
                disabled={sendingEmail}
              >
                {sendingEmail ? 'Sending...' : 'Send Email'}
              </button>
              <button 
                className="btn btn-danger" 
                onClick={() => handleDeleteEmail(viewingEmail.contact_id)}
                disabled={sendingEmail}
              >
                Delete Email
              </button>
              <button 
                className="btn btn-secondary" 
                onClick={() => setViewingEmail(null)}
                disabled={sendingEmail}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function EditableCell({ value, onSave, editing, onEdit, onCancel, type = 'text' }) {
  const [editValue, setEditValue] = useState(value)

  useEffect(() => {
    setEditValue(value)
  }, [value])

  if (editing) {
    return (
      <input
        type={type}
        value={editValue}
        onChange={(e) => setEditValue(e.target.value)}
        onBlur={() => {
          onSave(editValue)
          onCancel()
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            onSave(editValue)
            onCancel()
          } else if (e.key === 'Escape') {
            setEditValue(value)
            onCancel()
          }
        }}
        autoFocus
        className="editable-input"
      />
    )
  }

  return (
    <span onClick={onEdit} className="editable-cell">
      {value || <span className="placeholder">Click to edit</span>}
    </span>
  )
}

export default CSVManager
