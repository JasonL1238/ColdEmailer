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
  settingsAPI, dashboardAPI, cadenceAPI, pipelineAPI, campaignsAPI,
  suppressionsAPI, personFinderAPI, errMessage,
} from '../../src/api.js'

beforeEach(() => {
  Object.values(mockAxiosInstance).forEach((fn) => fn.mockClear?.())
})

describe('API client', () => {
  it('starts discovery with query and count, defaulting to full mode', () => {
    discoveryAPI.start('fintech startups', 10)
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/discovery', {
      query: 'fintech startups', count: 10, mode: 'full',
    })
  })

  it('passes an explicit discovery mode through', () => {
    discoveryAPI.start('fintech startups', 10, 'fast')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/discovery', {
      query: 'fintech startups', count: 10, mode: 'fast',
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

describe('follow-up cadence wiring', () => {
  it('asks for due follow-ups without a days override', () => {
    /* This one line decides whether the cadence is honoured at all. With the
       old `days = 7` default, Emails.jsx's argument-less call pinned every
       query to a week: a "chase after 3 days" setting never surfaced anyone,
       and a [7,14] cadence listed people as due for rung 2 whom the server
       then refused with "nobody is due". Reverting it broke no test. */
    emailsAPI.followUps()
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/follow-ups', { params: {} })
  })

  it('passes an explicit days override through', () => {
    emailsAPI.followUps(3)
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/follow-ups', { params: { days: 3 } })
  })

  it('drafts and cancels the batch run', () => {
    emailsAPI.draftAllFollowUps()
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/follow-ups/draft-all')
    emailsAPI.cancelDraftFollowUps('job1')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/follow-ups/draft-all/job1/cancel')
  })

  it('reads and writes the cadence', () => {
    cadenceAPI.get()
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/follow-ups/cadence')
    cadenceAPI.update({ enabled: true, steps: [3, 7] })
    expect(mockAxiosInstance.put).toHaveBeenCalledWith('/follow-ups/cadence',
      { enabled: true, steps: [3, 7] })
  })

  /* Page suites mock this whole module, so a typo in a path is invisible to
     them and to the backend tests, which call the route directly. This file is
     the only place the two ends are held to the same string. */
  it('asks for the pipeline at the route the backend serves', () => {
    pipelineAPI.get()
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/pipeline')
  })

  it('reads a thread at the route the backend serves', () => {
    emailsAPI.thread('e1')
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/emails/e1/thread')
  })

  it('reads, adds and removes suppressions at the routes the backend serves', () => {
    suppressionsAPI.list()
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/suppressions')
    suppressionsAPI.add('dana@acme.com', 'asked to stop')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/suppressions',
      { value: 'dana@acme.com', reason: 'asked to stop' })
    suppressionsAPI.remove('s1')
    expect(mockAxiosInstance.delete).toHaveBeenCalledWith('/suppressions/s1')
  })

  it('lists and patches campaigns at the routes the backend serves', () => {
    campaignsAPI.list()
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/campaigns')
    campaignsAPI.update('c1', { archived: true })
    expect(mockAxiosInstance.patch).toHaveBeenCalledWith('/campaigns/c1', { archived: true })
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

describe('personFinderAPI', () => {
  it('starts a person search with the form payload untouched', () => {
    const payload = { name: 'Jane Doe', company_name: 'Acme', school: 'Penn' }
    personFinderAPI.start(payload)
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/person-finder', payload)
  })

  it('polls the typed endpoint by job id', () => {
    personFinderAPI.get('j1')
    expect(mockAxiosInstance.get).toHaveBeenCalledWith('/person-finder/j1')
  })

  it('cancels by job id', () => {
    personFinderAPI.cancel('j1')
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/person-finder/j1/cancel')
  })

  it('approves one candidate with the chosen channels', () => {
    personFinderAPI.approve('j1', {
      candidate_id: 'c1', email: 'jane.doe@acme.com',
      include_linkedin: true, confirm_email_ownership: false,
    })
    expect(mockAxiosInstance.post).toHaveBeenCalledWith('/person-finder/j1/approve', {
      candidate_id: 'c1', email: 'jane.doe@acme.com',
      include_linkedin: true, confirm_email_ownership: false,
    })
  })
})
