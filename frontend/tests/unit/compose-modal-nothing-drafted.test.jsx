/* A generation run in which every contact was skipped is not a success.
   It used to finish as "Drafts ready" with a 100%-full bar reading "3/3 done"
   and "Review drafts" as the primary action — which navigates to Drafts where
   nothing new exists. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react'

const doneJob = (generated, skipped) => ({
  id: 'job1', status: 'done', stage: 'done',
  progress_current: generated, progress_total: generated + skipped,
  result: {
    generated, total: generated + skipped, email_ids: [],
    skipped_count: skipped,
    skipped: Array.from({ length: skipped }, (_, i) => ({
      contact_id: `c${i}`, name: `ZZTEST Person ${i}`,
      reason: 'archived — unarchive them first if you want to reach out',
    })),
  },
})

let jobResponse = doneJob(0, 3)

vi.mock('../../src/api', async () => (await import('../_mocks')).composeModalApi({
  jobsAPI: { get: vi.fn(() => Promise.resolve({ data: jobResponse })) },
}))

const navigate = vi.fn()
vi.mock('../../src/App', () => ({ useApp: () => ({ navigate, settings: {} }) }))
const toastCalls = { plain: [], success: [], error: [] }
vi.mock('react-hot-toast', () => ({
  default: Object.assign((m) => toastCalls.plain.push(m), {
    success: (m) => toastCalls.success.push(m),
    error: (m) => toastCalls.error.push(m),
  }),
}))

import ComposeModal from '../../src/pages/ComposeModal'

const modal = () => document.querySelector('.modal')

async function runGeneration() {
  render(<ComposeModal contactIds={['c1', 'c2', 'c3']} onClose={vi.fn()} onDone={vi.fn()} />)
  await waitFor(() => expect(screen.getByText('ZZTEST resume (default)')).toBeTruthy())
  fireEvent.click(screen.getByRole('button', { name: /Generate 3 emails/i }))
  await act(async () => { await vi.advanceTimersByTimeAsync(1600) })
  await waitFor(() => expect(modal().textContent).toMatch(/drafted/))
}

describe('ComposeModal — a run that drafted nothing', () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    toastCalls.plain = []; toastCalls.success = []; toastCalls.error = []
    navigate.mockClear()
  })
  afterEach(() => { vi.useRealTimers() })

  it('does not claim drafts are ready', async () => {
    jobResponse = doneJob(0, 3)
    await runGeneration()

    expect(modal().textContent).toContain('Nothing drafted')
    expect(modal().textContent).not.toContain('Drafts ready')
  })

  it('never labels skipped contacts "done"', async () => {
    jobResponse = doneJob(0, 3)
    await runGeneration()

    expect(modal().textContent).toContain('0 of 3 drafted')
    expect(modal().textContent).toContain('3 skipped')
    expect(modal().textContent).not.toContain('3/3 done')
  })

  it('offers no "Review drafts" when there is nothing to review', async () => {
    jobResponse = doneJob(0, 3)
    await runGeneration()

    expect([...modal().querySelectorAll('button')]
      .some((b) => /review drafts/i.test(b.textContent))).toBe(false)
    expect(toastCalls.success).toHaveLength(0)
  })

  it('still reviews drafts when some were actually written', async () => {
    jobResponse = doneJob(2, 1)
    await runGeneration()

    expect(modal().textContent).toContain('Drafts ready')
    expect(modal().textContent).toContain('2 of 3 drafted')
    expect([...modal().querySelectorAll('button')]
      .some((b) => /review drafts/i.test(b.textContent))).toBe(true)
    expect(toastCalls.success[0]).toContain('2 emails drafted')
  })
})
