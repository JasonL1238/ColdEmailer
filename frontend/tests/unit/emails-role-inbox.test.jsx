/* Two warnings the drafts screen owes the user before they hit Send.

   1. A draft addressed to a shared company inbox (info@, careers@) rather than
      a named person. New contacts cannot enter the DB that way, but older ones
      did, and a role inbox replies far less often.
   2. Why a draft came out of the plain template. "AI was unavailable" was the
      only thing the app could ever say, which does not tell you whether to
      wait for a quota reset, fix a key, or check your connection. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const emails = [
  {
    id: 'role', status: 'draft', subject: 'ZZTEST to a shared inbox',
    body: 'Hi there,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: '', contact_email: 'info@zztest.invalid',
    contact_email_kind: 'generic',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: null, gmail_message_id: null, created_at: '2026-07-01T09:00:00',
    has_response: 0, has_follow_up: 0, used_template_fallback: 0,
  },
  {
    id: 'person', status: 'draft', subject: 'ZZTEST to a human',
    body: 'Hi Jane,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: 'ZZTEST Jane', contact_email: 'jane.doe@zztest.invalid',
    contact_email_kind: 'personal',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: null, gmail_message_id: null, created_at: '2026-07-01T10:00:00',
    has_response: 0, has_follow_up: 0, used_template_fallback: 0,
  },
  {
    id: 'quota', status: 'draft', subject: 'ZZTEST fell back',
    body: 'Hi Sam,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: 'ZZTEST Sam', contact_email: 'sam@zztest.invalid',
    contact_email_kind: 'personal',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: null, gmail_message_id: null, created_at: '2026-07-01T11:00:00',
    has_response: 0, has_follow_up: 0,
    used_template_fallback: 1, fallback_reason: 'llm_quota',
  },
  {
    id: 'mystery', status: 'draft', subject: 'ZZTEST fell back vaguely',
    body: 'Hi Kim,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: 'ZZTEST Kim', contact_email: 'kim@zztest.invalid',
    contact_email_kind: 'personal',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: null, gmail_message_id: null, created_at: '2026-07-01T12:00:00',
    has_response: 0, has_follow_up: 0,
    used_template_fallback: 1, fallback_reason: 'llm_unavailable',
  },
]

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => f || 'err',
  emailsAPI: {
    list: vi.fn(() => Promise.resolve({ data: emails })),
    followUps: vi.fn(() => Promise.resolve({ data: [] })),
    update: vi.fn(() => Promise.resolve({ data: {} })),
    bulkStatus: vi.fn(() => Promise.resolve({ data: { updated: 0 } })),
    generateFollowUp: vi.fn(() => Promise.resolve({ data: {} })),
    send: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'running' } })),
  },
  resumesAPI: { list: vi.fn(() => Promise.resolve({ data: [] })) },
  jobsAPI: { get: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'running' } })) },
}))
vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: vi.fn(), settings: {} }) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import Emails, { isRoleInbox, fallbackExplanation } from '../../src/pages/Emails'

const rows = () => [...document.querySelectorAll('.email-row')]
const rowFor = (name) => rows().find((r) => r.textContent.includes(name))
const openRow = (name) => fireEvent.click(rowFor(name))
const detail = () => document.querySelector('.email-detail')

async function open() {
  render(<Emails />)
  await waitFor(() => expect(rows().length).toBeGreaterThan(0))
}

describe('isRoleInbox', () => {
  it('is true only for a shared company inbox', () => {
    expect(isRoleInbox({ contact_email_kind: 'generic' })).toBe(true)
    expect(isRoleInbox({ contact_email_kind: 'personal' })).toBe(false)
    expect(isRoleInbox({ contact_email_kind: 'named_unmatched' })).toBe(false)
  })

  it('does not guess when the classification is missing', () => {
    // An older row, or a payload that never joined the contact. Claiming
    // "role inbox" on absent data would be a warning the app cannot back up.
    expect(isRoleInbox({})).toBe(false)
    expect(isRoleInbox({ contact_email_kind: null })).toBe(false)
    expect(isRoleInbox({ contact_email_kind: 'unknown' })).toBe(false)
  })
})

describe('fallbackExplanation', () => {
  it('says what to actually do about each cause', () => {
    expect(fallbackExplanation('llm_quota')).toMatch(/quota/i)
    expect(fallbackExplanation('llm_auth')).toMatch(/key/i)
    expect(fallbackExplanation('llm_no_key')).toMatch(/no ai provider/i)
    expect(fallbackExplanation('llm_network')).toMatch(/reach/i)
  })

  it('falls back to the old wording for an unrecognised reason', () => {
    expect(fallbackExplanation('llm_unavailable')).toMatch(/AI was unavailable/i)
    expect(fallbackExplanation(undefined)).toMatch(/AI was unavailable/i)
    expect(fallbackExplanation('something_new')).toMatch(/AI was unavailable/i)
  })
})

describe('Emails — role inbox warning', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('chips the draft aimed at a shared inbox, and only that one', async () => {
    await open()
    expect(rowFor('info@zztest.invalid').textContent).toContain('role inbox')
    expect(rowFor('ZZTEST Jane').textContent).not.toContain('role inbox')
  })

  it('repeats it in the send dialog, where the decision is actually made', async () => {
    await open()
    fireEvent.click(document.querySelector('.email-row input[type="checkbox"]'))
    const send = [...document.querySelectorAll('button')]
      .find((b) => /^Send\b/.test(b.textContent.trim()))
    fireEvent.click(send)
    await waitFor(() => expect(document.querySelector('.modal')).toBeTruthy())
    expect(document.querySelector('.modal').textContent).toContain('role inbox')
  })
})

describe('Emails — why a draft used the template', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('names the cause when it knows it', async () => {
    await open()
    openRow('ZZTEST Sam')
    await waitFor(() => expect(detail()).toBeTruthy())
    expect(detail().textContent).toMatch(/quota is exhausted/i)
    expect(detail().textContent).not.toMatch(/AI was unavailable/i)
  })

  it('still says something useful when it does not', async () => {
    await open()
    openRow('ZZTEST Kim')
    await waitFor(() => expect(detail()).toBeTruthy())
    expect(detail().textContent).toMatch(/AI was unavailable/i)
  })
})
