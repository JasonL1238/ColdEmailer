import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, act } from '@testing-library/react'

const companies = [
  {
    id: 'zz-co', name: 'ZZTEST Cascade Co', scrape_status: 'pending',
    contact_count: 1, sent_count: 1, reply_count: 1, contacts: [], emails: [],
  },
]

const conflict = {
  response: {
    status: 409,
    data: {
      detail: 'ZZTEST Cascade Co has 1 sent email(s) and 1 reply. Deleting removes '
        + 'that record, which is what stops the same person being emailed twice — '
        + 'archive the contacts instead, or confirm to delete anyway.',
    },
  },
}

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => e?.response?.data?.detail || f || 'err',
  companiesAPI: {
    list: vi.fn(() => Promise.resolve({ data: companies })),
    get: vi.fn((id) => Promise.resolve({ data: companies.find((c) => c.id === id) })),
    enrich: vi.fn(() => Promise.resolve({ data: {} })),
    // Refuses unless the caller explicitly forces, the way the backend does.
    delete: vi.fn((id, force = false) => (force
      ? Promise.resolve({})
      : Promise.reject(conflict))),
    create: vi.fn(() => Promise.resolve({})),
  },
  contactsAPI: {
    list: vi.fn(() => Promise.resolve({ data: [] })),
    delete: vi.fn(() => Promise.resolve({})),
    bulkDelete: vi.fn(() => Promise.resolve({ data: {} })),
  },
}))

vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: vi.fn(), settings: {} }) }))
vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn(), loading: vi.fn(), dismiss: vi.fn() },
}))
vi.mock('../../src/pages/ComposeModal', () => ({ default: () => null }))

import DatabasePage from '../../src/pages/DatabasePage'
import { companiesAPI } from '../../src/api'
import toast from 'react-hot-toast'

async function openDrawerAndDelete() {
  render(<DatabasePage />)
  await waitFor(() => expect(screen.getByText('ZZTEST Cascade Co')).toBeTruthy())
  await act(async () => { screen.getByText('ZZTEST Cascade Co').click() })
  await waitFor(() => expect(document.querySelector('.drawer')).toBeTruthy())
  const trash = [...document.querySelectorAll('.drawer button')]
    .find((b) => /delete company/i.test(b.getAttribute('title') || ''))
  expect(trash).toBeTruthy()
  await act(async () => { trash.click() })
}

describe('CompanyDrawer delete', () => {
  let confirmSpy

  beforeEach(() => { vi.clearAllMocks() })
  afterEach(() => { confirmSpy?.mockRestore() })

  it('keeps the company when the cascade would erase delivered emails', async () => {
    // Only the first confirm ("Delete X and all its contacts + emails?") is
    // accepted; the second — the one naming the sent history — is declined.
    let calls = 0
    confirmSpy = vi.spyOn(window, 'confirm').mockImplementation(() => (++calls === 1))

    await openDrawerAndDelete()

    expect(companiesAPI.delete).toHaveBeenCalledTimes(1)
    expect(companiesAPI.delete).toHaveBeenCalledWith('zz-co')
    // the refusal was surfaced as a second confirm, not swallowed
    expect(calls).toBe(2)
    expect(confirmSpy.mock.calls[1][0]).toContain('1 sent email')
    expect(confirmSpy.mock.calls[1][0]).toContain('archive')
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('deletes once the user confirms knowing the sent count', async () => {
    confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)

    await openDrawerAndDelete()

    expect(companiesAPI.delete).toHaveBeenCalledTimes(2)
    expect(companiesAPI.delete).toHaveBeenLastCalledWith('zz-co', true)
    await waitFor(() => expect(toast.success).toHaveBeenCalled())
  })
})
