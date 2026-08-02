/* The do-not-contact list is the only thing on the Settings page that is a
   promise rather than a preference, so the UI's job is to make the promise
   legible: what an entry actually covers, and how much of the database it
   already applies to.

   The case people do not expect is a domain entry — it blocks every address
   there, including subdomains. Saying so at the moment of adding is the whole
   difference between a list users trust and one they route around. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'

const { listSpy, addSpy, removeSpy, toastSuccess, toastError } = vi.hoisted(() => ({
  listSpy: vi.fn(), addSpy: vi.fn(), removeSpy: vi.fn(),
  toastSuccess: vi.fn(), toastError: vi.fn(),
}))

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => e?.response?.data?.detail || f || 'err',
  suppressionsAPI: { list: listSpy, add: addSpy, remove: removeSpy },
  settingsAPI: { get: vi.fn(), update: vi.fn() },
  gmailAPI: { disconnect: vi.fn() },
  cadenceAPI: { get: vi.fn(), update: vi.fn() },
  sendWindowAPI: { get: vi.fn(), update: vi.fn() },
}))
vi.mock('../../src/App', () => ({
  useApp: () => ({ settings: {}, refreshSettings: vi.fn() }),
}))
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: toastSuccess, error: toastError }),
}))

import { DoNotContact } from '../../src/pages/Settings'

const entry = (over = {}) => ({
  id: 's1', value: 'dana@acme.com', kind: 'address',
  reason: null, source: 'manual', created_at: '2026-03-04T10:00:00', ...over,
})

describe('DoNotContact', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listSpy.mockResolvedValue({ data: [] })
    addSpy.mockResolvedValue({ data: { ...entry(), matched_contacts: 0 } })
    removeSpy.mockResolvedValue({ data: { success: true } })
  })

  it('says when the list is empty', async () => {
    render(<DoNotContact />)
    await waitFor(() => expect(document.body.textContent).toMatch(/Nobody on the list/))
  })

  it('says the check happens again at send time', async () => {
    /* The distinction that matters: a draft written before an entry was added
       is still refused. Without this the user assumes existing drafts are
       already in flight. */
    render(<DoNotContact />)
    await waitFor(() => expect(document.body.textContent).toMatch(/before every send/i))
  })

  it('adds an address', async () => {
    render(<DoNotContact />)
    await waitFor(() => expect(listSpy).toHaveBeenCalled())
    fireEvent.change(document.querySelector('.input'),
      { target: { value: 'dana@acme.com' } })
    fireEvent.click(screen.getByText('Add'))
    await waitFor(() => expect(addSpy).toHaveBeenCalledWith('dana@acme.com', null))
  })

  it('passes the reason through, since a refusal quotes it back', async () => {
    render(<DoNotContact />)
    await waitFor(() => expect(listSpy).toHaveBeenCalled())
    const [value, reason] = document.querySelectorAll('.input')
    fireEvent.change(value, { target: { value: 'acme.com' } })
    fireEvent.change(reason, { target: { value: 'asked us to stop' } })
    fireEvent.click(screen.getByText('Add'))
    await waitFor(() =>
      expect(addSpy).toHaveBeenCalledWith('acme.com', 'asked us to stop'))
  })

  it('says how much of the database an entry already covers', async () => {
    /* Otherwise the user finds out a week later, by a send being refused. */
    addSpy.mockResolvedValue({ data: { ...entry({ kind: 'domain', value: 'acme.com' }),
      matched_contacts: 12 } })
    render(<DoNotContact />)
    await waitFor(() => expect(listSpy).toHaveBeenCalled())
    fireEvent.change(document.querySelector('.input'), { target: { value: 'acme.com' } })
    fireEvent.click(screen.getByText('Add'))
    await waitFor(() =>
      expect(toastSuccess).toHaveBeenCalledWith(expect.stringMatching(/12 contacts/)))
  })

  it('marks a domain entry as covering the whole company', async () => {
    listSpy.mockResolvedValue({ data: [entry({ kind: 'domain', value: 'acme.com' })] })
    render(<DoNotContact />)
    await waitFor(() => expect(document.body.textContent).toContain('@acme.com'))
    expect(document.body.textContent).toContain('whole domain')
  })

  it('does not mark an address entry as a domain', async () => {
    listSpy.mockResolvedValue({ data: [entry()] })
    render(<DoNotContact />)
    await waitFor(() => expect(document.body.textContent).toContain('dana@acme.com'))
    expect(document.body.textContent).not.toContain('whole domain')
  })

  it('shows the reason on the entry', async () => {
    listSpy.mockResolvedValue({ data: [entry({ reason: 'asked us to stop' })] })
    render(<DoNotContact />)
    await waitFor(() => expect(document.body.textContent).toContain('asked us to stop'))
  })

  it('confirms before removing, naming what will be allowed again', async () => {
    listSpy.mockResolvedValue({ data: [entry({ kind: 'domain', value: 'acme.com' })] })
    vi.stubGlobal('confirm', vi.fn(() => false))
    render(<DoNotContact />)
    await waitFor(() => expect(document.body.textContent).toContain('@acme.com'))

    fireEvent.click(screen.getByTitle('Remove from the list'))
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringMatching(/everyone at @acme\.com/))
    expect(removeSpy).not.toHaveBeenCalled()

    window.confirm.mockReturnValue(true)
    fireEvent.click(screen.getByTitle('Remove from the list'))
    await waitFor(() => expect(removeSpy).toHaveBeenCalledWith('s1'))
    vi.unstubAllGlobals()
  })

  it('does not submit an empty value', async () => {
    /* Two ways in, and the disabled button only closes one: pressing Enter in
       the field submits the form whatever the button's state, so the guard
       inside the handler is the one that has to hold. Clicking alone passed
       with the guard deleted. */
    render(<DoNotContact />)
    await waitFor(() => expect(listSpy).toHaveBeenCalled())
    fireEvent.change(document.querySelector('.input'), { target: { value: '   ' } })
    fireEvent.click(screen.getByText('Add'))
    fireEvent.submit(document.querySelector('form'))
    expect(addSpy).not.toHaveBeenCalled()
  })

  it('reports a rejected value instead of silently doing nothing', async () => {
    addSpy.mockRejectedValue({
      response: { data: { detail: 'That is not an email address or a domain.' } } })
    render(<DoNotContact />)
    await waitFor(() => expect(listSpy).toHaveBeenCalled())
    fireEvent.change(document.querySelector('.input'), { target: { value: 'nonsense' } })
    fireEvent.click(screen.getByText('Add'))
    await waitFor(() => expect(toastError).toHaveBeenCalledWith(
      expect.stringMatching(/not an email address/)))
  })

  it('never renders an unread list as an empty one', async () => {
    /* "Nobody on the list." is a positive claim that nothing is suppressed —
       made by the one feature documented as fail-closed. A failed GET must
       say it failed, and say the list is still enforced regardless. */
    listSpy.mockRejectedValueOnce(new Error('boom'))
    render(<DoNotContact />)
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/Could not load the do-not-contact list/))
    expect(document.body.textContent).not.toMatch(/Nobody on the list/)
    expect(document.body.textContent).toMatch(/still enforced at send time/)

    listSpy.mockResolvedValue({ data: [entry()] })
    fireEvent.click(screen.getByText('Try again'))
    await waitFor(() => expect(document.body.textContent).toContain('dana@acme.com'))
  })
})
