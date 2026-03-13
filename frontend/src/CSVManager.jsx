import { useState, useEffect, useMemo } from 'react'
import toast from 'react-hot-toast'
import { contactsAPI, emailAPI } from './api'
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
  const [dragOver, setDragOver] = useState(false)
  const [selectedGeneratedEmailIds, setSelectedGeneratedEmailIds] = useState(new Set()) // email IDs for bulk delete
  const [selectedNoEmailContactIds, setSelectedNoEmailContactIds] = useState(new Set()) // contact IDs in "no emails" for bulk delete
  const [showUploadModal, setShowUploadModal] = useState(false)
  const [editingSubject, setEditingSubject] = useState('')
  const [editingBody, setEditingBody] = useState('')
  const [savingEmail, setSavingEmail] = useState(false)
  const [attachResume, setAttachResume] = useState('resume28.pdf') // default 2028 resume; 'none' | 'resume28.pdf' | 'resume29.pdf'

  const handleTabChange = (tab) => {
    setActiveTab(tab)
  }
  
  // User info for follow-up generation
  const [userName] = useState(localStorage.getItem('userName') || 'Jason Li')
  const [userBackground] = useState(localStorage.getItem('userBackground') || '')
  const [userEmail] = useState(localStorage.getItem('userEmail') || 'jason.ye.li.7@gmail.com')

  useEffect(() => {
    loadCategorizedContacts()
    loadFollowUpReminders()
  }, [])

  useEffect(() => {
    const onRefresh = () => loadCategorizedContacts()
    window.addEventListener('contacts-refresh', onRefresh)
    return () => window.removeEventListener('contacts-refresh', onRefresh)
  }, [])

  useEffect(() => {
    if (viewingEmail) {
      setEditingSubject(viewingEmail.subject ?? '')
      setEditingBody(viewingEmail.body ?? '')
    }
  }, [viewingEmail])

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
    if (activeTab !== 'no_emails') {
      toast.error('You can only add contacts in the "No Emails Generated" section')
      return
    }

    const newContact = {
      name: '',
      company: '',
      email: '',
      status: 'pending',
    }

    try {
      const response = await contactsAPI.create(newContact)
      const contactId = response.data.id
      toast.success('Contact added and saved')
      await loadCategorizedContacts()
      setEditingCell({ id: contactId, field: 'name' })
      setTimeout(() => {
        document.querySelector(`tr[data-contact-id="${contactId}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      }, 100)
    } catch (error) {
      toast.error('Failed to save contact')
      console.error(error)
    }
  }

  const handleViewEmail = (email) => {
    setViewingEmail(email)
  }

  const handleSaveEmailEdit = async () => {
    if (!viewingEmail?.id) return
    try {
      setSavingEmail(true)
      const response = await emailAPI.update(viewingEmail.id, {
        subject: editingSubject,
        body: editingBody
      })
      const updated = response.data?.email
      if (updated) {
        setViewingEmail(updated)
        setEditingSubject(updated.subject ?? '')
        setEditingBody(updated.body ?? '')
      }
      await loadCategorizedContacts()
      toast.success('Email updated')
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Failed to save email')
    } finally {
      setSavingEmail(false)
    }
  }

  const handleSendEmail = async (emailId, resumeFile = null) => {
    if (!window.confirm('Send this email now?')) {
      return
    }
    const attach = resumeFile === 'none' || !resumeFile ? null : resumeFile
    try {
      setSendingEmail(true)
      await emailAPI.send([emailId], attach)
      toast.success('Email sent!')
      await loadCategorizedContacts()
      setViewingEmail(null)
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
      setSelectedGeneratedEmailIds((prev) => {
        const next = new Set(prev)
        next.delete(email.id)
        return next
      })
      toast.success('Email deleted. Contact moved to "No Emails Generated" section.')
    } catch (error) {
      toast.error('Failed to delete email')
      console.error(error)
    }
  }

  const handleToggleGeneratedEmailSelection = (emailId) => {
    setSelectedGeneratedEmailIds((prev) => {
      const next = new Set(prev)
      if (next.has(emailId)) next.delete(emailId)
      else next.add(emailId)
      return next
    })
  }

  const handleSelectAllGenerated = () => {
    const contacts = categorizedContacts.emails_generated || []
    const ids = new Set(contacts.map((c) => getEmailForContact(c.id)?.id).filter(Boolean))
    setSelectedGeneratedEmailIds(ids)
  }

  const handleDeselectAllGenerated = () => {
    setSelectedGeneratedEmailIds(new Set())
  }

  const handleBulkDeleteGenerated = async () => {
    const count = selectedGeneratedEmailIds.size
    if (count === 0) {
      toast.error('Select at least one email to delete')
      return
    }
    if (!window.confirm(`Delete ${count} email(s)? Those contacts will move to "No Emails Generated" section.`)) {
      return
    }
    try {
      const response = await emailAPI.bulkDelete(Array.from(selectedGeneratedEmailIds))
      const deleted = response.data?.deleted ?? count
      setSelectedGeneratedEmailIds(new Set())
      await loadCategorizedContacts()
      setViewingEmail(null)
      toast.success(`${deleted} email(s) deleted. Contacts moved to "No Emails Generated" section.`)
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Failed to delete emails')
      console.error(error)
    }
  }

  const handleToggleNoEmailContactSelection = (contactId) => {
    setSelectedNoEmailContactIds((prev) => {
      const next = new Set(prev)
      if (next.has(contactId)) next.delete(contactId)
      else next.add(contactId)
      return next
    })
  }

  const handleSelectAllNoEmailContacts = () => {
    const ids = (categorizedContacts.no_emails || []).map((c) => c.id).filter(Boolean)
    setSelectedNoEmailContactIds(new Set(ids))
  }

  const handleDeselectAllNoEmailContacts = () => {
    setSelectedNoEmailContactIds(new Set())
  }

  const handleBulkDeleteNoEmailContacts = async () => {
    const count = selectedNoEmailContactIds.size
    if (count === 0) {
      toast.error('Select at least one contact to delete')
      return
    }
    if (!window.confirm(`Delete ${count} contact(s) from the list? This cannot be undone.`)) {
      return
    }
    try {
      const response = await contactsAPI.bulkDelete(Array.from(selectedNoEmailContactIds))
      const deleted = response.data?.deleted ?? count
      setSelectedNoEmailContactIds(new Set())
      await loadCategorizedContacts()
      toast.success(`${deleted} contact(s) deleted`)
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Failed to delete contacts')
      console.error(error)
    }
  }

  const handleDelete = async (id) => {
    const allContacts = [
      ...(categorizedContacts.no_emails || []),
      ...(categorizedContacts.emails_generated || []),
      ...(categorizedContacts.emailed || []),
    ]
    const contact = allContacts.find((c) => c.id === id)
    const isEmpty = contact && !(contact.name || '').trim() && !(contact.email || '').trim()
    const confirmMsg = isEmpty
      ? 'This contact has no name or email yet (e.g. just added). Remove it from the list?'
      : 'Are you sure you want to delete this contact?'
    if (!window.confirm(confirmMsg)) {
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

  const processUploadFile = async (file) => {
    if (!file) return
    const isCsv = file.name.toLowerCase().endsWith('.csv') || (file.type && file.type === 'text/csv')
    if (!isCsv) {
      toast.error('Please drop a CSV file')
      return
    }
    try {
      await contactsAPI.upload(file)
      await loadCategorizedContacts()
      toast.success('CSV uploaded successfully')
      setShowUploadModal(false)
    } catch (error) {
      const msg = error.response?.data?.detail || error.message || 'Failed to upload CSV'
      toast.error(Array.isArray(msg) ? msg.join(' ') : msg)
      console.error(error)
    }
  }

  const handleFileUpload = async (e) => {
    const file = e.target.files[0]
    await processUploadFile(file)
    e.target.value = ''
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
  }

  const handleDrop = async (e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    const file = e.dataTransfer.files?.[0]
    await processUploadFile(file)
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
      console.error('Error rendering emailed section:', error)
      return <div>Error loading emailed contacts: {error.message}</div>
    }
  }

  const renderGeneratedSection = () => {
    try {
      const generatedContacts = categorizedContacts.emails_generated || []
      const bulkDeleteOptions = {
        selectedEmailIds: selectedGeneratedEmailIds,
        onToggle: handleToggleGeneratedEmailSelection,
        selectAll: handleSelectAllGenerated,
        deselectAll: handleDeselectAllGenerated,
      }
      const allGeneratedEmailIds = generatedContacts.map((c) => getEmailForContact(c.id)?.id).filter(Boolean)
      const allSelected = allGeneratedEmailIds.length > 0 && allGeneratedEmailIds.every((id) => selectedGeneratedEmailIds.has(id))
      return (
        <section className="section-emails-generated">
          <h3>Emails Generated - Not Sent ({generatedContacts.length})</h3>
          <p className="section-description">
            Includes pending, accepted, and trashed emails. Select rows to delete in bulk, or click "View Email" to see content, then send or delete.
          </p>
          <div className="bulk-actions generated-bulk-actions">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={allSelected}
                onChange={(e) => (e.target.checked ? handleSelectAllGenerated() : handleDeselectAllGenerated())}
                title="Select all"
              />
              <span>Select all</span>
            </label>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={handleDeselectAllGenerated}
            >
              Deselect all
            </button>
            <button
              type="button"
              className="btn btn-danger btn-small"
              onClick={handleBulkDeleteGenerated}
              disabled={selectedGeneratedEmailIds.size === 0}
              title={selectedGeneratedEmailIds.size === 0 ? 'Select emails to delete' : `Delete ${selectedGeneratedEmailIds.size} selected`}
            >
              Delete selected ({selectedGeneratedEmailIds.size})
            </button>
          </div>
          <div className="table-container">
            <table className="contacts-table">
              <thead>
                <tr>
                  <th className="th-checkbox">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={(e) => (e.target.checked ? handleSelectAllGenerated() : handleDeselectAllGenerated())}
                      title="Select all"
                    />
                  </th>
                  <th>Name</th>
                  <th>Company</th>
                  <th>Email</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {renderContactTable(generatedContacts, false, true, true, bulkDeleteOptions)}
              </tbody>
            </table>
          </div>
        </section>
      )
    } catch (error) {
      console.error('Error rendering generated section:', error)
      return <div>Error loading generated contacts: {error.message}</div>
    }
  }

  const renderNoEmailsSection = () => {
    try {
      const noEmailContacts = categorizedContacts.no_emails || []
      const bulkContactOptions = {
        selectedContactIds: selectedNoEmailContactIds,
        onToggle: handleToggleNoEmailContactSelection,
        selectAll: handleSelectAllNoEmailContacts,
        deselectAll: handleDeselectAllNoEmailContacts,
      }
      const allNoEmailContactIds = noEmailContacts.map((c) => c.id).filter(Boolean)
      const allNoEmailSelected = allNoEmailContactIds.length > 0 && allNoEmailContactIds.every((id) => selectedNoEmailContactIds.has(id))
      return (
        <section className="section-no-emails">
          <h3>No Emails Generated ({noEmailContacts.length})</h3>
          <p className="section-description">
            These contacts don't have generated emails yet. 
            Click "Generate Emails" in the Review Emails tab to select and generate emails for contacts.
          </p>
          <div className="bulk-actions no-emails-bulk-actions">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={allNoEmailSelected}
                onChange={(e) => (e.target.checked ? handleSelectAllNoEmailContacts() : handleDeselectAllNoEmailContacts())}
                title="Select all"
              />
              <span>Select all</span>
            </label>
            <button
              type="button"
              className="btn btn-secondary btn-small"
              onClick={handleDeselectAllNoEmailContacts}
            >
              Deselect all
            </button>
            <button
              type="button"
              className="btn btn-danger btn-small"
              onClick={handleBulkDeleteNoEmailContacts}
              disabled={selectedNoEmailContactIds.size === 0}
              title={selectedNoEmailContactIds.size === 0 ? 'Select contacts to delete' : `Delete ${selectedNoEmailContactIds.size} selected`}
            >
              Delete selected ({selectedNoEmailContactIds.size})
            </button>
          </div>
          <div className="table-container">
            <table className="contacts-table">
              <thead>
                <tr>
                  <th className="th-checkbox">
                    <input
                      type="checkbox"
                      checked={allNoEmailSelected}
                      onChange={(e) => (e.target.checked ? handleSelectAllNoEmailContacts() : handleDeselectAllNoEmailContacts())}
                      title="Select all"
                    />
                  </th>
                  <th>Name</th>
                  <th>Company</th>
                  <th>Email</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {renderContactTable(noEmailContacts, false, false, false, null, bulkContactOptions)}
              </tbody>
            </table>
          </div>
        </section>
      )
    } catch (error) {
      console.error('Error rendering no_emails section:', error)
      return <div>Error loading no_emails contacts: {error.message}</div>
    }
  }

  const renderContactTable = (contacts, showTracking = false, showDeleteEmail = false, showEmailActions = false, bulkDeleteOptions = null, bulkContactOptions = null) => {
    const hasCheckboxColumn = (bulkDeleteOptions != null && showEmailActions) || bulkContactOptions != null
    const baseColSpan = showTracking ? 7 : (showDeleteEmail || showEmailActions ? 6 : 5)
    const colSpan = hasCheckboxColumn ? baseColSpan + 1 : baseColSpan

    if (!contacts || !Array.isArray(contacts)) {
      return (
        <tr>
          <td colSpan={colSpan} className="empty-state">
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
        <tr key={contact.id} data-contact-id={contact.id}>
          {hasCheckboxColumn && (
            <td className="td-checkbox">
              {bulkContactOptions ? (
                <input
                  type="checkbox"
                  checked={bulkContactOptions.selectedContactIds.has(contact.id)}
                  onChange={() => bulkContactOptions.onToggle(contact.id)}
                  title="Select for bulk delete"
                />
              ) : email ? (
                <input
                  type="checkbox"
                  checked={bulkDeleteOptions.selectedEmailIds.has(email.id)}
                  onChange={() => bulkDeleteOptions.onToggle(email.id)}
                  title="Select for bulk delete"
                />
              ) : (
                <span className="text-muted">—</span>
              )}
            </td>
          )}
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
                    onClick={() => handleViewEmail(email)}
                    title="View email content"
                  >
                    View Email
                  </button>
                  <button
                    className="btn btn-small btn-success"
                    onClick={() => handleSendEmail(email.id)}
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
    <>
    <div className="csv-manager">
      <div className="csv-manager-header">
        <h2>Contacts</h2>
        <div className="csv-manager-actions">
          {activeTab === 'no_emails' && (
            <button type="button" className="btn btn-secondary" onClick={() => setShowUploadModal(true)}>
              Upload CSV
            </button>
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
          onClick={() => handleTabChange('emailed')}
        >
          Emailed ({categorizedContacts.emailed.length})
        </button>
        <button
          className={`tab ${activeTab === 'generated' ? 'active' : ''}`}
          onClick={() => handleTabChange('generated')}
        >
          Emails Generated - Not Sent ({categorizedContacts.emails_generated.length})
        </button>
        <button
          className={`tab ${activeTab === 'no_emails' ? 'active' : ''}`}
          onClick={() => handleTabChange('no_emails')}
        >
          No Emails Generated ({categorizedContacts.no_emails.length})
        </button>
      </div>

      {/* Contacts sections */}
      <div className="contacts-sections">
        {activeTab === 'emailed' && renderEmailedSection()}
        {activeTab === 'generated' && renderGeneratedSection()}
        {activeTab === 'no_emails' && renderNoEmailsSection()}
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
                <strong>Subject:</strong>{' '}
                {viewingEmail.status === 'sent' ? (
                  viewingEmail.subject
                ) : (
                  <input
                    type="text"
                    className="email-edit-subject"
                    value={editingSubject}
                    onChange={(e) => setEditingSubject(e.target.value)}
                    placeholder="Subject"
                  />
                )}
              </div>
              <div className="email-preview-body-wrap">
                {viewingEmail.status === 'sent' ? (
                  <div className="email-preview-body">{viewingEmail.body}</div>
                ) : (
                  <textarea
                    className="email-edit-body"
                    value={editingBody}
                    onChange={(e) => setEditingBody(e.target.value)}
                    placeholder="Email body"
                    rows={12}
                  />
                )}
              </div>
            </div>
            {viewingEmail.status !== 'sent' && (
              <div className="email-preview-attach">
                <label htmlFor="attach-resume-modal">Attach resume:</label>
                <select
                  id="attach-resume-modal"
                  value={attachResume}
                  onChange={(e) => setAttachResume(e.target.value)}
                  className="attach-resume-select"
                >
                  <option value="none">None</option>
                  <option value="resume28.pdf">2028 resume</option>
                  <option value="resume29.pdf">2029 resume</option>
                </select>
              </div>
            )}
            <div className="settings-actions">
              {viewingEmail.status !== 'sent' && (
                <button
                  className="btn btn-primary"
                  onClick={handleSaveEmailEdit}
                  disabled={savingEmail}
                >
                  {savingEmail ? 'Saving...' : 'Save changes'}
                </button>
              )}
              {viewingEmail.status !== 'sent' && (
                <button
                  className="btn btn-success"
                  onClick={() => handleSendEmail(viewingEmail.id, attachResume)}
                  disabled={sendingEmail}
                >
                  {sendingEmail ? 'Sending...' : 'Send Email'}
                </button>
              )}
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
                disabled={sendingEmail || savingEmail}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>

    {showUploadModal && (
      <div
        className="upload-modal-overlay"
        onClick={() => setShowUploadModal(false)}
        role="dialog"
        aria-modal="true"
        aria-labelledby="upload-modal-title"
      >
        <div className="upload-modal" onClick={(e) => e.stopPropagation()}>
          <div className="upload-modal-header">
            <h3 id="upload-modal-title">Upload CSV</h3>
            <button
              type="button"
              className="upload-modal-close"
              onClick={() => setShowUploadModal(false)}
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <div
            className={`csv-drop-zone csv-drop-zone-modal ${dragOver ? 'drag-over' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            <input
              type="file"
              accept=".csv,text/csv"
              onChange={handleFileUpload}
              className="input-file-hidden"
              id="csv-upload-modal"
            />
            <p className="csv-drop-zone-prompt">
              {dragOver ? 'Drop CSV here' : 'Drag and drop your CSV file here'}
            </p>
            <p className="csv-drop-zone-or">or</p>
            <label htmlFor="csv-upload-modal" className="btn btn-primary csv-drop-zone-browse">
              Find in your files
            </label>
          </div>
        </div>
      </div>
    )}
    </>
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
