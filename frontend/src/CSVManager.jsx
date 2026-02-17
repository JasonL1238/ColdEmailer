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
    // #region agent log
    fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'CSVManager.jsx:handleAddContact:start','message':'Add contact called','data':{activeTab},timestamp:Date.now(),runId:'add-contact',hypothesisId:'A'})}).catch(()=>{});
    // #endregion
    
    // Only allow adding contacts in "No Emails Generated" section
    if (activeTab !== 'no_emails') {
      // #region agent log
      fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'CSVManager.jsx:handleAddContact:wrongTab','message':'Add contact blocked - wrong tab','data':{activeTab},timestamp:Date.now(),runId:'add-contact',hypothesisId:'B'})}).catch(()=>{});
      // #endregion
      toast.error('You can only add contacts in the "No Emails Generated" section')
      return
    }
    
    const newContact = {
      name: '',
      company: '',
      email: '',
      status: 'pending',
    }
    
    // #region agent log
    fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'CSVManager.jsx:handleAddContact:beforeAPI','message':'About to call API','data':{newContact},timestamp:Date.now(),runId:'add-contact',hypothesisId:'C'})}).catch(()=>{});
    // #endregion
    
    try {
      const response = await contactsAPI.create(newContact)
      // #region agent log
      fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'CSVManager.jsx:handleAddContact:success','message':'API call successful','data':{contactId:response.data?.id},timestamp:Date.now(),runId:'add-contact',hypothesisId:'D'})}).catch(()=>{});
      // #endregion
      const contactId = response.data.id
      toast.success('Contact added and saved')
      await loadCategorizedContacts()
      setEditingCell({ id: contactId, field: 'name' })
    } catch (error) {
      // #region agent log
      fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'CSVManager.jsx:handleAddContact:error','message':'API call failed','data':{error:error.message,response:error.response?.data,status:error.response?.status},timestamp:Date.now(),runId:'add-contact',hypothesisId:'E'})}).catch(()=>{});
      // #endregion
      toast.error('Failed to save contact')
      console.error(error)
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

  const handleGenerateFollowUp = async (emailId) => {
    try {
      const response = await contactsAPI.generateFollowUp(emailId, {
        user_name: userName,
        user_background: userBackground,
        user_email: userEmail
      })
      toast.success('Follow-up email generated! Check Email Review section.')
      // Optionally navigate to email review or show the generated email
    } catch (error) {
      toast.error('Failed to generate follow-up email')
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

  const needsFollowUp = (contactId) => {
    return followUpReminders.some(reminder => reminder.contact.id === contactId)
  }

  const getFollowUpInfo = (contactId) => {
    return followUpReminders.find(reminder => reminder.contact.id === contactId)
  }

  const renderContactTable = (contacts, showTracking = false, showDeleteEmail = false) => {
    const filtered = searchTerm
      ? contacts.filter(c => 
          c.name?.toLowerCase().includes(searchTerm.toLowerCase()) ||
          c.company?.toLowerCase().includes(searchTerm.toLowerCase()) ||
          c.email?.toLowerCase().includes(searchTerm.toLowerCase())
        )
      : contacts

    const colSpan = showTracking ? 7 : (showDeleteEmail ? 6 : 5)
    
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
              {showDeleteEmail && email && (
                <button
                  className="btn btn-small btn-danger"
                  onClick={() => handleDeleteEmail(contact.id)}
                  title="Delete email (moves contact to 'No Emails Generated')"
                >
                  Delete Email
                </button>
              )}
              <button
                className="btn-icon"
                onClick={() => handleDelete(contact.id)}
                title="Delete contact"
              >
                🗑️
              </button>
            </div>
          </td>
        </tr>
      )
    })
  }

  if (loading) {
    return <div className="loading">Loading contacts...</div>
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
                style={{ display: 'none' }}
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
        <input
          type="text"
          placeholder="Search contacts..."
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="search-input"
        />
      </div>

      {/* Tabs for sections */}
      <div className="section-tabs">
        <button
          className={`tab ${activeTab === 'emailed' ? 'active' : ''}`}
          onClick={() => setActiveTab('emailed')}
        >
          Emailed ({categorizedContacts.emailed.length})
        </button>
        <button
          className={`tab ${activeTab === 'generated' ? 'active' : ''}`}
          onClick={() => setActiveTab('generated')}
        >
          Emails Generated - Not Sent ({categorizedContacts.emails_generated.length})
        </button>
        <button
          className={`tab ${activeTab === 'no_emails' ? 'active' : ''}`}
          onClick={() => setActiveTab('no_emails')}
        >
          No Emails Generated ({categorizedContacts.no_emails.length})
        </button>
      </div>

      {/* Contacts sections */}
      <div className="contacts-sections">
        {activeTab === 'emailed' && (
          <section className="section-emailed">
            <h3>Emailed ({categorizedContacts.emailed.length})</h3>
            <div className="table-container">
              <table className="contacts-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Company</th>
                    <th>Email</th>
                    <th>Sent Date</th>
                    <th>Response</th>
                    <th>Follow-Up</th>
                    <th>Status</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {renderContactTable(categorizedContacts.emailed, true)}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {activeTab === 'generated' && (
          <section className="section-emails-generated">
            <h3>Emails Generated - Not Sent ({categorizedContacts.emails_generated.length})</h3>
            <p className="section-description">
              Includes pending, accepted, and trashed emails (trashed emails are kept but not sent). 
              Use "Delete Email" to remove the email and move the contact to "No Emails Generated" section.
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
                  {renderContactTable(categorizedContacts.emails_generated, false, true, false)}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {activeTab === 'no_emails' && (
          <section className="section-no-emails">
            <h3>No Emails Generated ({categorizedContacts.no_emails.length})</h3>
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
                  {renderContactTable(categorizedContacts.no_emails, false, false, false)}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </div>
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
