import axios from 'axios'

const API_BASE_URL = '/api'

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Add request interceptor to log all API calls
api.interceptors.request.use(
  (config) => {
    // #region agent log
    fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'api.js:interceptor:request','message':'API request','data':{url:config.url,method:config.method,baseURL:config.baseURL},timestamp:Date.now(),runId:'run1',hypothesisId:'C'})}).catch(()=>{});
    // #endregion
    return config
  },
  (error) => {
    // #region agent log
    fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'api.js:interceptor:request:error','message':'API request error','data':{error:error.message},timestamp:Date.now(),runId:'run1',hypothesisId:'C'})}).catch(()=>{});
    // #endregion
    return Promise.reject(error)
  }
)

// Add response interceptor to log API responses
api.interceptors.response.use(
  (response) => {
    // #region agent log
    fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'api.js:interceptor:response','message':'API response','data':{url:response.config.url,status:response.status},timestamp:Date.now(),runId:'run1',hypothesisId:'C'})}).catch(()=>{});
    // #endregion
    return response
  },
  (error) => {
    // #region agent log
    fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'api.js:interceptor:response:error','message':'API response error','data':{url:error.config?.url,status:error.response?.status,message:error.message,code:error.code},timestamp:Date.now(),runId:'run1',hypothesisId:'C'})}).catch(()=>{});
    // #endregion
    return Promise.reject(error)
  }
)

// #region agent log
fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'api.js:init','message':'API client initialized','data':{baseURL:API_BASE_URL},timestamp:Date.now(),runId:'run1',hypothesisId:'D'})}).catch(()=>{});
// #endregion

// Contacts API
export const contactsAPI = {
  getAll: (status) => {
    // #region agent log
    fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'api.js:getAll','message':'API call attempt','data':{endpoint:'/contacts',status},timestamp:Date.now(),runId:'run1',hypothesisId:'D'})}).catch(()=>{});
    // #endregion
    return api.get('/contacts', { params: { status } }).catch(err => {
      // #region agent log
      fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'api.js:getAll:error','message':'API call failed','data':{endpoint:'/contacts',error:err.message,code:err.code},timestamp:Date.now(),runId:'run1',hypothesisId:'D'})}).catch(()=>{});
      // #endregion
      throw err;
    });
  },
  create: (contact) => api.post('/contacts', contact),
  update: (id, updates) => api.put(`/contacts/${id}`, updates),
  delete: (id) => api.delete(`/contacts/${id}`),
  bulkDelete: (ids) => api.delete('/contacts/bulk', { data: ids }),
  bulkUpdate: (updates) => api.put('/contacts/bulk', updates),
  save: () => api.post('/contacts/save'),
  upload: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
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
  generate: (contactIds = null, userName = null, userBackground = null, userEmail = null) => 
    api.post('/generate-emails', { 
      contact_ids: contactIds,
      user_name: userName,
      user_background: userBackground,
      user_email: userEmail
    }),
  getAll: (status) => api.get('/emails', { params: { status } }),
  updateStatus: (id, status) => api.put(`/emails/${id}`, null, {
    params: { status },
  }),
  delete: (id) => api.delete(`/emails/${id}`),
  send: (emailIds) => api.post('/send-emails', { email_ids: emailIds }),
}

// Usage API
export const usageAPI = {
  getStats: () => api.get('/usage'),
}

export default api
