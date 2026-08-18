import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

vi.mock('../../src/api', async () => (await import('../_mocks')).composeModalApi())

vi.mock('../../src/App', async () => (await import('../_mocks')).appMock())
vi.mock('react-hot-toast', async () => (await import('../_mocks')).toastMock())

import ComposeModal from '../../src/pages/ComposeModal'
import { emailsAPI } from '../../src/api'

async function open() {
  render(<ComposeModal contactIds={['c1', 'c2']} onClose={vi.fn()} onDone={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('ZZTEST resume (default)')).toBeTruthy())
}

describe('ComposeModal', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('sends the default resume for application emails', async () => {
    await open()
    fireEvent.click(screen.getByRole('button', { name: /Generate 2 emails/i }))
    await waitFor(() => expect(emailsAPI.generate).toHaveBeenCalled())
    expect(emailsAPI.generate.mock.calls[0][0].resume_id).toBe('r1')
  })

  it('never attaches a resume to a sales email — the body says nothing is attached', async () => {
    await open()
    fireEvent.click(screen.getByText('Sales / Pitch'))
    fireEvent.click(screen.getByRole('button', { name: /Generate 2 emails/i }))
    await waitFor(() => expect(emailsAPI.generate).toHaveBeenCalled())
    const payload = emailsAPI.generate.mock.calls[0][0]
    expect(payload.email_type).toBe('sales')
    expect(payload.resume_id).toBe(null)
  })

  it('skips people already emailed unless the user opts in', async () => {
    await open()
    // Off by default: a second first-contact email reads as spam.
    fireEvent.click(screen.getByRole('button', { name: /Generate 2 emails/i }))
    await waitFor(() => expect(emailsAPI.generate).toHaveBeenCalled())
    expect(emailsAPI.generate.mock.calls[0][0].allow_recontact).toBe(false)
  })

  it('passes the re-contact override when the user ticks it', async () => {
    await open()
    fireEvent.click(screen.getByLabelText(/already emailed/i))
    fireEvent.click(screen.getByRole('button', { name: /Generate 2 emails/i }))
    await waitFor(() => expect(emailsAPI.generate).toHaveBeenCalled())
    expect(emailsAPI.generate.mock.calls[0][0].allow_recontact).toBe(true)
  })

  it('will not let a custom email skip AI — the template cannot follow instructions', async () => {
    await open()
    // tick Skip AI first, then switch to custom
    const skipAi = () => screen.getByLabelText(/Skip AI/i)
    fireEvent.click(skipAi())
    fireEvent.click(screen.getByText('Custom'))

    expect(skipAi().disabled).toBe(true)
    expect(skipAi().checked).toBe(false)
    expect(screen.getByText(/plain template can't follow your instructions/i)).toBeTruthy()

    fireEvent.change(screen.getByPlaceholderText(/summer research program/i),
      { target: { value: 'Ask about their summer research program.' } })
    fireEvent.click(screen.getByRole('button', { name: /Generate 2 emails/i }))
    await waitFor(() => expect(emailsAPI.generate).toHaveBeenCalled())
    expect(emailsAPI.generate.mock.calls[0][0].use_template_only).toBe(false)
  })
})
