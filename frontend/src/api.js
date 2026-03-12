import axios from 'axios'
import { API_BASE_URL, sendTelemetry } from './config'

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Add request interceptor to log all API calls
api.interceptors.request.use(
  (config) => {
    sendTelemetry('api.js:interceptor:request', 'API request', { url: config.url, method: config.method, baseURL: config.baseURL })
    return config
  },
  (error) => {
    sendTelemetry('api.js:interceptor:request:error', 'API request error', { error: error.message })
    return Promise.reject(error)
  }
)

// Add response interceptor to log API responses
api.interceptors.response.use(
  (response) => {
    sendTelemetry('api.js:interceptor:response', 'API response', { url: response.config.url, status: response.status })
    return response
  },
  (error) => {
    sendTelemetry('api.js:interceptor:response:error', 'API response error', {
      url: error.config?.url,
      status: error.response?.status,
      message: error.message,
      code: error.code,
    })
    return Promise.reject(error)
  }
)

sendTelemetry('api.js:init', 'API client initialized', { baseURL: API_BASE_URL })

// Contacts API
export const contactsAPI = {
  getAll: (status) => {
    sendTelemetry('api.js:getAll', 'API call attempt', { endpoint: '/contacts', status })
    return api.get('/contacts', { params: { status } }).catch(err => {
      sendTelemetry('api.js:getAll:error', 'API call failed', { endpoint: '/contacts', error: err.message, code: err.code })
      throw err
    })
  },
  create: (contact) => api.post('/contacts', contact),
  update: (id, updates) => api.put(`/contacts/${id}`, updates),
  delete: (id) => api.delete(`/contacts/${id}`),
  bulkDelete: (ids) => api.post('/contacts/bulk-delete', ids),
  bulkUpdate: (updates) => api.put('/contacts/bulk', updates),
  save: () => api.post('/contacts/save'),
  upload: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    // Omit Content-Type so browser sets multipart/form-data with boundary (required for server to parse)
    return api.post('/upload', formData, {
      transformRequest: [(data, headers) => {
        delete headers['Content-Type']
        return data
      }],
    })
  },
  export: (section) => api.get('/contacts/export', { 
    params: section ? { section } : {},
    responseType: 'blob' 
  }),
  getCategorized: () => api.get('/contacts/categorized'),
  getFollowUpReminders: () => api.get('/follow-up-reminders'),
  generateFollowUp: (emailId, userInfo) => api.post('/generate-follow-up', {
    email_id: emailId,
    ...userInfo
  }),
}

// Company API
export const companyAPI = {
  enrich: (companyName, url) => api.post('/enrich-company', null, {
    params: { company_name: companyName, ...(url && { url }) },
  }),
  getMetadata: (companyName) => api.get(`/company-metadata/${companyName}`),
}

// Email API
export const emailAPI = {
  generate: (contactIds = null, userName = null, userBackground = null, userEmail = null, useTemplateOnly = false) =>
    api.post('/generate-emails', {
      contact_ids: contactIds,
      user_name: userName,
      user_background: userBackground,
      user_email: userEmail,
      use_template_only: useTemplateOnly,
    }),
  getAll: (status) => api.get('/emails', { params: { status } }),
  updateStatus: (id, status) => api.put(`/emails/${id}`, null, {
    params: { status },
  }),
  update: (id, data) => api.patch(`/emails/${id}`, data),
  delete: (id) => api.delete(`/emails/${id}`),
  bulkDelete: (emailIds) => api.post('/emails/bulk-delete', { email_ids: emailIds }),
  send: (emailIds, attachResume = null, fromEmail = null) =>
    api.post('/send-emails', { email_ids: emailIds, attach_resume: attachResume || null, from_email: fromEmail || null }),
  disconnectGmail: () => api.post('/gmail-disconnect'),
}

// Usage API
export const usageAPI = {
  getStats: () => api.get('/usage'),
}

export default api
