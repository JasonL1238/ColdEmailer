/* The review screen's job is honest labeling: found vs guessed vs personal
   must be visibly different things, a guess must not be selectable as a
   sendable address, and an address that doesn't contain the person's name
   must not save without the user asserting ownership. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'

const { startSpy, listSpy, getSpy, cancelSpy, approveSpy, toastMock } = vi.hoisted(() => ({
  startSpy: vi.fn(),
  listSpy: vi.fn(),
  getSpy: vi.fn(),
  cancelSpy: vi.fn(),
  approveSpy: vi.fn(),
  toastMock: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => f || 'err',
  personFinderAPI: {
    start: startSpy, list: listSpy, get: getSpy,
    cancel: cancelSpy, approve: approveSpy,
  },
}))
vi.mock('../../src/App', () => ({ useApp: () => ({ navigate: vi.fn() }) }))
vi.mock('react-hot-toast', () => ({ default: toastMock }))

import FindPerson from '../../src/pages/FindPerson'

const doneJob = (candidateOver = {}) => ({
  id: 'j1', status: 'done', type: 'person_finder',
  payload: { name: 'Jane Doe', company_name: 'Acme' },
  created_at: new Date().toISOString(),
  result: {
    query: { name: 'Jane Doe', company_name: 'Acme' },
    company: { company_id: null, name: 'Acme', domain: 'acme.com' },
    keyless: true, timed_out: false, searches_used: 4,
    candidates: [{
      id: 'c1', name: 'Jane Doe', role: 'VP Engineering',
      linkedin_url: 'https://www.linkedin.com/in/jane-doe',
      linkedin_verified: true,
      confidence: 'strong', score: 6,
      matched_signals: [{
        signal: 'company:Acme', evidence: 'VP Engineering at Acme',
        source_url: 'https://acme.com/team',
      }],
      conflicting_signals: [], evidence: [], channels: [],
      emails: [
        { email: 'jane.doe@acme.com', origin: 'found', domain_kind: 'company',
          email_kind: 'personal', email_person_match: true, email_mx_ok: true,
          email_verified: true, source_url: 'https://acme.com/team' },
        { email: 'coolhacker99@gmail.com', origin: 'found',
          domain_kind: 'personal', email_kind: 'named_unmatched',
          email_person_match: false, email_mx_ok: true,
          email_verified: false, source_url: 'https://github.com/ch99' },
        { email: 'jdoe@acme.com', origin: 'guessed', domain_kind: 'company',
          email_kind: 'pattern_guess', email_person_match: true,
          email_mx_ok: true, email_verified: false, source_url: null },
      ],
      llm_summary: null, approved_contact_id: null,
      ...candidateOver,
    }],
  },
})

const openDoneRun = async (job = doneJob()) => {
  listSpy.mockResolvedValue({ data: [job] })
  getSpy.mockResolvedValue({ data: job })
  render(<FindPerson />)
  await waitFor(() => expect(listSpy).toHaveBeenCalled())
  fireEvent.click(await screen.findByText('Jane Doe'))
  await waitFor(() => expect(getSpy).toHaveBeenCalledWith('j1'))
  await screen.findByText(/Candidates for Jane Doe/)
}

describe('FindPerson', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listSpy.mockResolvedValue({ data: [] })
  })

  it('refuses to start without a first and last name', async () => {
    render(<FindPerson />)
    fireEvent.change(
      screen.getByPlaceholderText(/Full name/), { target: { value: 'Cher' } })
    fireEvent.click(screen.getByText('Find this person'))
    expect(toastMock.error).toHaveBeenCalled()
    expect(startSpy).not.toHaveBeenCalled()
  })

  it('sends the structured form as the start payload', async () => {
    startSpy.mockResolvedValue({ data: doneJob() })
    getSpy.mockResolvedValue({ data: doneJob() })
    render(<FindPerson />)
    fireEvent.change(
      screen.getByPlaceholderText(/Full name/),
      { target: { value: 'Jane Doe' } })
    fireEvent.change(
      screen.getByPlaceholderText(/Company \(e\.g\./),
      { target: { value: 'Acme' } })
    fireEvent.change(
      screen.getByPlaceholderText(/LinkedIn profile URL/),
      { target: { value: 'https://www.linkedin.com/in/jane-doe' } })
    fireEvent.change(
      screen.getByPlaceholderText(/School \(e\.g\./),
      { target: { value: 'Northwestern' } })
    fireEvent.change(
      screen.getByPlaceholderText(/Years there/),
      { target: { value: '2018-2022' } })
    fireEvent.click(screen.getByText('Find this person'))
    await waitFor(() => expect(startSpy).toHaveBeenCalledWith({
      name: 'Jane Doe',
      linkedin_url: 'https://www.linkedin.com/in/jane-doe',
      company_name: 'Acme', company_url: null,
      school: 'Northwestern', school_dates: '2018-2022',
      past_companies: null, role_hint: null, location: null, notes: null,
    }))
  })

  it('shows when a result is locked to the supplied profile', async () => {
    await openDoneRun(doneJob({ identity_anchor: true }))
    expect(screen.getByText('Exact profile supplied')).toBeTruthy()
  })

  it('labels found, guessed, and personal emails distinctly', async () => {
    await openDoneRun()
    expect(screen.getAllByText('Found').length).toBe(2)
    expect(screen.getByText('Guessed — note only')).toBeTruthy()
    expect(screen.getByText('Personal')).toBeTruthy()
    expect(screen.getByText('Verified')).toBeTruthy()
    expect(screen.getByText('Strong match')).toBeTruthy()
  })

  it('a guessed email cannot be selected as the sendable address', async () => {
    await openDoneRun()
    const guessedRadio = screen.getByText('jdoe@acme.com')
      .closest('label').querySelector('input[type=radio]')
    expect(guessedRadio.disabled).toBe(true)
  })

  it('an address that does not match the name needs explicit confirmation', async () => {
    await openDoneRun()
    const handleRadio = screen.getByText('coolhacker99@gmail.com')
      .closest('label').querySelector('input[type=radio]')
    expect(handleRadio.disabled).toBe(false)
    fireEvent.click(handleRadio)
    const save = screen.getByText('Save as contact').closest('button')
    expect(save.disabled).toBe(true)
    fireEvent.click(screen.getByText(/I confirmed this address/))
    expect(save.disabled).toBe(false)
    approveSpy.mockResolvedValue({ data: {
      contact: { name: 'Jane Doe' }, conflicts: [], already_existed: false,
    } })
    fireEvent.click(save)
    await waitFor(() => expect(approveSpy).toHaveBeenCalledWith('j1', {
      candidate_id: 'c1', email: 'coolhacker99@gmail.com',
      include_linkedin: true, confirm_email_ownership: true,
    }))
  })

  it('approves the default verified email without confirmation', async () => {
    await openDoneRun()
    approveSpy.mockResolvedValue({ data: {
      contact: { name: 'Jane Doe' }, conflicts: [], already_existed: false,
    } })
    fireEvent.click(screen.getByText('Save as contact'))
    await waitFor(() => expect(approveSpy).toHaveBeenCalledWith('j1', {
      candidate_id: 'c1', email: 'jane.doe@acme.com',
      include_linkedin: true, confirm_email_ownership: false,
    }))
    expect(toastMock.success).toHaveBeenCalled()
  })

  it('an approved candidate renders as saved, not re-approvable', async () => {
    await openDoneRun(doneJob({ approved_contact_id: 'ct1' }))
    expect(screen.getByText('Saved to your database')).toBeTruthy()
    expect(screen.queryByText('Save as contact')).toBeNull()
  })
})

describe('FindPerson — found sources and mailbox checks', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listSpy.mockResolvedValue({ data: [] })
  })

  const withEmails = (emails) => {
    const job = doneJob()
    job.result.candidates[0].emails = emails
    return job
  }

  it('shows where a found address was published', async () => {
    await openDoneRun(withEmails([{
      email: 'torvalds@linux-foundation.org', origin: 'found',
      domain_kind: 'other', source_kind: 'github_commit',
      attribution: 'Commit abc123 in torvalds/linux',
      email_kind: 'named_unmatched', email_person_match: false,
      email_mx_ok: true, email_verified: false,
      source_url: 'https://github.com/torvalds/linux/commit/abc123',
    }]))
    expect(screen.getByText('GitHub commit')).toBeTruthy()
    // An attributed address that lacks the name is vouched for, not suspect.
    expect(screen.getByText(/attributed by source/)).toBeTruthy()
  })

  it('an unverified guess stays unselectable', async () => {
    await openDoneRun(withEmails([{
      email: 'jane.doe@acme.com', origin: 'guessed', domain_kind: 'company',
      email_kind: 'pattern_guess', email_person_match: true,
      email_mx_ok: true, email_verified: false, smtp_status: null,
    }]))
    const radio = screen.getByText('jane.doe@acme.com')
      .closest('label').querySelector('input[type=radio]')
    expect(radio.disabled).toBe(true)
  })

  it('an inconclusive check does not unlock the guess', async () => {
    await openDoneRun(withEmails([{
      email: 'jane.doe@acme.com', origin: 'guessed', domain_kind: 'company',
      email_kind: 'pattern_guess', email_person_match: true,
      email_mx_ok: true, email_verified: false, smtp_status: 'accept_all',
    }]))
    expect(screen.getByText('Domain accepts everything')).toBeTruthy()
    const radio = screen.getByText('jane.doe@acme.com')
      .closest('label').querySelector('input[type=radio]')
    expect(radio.disabled).toBe(true)
  })

  it('a confirmed mailbox unlocks the guess but still demands ownership', async () => {
    await openDoneRun(withEmails([{
      email: 'jane.doe@acme.com', origin: 'guessed', domain_kind: 'company',
      email_kind: 'pattern_guess', email_person_match: true,
      email_mx_ok: true, email_verified: false, smtp_status: 'deliverable',
    }]))
    expect(screen.getByText('Mailbox exists')).toBeTruthy()
    const radio = screen.getByText('jane.doe@acme.com')
      .closest('label').querySelector('input[type=radio]')
    expect(radio.disabled).toBe(false)
    fireEvent.click(radio)
    const save = screen.getByText('Save as contact').closest('button')
    expect(save.disabled).toBe(true)
    fireEvent.click(screen.getByText(/not a namesake/))
    expect(save.disabled).toBe(false)
  })

  const guessWith = (corroboration) => withEmails([{
    email: 'jane.doe@acme.com', origin: 'guessed', domain_kind: 'company',
    email_kind: 'pattern_guess', email_person_match: true,
    email_mx_ok: true, email_verified: false, smtp_status: null,
    corroboration,
  }])

  it('a corpus hit under this name unlocks the guess with no mailbox check',
    async () => {
      // The common case on a home network: port 25 is blocked so smtp_status
      // is null, and corroboration is the only evidence there is.
      await openDoneRun(guessWith({
        found: true, sources: ['github_commit'], names: ['Jane Doe'],
        name_match: true,
      }))
      expect(screen.getByText('In public use under this name')).toBeTruthy()
      const radio = screen.getByText('jane.doe@acme.com')
        .closest('label').querySelector('input[type=radio]')
      expect(radio.disabled).toBe(false)
      fireEvent.click(radio)
      const save = screen.getByText('Save as contact').closest('button')
      expect(save.disabled).toBe(true)
      fireEvent.click(screen.getByText(/not a namesake/))
      expect(save.disabled).toBe(false)
    })

  it('a real address belonging to a namesake stays locked', async () => {
    await openDoneRun(guessWith({
      found: true, sources: ['github_commit'], names: ['Bob Stone'],
      name_match: false,
    }))
    expect(screen.getByText(/Real, but someone else/)).toBeTruthy()
    const radio = screen.getByText('jane.doe@acme.com')
      .closest('label').querySelector('input[type=radio]')
    expect(radio.disabled).toBe(true)
  })

  it('a corpus hit with no name attached stays locked', async () => {
    await openDoneRun(guessWith({
      found: true, sources: ['mailing_list'], names: [], name_match: null,
    }))
    expect(screen.getByText('Address is real — owner unknown')).toBeTruthy()
    const radio = screen.getByText('jane.doe@acme.com')
      .closest('label').querySelector('input[type=radio]')
    expect(radio.disabled).toBe(true)
  })

  it('warns when GitHub throttled the run', async () => {
    const job = doneJob()
    job.result.found_sources = { github_rate_limited: true }
    await openDoneRun(job)
    expect(screen.getByText('GitHub rate-limited')).toBeTruthy()
  })
})
