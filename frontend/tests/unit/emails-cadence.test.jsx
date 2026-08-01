/* The follow-up control has to answer a three-way question, not a yes/no one:
   is a draft already waiting, has this person had every nudge the cadence
   allows, or is another one owed?

   `has_follow_up` — the flag the button used to live behind — collapses the
   first two. It goes true when a follow-up is created and stays true after it
   is sent, so with a two-step cadence the second rung could never be drafted:
   the button had already been replaced by a "follow-up drafted" chip, for
   good, on every one of that person's emails. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const sentEmail = (extra) => ({
  id: 'e1', status: 'sent', subject: 'ZZTEST original',
  body: 'Hi Jane,\n\nA body.\n\nThanks,',
  contact_name: 'ZZTEST Jane', contact_email: 'jane@zztest.invalid',
  contact_email_kind: 'personal', company_name: 'ZZTEST Corp',
  email_type: 'application', sent_at: '2026-06-01T09:00:00',
  gmail_message_id: 'gm1', created_at: '2026-06-01T09:00:00',
  has_response: 0, has_follow_up: 0, follow_ups_sent: 0, follow_up_pending: 0,
  used_template_fallback: 0, ...extra,
})

let emails = [sentEmail()]
let settings = { llm_provider: 'gemini', follow_up_cadence: { enabled: true, steps: [7, 7] } }
// vi.mock is hoisted above every `const` in this file, so the spy the factory
// closes over has to be hoisted with it.
const { draftAll } = vi.hoisted(() => ({
  draftAll: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'running' } })),
}))

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => f || 'err',
  emailsAPI: {
    list: vi.fn(() => Promise.resolve({ data: emails })),
    followUps: vi.fn(() => Promise.resolve({ data: followUps })),
    update: vi.fn(() => Promise.resolve({ data: {} })),
    bulkStatus: vi.fn(() => Promise.resolve({ data: { updated: 0 } })),
    generateFollowUp: vi.fn(() => Promise.resolve({ data: {} })),
    draftAllFollowUps: draftAll,
    send: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'running' } })),
  },
  resumesAPI: { list: vi.fn(() => Promise.resolve({ data: [] })) },
  jobsAPI: { get: vi.fn(() => Promise.resolve({ data: { id: 'job1', status: 'running' } })) },
}))
vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: vi.fn(), settings }) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

let followUps = []

import Emails, { followUpState, DEFAULT_CADENCE } from '../../src/pages/Emails'

const TWO_STEP = { enabled: true, steps: [7, 7] }

describe('followUpState', () => {
  it('offers the next rung once the previous one has been sent', () => {
    /* Deleting the cadence branch and going back to `has_follow_up` leaves
       this the only failing test in the file. */
    expect(followUpState({ follow_ups_sent: 0, has_follow_up: 0 }, TWO_STEP))
      .toEqual({ kind: 'ready', step: 1, total: 2 })
    expect(followUpState({ follow_ups_sent: 1, has_follow_up: 1 }, TWO_STEP))
      .toEqual({ kind: 'ready', step: 2, total: 2 })
  })

  it('says nothing more is owed once the cadence is spent', () => {
    expect(followUpState({ follow_ups_sent: 2, has_follow_up: 1 }, TWO_STEP))
      .toEqual({ kind: 'done', sent: 2, total: 2 })
    // a shortened cadence retires people who already had more than it allows
    expect(followUpState({ follow_ups_sent: 2 }, { enabled: true, steps: [7] }).kind)
      .toBe('done')
  })

  it('refuses to offer another while one is still sitting unsent', () => {
    expect(followUpState({ follow_ups_sent: 0, follow_up_pending: 1 }, TWO_STEP))
      .toEqual({ kind: 'pending' })
    // pending outranks "a rung is available" — two drafts to one person is
    // exactly what "Select all" then delivers
    expect(followUpState({ follow_ups_sent: 1, follow_up_pending: 1 }, TWO_STEP))
      .toEqual({ kind: 'pending' })
  })

  it('offers nothing on a bounced address, whichever field carries it', () => {
    /* The send path and both drafting routes all refuse a bounced address, so
       an enabled button here can only ever produce the same 409. The component
       already reads these fields for the address warning; this branch was the
       one place it did not. */
    expect(followUpState({ contact_bounced_at: '2026-07-01T09:00:00' }, TWO_STEP))
      .toEqual({ kind: 'bounced' })
    expect(followUpState({ bounced_at: '2026-07-01T09:00:00' }, TWO_STEP))
      .toEqual({ kind: 'bounced' })
    // it outranks a rung being owed
    expect(followUpState({ bounced_at: '2026-07-01T09:00:00', follow_ups_sent: 0 },
      TWO_STEP).kind).toBe('bounced')
  })

  it('goes quiet when the user has switched follow-ups off', () => {
    expect(followUpState({}, { enabled: false, steps: [7] })).toEqual({ kind: 'off' })
    expect(followUpState({}, { enabled: true, steps: [] })).toEqual({ kind: 'off' })
  })

  it('treats a missing cadence as the default, not as off', () => {
    /* Settings load asynchronously. Reading "not here yet" as "switched off"
       blanked the follow-up control on every sent email until the request came
       back — and permanently against a backend too old to send the field. */
    expect(followUpState({ follow_ups_sent: 0 }, undefined))
      .toEqual({ kind: 'ready', step: 1, total: 1 })
    expect(DEFAULT_CADENCE.steps.length).toBe(1)
  })

  it('falls back to has_follow_up when the row predates the new fields', () => {
    expect(followUpState({ has_follow_up: 1 }, TWO_STEP)).toEqual({ kind: 'pending' })
  })
})

