import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const emails = [
  // Legacy row: Gmail already delivered it, but the status column still says
  // "approved" — it must not sit in Drafts with a live Send button.
  {
    id: 'delivered', status: 'approved', subject: 'ZZTEST already sent',
    body: 'hello', contact_name: 'ZZTEST Jason', contact_email: 'zz@example.com',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: '2026-02-17T18:17:16', gmail_message_id: '19c6de4ca8e06b84',
    created_at: '2026-02-17T18:00:00', has_response: 0, has_follow_up: 0,
  },
  {
    id: 'fresh', status: 'draft', subject: 'ZZTEST real draft', body: 'hello',
    contact_name: 'ZZTEST Fresh', contact_email: 'zzfresh@example.com',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: null, gmail_message_id: null,
    created_at: '2026-02-18T18:00:00', has_response: 0, has_follow_up: 0,
  },
  {
    id: 'followed', status: 'sent', subject: 'ZZTEST followed up', body: 'hello',
    contact_name: 'ZZTEST Followed', contact_email: 'zzf@example.com',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: '2026-02-10T10:00:00', gmail_message_id: 'abc',
    created_at: '2026-02-10T09:00:00', has_response: 0, has_follow_up: 1,
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
  sendWindowAPI: { get: vi.fn(() => Promise.resolve({ data: { enabled: false } })) },
  jobsAPI: { get: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'running' } })) },
}))

vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: vi.fn(), settings: {} }) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import Emails from '../../src/pages/Emails'

const rowTitles = () => [...document.querySelectorAll('.email-row-title')].map((n) => n.textContent)
const openTab = (label) => fireEvent.click(
  [...document.querySelectorAll('.segmented button')].find((b) => b.textContent.startsWith(label)))
const openRow = (name) => fireEvent.click(
  [...document.querySelectorAll('.email-row')].find((r) => r.textContent.includes(name)))
const detail = () => document.querySelector('.email-detail')

async function open() {
  render(<Emails />)
  await waitFor(() => expect(rowTitles().length).toBeGreaterThan(0))
}

describe('Emails — already-delivered rows', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('keeps an email Gmail already delivered out of Drafts', async () => {
    await open()
    // Drafts holds only the genuine draft, so neither "Select all" nor the
    // Send button can pick up a message the recipient already has.
    expect(rowTitles()).toEqual(['ZZTEST Fresh'])

    openTab('Sent')
    await waitFor(() => expect(rowTitles()).toContain('ZZTEST Jason'))
  })

  it('shows a delivered email as sent, with no Send or Rewrite button', async () => {
    await open()
    openTab('Sent')
    await waitFor(() => expect(rowTitles()).toContain('ZZTEST Jason'))
    openRow('ZZTEST Jason')

    const labels = [...detail().querySelectorAll('button')].map((b) => b.textContent)
    expect(labels.some((t) => t.trim() === 'Send')).toBe(false)
    expect(labels.some((t) => /Rewrite/.test(t))).toBe(false)
    expect(detail().textContent).toContain('Sent ')
  })

  it('does not offer a second follow-up once one is drafted', async () => {
    await open()
    openTab('Sent')
    await waitFor(() => expect(rowTitles()).toContain('ZZTEST Followed'))
    openRow('ZZTEST Followed')

    expect(detail().textContent).toContain('follow-up drafted')
    expect([...detail().querySelectorAll('button')]
      .some((b) => /follow.up/i.test(b.textContent))).toBe(false)
  })
})
