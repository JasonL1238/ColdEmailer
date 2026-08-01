/* The follow-up cadence editor decides when real email goes to real people,
   so what it shows and what it stores must be the same thing. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent } from '@testing-library/react'

const { updateCadence, refreshSettings } = vi.hoisted(() => ({
  updateCadence: vi.fn((payload) => Promise.resolve({ data: payload })),
  refreshSettings: vi.fn(() => Promise.resolve()),
}))

let settings = {
  profile: { full_name: 'ZZTEST', email: 'zz@test.invalid', background: 'x' },
  profile_incomplete: [],
  llm_provider: 'gemini',
  gmail_connected: true,
  limits: { emails_per_day: 50, generations_per_day: 500 },
  follow_up_cadence: { enabled: true, steps: [7] },
}

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => e?.response?.data?.detail?.[0]?.msg || f || 'err',
  settingsAPI: { update: vi.fn(() => Promise.resolve({ data: {} })) },
  gmailAPI: { disconnect: vi.fn() },
  cadenceAPI: { update: updateCadence },
  sendWindowAPI: {
    get: vi.fn(() => Promise.resolve({ data: {
      enabled: false, timezone: '', days: [0, 1, 2, 3, 4], start_hour: 8,
      end_hour: 17, description: 'Sending is not held for business hours.',
      scheduled_count: 0,
    } })),
    update: vi.fn((p) => Promise.resolve({ data: p })),
  },
}))
vi.mock('../../src/App', () => ({ useApp: () => ({ settings, refreshSettings }) }))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

import Settings, { cadenceSummary } from '../../src/pages/Settings'

const gapInputs = () => [...document.querySelectorAll('input[type="number"]')]
const saveButton = () => [...document.querySelectorAll('button')]
  .find((b) => /save cadence/i.test(b.textContent))

async function open() {
  render(<Settings />)
  await waitFor(() => expect(gapInputs().length).toBeGreaterThan(0))
}

describe('cadenceSummary', () => {
  it('accumulates the gaps rather than listing them', () => {
    expect(cadenceSummary({ enabled: true, steps: [7, 7] }))
      .toMatch(/2 follow-ups, on day 7, 14/)
    expect(cadenceSummary({ enabled: true, steps: [3, 5, 5] }))
      .toMatch(/on day 3, 8, 13/)
  })

  it('says plainly when nothing will be drafted', () => {
    expect(cadenceSummary({ enabled: false, steps: [7] })).toMatch(/no follow-ups/i)
    expect(cadenceSummary({ enabled: true, steps: [] })).toMatch(/no follow-ups/i)
    expect(cadenceSummary(undefined)).toMatch(/no follow-ups/i)
  })
})

describe('Settings — the cadence editor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    settings = { ...settings, follow_up_cadence: { enabled: true, steps: [7] } }
  })

  it('rounds a fractional gap instead of displaying one it cannot store', async () => {
    /* A number input happily yields "7.5". The summary then promised a
       7.5-day gap and the save 422'd on List[int], leaving the editor showing
       a value that was never stored. */
    await open()
    fireEvent.change(gapInputs()[0], { target: { value: '7.5' } })

    expect(gapInputs()[0].value).toBe('8')
    expect(document.body.textContent).not.toContain('7.5')
    fireEvent.click(saveButton())
    await waitFor(() => expect(updateCadence).toHaveBeenCalledWith(
      { enabled: true, steps: [8] }))
  })

  it('clamps a gap outside the allowed range on entry, not at save', async () => {
    await open()
    fireEvent.change(gapInputs()[0], { target: { value: '900' } })
    expect(gapInputs()[0].value).toBe('90')
    fireEvent.change(gapInputs()[0], { target: { value: '0' } })
    expect(gapInputs()[0].value).toBe('1')
  })

  it('adds and removes rungs, up to the cap', async () => {
    await open()
    const add = () => [...document.querySelectorAll('button')]
      .find((b) => /add step/i.test(b.textContent))
    for (let i = 0; i < 3; i++) fireEvent.click(add())
    expect(gapInputs().length).toBe(4)
    expect(add()).toBeUndefined()          // four is the maximum

    fireEvent.click([...document.querySelectorAll('button')]
      .find((b) => /remove/i.test(b.textContent)))
    expect(gapInputs().length).toBe(3)
  })

  it('saves the switch-off as an explicit choice', async () => {
    await open()
    fireEvent.click(document.querySelector('input[type="checkbox"]'))
    fireEvent.click(saveButton())
    await waitFor(() => expect(updateCadence).toHaveBeenCalledWith(
      { enabled: false, steps: [7] }))
    expect(document.body.textContent).toMatch(/no follow-ups will be drafted/i)
  })
})
