/* The send dialog must not silently contradict the draft's own words. The body
   promises "My resume is attached for convenience." around one specific PDF;
   unticking "Attach resume" or "Attach X to all" used to strip or swap it with
   no warning in the modal, the recipient list, or the results screen. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const emails = [
  {
    id: 'claims', status: 'draft', subject: 'ZZTEST internship inquiry',
    body: 'Hi there,\n\nI would love to contribute. My resume is attached for '
      + 'convenience.\n\nThanks so much,',
    contact_name: 'ZZTEST Claim', contact_email: 'zzclaim@example.invalid',
    company_name: 'ZZTEST Corp', email_type: 'application',
    resume_id: 'r1', claims_attachment: 1,
    sent_at: null, gmail_message_id: null, created_at: '2026-07-01T09:00:00',
    has_response: 0, contact_has_replied: 0, has_follow_up: 0,
  },
  {
    id: 'plain', status: 'draft', subject: 'ZZTEST quick idea',
    body: 'Hi there,\n\nOne idea for your team.\n\nThanks,',
    contact_name: 'ZZTEST Plain', contact_email: 'zzplain@example.invalid',
    company_name: 'ZZTEST Corp', email_type: 'sales',
    resume_id: null, claims_attachment: 0,
    sent_at: null, gmail_message_id: null, created_at: '2026-07-01T09:00:00',
    has_response: 0, contact_has_replied: 0, has_follow_up: 0,
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
    checkReplies: vi.fn(() => Promise.resolve({ data: {} })),
    send: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'running' } })),
  },
  sendWindowAPI: { get: vi.fn(() => Promise.resolve({ data: { enabled: false } })) },
  resumesAPI: {
    list: vi.fn(() => Promise.resolve({
      data: [
        { id: 'r1', label: 'ZZTEST Software resume', is_default: true },
        { id: 'r2', label: 'ZZTEST Research resume', is_default: false },
      ],
    })),
  },
  jobsAPI: { get: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'running' } })) },
}))

vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: vi.fn(), settings: {} }) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import Emails from '../../src/pages/Emails'
import { emailsAPI } from '../../src/api'

const rowTitles = () => [...document.querySelectorAll('.email-row-title')].map((n) => n.textContent)
const modal = () => document.querySelector('.modal')
const openRow = (name) => fireEvent.click(
  [...document.querySelectorAll('.email-row')].find((r) => r.textContent.includes(name)))
const detail = () => document.querySelector('.email-detail')
const button = (root, re) => [...root.querySelectorAll('button')]
  .find((b) => re.test(b.textContent))
const labelled = (re) => [...modal().querySelectorAll('label')]
  .find((l) => re.test(l.textContent))?.querySelector('input')

async function openSendModal(name) {
  render(<Emails />)
  await waitFor(() => expect(rowTitles().length).toBeGreaterThan(0))
  openRow(name)
  fireEvent.click([...detail().querySelectorAll('button')]
    .find((b) => b.textContent.trim() === 'Send'))
  await waitFor(() => expect(modal()).toBeTruthy())
  // the resume list has to be in before the cross-check can be meaningful
  await waitFor(() => expect(modal().textContent).toContain('ZZTEST Software resume'))
}

describe('SendModal — the body says a resume is attached', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows which resume each draft would actually send', async () => {
    await openSendModal('ZZTEST Claim')
    expect(modal().textContent).toContain('attaching ZZTEST Software resume')
  })

  it('warns when the attachment would be stripped, and blocks the send', async () => {
    await openSendModal('ZZTEST Claim')
    fireEvent.click(labelled(/Attach resume/i))

    expect(modal().textContent).toContain('says a resume is attached')
    expect(modal().textContent).toContain('promising an attachment that is not there')
    expect(modal().textContent).toContain('zzclaim@example.invalid')
    expect(button(modal(), /send now/i).disabled).toBe(true)
  })

  it('warns when a different resume would be stapled on', async () => {
    await openSendModal('ZZTEST Claim')
    fireEvent.change(modal().querySelector('select'), { target: { value: 'r2' } })

    expect(modal().textContent).toContain('attach a different PDF')
    expect(modal().textContent).toContain('written around ZZTEST Software resume')
    expect(button(modal(), /send now/i).disabled).toBe(true)
  })

  it('sends once the user acknowledges the mismatch, and says so to the backend', async () => {
    await openSendModal('ZZTEST Claim')
    fireEvent.click(labelled(/Attach resume/i))
    fireEvent.click(labelled(/Send anyway/i))

    const send = button(modal(), /send now/i)
    expect(send.disabled).toBe(false)
    fireEvent.click(send)
    await waitFor(() => expect(emailsAPI.send).toHaveBeenCalled())
    const payload = emailsAPI.send.mock.calls[0][0]
    expect(payload.attach_resume).toBe(false)
    expect(payload.confirm_attachment_change).toBe(true)
  })

  it('does not nag about a draft that promises nothing', async () => {
    await openSendModal('ZZTEST Plain')
    fireEvent.click(labelled(/Attach resume/i))

    expect(modal().textContent).not.toContain('says a resume is attached')
    expect(button(modal(), /send now/i).disabled).toBe(false)
    fireEvent.click(button(modal(), /send now/i))
    await waitFor(() => expect(emailsAPI.send).toHaveBeenCalled())
    expect(emailsAPI.send.mock.calls[0][0].confirm_attachment_change).toBe(false)
  })
})
