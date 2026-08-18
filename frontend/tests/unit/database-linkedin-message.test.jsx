import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

const contact = {
  id: 'ct-1',
  name: 'Jane Doe',
  email: '',
  linkedin_url: 'https://www.linkedin.com/in/jane-doe',
  company_name: 'Acme',
  role: 'CTO',
  affinity: 'University of Pennsylvania, Shared: Stripe',
  source_url: 'https://acme.com/team',
  status: 'new',
}

vi.mock('../../src/api', () => ({
  errMessage: (_e, fallback) => fallback || 'error',
  companiesAPI: {
    list: vi.fn(() => Promise.resolve({ data: [] })),
  },
  contactsAPI: {
    list: vi.fn(() => Promise.resolve({ data: [contact] })),
    linkedinDraft: vi.fn(() => Promise.resolve({
      data: {
        message: 'Hi Jane — would you be open to a brief chat?',
        manual_send_required: true,
      },
    })),
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
import { contactsAPI } from '../../src/api'

describe('LinkedIn message handoff', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('drafts for a LinkedIn-only lead and keeps sending manual', async () => {
    render(<DatabasePage />)
    const contactsTab = await screen.findByRole('button', { name: /Contacts\s+1/ })
    fireEvent.click(contactsTab)
    await waitFor(() => expect(screen.getByText('Jane Doe')).toBeTruthy())

    expect(screen.getByTitle('No email address found').disabled).toBe(true)
    fireEvent.click(screen.getByTitle('Draft LinkedIn message'))

    await waitFor(() => expect(contactsAPI.linkedinDraft).toHaveBeenCalledWith('ct-1', null))
    expect(screen.getByDisplayValue('Hi Jane — would you be open to a brief chat?')).toBeTruthy()
    expect(screen.getByText(/You review it and click Send in LinkedIn/)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Open profile to send' })).toBeTruthy()
  })
})
