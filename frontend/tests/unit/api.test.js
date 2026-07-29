import { describe, it, expect, vi, beforeEach } from 'vitest'

const { mockAxiosInstance } = vi.hoisted(() => {
  const instance = {
    get: vi.fn(() => Promise.resolve({ data: {} })),
    post: vi.fn(() => Promise.resolve({ data: {} })),
    put: vi.fn(() => Promise.resolve({ data: {} })),
    patch: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({ data: {} })),
  }
  return { mockAxiosInstance: instance }
})

vi.mock('axios', () => ({
  default: { create: vi.fn(() => mockAxiosInstance) },
}))

import {
  discoveryAPI, companiesAPI, contactsAPI, resumesAPI, emailsAPI,
  settingsAPI, dashboardAPI, errMessage,
} from '../../src/api.js'

beforeEach(() => {
  Object.values(mockAxiosInstance).forEach((fn) => fn.mockClear?.())
})

describe('API client', () => {
  it('starts discovery with query and count', () => {
    discoveryAPI.start('fintech startups', 10)
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/discovery', {
      query: 'fintech startups', count: 10,
    })
  })

  it('lists companies with search param', () => {
    companiesAPI.list('acme')
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/companies', { params: { search: 'acme' } })
  })

  it('bulk deletes contacts, protecting sent history by default', () => {
    contactsAPI.bulkDelete(['a', 'b'])
    expect(mockAxiosInstance.post).toHaveBeenCalledWith(
      '/contacts/bulk-delete', { ids: ['a', 'b'] }, { params: { force: false } })
  })

  it('bulk deletes with force when the user confirms', () => {
    contactsAPI.bulkDelete(['a'], true)
    expect(mockAxiosInstance.post).toHaveBeenCalledWith(
      '/contacts/bulk-delete', { ids: ['a'] }, { params: { force: true } })
  })

  it('deletes a single contact without force by default', () => {
    contactsAPI.delete('c1')
    expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/contacts/c1', { params: { force: false } })
  })

  it('cancels an in-flight send batch', () => {
    emailsAPI.cancelSend('job1')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/emails/send/job1/cancel')
  })

  it('generates emails with full payload', () => {
    emailsAPI.generate({ contact_ids: ['x'], email_type: 'coffee_chat' })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/emails/generate', {
      contact_ids: ['x'], email_type: 'coffee_chat',
    })
  })

  it('updates email via PATCH', () => {
    emailsAPI.update('e1', { subject: 'Hi' })
    expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/emails/e1', { subject: 'Hi' })
  })

  it('sends emails with attachment options', () => {
    emailsAPI.send({ email_ids: ['e1'], attach_resume: true, resume_id: null })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/emails/send', {
      email_ids: ['e1'], attach_resume: true, resume_id: null,
    })
  })

  it('uploads resumes as multipart form', () => {
    const file = new File(['%PDF'], 'r.pdf')
    resumesAPI.upload(file, 'ML')
    const [url, form] = mockAxiosInstance.post.mock.calls[0]
    expect(url).toBe('/resumes')
    expect(form).toBeInstanceOf(FormData)
  })

  it('fetches settings and dashboard', () => {
    settingsAPI.get()
    dashboardAPI.get()
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/settings')
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/dashboard')
  })
})

describe('errMessage', () => {
  it('extracts FastAPI detail strings', () => {
    expect(errMessage({ response: { data: { detail: 'Nope' } } })).toBe('Nope')
  })
  it('extracts pydantic validation messages', () => {
    expect(errMessage({ response: { data: { detail: [{ msg: 'field required' }] } } })).toBe('field required')
  })
  it('handles network errors readably', () => {
    expect(errMessage({ message: 'Network Error' })).toMatch(/backend/i)
  })
  it('falls back gracefully', () => {
    expect(errMessage({}, 'fallback')).toBe('fallback')
  })
})
