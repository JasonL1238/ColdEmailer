import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const emails = [
  // Sent, never answered on this row — but this person replied to a different
  // email. A follow-up is written on the premise of silence, so offering one
  // here would send "still very interested?" to someone who already wrote back.
  {
    id: 'sibling-replied', status: 'sent', subject: 'ZZTEST second note',
    body: 'hello', contact_name: 'ZZTEST Answered', contact_email: 'zzans@example.invalid',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: '2026-03-01T10:00:00', gmail_message_id: 'abc1',
    created_at: '2026-03-01T09:00:00',
    has_response: 0, contact_has_replied: 1, has_follow_up: 0,
  },
  {
    id: 'truly-silent', status: 'sent', subject: 'ZZTEST only note',
    body: 'hello', contact_name: 'ZZTEST Silent', contact_email: 'zzsil@example.invalid',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: '2026-03-01T10:00:00', gmail_message_id: 'abc2',
    created_at: '2026-03-01T09:00:00',
    has_response: 0, contact_has_replied: 0, has_follow_up: 0,
  },
  // Handed to Gmail once with no confirmation back. Gmail may already have
  // queued it, so this is not a clean draft to retry.
  {
    id: 'unconfirmed', status: 'approved', subject: 'ZZTEST unconfirmed',
    body: 'hello', contact_name: 'ZZTEST Unsure', contact_email: 'zzunsure@example.invalid',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: null, gmail_message_id: null, created_at: '2026-03-02T09:00:00',
    has_response: 0, contact_has_replied: 0, has_follow_up: 0,
    send_attempted_at: '2026-03-02T09:30:00',
    send_attempt_error: 'timed out reading response from Gmail',
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

import Emails from '../../src/pages/Emails'
import { emailsAPI } from '../../src/api'

const rowTitles = () => [...document.querySelectorAll('.email-row-title')].map((n) => n.textContent)
const openTab = (label) => fireEvent.click(
  [...document.querySelectorAll('.segmented button')].find((b) => b.textContent.startsWith(label)))
const openRow = (name) => fireEvent.click(
  [...document.querySelectorAll('.email-row')].find((r) => r.textContent.includes(name)))
const detail = () => document.querySelector('.email-detail')
const modal = () => document.querySelector('.modal')

async function open() {
  render(<Emails />)
  await waitFor(() => expect(rowTitles().length).toBeGreaterThan(0))
}

async function openSendModal() {
  await open()
  openRow('ZZTEST Unsure')
  fireEvent.click([...detail().querySelectorAll('button')]
    .find((b) => b.textContent.trim() === 'Send'))
  await waitFor(() => expect(modal()).toBeTruthy())
}

const clickSendNow = () => fireEvent.click(
  [...modal().querySelectorAll('button')].find((b) => /send now/i.test(b.textContent)))

describe('Emails — follow-up to someone who already replied', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('offers no follow-up when the reply landed on another email', async () => {
    await open()
    openTab('Sent')
    await waitFor(() => expect(rowTitles()).toContain('ZZTEST Answered'))
    openRow('ZZTEST Answered')

    expect(detail().textContent).toContain('this person already replied')
    expect([...detail().querySelectorAll('button')]
      .some((b) => /follow.up/i.test(b.textContent))).toBe(false)
  })

  it('still offers a follow-up to someone who never replied', async () => {
    await open()
    openTab('Sent')
    await waitFor(() => expect(rowTitles()).toContain('ZZTEST Silent'))
    openRow('ZZTEST Silent')

    expect([...detail().querySelectorAll('button')]
      .some((b) => /follow.up/i.test(b.textContent))).toBe(true)
  })
})

describe('Emails — a send whose delivery was never confirmed', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('marks the row instead of presenting it as a clean draft', async () => {
    await open()
    const row = [...document.querySelectorAll('.email-row')]
      .find((r) => r.textContent.includes('ZZTEST Unsure'))
    expect(row.textContent).toContain('delivery unconfirmed')
  })

  it('warns before a retry and does not claim it as safe', async () => {
    await open()
    openRow('ZZTEST Unsure')
    fireEvent.click([...detail().querySelectorAll('button')]
      .find((b) => b.textContent.trim() === 'Send'))
    await waitFor(() => expect(modal()).toBeTruthy())

    expect(modal().textContent).toContain('already handed to Gmail')
    expect(modal().textContent).toContain('second copy')
    expect(modal().textContent).toContain('zzunsure@example.invalid')
  })

  it('does not ask the backend to retry unless the user confirms', async () => {
    await openSendModal()
    clickSendNow()
    await waitFor(() => expect(emailsAPI.send).toHaveBeenCalled())
    expect(emailsAPI.send.mock.calls[0][0].confirm_resend).toBe(false)
  })

  it('retries only what the user explicitly confirmed', async () => {
    await openSendModal()
    const confirmBox = [...modal().querySelectorAll('label')]
      .find((l) => /send it anyway/i.test(l.textContent))
      ?.querySelector('input[type=checkbox]')
    expect(confirmBox).toBeTruthy()

    fireEvent.click(confirmBox)
    clickSendNow()
    await waitFor(() => expect(emailsAPI.send).toHaveBeenCalled())
    expect(emailsAPI.send.mock.calls[0][0].confirm_resend).toBe(true)
  })
})
