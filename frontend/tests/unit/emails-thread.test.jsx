/* The reply pane is the one place in this app where text written by a
   stranger reaches the screen. So the tests are about two things: that what
   they wrote arrives intact, and that nothing they wrote can do anything.

   Plus one rule the backend also enforces: reading a thread changes no
   record, so the pane must never imply it did. */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'

const { threadSpy } = vi.hoisted(() => ({ threadSpy: vi.fn() }))

vi.mock('../../src/api', () => ({
  errMessage: (e, f) => e?.response?.data?.detail || f || 'err',
  emailsAPI: { thread: threadSpy },
}))
vi.mock('react-hot-toast', async () => (await import('../_mocks')).toastMock())

import { ThreadPane } from '../../src/pages/Emails'

const email = { id: 'e1' }

const message = (over = {}) => ({
  id: 'm1', kind: 'reply', outgoing: false, is_tracked_send: false,
  from_name: 'Dana Lee', from_email: 'dana@x.com', subject: 'Re: hello',
  to: 'me@x.com', sent_at: new Date().toISOString(), snippet: '',
  text: 'Happy to chat Thursday.', quoted: '', truncated: false,
  attachments: [], ...over,
})

const payload = (over = {}) => ({
  messages: [message()], older_omitted: 0, reply_count: 1, bounce_count: 0,
  thread_id: 't1', unrecorded_reply: false, unrecorded_bounce: false, ...over,
})

const open = async () => {
  fireEvent.click(screen.getByText('Read the thread'))
  await waitFor(() => expect(threadSpy).toHaveBeenCalled())
}

