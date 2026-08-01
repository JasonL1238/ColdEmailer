/* Scheduling is the one path in this app where email leaves without anyone
   watching, so it takes two independent yeses: the sending window switched on
   in Settings, and this batch picking "Send at…". The dialog must never offer
   the second when the first is missing — the endpoint refuses it, so a button
   there could only ever produce an error. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const { sendSpy, windowSpy } = vi.hoisted(() => ({
  sendSpy: vi.fn(() => Promise.resolve({
    data: { id: 'job1', status: 'done', result: { scheduled: 1, scheduled_at: '2026-08-04T08:00:00' } },
  })),
  windowSpy: vi.fn(),
}))

let emails = [{
  id: 'e1', status: 'draft', subject: 'ZZTEST draft', body: 'Hi there,',
  contact_name: 'ZZTEST Jane', contact_email: 'jane@zztest.invalid',
  contact_email_kind: 'personal', company_name: 'ZZTEST Corp',
  email_type: 'application', sent_at: null, gmail_message_id: null,
  created_at: '2026-08-01T09:00:00', has_response: 0, claims_attachment: 0,
  follow_ups_sent: 0, follow_up_pending: 0,
}]

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => f || 'err',
  emailsAPI: {
    list: vi.fn(() => Promise.resolve({ data: emails })),
    followUps: vi.fn(() => Promise.resolve({ data: [] })),
    update: vi.fn(() => Promise.resolve({ data: {} })),
    bulkStatus: vi.fn(() => Promise.resolve({ data: { updated: 0 } })),
    send: sendSpy,
  },
  resumesAPI: { list: vi.fn(() => Promise.resolve({ data: [] })) },
  sendWindowAPI: { get: windowSpy },
  jobsAPI: { get: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'done', result: {} } })) },
}))
vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: vi.fn(), settings: {} }) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import Emails, { whenLabel } from '../../src/pages/Emails'

const modal = () => document.querySelector('.modal')
const buttons = () => [...(modal()?.querySelectorAll('button') || [])]
const scheduleButton = () => buttons().find((b) => /^Send at/.test(b.textContent.trim()))
const sendNowButton = () => buttons().find((b) => /^Send now$/.test(b.textContent.trim()))

async function openSendDialog() {
  render(<Emails />)
  await waitFor(() => expect(document.querySelectorAll('.email-row').length).toBe(1))
  fireEvent.click(document.querySelector('.email-row input[type="checkbox"]'))
  fireEvent.click([...document.querySelectorAll('button')]
    .find((b) => /^Send\b/.test(b.textContent.trim())))
  await waitFor(() => expect(modal()).toBeTruthy())
}

describe('whenLabel', () => {
  it('is specific enough to know what you are agreeing to', () => {
    expect(whenLabel('2026-08-04T08:00:00')).toMatch(/Tue/)
    expect(whenLabel('2026-08-04T08:00:00')).toMatch(/8:00/)
  })

  it('says nothing rather than something wrong', () => {
    expect(whenLabel(null)).toBe('')
    expect(whenLabel('not a date')).toBe('')
    expect(whenLabel(undefined)).toBe('')
  })
})

describe('SendModal — the scheduling gate', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    windowSpy.mockResolvedValue({ data: { enabled: false } })
  })

  it('offers no schedule button while the window is switched off', async () => {
    await openSendDialog()
    await waitFor(() => expect(windowSpy).toHaveBeenCalled())
    expect(scheduleButton()).toBeUndefined()
    expect(sendNowButton()).toBeTruthy()
  })

  it('offers no schedule button when the window is already open', async () => {
    // Nothing to wait for — holding the mail would just delay it pointlessly.
    windowSpy.mockResolvedValue({ data: { enabled: true, open_now: true,
      description: '8am–5pm on weekdays', next_opening: '2026-08-04T08:00:00' } })
    await openSendDialog()
    await waitFor(() => expect(windowSpy).toHaveBeenCalled())
    expect(scheduleButton()).toBeUndefined()
  })

  it('offers it, naming the time, when the window is on but shut', async () => {
    windowSpy.mockResolvedValue({ data: { enabled: true, open_now: false,
      description: '8am–5pm on weekdays', next_opening: '2026-08-04T08:00:00' } })
    await openSendDialog()
    await waitFor(() => expect(scheduleButton()).toBeTruthy())
    expect(scheduleButton().textContent).toMatch(/Tue/)
  })

  it('asks the backend to schedule only when that button is used', async () => {
    windowSpy.mockResolvedValue({ data: { enabled: true, open_now: false,
      description: '8am–5pm on weekdays', next_opening: '2026-08-04T08:00:00' } })
    await openSendDialog()
    await waitFor(() => expect(scheduleButton()).toBeTruthy())

    fireEvent.click(scheduleButton())
    await waitFor(() => expect(sendSpy).toHaveBeenCalled())
    expect(sendSpy.mock.calls[0][0].schedule).toBe('next_window')
  })

  it('never puts a schedule on an ordinary send, even with the window open', async () => {
    /* The field has to be absent, not falsy: this is the per-batch half of the
       permission, and "Send now" is the user watching it go. */
    windowSpy.mockResolvedValue({ data: { enabled: true, open_now: false,
      description: '8am–5pm on weekdays', next_opening: '2026-08-04T08:00:00' } })
    await openSendDialog()
    await waitFor(() => expect(scheduleButton()).toBeTruthy())

    fireEvent.click(sendNowButton())
    await waitFor(() => expect(sendSpy).toHaveBeenCalled())
    expect('schedule' in sendSpy.mock.calls[0][0]).toBe(false)
  })

  it('still sends when the window endpoint is missing entirely', async () => {
    /* An older backend has no /send-window. Losing the schedule button is a
       missing convenience; losing the dialog is losing the ability to send. */
    windowSpy.mockRejectedValue(new Error('404'))
    await openSendDialog()
    expect(sendNowButton()).toBeTruthy()
    fireEvent.click(sendNowButton())
    await waitFor(() => expect(sendSpy).toHaveBeenCalled())
  })
})
