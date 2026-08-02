/* The board's failure mode is not a crash — it is filing someone under a
   stage that means "handled". That person is then never written to again,
   and nothing on screen ever says why.

   So the tests are about the stages the backend distinguishes surviving the
   trip: an unverified reply flag that must not read as a reply, a bounced
   address that must not sit among live prospects, and an empty column that
   has to say it is empty rather than disappear. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'

const { getSpy, navSpy } = vi.hoisted(() => ({ getSpy: vi.fn(), navSpy: vi.fn() }))

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => f || 'err',
  pipelineAPI: { get: getSpy },
}))
vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: navSpy }) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import Pipeline from '../../src/pages/Pipeline'

const card = (over = {}) => ({
  id: 'c1', name: 'Dana Lee', email: 'dana@x.com', role: 'Eng lead',
  company_id: 'co1', company_name: 'Acme', stage: 'ready',
  last_sent_at: null, drafts: 0, sent: 0, queued: 0, unconfirmed: 0,
  reply_unverified: false, blocked_draft: false, follow_ups_sent: 0, ...over,
})

const STAGE_META = [
  ['ready', 'Ready to write'], ['no_address', 'No address'],
  ['drafted', 'Drafted'], ['awaiting', 'Awaiting reply'],
  ['replied', 'Replied'], ['bounced', 'Bounced'], ['archived', 'Archived'],
]

const payload = (cardsByStage = {}, over = {}) => ({
  stages: STAGE_META.map(([key, label]) => ({
    key, label, hint: `${label} hint`,
    cards: cardsByStage[key] || [],
    total: (cardsByStage[key] || []).length,
    hidden: 0,
  })),
  total: Object.values(cardsByStage).flat().length,
  status_drift: 0, waiting_on_you: 0, waiting_on_them: 0, queued: 0,
  ...over,
})

const columnOf = (name) => {
  const el = [...document.querySelectorAll('.board-card')]
    .find((c) => c.textContent.includes(name))
  return el?.closest('.board-col')?.querySelector('b')?.textContent
}

describe('Pipeline board', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getSpy.mockResolvedValue({ data: payload({ ready: [card()] }, { total: 1, waiting_on_you: 1 }) })
  })

  it('shows every stage, including the ones with nobody in them', async () => {
    /* An empty column is information — "nothing is waiting on you". Hiding it
       makes the user count the columns that are there to work out what is
       missing. */
    render(<Pipeline />)
    await waitFor(() => expect(document.querySelectorAll('.board-col').length).toBe(7))
    for (const [, label] of STAGE_META) {
      expect(document.body.textContent).toContain(label)
    }
    expect(document.body.textContent).toMatch(/Nobody here/)
  })

  it('puts a contact in the column the backend assigned', async () => {
    getSpy.mockResolvedValue({ data: payload({
      awaiting: [card({ id: 'a', name: 'Awaiting Person', last_sent_at: '2026-01-01T00:00:00' })],
      replied: [card({ id: 'r', name: 'Replied Person' })],
      bounced: [card({ id: 'b', name: 'Bounced Person' })],
    }, { total: 3, waiting_on_them: 1 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.querySelectorAll('.board-card').length).toBe(3))
    expect(columnOf('Awaiting Person')).toBe('Awaiting reply')
    expect(columnOf('Replied Person')).toBe('Replied')
    expect(columnOf('Bounced Person')).toBe('Bounced')
  })

  it('says a reply flag is unverified rather than letting it read as a reply', async () => {
    getSpy.mockResolvedValue({ data: payload({
      awaiting: [card({ name: 'Maybe Replied', reply_unverified: true })],
    }, { total: 1, waiting_on_them: 1 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toContain('reply unverified'))
    // and it stays in Awaiting — the chip explains the column, it does not move it
    expect(columnOf('Maybe Replied')).toBe('Awaiting reply')
  })

  it('says when a blocked contact has finished writing attached', async () => {
    /* Otherwise "No address" reads as "nothing has been done here", when in
       fact there is a draft with nowhere to send it. */
    getSpy.mockResolvedValue({ data: payload({
      no_address: [card({ name: 'No Inbox', email: '', blocked_draft: true })],
    }, { total: 1 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toContain('draft, no address'))
  })

  it('leads with the two numbers the board exists to compare', async () => {
    getSpy.mockResolvedValue({ data: payload(
      { ready: [card()], awaiting: [card({ id: 'x' })] },
      { total: 2, waiting_on_you: 5, waiting_on_them: 12 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Waiting on you/))
    const tallies = [...document.querySelectorAll('.card-pad')]
      .map((c) => c.textContent)
    expect(tallies.some((t) => /Waiting on you5/.test(t))).toBe(true)
    expect(tallies.some((t) => /Waiting on them12/.test(t))).toBe(true)
  })

  it('shows each column’s own count, not just the cards it drew', async () => {
    getSpy.mockResolvedValue({ data: payload({ ready: [card()] }, { total: 300 }) })
    const data = payload({ ready: [card()] }, { total: 300 })
    data.stages[0].total = 300
    data.stages[0].hidden = 299
    getSpy.mockResolvedValue({ data })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toContain('+299 more'))
    // the chip reports the real size of the column, not the page of it shown
    expect(document.querySelector('.board-col .chip').textContent).toBe('300')
  })

  it('explains the drift instead of quietly disagreeing with the Database page', async () => {
    getSpy.mockResolvedValue({ data: payload({ ready: [card()] },
      { total: 1, status_drift: 4 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toMatch(/4 contacts/))
    expect(document.body.textContent).toMatch(/never reset when a draft is deleted/i)
    expect(document.body.textContent).toMatch(/nothing was changed/i)
  })

  it('says nothing about drift when there is none', async () => {
    render(<Pipeline />)
    await waitFor(() => expect(document.querySelectorAll('.board-col').length).toBe(7))
    expect(document.body.textContent).not.toMatch(/never reset when a draft/i)
  })

  it('points an empty database at where contacts come from', async () => {
    getSpy.mockResolvedValue({ data: payload({}, { total: 0 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toMatch(/No contacts yet/))
    expect(document.querySelectorAll('.board-col').length).toBe(0)

    fireEvent.click(screen.getByText('Go to Discover'))
    expect(navSpy).toHaveBeenCalledWith('discover')
  })

  it('reports a failure and offers a retry rather than a blank page', async () => {
    getSpy.mockRejectedValueOnce(new Error('boom'))
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Pipeline unavailable/i))

    getSpy.mockResolvedValue({ data: payload({ ready: [card()] }, { total: 1 }) })
    fireEvent.click(screen.getByText('Try again'))
    await waitFor(() => expect(document.querySelectorAll('.board-col').length).toBe(7))
  })

  it('renders a card as text, with no message content on it', async () => {
    /* A board is a list of people, not of correspondence — nothing here
       should be putting a stranger's writing on screen. */
    getSpy.mockResolvedValue({ data: payload({
      ready: [card({ name: '<img src=x onerror="alert(1)">' })],
    }, { total: 1 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.querySelectorAll('.board-card').length).toBe(1))
    expect(document.querySelector('.board-card img')).toBeNull()
    expect(document.body.textContent).toContain('<img src=x')
  })

  it('shows follow-up effort where it has been spent', async () => {
    getSpy.mockResolvedValue({ data: payload({
      awaiting: [card({ name: 'Chased', follow_ups_sent: 2,
        last_sent_at: '2026-01-01T00:00:00' })],
    }, { total: 1 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toContain('2 follow-ups'))
  })

  it('shows an unsent follow-up as work, not as a completed send', async () => {
    /* This is the ordinary output of the cadence — a draft written for
       somebody already emailed. Folding it into "you have written to this
       person" made the whole follow-up backlog invisible. */
    getSpy.mockResolvedValue({ data: payload({
      drafted: [card({ name: 'Owed', drafts: 1, sent: 2,
        last_sent_at: '2026-01-01T00:00:00' })],
    }, { total: 1, waiting_on_you: 1 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toContain('1 unsent'))
    expect(columnOf('Owed')).toBe('Drafted')
  })

  it('marks a queued send as needing nothing from the user', async () => {
    getSpy.mockResolvedValue({ data: payload({
      drafted: [card({ name: 'Armed', queued: 1 })],
    }, { total: 1, waiting_on_you: 0, queued: 1 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toContain('1 queued'))
    // and the tally says so out loud, rather than letting it vanish from both
    expect(document.body.textContent).toMatch(/Queued to send/)
    expect(document.body.textContent).toMatch(/no action needed/)
  })

  it('warns that an in-flight send is not a safe retry', async () => {
    getSpy.mockResolvedValue({ data: payload({
      awaiting: [card({ name: 'InFlight', unconfirmed: 1 })],
    }, { total: 1 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toContain('send unconfirmed'))
  })

  it('shows the contact total when nothing is queued', async () => {
    getSpy.mockResolvedValue({ data: payload({ ready: [card()] },
      { total: 42, queued: 0 }) })
    render(<Pipeline />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Contacts/))
    expect(document.body.textContent).toContain('42')
  })
})