describe('ThreadPane', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    threadSpy.mockResolvedValue({ data: payload() })
  })

  it('does not touch Gmail until asked', () => {
    /* A thread costs a Gmail round-trip. Fetching one per row as the list
       renders spends the quota reply-checking needs. */
    render(<ThreadPane email={email} />)
    expect(threadSpy).not.toHaveBeenCalled()
    expect(document.body.textContent).toMatch(/nothing is stored/i)
  })

  it('shows what the person actually wrote, labelled as a reply', async () => {
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() =>
      expect(document.body.textContent).toContain('Happy to chat Thursday.'))
    expect(document.body.textContent).toContain('Dana Lee')
    // The label is the whole point of the chips — it is what makes the pane
    // and the dashboard say the same thing about a message. Asserting it only
    // by absence elsewhere let a genuine reply render under any label at all.
    expect(document.querySelector('.thread-msg .chip').textContent.trim())
      .toBe('Reply')
  })

  it('renders a reply as text, never as markup', async () => {
    /* React escapes by default and this test exists to keep it that way: one
       dangerouslySetInnerHTML added later for "nicer formatting" would let a
       reply run script in the app that holds a live Gmail token. */
    threadSpy.mockResolvedValue({ data: payload({
      messages: [message({ text: '<img src=x onerror="alert(1)"> <b>bold</b>' })],
    }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() => expect(document.body.textContent).toContain('<b>bold</b>'))
    expect(document.querySelector('.thread-body img')).toBeNull()
    expect(document.querySelector('.thread-body b')).toBeNull()
  })

  it('collapses quoted history behind a disclosure rather than dropping it', async () => {
    threadSpy.mockResolvedValue({ data: payload({
      messages: [message({ text: 'Yes.', quoted: '> my original pitch' })],
    }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() => expect(document.body.textContent).toContain('Yes.'))
    expect(document.body.textContent).not.toContain('my original pitch')

    fireEvent.click(screen.getByText('Show quoted text'))
    expect(document.body.textContent).toContain('> my original pitch')
  })

  it('says a reply is unrecorded instead of quietly recording it', async () => {
    /* Reading writes nothing on purpose — one code path decides "replied",
       on its own rules. The pane's job is to point at it. */
    threadSpy.mockResolvedValue({ data: payload({ unrecorded_reply: true }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/has not recorded/i))
    expect(document.body.textContent).toMatch(/Re-verify replies/i)
  })

  it('flags an unrecorded delivery failure', async () => {
    threadSpy.mockResolvedValue({ data: payload({
      unrecorded_bounce: true, bounce_count: 1, reply_count: 0,
      messages: [message({ kind: 'bounce', from_name: 'Mail Delivery Subsystem',
        text: '550 no such user' })],
    }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/delivery failure/i))
    expect(document.body.textContent).toContain('Bounce')
  })

  it('labels a bounce and an auto-reply as what they are', async () => {
    /* Showing "Reply" over an out-of-office beside a dashboard that reports
       no reply leaves the user with two truths and no tiebreak. */
    threadSpy.mockResolvedValue({ data: payload({
      reply_count: 0,
      messages: [
        message({ id: 'a', kind: 'auto', text: 'I am on leave until the 9th.' }),
        message({ id: 'b', kind: 'own', outgoing: true, text: 'my pitch' }),
      ],
    }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() => expect(document.body.textContent).toContain('Auto-reply'))
    expect(document.body.textContent).toContain('You')
    expect(document.body.textContent).not.toContain('Reply')
  })

  it('says no one has written back rather than showing an empty pane', async () => {
    threadSpy.mockResolvedValue({ data: payload({
      reply_count: 0,
      messages: [message({ kind: 'own', outgoing: true, text: 'my pitch' })],
    }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/No one has written back/i))
  })

  it('names attachments without offering to open them', async () => {
    threadSpy.mockResolvedValue({ data: payload({
      messages: [message({ attachments: ['contract.pdf', 'photo.jpg'] })],
    }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() => expect(document.body.textContent).toContain('contract.pdf'))
    // No download links: this app never fetches a stranger's file.
    expect(document.querySelectorAll('.thread-msg a').length).toBe(0)
  })

  it('says a long message was cut rather than cutting it silently', async () => {
    threadSpy.mockResolvedValue({ data: payload({
      messages: [message({ truncated: true })],
    }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() => expect(document.body.textContent).toMatch(/40,000 characters/))
  })

  it('says how many older messages it is not showing', async () => {
    threadSpy.mockResolvedValue({ data: payload({ older_omitted: 12 }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() => expect(document.body.textContent).toMatch(/12 older message/))
  })

  it('shows a missing date as "no date", not as 1970', async () => {
    threadSpy.mockResolvedValue({ data: payload({
      messages: [message({ sent_at: null })],
    }) })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() => expect(document.body.textContent).toContain('no date'))
    expect(document.body.textContent).not.toMatch(/1970/)
  })

  it('reports a failure and offers a retry', async () => {
    threadSpy.mockRejectedValueOnce({ response: { data: { detail: 'Gmail said no' } } })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() => expect(document.body.textContent).toContain('Gmail said no'))

    threadSpy.mockResolvedValue({ data: payload() })
    fireEvent.click(screen.getByText('Try again'))
    await waitFor(() =>
      expect(document.body.textContent).toContain('Happy to chat Thursday.'))
  })

  it('never shows one contact’s conversation under another’s email', async () => {
    /* The pane is keyed on the selected email, and the list swaps that email
       in place. Holding the old thread across the swap put a stranger's reply
       under a different person's name. */
    const { rerender } = render(<ThreadPane email={email} />)
    await open()
    await waitFor(() =>
      expect(document.body.textContent).toContain('Happy to chat Thursday.'))

    rerender(<ThreadPane email={{ id: 'e2' }} />)
    expect(document.body.textContent).not.toContain('Happy to chat Thursday.')
    expect(screen.getByText('Read the thread')).toBeTruthy()
  })

  it('requests the thread for the email on screen', async () => {
    render(<ThreadPane email={{ id: 'e9' }} />)
    await open()
    expect(threadSpy).toHaveBeenCalledWith('e9')
  })

  it('offers no retry for a thread that can never exist', async () => {
    /* A legacy row imported as status='sent' with no Gmail id 409s forever.
       A "Try again" button on it is an invitation to click at a wall. */
    threadSpy.mockRejectedValue({
      response: { status: 409, data: { detail: 'This email has no Gmail thread yet' } },
    })
    render(<ThreadPane email={email} />)
    await open()
    await waitFor(() =>
      expect(document.body.textContent).toContain('no Gmail thread yet'))
    expect([...document.querySelectorAll('button')]
      .some((b) => /try again/i.test(b.textContent))).toBe(false)
  })

  it('drops the "not recorded" banner once the record catches up', async () => {
    /* The payload is a snapshot. Check replies flips the row to replied and
       re-renders the parent; an unrevalidated banner then sat under a green
       "replied" chip still insisting nothing had been recorded. */
    threadSpy.mockResolvedValue({ data: payload({ unrecorded_reply: true }) })
    const { rerender } = render(<ThreadPane email={email} />)
    await open()
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/has not recorded/i))

    rerender(<ThreadPane email={{ ...email, has_response: true }} />)
    expect(document.body.textContent).not.toMatch(/has not recorded/i)
    // and the conversation itself is still on screen
    expect(document.body.textContent).toContain('Happy to chat Thursday.')
  })

  it('drops the bounce banner once the bounce is recorded', async () => {
    threadSpy.mockResolvedValue({ data: payload({ unrecorded_bounce: true }) })
    const { rerender } = render(<ThreadPane email={email} />)
    await open()
    await waitFor(() =>
      expect(document.body.textContent).toMatch(/delivery failure/i))

    rerender(<ThreadPane email={{ ...email, bounced_at: '2026-01-01T00:00:00' }} />)
    expect(document.body.textContent).not.toMatch(/delivery failure/i)
  })
})
