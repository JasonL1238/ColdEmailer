import axios from 'axios'
import { API_BASE_URL } from './config'

const api = axios.create({ baseURL: API_BASE_URL })

// Normalize backend errors into readable messages
export function errMessage(error, fallback = 'Something went wrong') {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg
  if (error?.message === 'Network Error') return 'Cannot reach the backend — is it running?'
  return error?.message || fallback
}

export const settingsAPI = {
  get: () => api.get('/settings'),
  update: (profile) => api.put('/settings', profile),
}

export const discoveryAPI = {
  start: (query, count, mode = 'full') => api.post('/discovery', { query, count, mode }),
  list: () => api.get('/discovery'),
  get: (id) => api.get(`/discovery/${id}`),
  cancel: (id) => api.post(`/discovery/${id}/cancel`),
}

export const deepResearchAPI = {
  start: (payload) => api.post('/deep-research', payload),
  list: () => api.get('/deep-research'),
  consolidated: (search, includeAll = false) => api.get('/deep-research/consolidated', {
    params: { ...(search ? { search } : {}), ...(includeAll ? { include_all: true } : {}) },
  }),
  get: (id) => api.get(`/deep-research/${id}`),
  cancel: (id) => api.post(`/deep-research/${id}/cancel`),
}

export const jobsAPI = {
  get: (id) => api.get(`/jobs/${id}`),
}

export const companiesAPI = {
  list: (search) => api.get('/companies', { params: search ? { search } : {} }),
  get: (id) => api.get(`/companies/${id}`),
  create: (name, url) => api.post('/companies', { name, url: url || null }),
  enrich: (id, mode = 'full') => api.post(`/companies/${id}/enrich`, { mode }),
  delete: (id, force = false) => api.delete(`/companies/${id}`, { params: { force } }),
}

export const contactsAPI = {
  list: (params = {}) => api.get('/contacts', { params }),
  create: (data) => api.post('/contacts', data),
  update: (id, data) => api.put(`/contacts/${id}`, data),
  linkedinDraft: (id, customInstructions = null) =>
    api.post(`/contacts/${id}/linkedin-draft`, { custom_instructions: customInstructions }),
  delete: (id, force = false) => api.delete(`/contacts/${id}`, { params: { force } }),
  bulkDelete: (ids, force = false) => api.post('/contacts/bulk-delete', { ids }, { params: { force } }),
  import: (file) => {
    const form = new FormData()
    form.append('file', file)
    return api.post('/contacts/import', form)
  },
  exportUrl: () => `${API_BASE_URL}/contacts/export`,
}

export const resumesAPI = {
  list: () => api.get('/resumes'),
  upload: (file, label) => {
    const form = new FormData()
    form.append('file', file)
    form.append('label', label || '')
    return api.post('/resumes', form)
  },
  update: (id, data) => api.put(`/resumes/${id}`, data),
  delete: (id, force = false) => api.delete(`/resumes/${id}`, { params: { force } }),
  fileUrl: (id) => `${API_BASE_URL}/resumes/${id}/file`,
  downloadUrl: (id) => `${API_BASE_URL}/resumes/${id}/file?download=true`,
}

export const emailsAPI = {
  generate: (payload) => api.post('/emails/generate', payload),
  cancelGeneration: (jobId) => api.post(`/emails/generate/${jobId}/cancel`),
  list: (status) => api.get('/emails', { params: status ? { status } : {} }),
  update: (id, data) => api.patch(`/emails/${id}`, data),
  bulkStatus: (emailIds, status) => api.post('/emails/bulk-status', { email_ids: emailIds, status }),
  bulkDelete: (ids) => api.post('/emails/bulk-delete', { ids }),
  regenerate: (id) => api.post(`/emails/${id}/regenerate`),
  send: (payload) => api.post('/emails/send', payload),
  cancelSend: (jobId) => api.post(`/emails/send/${jobId}/cancel`),
  followUps: (days = 7) => api.get('/follow-ups', { params: { days } }),
  generateFollowUp: (emailId) => api.post(`/emails/${emailId}/follow-up`),
  checkReplies: (recheck = false) => api.post('/emails/check-replies', null, { params: { recheck } }),
}

export const dashboardAPI = {
  get: () => api.get('/dashboard'),
}

export const gmailAPI = {
  disconnect: () => api.post('/gmail/disconnect'),
}
