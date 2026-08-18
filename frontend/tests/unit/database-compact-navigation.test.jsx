import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const companies = Array.from({ length: 32 }, (_, index) => ({
  id: `co-${index + 1}`,
  name: `Company ${String(index + 1).padStart(2, '0')}`,
  domain: `company${index + 1}.com`,
  industry: index % 2 ? 'Robotics' : 'AI',
  scrape_status: index % 2 ? 'pending' : 'scraped',
  contact_count: index % 3,
  sent_count: 0,
  reply_count: 0,
  unverified_reply_count: 0,
  created_at: new Date(Date.UTC(2026, 6, 31 - index)).toISOString(),
  contacts: [],
  emails: [],
}))

const contacts = Array.from({ length: 32 }, (_, index) => ({
  id: `ct-${index + 1}`,
  name: `Person ${String(index + 1).padStart(2, '0')}`,
  email: `person${index + 1}@example.com`,
  company_name: companies[index].name,
  role: index % 2 ? 'CEO' : 'Engineer',
  status: index === 31 ? 'archived' : 'new',
  email_count: index % 3,
  verified_reply_count: 0,
  unverified_reply_count: 0,
  created_at: companies[index].created_at,
}))

vi.mock('../../src/api', () => ({
  errMessage: (_e, fallback) => fallback || 'error',
  companiesAPI: {
    list: vi.fn(() => Promise.resolve({ data: companies })),
    get: vi.fn((id) => Promise.resolve({
      data: companies.find((company) => company.id === id),
    })),
    enrich: vi.fn(),
    delete: vi.fn(),
    create: vi.fn(),
  },
  contactsAPI: {
    list: vi.fn(() => Promise.resolve({ data: contacts })),
    update: vi.fn(),
    delete: vi.fn(),
    bulkDelete: vi.fn(),
    exportUrl: vi.fn(() => '/contacts.csv'),
  },
}))

vi.mock('../../src/App', async () => (await import('../_mocks')).appMock())
vi.mock('react-hot-toast', async () => (await import('../_mocks')).toastMock())
vi.mock('../../src/pages/ComposeModal', () => ({ default: () => null }))

import DatabasePage from '../../src/pages/DatabasePage'

describe('Compact database navigation', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('shows 15 rows at a time and resets to page one when searching', async () => {
    render(<DatabasePage />)
    await waitFor(() => expect(screen.getByText('Company 01')).toBeTruthy())

    expect(document.querySelectorAll('.database-table tbody tr')).toHaveLength(15)
    expect(screen.getByText('1–15 of 32')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.getByText('Company 16')).toBeTruthy()
    expect(screen.getByText('16–30 of 32')).toBeTruthy()

    fireEvent.change(screen.getByPlaceholderText('Search companies…'), {
      target: { value: 'Company 03' },
    })
    expect(screen.getByText('Company 03')).toBeTruthy()
    expect(screen.getByText('1–1 of 1')).toBeTruthy()
    expect(screen.getByText('Page 1 of 1')).toBeTruthy()
  })

  it('filters companies without expanding the page', async () => {
    render(<DatabasePage />)
    await waitFor(() => expect(screen.getByText('Company 01')).toBeTruthy())

    fireEvent.change(screen.getByLabelText('Filter companies'), {
      target: { value: 'needs_research' },
    })

    expect(screen.getByText('1–15 of 16')).toBeTruthy()
    expect(document.querySelectorAll('.database-table tbody tr')).toHaveLength(15)
    expect(document.querySelector('.database-table tbody').textContent)
      .not.toContain('Researched')
  })

  it('selects only the visible contact page and clears selection when paging', async () => {
    render(<DatabasePage />)
    await waitFor(() => expect(screen.getByText('Company 01')).toBeTruthy())
    fireEvent.click(screen.getByRole('button', { name: /Contacts\s+32/ }))
    await waitFor(() => expect(screen.getByText('Person 01')).toBeTruthy())

    fireEvent.click(document.querySelector('.database-table thead input[type="checkbox"]'))
    expect(screen.getByText('15 selected')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.queryByText('15 selected')).toBeNull()
    expect(screen.getByText('16–30 of 32')).toBeTruthy()
  })
})