const rows = () => [...document.querySelectorAll('.email-row')]
const detail = () => document.querySelector('.email-detail')

async function open(tab = 'Sent') {
  render(<Emails />)
  await waitFor(() => expect(rows().length + 1).toBeGreaterThan(0))
  const btn = [...document.querySelectorAll('button')]
    .find((b) => b.textContent.trim().startsWith(tab))
  if (btn) fireEvent.click(btn)
  await waitFor(() => expect(rows().length).toBeGreaterThan(0))
}

describe('Emails — the follow-up control across the cadence', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    followUps = []
    settings = { llm_provider: 'gemini', follow_up_cadence: { enabled: true, steps: [7, 7] } }
    emails = [sentEmail()]
  })

  it('offers rung 2 after rung 1 went out, labelled with the step', async () => {
    emails = [sentEmail({ follow_ups_sent: 1, has_follow_up: 1, follow_up_pending: 0 })]
    await open()
    const button = [...detail().querySelectorAll('button')]
      .find((b) => /follow.up/i.test(b.textContent))
    expect(button).toBeTruthy()
    expect(button.textContent).toContain('2 of 2')
  })

  it('shows a chip instead of a dead button on a bounced address', async () => {
    emails = [sentEmail({ contact_bounced_at: '2026-07-01T09:00:00' })]
    await open()
    expect([...detail().querySelectorAll('button')]
      .some((b) => /^Draft follow-up/i.test(b.textContent.trim()))).toBe(false)
    expect(detail().textContent).toMatch(/address bounced/i)
  })

  it('stops offering once the cadence is spent, and says how many went', async () => {
    emails = [sentEmail({ follow_ups_sent: 2, has_follow_up: 1 })]
    await open()
    expect([...detail().querySelectorAll('button')]
      .some((b) => /follow.up/i.test(b.textContent))).toBe(false)
    expect(detail().textContent).toContain('2 of 2 follow-ups sent')
  })

  it('drafts the whole due list in one click, and never sends it', async () => {
    followUps = [
      { id: 'e1', contact_id: 'c1', next_follow_up_step: 1, follow_up_steps_total: 2 },
      { id: 'e2', contact_id: 'c2', next_follow_up_step: 2, follow_up_steps_total: 2 },
    ]
    await open()
    const draftButton = [...document.querySelectorAll('button')]
      .find((b) => /^Draft 2 follow-ups$/.test(b.textContent.trim()))
    expect(draftButton).toBeTruthy()
    fireEvent.click(draftButton)
    await waitFor(() => expect(draftAll).toHaveBeenCalledTimes(1))
  })

  it('names which rungs the due contacts are on', async () => {
    followUps = [
      { id: 'e1', contact_id: 'c1', next_follow_up_step: 1, follow_up_steps_total: 2 },
      { id: 'e2', contact_id: 'c2', next_follow_up_step: 2, follow_up_steps_total: 2 },
      { id: 'e3', contact_id: 'c3', next_follow_up_step: 2, follow_up_steps_total: 2 },
    ]
    await open()
    // a step-2-of-2 batch is the last thing those people hear from you
    expect(document.body.textContent).toMatch(/1 on step 1, 2 on step 2/)
  })
})
