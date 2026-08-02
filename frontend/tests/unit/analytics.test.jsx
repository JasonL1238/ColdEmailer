/* The page exists to change what you send next, so its worst failure mode is
   not a crash — it is a confident number the evidence does not support.

   The backend withholds a rate below its sample floor and sends `rate: null`.
   Every test here is about that null surviving the trip to the screen: as
   counts, not as `rate ?? 0`, and with no bar drawn beside it. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const { getSpy } = vi.hoisted(() => ({ getSpy: vi.fn() }))

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => f || 'err',
  analyticsAPI: { get: getSpy },
}))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import Analytics, { humanHours, rateLabel } from '../../src/pages/Analytics'

const seg = (over) => ({
  key: 'application', label: 'application', sent: 20, replied: 4,
  unverified: 0, enough_data: true, rate: 20.0, ...over,
})

const payload = (over) => ({
  sent: 40, replied: 8, rate: 20.0, unverified: 0, min_sample: 10, days: 90,
  time_to_reply_hours: { count: 8, median: 20, p25: 4, p75: 50, fastest: 1, slowest: 300 },
  segments: {
    email_type: [seg()], email_kind: [], seniority: [], company: [],
    written_by: [], follow_up_step: [],
  },
  headline: { email_type: { best: null, worst: null, spread: null },
    email_kind: { best: null, worst: null, spread: null } },
  ...over,
})

describe('humanHours', () => {
  it('reads as a wait, not as arithmetic', () => {
    expect(humanHours(0.5)).toBe('30 min')
    expect(humanHours(20)).toBe('20h')
    expect(humanHours(72)).toBe('3d')
  })

  it('says nothing rather than zero when there is nothing', () => {
    expect(humanHours(null)).toBe('—')
    expect(humanHours(undefined)).toBe('—')
  })
})

describe('rateLabel', () => {
  it('shows a percentage only when the sample supports one', () => {
    expect(rateLabel(seg()).text).toBe('20%')
    expect(rateLabel(seg()).muted).toBe(false)
  })

  it('falls back to raw counts, never to 0%', () => {
    /* `rate ?? 0` would render a two-send segment as a confident 0% beside a
       real one — the exact comparison the backend refused to invite. */
    const thin = rateLabel(seg({ sent: 2, replied: 1, enough_data: false, rate: null }))
    expect(thin.text).toBe('1/2')
    expect(thin.muted).toBe(true)
    expect(thin.text).not.toContain('%')
  })
})

describe('Analytics page', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getSpy.mockResolvedValue({ data: payload() })
  })

  it('draws no bar for a segment that has no rate', async () => {
    getSpy.mockResolvedValue({ data: payload({
      segments: { ...payload().segments,
        email_type: [seg({ sent: 3, replied: 2, enough_data: false, rate: null })] },
    }) })
    render(<Analytics />)
    await waitFor(() => expect(document.querySelectorAll('.seg-track').length).toBe(1))

    expect(document.querySelectorAll('.seg-fill').length).toBe(0)
    expect(document.body.textContent).toContain('2/3')
    expect(document.body.textContent).toContain('counts only')
  })

  it('draws a bar once a segment clears the floor', async () => {
    render(<Analytics />)
    await waitFor(() => expect(document.querySelectorAll('.seg-fill').length).toBe(1))
    expect(document.body.textContent).toContain('20%')
  })

  it('says why there is no verdict rather than leaving a gap', async () => {
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/No comparison is solid yet/i))
    expect(document.body.textContent).toContain('10')
  })

  it('states the verdict when two segments each cleared the floor', async () => {
    getSpy.mockResolvedValue({ data: payload({
      headline: {
        email_type: {
          best: seg({ key: 'coffee_chat', label: 'coffee_chat', rate: 50.0 }),
          worst: seg({ rate: 10.0 }),
          spread: 40.0,
        },
        email_kind: { best: null, worst: null, spread: null },
      },
    }) })
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/coffee_chat/))
    expect(document.body.textContent).toMatch(/40 points of\s+difference/)
  })

  it('withholds the headline reply rate too when the whole window is thin', async () => {
    // the backend withholds the overall rate on the same floor
    getSpy.mockResolvedValue({ data: payload({ sent: 4, replied: 2, rate: null }) })
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/too few to rate/i))
    expect(document.body.textContent).not.toContain('50%')
  })

  it('names the unverified flags it is leaving out', async () => {
    /* Otherwise the number here silently disagrees with the Emails page and
       the user cannot tell which one is wrong. */
    getSpy.mockResolvedValue({ data: payload({ unverified: 12 }) })
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/12 reply flags/))
    expect(document.body.textContent).toMatch(/older check/i)
  })

  it('says the window is empty instead of rendering zeroes everywhere', async () => {
    getSpy.mockResolvedValue({ data: payload({
      sent: 0, replied: 0,
      time_to_reply_hours: { count: 0, median: null, p25: null, p75: null, fastest: null, slowest: null },
    }) })
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Nothing sent in this window/i))
    expect(document.querySelectorAll('.seg-track').length).toBe(0)
  })

  it('refetches for a different window', async () => {
    render(<Analytics />)
    await waitFor(() => expect(getSpy).toHaveBeenCalledWith(90))
    fireEvent.click([...document.querySelectorAll('button')]
      .find((b) => b.textContent.trim() === '30 days'))
    await waitFor(() => expect(getSpy).toHaveBeenCalledWith(30))
  })

  it('says so when the endpoint is unavailable rather than showing a blank page', async () => {
    getSpy.mockRejectedValue(new Error('boom'))
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Analytics unavailable/i))
  })
})

