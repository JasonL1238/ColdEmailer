/* The page exists to answer "which batch worked", so its worst failure is a
   confident number the evidence does not support — and a campaign is always
   small when it is new, which makes this the most tempting place in the app
   to render one reply out of three as 33%.

   The second failure is quieter: letting the campaign totals read as the
   whole database, when everything from before campaigns existed is
   permanently unassigned. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'

const { listSpy, updateSpy, navSpy } = vi.hoisted(() => ({
  listSpy: vi.fn(), updateSpy: vi.fn(), navSpy: vi.fn(),
}))

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => f || 'err',
  campaignsAPI: { list: listSpy, update: updateSpy },
}))
vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: navSpy }) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import Campaigns, { rateLabel } from '../../src/pages/Campaigns'

const campaign = (over = {}) => ({
  id: 'c1', name: 'fintech NYC', query: 'seed fintech in NYC',
  created_at: '2026-01-01T09:00:00', archived_at: null, notes: '',
  companies: 5, contacts: 9, drafts: 0, sent: 20, replied: 4,
  bounced: 0, unverified: 0, last_sent_at: '2026-02-01T09:00:00',
  enough_data: true, rate: 20.0, ...over,
})

const payload = (over = {}) => ({
  campaigns: [campaign()],
  headline: { best: null, worst: null, spread: null },
  min_sample: 10,
  totals: { campaigns: 1, active: 1, sent: 20, replied: 4 },
  unassigned: { companies: 0, contacts: 0, sent: 0, replied: 0,
    enough_data: false, rate: null },
  ...over,
})

describe('rateLabel', () => {
  it('shows a percentage only when the sample supports one', () => {
    expect(rateLabel(campaign()).text).toBe('20%')
    expect(rateLabel(campaign()).muted).toBe(false)
  })

  it('falls back to counts, never to 0%', () => {
    const thin = rateLabel(campaign({ sent: 3, replied: 1, enough_data: false, rate: null }))
    expect(thin.text).toBe('1/3')
    expect(thin.muted).toBe(true)
    expect(thin.text).not.toContain('%')
  })
})

describe('Campaigns page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listSpy.mockResolvedValue({ data: payload() })
    updateSpy.mockResolvedValue({ data: {} })
  })

  it('shows a campaign with its counts and its rate', async () => {
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toContain('fintech NYC'))
    expect(document.body.textContent).toContain('20%')
    expect(document.body.textContent).toContain('9 contacts')
    expect(document.body.textContent).toMatch(/searched .seed fintech in NYC./)
  })

  it('states counts rather than a rate below the floor', async () => {
    listSpy.mockResolvedValue({ data: payload({
      campaigns: [campaign({ sent: 3, replied: 1, enough_data: false, rate: null })],
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toContain('1/3'))
    expect(document.body.textContent).toContain('counts only')
    expect(document.body.textContent).not.toMatch(/33/)
  })

  it('shows a bounce count, since it is the reason a rate looks bad', async () => {
    listSpy.mockResolvedValue({ data: payload({
      campaigns: [campaign({ bounced: 3 })],
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toMatch(/3 bounced/))
  })

  it('says nothing about bounces when there are none', async () => {
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toContain('fintech NYC'))
    expect(document.body.textContent).not.toMatch(/bounced/)
  })

  it('names the unverified flags it is leaving out', async () => {
    listSpy.mockResolvedValue({ data: payload({
      campaigns: [campaign({ unverified: 6 })],
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toMatch(/6 unverified/))
    // and they are not folded into the rate
    expect(document.body.textContent).toContain('20%')
  })

  it('says why there is no verdict rather than leaving a gap', async () => {
    render(<Campaigns />)
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/No comparison is solid yet/i))
    expect(document.body.textContent).toContain('10')
  })

  it('states the verdict when two campaigns each cleared the floor', async () => {
    listSpy.mockResolvedValue({ data: payload({
      headline: {
        best: campaign({ id: 'a', name: 'coffee chats', rate: 50.0 }),
        worst: campaign({ id: 'b', name: 'cold apps', rate: 10.0 }),
        spread: 40.0,
      },
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toMatch(/coffee chats/))
    expect(document.body.textContent).toMatch(/40 points/)
  })

  it('calls a tie a tie instead of blaming the sample size', async () => {
    listSpy.mockResolvedValue({ data: payload({
      headline: {
        best: campaign({ id: 'a', name: 'A', rate: 20.0 }),
        worst: campaign({ id: 'b', name: 'B', rate: 20.0 }),
        spread: 0.0,
      },
    }) })
    render(<Campaigns />)
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/No measurable difference/i))
    expect(document.body.textContent).not.toMatch(/No comparison is solid yet/i)
  })

  it('shows what was never in a campaign, and says why it stays that way', async () => {
    /* Otherwise the campaign totals read as the whole database and a user with
       300 pre-campaign contacts sees "9 contacts". */
    listSpy.mockResolvedValue({ data: payload({
      unassigned: { companies: 120, contacts: 300, sent: 40, replied: 6,
        enough_data: true, rate: 15.0 },
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Not in a campaign/))
    expect(document.body.textContent).toContain('300 contacts')
    expect(document.body.textContent).toContain('15% replied')
    expect(document.body.textContent).toMatch(/would invent the thing this page reports/i)
  })

  it('holds the unassigned pile to the same floor', async () => {
    listSpy.mockResolvedValue({ data: payload({
      unassigned: { companies: 1, contacts: 2, sent: 4, replied: 2,
        enough_data: false, rate: null },
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Not in a campaign/))
    expect(document.body.textContent).toContain('2/4 replied')
    expect(document.body.textContent).not.toContain('50%')
  })

  it('shows the block when only companies are unassigned', async () => {
    /* Discovery routinely creates companies whose scrape finds no addresses,
       so a database can hold forty unassigned companies and no unassigned
       contacts — and the block explaining them never appeared. */
    listSpy.mockResolvedValue({ data: payload({
      unassigned: { companies: 40, contacts: 0, sent: 0, replied: 0,
        enough_data: false, rate: null },
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Not in a campaign/))
    expect(document.body.textContent).toContain('40 companies')
  })

  it('does not claim unassigned rows all predate campaigns', async () => {
    /* Only a Discover search starts a campaign, so anything added by hand,
       imported, or found by Deep Dive lands here today — not in the past. */
    listSpy.mockResolvedValue({ data: payload({
      unassigned: { companies: 2, contacts: 3, sent: 1, replied: 0,
        enough_data: false, rate: null },
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Not in a campaign/))
    expect(document.body.textContent).toMatch(/added by hand/i)
    expect(document.body.textContent).toMatch(/Deep Dive/)
    expect(document.body.textContent).not.toMatch(/^These predate campaigns\./m)
  })

  it('says nothing about unassigned rows when there are none', async () => {
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toContain('fintech NYC'))
    expect(document.body.textContent).not.toMatch(/Not in a campaign/)
  })

  it('renames a campaign', async () => {
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toContain('fintech NYC'))
    fireEvent.click(screen.getByTitle('Rename'))
    const input = document.querySelector('.input')
    fireEvent.change(input, { target: { value: 'Q1 fintech push' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    await waitFor(() =>
      expect(updateSpy).toHaveBeenCalledWith('c1', { name: 'Q1 fintech push' }))
  })

  it('does not save an empty rename', async () => {
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toContain('fintech NYC'))
    fireEvent.click(screen.getByTitle('Rename'))
    const input = document.querySelector('.input')
    fireEvent.change(input, { target: { value: '   ' } })
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(updateSpy).not.toHaveBeenCalled()
  })

  it('archives and unarchives without offering a delete', async () => {
    /* The rows pointing at a campaign are real outreach history. There is no
       delete on the backend and there must be no button for one here. */
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toContain('fintech NYC'))
    expect([...document.querySelectorAll('button')]
      .some((b) => /delete/i.test(b.textContent + (b.title || '')))).toBe(false)

    fireEvent.click(screen.getByTitle(/^Archive/))
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith('c1', { archived: true }))
  })

  it('dims an archived campaign but keeps its numbers on screen', async () => {
    listSpy.mockResolvedValue({ data: payload({
      campaigns: [campaign({ archived_at: '2026-03-01T09:00:00' })],
    }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toContain('archived'))
    expect(document.querySelector('.campaign-archived')).toBeTruthy()
    expect(document.body.textContent).toContain('20%')
  })

  it('points an empty page at where campaigns come from', async () => {
    listSpy.mockResolvedValue({ data: payload({ campaigns: [] }) })
    render(<Campaigns />)
    await waitFor(() => expect(document.body.textContent).toMatch(/No campaigns yet/))
    fireEvent.click(screen.getByText('Go to Discover'))
    expect(navSpy).toHaveBeenCalledWith('discover')
  })

  it('reports a failure and offers a retry', async () => {
    listSpy.mockRejectedValueOnce(new Error('boom'))
    render(<Campaigns />)
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/Campaigns unavailable/i))

    listSpy.mockResolvedValue({ data: payload() })
    fireEvent.click(screen.getByText('Try again'))
    await waitFor(() => expect(document.body.textContent).toContain('fintech NYC'))
  })
})
