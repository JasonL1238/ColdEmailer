import { describe, it, expect, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'

vi.mock('../../src/api', () => ({
  errMessage: (_e, fallback) => fallback || 'error',
  resumesAPI: {
    list: vi.fn(() => Promise.resolve({ data: [{
      id: 'r1', label: 'ML resume', filename: 'ml.pdf',
      uploaded_at: '2026-01-01T00:00:00', has_text: true,
      text_preview: 'Machine learning experience',
    }] })),
    upload: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    fileUrl: (id) => `/api/resumes/${id}/file`,
    downloadUrl: (id) => `/api/resumes/${id}/file?download=true`,
  },
}))

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn(), loading: vi.fn() },
}))

import Resumes from '../../src/pages/Resumes'

describe('Resume PDF preview', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('opens an inline PDF viewer and keeps download explicit', async () => {
    render(<Resumes />)
    await waitFor(() => expect(screen.getByText('ML resume')).toBeTruthy())

    fireEvent.click(screen.getByRole('button', { name: 'Preview ML resume' }))

    const frame = screen.getByTitle('ML resume PDF preview')
    expect(frame.getAttribute('src')).toBe('/api/resumes/r1/file')
    expect(screen.getByRole('button', { name: 'Download' })).toBeTruthy()
  })
})