/* Everything below exists because the first adversarial round proved the
   assertions above could not see it: bar widths were unread, so a bar could
   encode volume instead of reply rate; the headline value was unread, so it
   could render a constant. */
describe('Analytics — the numbers on screen, not just their presence', () => {
  beforeEach(() => { vi.clearAllMocks() })

  const widths = () => [...document.querySelectorAll('.seg-fill')]
    .map((el) => el.style.width)

  it('scales bars by reply rate, relative to the best one shown', async () => {
    getSpy.mockResolvedValue({ data: payload({
      segments: { ...payload().segments, email_type: [
        seg({ key: 'a', label: 'a', sent: 20, replied: 8, rate: 40.0 }),
        seg({ key: 'b', label: 'b', sent: 20, replied: 2, rate: 10.0 }),
      ] },
    }) })
    render(<Analytics />)
    await waitFor(() => expect(widths().length).toBe(2))
    expect(widths()).toEqual(['100%', '25%'])
  })

  it('does not scale to a row the card never shows', async () => {
    /* The ceiling used to come from every rated row, so a 100% segment sorted
       off the bottom squashed all eight visible bars into slivers and the card
       looked like nothing worked. */
    const many = Array.from({ length: 9 }, (_, i) => seg({
      key: `c${i}`, label: `c${i}`, sent: 20 - i, replied: 2, rate: 10.0,
    }))
    many[8] = seg({ key: 'hidden', label: 'hidden', sent: 10, replied: 10, rate: 100.0 })
    getSpy.mockResolvedValue({ data: payload({
      segments: { ...payload().segments, email_type: many },
    }) })
    render(<Analytics />)
    await waitFor(() => expect(widths().length).toBe(8))
    expect(new Set(widths())).toEqual(new Set(['100%']))
    expect(document.body.textContent).toContain('+1 more')
  })

  it('renders 0% as a real zero rather than NaN', async () => {
    getSpy.mockResolvedValue({ data: payload({
      segments: { ...payload().segments, email_type: [
        seg({ sent: 20, replied: 0, rate: 0.0 }),
      ] },
    }) })
    render(<Analytics />)
    await waitFor(() => expect(widths().length).toBe(1))
    expect(widths()[0]).toBe('0%')
    expect(document.body.textContent).not.toMatch(/NaN/)
  })

  it('shows the backend rate verbatim instead of recomputing it', async () => {
    /* JS rounds halves up and Python rounds them to even, so the tile and the
       card below it disagreed for the same rows. */
    getSpy.mockResolvedValue({ data: payload({ sent: 16, replied: 1, rate: 6.2,
      segments: { ...payload().segments, email_type: [
        seg({ sent: 16, replied: 1, rate: 6.2 }) ] } }) })
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toContain('6.2%'))
    expect(document.body.textContent).not.toContain('6.3%')
  })

  it('calls a tie a tie, instead of blaming a sample size it has', async () => {
    getSpy.mockResolvedValue({ data: payload({
      headline: {
        email_type: { best: seg({ label: 'a', rate: 20.0 }),
          worst: seg({ label: 'b', rate: 20.0 }), spread: 0.0 },
        email_kind: { best: null, worst: null, spread: null },
      },
    }) })
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/No measurable difference/i))
    expect(document.body.textContent).not.toMatch(/No comparison is solid yet/i)
  })

  it('does not say "no replies yet" when there are replies it could not time', async () => {
    getSpy.mockResolvedValue({ data: payload({ replied: 5,
      time_to_reply_hours: { count: 0, median: null, p25: null, p75: null,
        fastest: null, slowest: null, excluded: 5 } }) })
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/usable timestamp/i))
    expect(document.body.textContent).not.toMatch(/no replies yet/i)
  })

  it('says why a segment card is empty when its rows had no key', async () => {
    getSpy.mockResolvedValue({ data: payload({
      segments: { ...payload().segments, company: [] } }) })
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/could not be attributed|could be attributed/i))
  })

  it('keeps the window control and a retry when the request fails', async () => {
    getSpy.mockRejectedValueOnce(new Error('boom'))
    render(<Analytics />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Analytics unavailable/i))
    const retry = [...document.querySelectorAll('button')]
      .find((b) => /try again/i.test(b.textContent))
    expect(retry).toBeTruthy()
    // the window control survives too — returning early removed the only
    // other thing that triggers a refetch
    expect([...document.querySelectorAll('button')]
      .some((b) => b.textContent.trim() === '30 days')).toBe(true)

    getSpy.mockResolvedValue({ data: payload() })
    fireEvent.click(retry)
    await waitFor(() => expect(document.body.textContent).toMatch(/By email type/))
  })
})
