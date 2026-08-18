import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'

const companies = [
  { id: 1, name: 'Alpha Corp', scrape_status: 'pending', contacts: [] },
  { id: 2, name: 'Beta Corp', scrape_status: 'pending', contacts: [] },
]

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => f || 'err',
  companiesAPI: {
    list: vi.fn(() => Promise.resolve({ data: companies })),
    get: vi.fn((id) => Promise.resolve({ data: companies.find((c) => c.id === id) })),
    enrich: vi.fn(() => Promise.resolve({ data: {} })),
    delete: vi.fn(() => Promise.resolve({})),
    create: vi.fn(() => Promise.resolve({})),
  },
  contactsAPI: {
    list: vi.fn(() => Promise.resolve({ data: [] })),
    delete: vi.fn(() => Promise.resolve({})),
    bulkDelete: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

vi.mock('../../src/App', async () => (await import('../_mocks')).appMock())
vi.mock('react-hot-toast', async () => (await import('../_mocks')).toastMock())
vi.mock('../../src/pages/ComposeModal', () => ({ default: () => null }))

import DatabasePage from '../../src/pages/DatabasePage'
import { companiesAPI } from '../../src/api'

describe('CompanyDrawer 20s refresh timer', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('does not re-open the drawer 20s after Research when the drawer was closed', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    render(<DatabasePage />)
    await waitFor(() => expect(screen.getByText('Alpha Corp')).toBeTruthy())

    // open drawer on company A
    await act(async () => { screen.getByText('Alpha Corp').click() })
    await waitFor(() => expect(document.querySelector('.drawer')).toBeTruthy())

    // click Research
    const research = [...document.querySelectorAll('button')]
      .find((b) => /research/i.test(b.textContent) && !/failed/i.test(b.textContent))
    expect(research).toBeTruthy()
    await act(async () => { research.click() })
    expect(companiesAPI.enrich).toHaveBeenCalledWith(1, 'full')

    const getCallsBeforeClose = companiesAPI.get.mock.calls.length

    // close the drawer with X
    const close = document.querySelector('.drawer button[aria-label="Close"]')
    await act(async () => { close.click() })
    await waitFor(() => expect(document.querySelector('.drawer')).toBeNull())

    // wait 20+ seconds
    await act(async () => { vi.advanceTimersByTime(30000); await Promise.resolve() })
    await act(async () => { await Promise.resolve() })

    console.log('get calls before close:', getCallsBeforeClose,
      'after 30s:', companiesAPI.get.mock.calls.length,
      'drawer present:', !!document.querySelector('.drawer'))

    expect(document.querySelector('.drawer')).toBeNull()
    expect(companiesAPI.get.mock.calls.length).toBe(getCallsBeforeClose)
    vi.useRealTimers()
  })

  it('does not swap drawer B back to company A 20s later', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    render(<DatabasePage />)
    await waitFor(() => expect(screen.getByText('Alpha Corp')).toBeTruthy())

    await act(async () => { screen.getByText('Alpha Corp').click() })
    await waitFor(() => expect(document.querySelector('.drawer')).toBeTruthy())
    const research = [...document.querySelectorAll('.drawer button')]
      .find((b) => /research/i.test(b.textContent))
    await act(async () => { research.click() })

    // close A, open B
    await act(async () => { document.querySelector('.drawer button[aria-label="Close"]').click() })
    await waitFor(() => expect(document.querySelector('.drawer')).toBeNull())
    await act(async () => { screen.getByText('Beta Corp').click() })
    await waitFor(() => expect(document.querySelector('.drawer')).toBeTruthy())

    const titleOf = () => document.querySelector('.drawer-head').textContent
    expect(titleOf()).toContain('Beta Corp')

    await act(async () => { vi.advanceTimersByTime(30000); await Promise.resolve() })
    await act(async () => { await Promise.resolve() })

    console.log('drawer title after 30s:', titleOf())
    expect(titleOf()).toContain('Beta Corp')
    expect(titleOf()).not.toContain('Alpha Corp')
    vi.useRealTimers()
  })
})
