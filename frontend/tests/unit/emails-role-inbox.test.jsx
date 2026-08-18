/* Two warnings the drafts screen owes the user before they hit Send.

   1. A draft addressed to a shared company inbox (info@, careers@) rather than
      a named person. New contacts cannot enter the DB that way, but older ones
      did, and a role inbox replies far less often.
   2. Why a draft came out of the plain template. "AI was unavailable" was the
      only thing the app could ever say, which does not tell you whether to
      wait for a quota reset, fix a key, or check your connection. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

/* The fields every row here shares. `body` and `contact_name` are passed at
   each call site on purpose: the greeting is paired with the name, and the
   'mismatch' row is 'Hi Jane,' sent to bob.smith@ — that pairing IS the
   named_unmatched fixture. */
const draft = (over) => ({
  status: 'draft', company_name: 'ZZTEST Corp', email_type: 'application',
  sent_at: null, gmail_message_id: null,
  has_response: 0, has_follow_up: 0, used_template_fallback: 0, ...over,
})

const emails = [
  draft({
    id: 'role', subject: 'ZZTEST to a shared inbox',
    body: 'Hi there,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: '', contact_email: 'info@zztest.invalid',
    contact_email_kind: 'generic', created_at: '2026-07-01T09:00:00',
  }),
  draft({
    id: 'person', subject: 'ZZTEST to a human',
    body: 'Hi Jane,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: 'ZZTEST Jane', contact_email: 'jane.doe@zztest.invalid',
    contact_email_kind: 'personal', created_at: '2026-07-01T10:00:00',
  }),
  draft({
    id: 'mismatch', subject: 'ZZTEST wrong human',
    body: 'Hi Jane,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: 'ZZTEST Jane Doe', contact_email: 'bob.smith@zztest.invalid',
    contact_email_kind: 'named_unmatched', created_at: '2026-07-01T10:30:00',
  }),
  draft({
    id: 'quota', subject: 'ZZTEST fell back',
    body: 'Hi Sam,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: 'ZZTEST Sam', contact_email: 'sam@zztest.invalid',
    contact_email_kind: 'personal', created_at: '2026-07-01T11:00:00',
    used_template_fallback: 1, fallback_reason: 'llm_quota',
  }),
  draft({
    id: 'mystery', subject: 'ZZTEST fell back vaguely',
    body: 'Hi Kim,\n\nA perfectly ordinary body.\n\nThanks so much,',
    contact_name: 'ZZTEST Kim', contact_email: 'kim@zztest.invalid',
    contact_email_kind: 'personal', created_at: '2026-07-01T12:00:00',
    used_template_fallback: 1, fallback_reason: 'llm_unavailable',
  }),
]

vi.mock('../../src/api', async () => (await import('../_mocks')).emailsPageApi({
  emailsAPI: { list: vi.fn(() => Promise.resolve({ data: emails })) },
}))
vi.mock('../../src/App', async () => (await import('../_mocks')).appMock())
vi.mock('react-hot-toast', async () => (await import('../_mocks')).toastMock())

import Emails, { addressWarning, fallbackExplanation } from '../../src/pages/Emails'

const rows = () => [...document.querySelectorAll('.email-row')]
const rowFor = (name) => rows().find((r) => r.textContent.includes(name))
const openRow = (name) => fireEvent.click(rowFor(name))
const detail = () => document.querySelector('.email-detail')

async function open() {
  render(<Emails />)
  await waitFor(() => expect(rows().length).toBeGreaterThan(0))
}

describe('addressWarning', () => {
  it('flags a shared inbox', () => {
    expect(addressWarning({ contact_email_kind: 'generic' }).label).toBe('role inbox')
  })

  it('flags an address that does not match the person it greets', () => {
    /* The sharper of the two: the body opens "Hi Jane," and arrives in
       bob.smith@'s mailbox. Every other layer rejects this, so the only rows
       carrying it are legacy — precisely where no chip reads as an all-clear. */
    const w = addressWarning({ contact_email_kind: 'named_unmatched', contact_name: 'Jane Doe' })
    expect(w.label).toMatch(/doesn't match/i)
    expect(w.title).toContain('Jane Doe')
  })

  it('flags a bounced address above everything else', () => {
    /* Deleting this branch left all 64 frontend tests green. */
    expect(addressWarning({ contact_bounced_at: '2026-07-01T09:00:00' }).label).toBe('bounced')
    expect(addressWarning({ bounced_at: '2026-07-01T09:00:00' }).label).toBe('bounced')
    // outranks the other two: a dead address is the more urgent fact
    expect(addressWarning({
      contact_bounced_at: '2026-07-01T09:00:00', contact_email_kind: 'generic',
    }).label).toBe('bounced')
  })

  it('stays quiet when the address is fine or unclassified', () => {
    expect(addressWarning({ contact_email_kind: 'personal' })).toBeNull()
    expect(addressWarning({ contact_email_kind: 'unknown' })).toBeNull()
    expect(addressWarning({})).toBeNull()
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
    expect(rowFor('ZZTEST to a shared inbox').textContent).toContain('role inbox')
    expect(rowFor('ZZTEST to a human').textContent).not.toContain('role inbox')
  })

  it('chips the draft whose address does not match the person it greets', async () => {
    /* The sharper case, and the one that had no rendered coverage: the body
       opens "Hi Jane," and goes to bob.smith@. Without a row of this shape in
       the fixture, both render sites could be reverted invisibly. */
    await open()
    const row = rowFor('ZZTEST wrong human')
    expect(row.textContent).toMatch(/doesn't match/i)
    expect(row.textContent).not.toContain('role inbox')
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
