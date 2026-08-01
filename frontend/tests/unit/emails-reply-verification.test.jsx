/* Reply flags inherited from the older checker must not be presented as fact —
   no fabricated "replied Mar 12" date, and they must not suppress follow-ups.
   The re-verification the backend implements also has to be reachable. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const emails = [
  // Flagged by the old reply check, never verified. response_at is the moment
  // that checker ran, not a reply time.
  {
    id: 'legacy', status: 'sent', subject: 'ZZTEST legacy note', body: 'hello',
    contact_name: 'ZZTEST Legacy', contact_email: 'zzlegacy@example.invalid',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: '2026-03-11T17:29:00', gmail_message_id: 'g1',
    created_at: '2026-03-11T17:00:00',
    has_response: 1, response_at: '2026-03-12T16:26:02',
    reply_unverified: 1, contact_reply_unverified: 1,
    contact_has_replied: 0, has_follow_up: 0,
  },
  // Confirmed by the current checker: this one may be stated as fact.
  {
    id: 'verified', status: 'sent', subject: 'ZZTEST verified note', body: 'hello',
    contact_name: 'ZZTEST Verified', contact_email: 'zzver@example.invalid',
    company_name: 'ZZTEST Corp', email_type: 'application',
    sent_at: '2026-03-11T17:29:00', gmail_message_id: 'g2',
    created_at: '2026-03-11T17:00:00',
    has_response: 1, response_at: '2026-03-14T08:15:00',
    response_verified_at: '2026-07-29T10:00:00',
    reply_unverified: 0, contact_reply_unverified: 0,
    contact_has_replied: 1, has_follow_up: 0,
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
    checkReplies: vi.fn(() => Promise.resolve({
      data: {
        checked: 2, new_replies: 0, cleared: 1, confirmed: 0,
        failed_checks: 0, unverified_remaining: 0,
      },
    })),
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
import { emailsAPI } from '../../src/api'

const checkReplies = emailsAPI.checkReplies

const rowTitles = () => [...document.querySelectorAll('.email-row-title')].map((n) => n.textContent)
const openTab = (label) => fireEvent.click(
  [...document.querySelectorAll('.segmented button')].find((b) => b.textContent.startsWith(label)))
const openRow = (name) => fireEvent.click(
  [...document.querySelectorAll('.email-row')].find((r) => r.textContent.includes(name)))
const detail = () => document.querySelector('.email-detail')
const buttonNamed = (root, re) => [...root.querySelectorAll('button')]
  .find((b) => re.test(b.textContent))

async function openSent() {
  render(<Emails />)
  // both fixtures are sent, so the default Drafts tab is legitimately empty
  await waitFor(() => expect(document.querySelectorAll('.segmented button').length)
    .toBeGreaterThan(0))
  openTab('Sent')
  await waitFor(() => expect(rowTitles()).toContain('ZZTEST Legacy'))
}

describe('Emails — unverified legacy reply flags', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('does not print a fabricated reply date', async () => {
    await openSent()
    openRow('ZZTEST Legacy')

    expect(detail().textContent).toContain('reply unverified')
    expect(detail().textContent).not.toMatch(/replied\s+(Mar|1|2)/)
    expect(detail().textContent).toContain('also counted bounces')
  })

  it('still offers a follow-up — an unverified flag is not a reply', async () => {
    await openSent()
    openRow('ZZTEST Legacy')

    expect(buttonNamed(detail(), /follow.up/i)).toBeTruthy()
    expect(detail().textContent).not.toContain('this person already replied')
  })

  it('states a verified reply as fact, with its date', async () => {
    await openSent()
    openRow('ZZTEST Verified')

    expect(detail().textContent).toMatch(/replied/)
    expect(detail().textContent).not.toContain('unverified')
    expect(buttonNamed(detail(), /follow.up/i)).toBeFalsy()
  })

  it('marks the list row as unverified rather than green "replied"', async () => {
    await openSent()
    const row = [...document.querySelectorAll('.email-row')]
      .find((r) => r.textContent.includes('ZZTEST Legacy'))
    expect(row.textContent).toContain('reply unverified')
  })

  it('exposes the re-verification the backend implements', async () => {
    await openSent()
    expect(document.body.textContent).toContain('older reply check')

    fireEvent.click(buttonNamed(document.body, /re-verify replies/i))
    await waitFor(() => expect(checkReplies).toHaveBeenCalled())
    // recheck=true is what lets the backend revisit an existing flag
    expect(checkReplies.mock.calls[0][0]).toBe(true)
  })

  it('an ordinary reply check does not ask for a full recheck', async () => {
    await openSent()
    fireEvent.click(buttonNamed(document.body, /^Check replies$/i))
    await waitFor(() => expect(checkReplies).toHaveBeenCalled())
    expect(checkReplies.mock.calls[0][0]).toBe(false)
  })
})
