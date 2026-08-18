/* Scheduling is the one path in this app where email leaves without anyone
   watching, so it takes two independent yeses: the sending window switched on
   in Settings, and this batch picking "Send at…". The dialog must never offer
   the second when the first is missing — the endpoint refuses it, so a button
   there could only ever produce an error. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const { sendSpy, windowSpy, unscheduleSpy } = vi.hoisted(() => ({
  sendSpy: vi.fn(() => Promise.resolve({
    data: { id: 'job1', status: 'done', result: { scheduled: 1, scheduled_at: '2026-08-04T08:00:00' } },
  })),
  windowSpy: vi.fn(),
  unscheduleSpy: vi.fn(() => Promise.resolve({ data: { cleared: 1 } })),
}))

const base = {
  id: 'e1', status: 'draft', subject: 'ZZTEST draft', body: 'Hi there,',
  contact_name: 'ZZTEST Jane', contact_email: 'jane@zztest.invalid',
  contact_email_kind: 'personal', company_name: 'ZZTEST Corp',
  email_type: 'application', sent_at: null, gmail_message_id: null,
  created_at: '2026-08-01T09:00:00', has_response: 0, claims_attachment: 0,
  follow_ups_sent: 0, follow_up_pending: 0,
}

let emails = [{ ...base }]

vi.mock('../../src/api', async () => (await import('../_mocks')).emailsPageApi({
  emailsAPI: {
    list: vi.fn(() => Promise.resolve({ data: emails })),
    unschedule: unscheduleSpy,
    send: sendSpy,
  },
  sendWindowAPI: { get: windowSpy },
  jobsAPI: { get: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'done', result: {} } })) },
}))
vi.mock('../../src/App', async () => (await import('../_mocks')).appMock())
vi.mock('react-hot-toast', async () => (await import('../_mocks')).toastMock())

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

describe('Emails — queued mail is visible and stoppable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    windowSpy.mockResolvedValue({ data: { enabled: true, open_now: false,
      description: '8am–5pm on weekdays', next_opening: '2026-08-04T08:00:00' } })
    emails = [
      { ...base, id: 'e1', scheduled_at: '2026-08-04T08:00:00', status: 'approved' },
      { ...base, id: 'e2', scheduled_at: '2026-08-05T08:00:00', status: 'approved',
        contact_email: 'sam@zztest.invalid', subject: 'ZZTEST second' },
    ]
  })

  it('says so on every queued row, and takes them all back out at once', async () => {
    /* This is the only thing in the app that happens without a click, and
       before this it was invisible: no chip, no banner, and the unschedule
       endpoint had no caller anywhere in the frontend.

       Two rows, not one: with a single fixture the button reads "Cancel it"
       and a regression that cancelled only the first of twelve queued messages
       — leaving eleven armed after a click labelled "Cancel them all" — was
       indistinguishable from correct. */
    render(<Emails />)
    await waitFor(() => expect(document.querySelectorAll('.email-row').length).toBe(2))

    expect(document.body.textContent).toMatch(/queued to\s+send/i)
    // the per-row chip, which the banner assertion alone never observed
    const chips = [...document.querySelectorAll('.email-row')]
      .filter((r) => /queued/i.test(r.textContent))
    expect(chips.length).toBe(2)

    const cancel = [...document.querySelectorAll('button')]
      .find((b) => /^Cancel them all$/.test(b.textContent.trim()))
    expect(cancel).toBeTruthy()

    fireEvent.click(cancel)
    await waitFor(() => expect(unscheduleSpy).toHaveBeenCalledWith(['e1', 'e2']))
  })

  it('says nothing when a queued row has already been delivered', async () => {
    /* A stamp left behind on a row that has since gone out — by a manual send
       or by reconciliation — must not read as "this will send again". */
    emails = [{ ...base, id: 'e1', scheduled_at: '2026-08-04T08:00:00',
      status: 'sent', gmail_message_id: 'gm1', sent_at: '2026-08-04T08:00:00' }]
    render(<Emails />)
    await waitFor(() => expect(document.querySelector('.page')).toBeTruthy())
    await waitFor(() => expect(document.body.textContent).toMatch(/Sent/))
    expect(document.body.textContent).not.toMatch(/queued to\s+send/i)
    // and the row chip is gone too, not just the banner
    fireEvent.click([...document.querySelectorAll('button')]
      .find((b) => b.textContent.trim().startsWith('Sent')))
    await waitFor(() => expect(document.querySelectorAll('.email-row').length).toBe(1))
    expect(document.querySelector('.email-row').textContent).not.toMatch(/queued/i)
  })
})
