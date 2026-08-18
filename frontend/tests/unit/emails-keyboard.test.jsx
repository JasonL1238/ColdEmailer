/* Keyboard review has exactly one catastrophic failure and several annoying
   ones, and the tests are weighted accordingly.

   The catastrophic one: a keystroke doing something irreversible. There is no
   send shortcut, and `x` must never fire while the user is typing into a body
   — "x" is a letter people write.

   The annoying ones: keys that fight the app's own guards (unsaved changes),
   keys that steal browser combinations, and keys that act on an email the
   backend will refuse anyway. */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, waitFor, fireEvent, screen } from '@testing-library/react'

const { listSpy, bulkStatusSpy, sendSpy, updateSpy } = vi.hoisted(() => ({
  listSpy: vi.fn(), bulkStatusSpy: vi.fn(), sendSpy: vi.fn(), updateSpy: vi.fn(),
}))

vi.mock('../../src/api', async () => (await import('../_mocks')).emailsPageApi({
  emailsAPI: {
    list: listSpy,
    update: updateSpy,
    bulkStatus: bulkStatusSpy,
    bulkDelete: vi.fn(() => Promise.resolve({ data: { deleted: 0 } })),
    regenerate: vi.fn(() => Promise.resolve({ data: {} })),
    send: sendSpy,
    thread: vi.fn(() => Promise.resolve({ data: { messages: [] } })),
  },
  jobsAPI: { get: vi.fn(() => Promise.resolve({ data: { id: 'j1', status: 'running' } })) },
}))
vi.mock('../../src/App', async () => (await import('../_mocks')).appMock())
vi.mock('react-hot-toast', async () => (await import('../_mocks')).toastMock())

import Emails, { shouldIgnoreShortcut, SHORTCUTS } from '../../src/pages/Emails'

const draft = (id, name, over = {}) => ({
  id, status: 'draft', subject: `ZZTEST ${name}`, body: 'hello there',
  contact_name: name, contact_email: `${id}@example.com`,
  company_name: 'ZZTEST Corp', email_type: 'application',
  sent_at: null, gmail_message_id: null, created_at: '2026-02-18T18:00:00',
  has_response: 0, has_follow_up: 0, ...over,
})

const EMAILS = [
  draft('a', 'Alice'),
  draft('b', 'Bob'),
  draft('c', 'Cara', { status: 'approved' }),
]

const key = (k, opts = {}) => fireEvent.keyDown(document.body, { key: k, ...opts })
const activeName = () =>
  document.querySelector('.email-row.active')?.querySelector('.email-row-title')?.textContent
const detail = () => document.querySelector('.email-detail')

async function open() {
  render(<Emails />)
  await waitFor(() => expect(document.querySelectorAll('.email-row').length).toBe(3))
  // Wait for the selection to settle too. Rows exist one render before
  // `activeId` is written, and firing a key into that gap made every
  // navigation test depend on timing rather than on behaviour.
  await waitFor(() => expect(activeName()).toBe('Alice'))
}

describe('shouldIgnoreShortcut', () => {
  const evt = (over = {}) => ({ target: { tagName: 'DIV' }, ...over })

  it('lets a plain keypress through', () => {
    expect(shouldIgnoreShortcut(evt(), { modalOpen: false })).toBe(false)
  })

  it('stands down while the user is typing', () => {
    /* "x" and "a" and "e" are letters. Acting on them mid-sentence trashes
       the draft being written. */
    for (const tagName of ['INPUT', 'TEXTAREA', 'SELECT']) {
      expect(shouldIgnoreShortcut(evt({ target: { tagName } }))).toBe(true)
    }
    expect(shouldIgnoreShortcut(evt({
      target: { tagName: 'DIV', isContentEditable: true } }))).toBe(true)
  })

  it('never steals a browser or OS combination', () => {
    expect(shouldIgnoreShortcut(evt({ metaKey: true }))).toBe(true)
    expect(shouldIgnoreShortcut(evt({ ctrlKey: true }))).toBe(true)
    expect(shouldIgnoreShortcut(evt({ altKey: true }))).toBe(true)
    // Shift is not a modifier here — "?" needs it.
    expect(shouldIgnoreShortcut(evt({ shiftKey: true }))).toBe(false)
  })

  it('stands down while a modal owns the screen', () => {
    expect(shouldIgnoreShortcut(evt(), { modalOpen: true })).toBe(true)
  })
})

describe('Emails keyboard review', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listSpy.mockResolvedValue({ data: EMAILS })
    bulkStatusSpy.mockResolvedValue({ data: { updated: 1 } })
    updateSpy.mockResolvedValue({ data: {} })
    sendSpy.mockResolvedValue({ data: { id: 'j1', status: 'running' } })
  })
  afterEach(() => { vi.unstubAllGlobals() })

  it('moves down and up the list', async () => {
    await open()
    expect(activeName()).toBe('Alice')
    key('j'); await waitFor(() => expect(activeName()).toBe('Bob'))
    key('j'); await waitFor(() => expect(activeName()).toBe('Cara'))
    key('k'); await waitFor(() => expect(activeName()).toBe('Bob'))
  })

  it('takes the arrow keys too', async () => {
    await open()
    key('ArrowDown'); await waitFor(() => expect(activeName()).toBe('Bob'))
    key('ArrowUp'); await waitFor(() => expect(activeName()).toBe('Alice'))
  })

  it('stops at the ends instead of wrapping', async () => {
    /* Wrapping silently restarts a review list, and you re-read what you
       just cleared without noticing. */
    await open()
    key('k'); await waitFor(() => expect(activeName()).toBe('Alice'))
    key('j'); key('j'); key('j'); key('j')
    await waitFor(() => expect(activeName()).toBe('Cara'))
  })

  it('approves the selected draft', async () => {
    await open()
    key('a')
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith('a', { status: 'approved' }))
  })

  it('approves the text on screen, not the text last saved', async () => {
    /* The Approve button folds unsaved edits into the approval. A second
       approval path that only set the status marked a draft ready to send
       while the sentence just typed stayed unsaved — and then sent the old
       one. There is now one path, so they cannot disagree. */
    await open()
    fireEvent.change(detail().querySelector('.email-body-input'),
      { target: { value: 'the version I actually want sent' } })
    key('a')
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith('a', {
      subject: 'ZZTEST Alice',
      body: 'the version I actually want sent',
      status: 'approved',
    }))
  })

  it('ignores auto-repeat on the state-changing keys', async () => {
    /* A held key fires at about 30Hz. Without this, one press trashed the
       whole queue: each trash reloads the list, so the repeats chew through
       drafts the user had already reviewed and kept. */
    await open()
    key('x', { repeat: true })
    key('a', { repeat: true })
    expect(bulkStatusSpy).not.toHaveBeenCalled()
    expect(updateSpy).not.toHaveBeenCalled()

    key('x')      // the same physical key, pressed once
    await waitFor(() => expect(bulkStatusSpy).toHaveBeenCalledTimes(1))
  })

  it('moves the cursor forward after a trash, never back to the top', async () => {
    /* `active` falls back to visible[0] once the trashed row disappears, so
       the cursor jumped to the first draft — one already reviewed and kept —
       and the next x hit that. */
    await open()
    key('j')
    await waitFor(() => expect(activeName()).toBe('Bob'))
    key('x')
    await waitFor(() => expect(bulkStatusSpy).toHaveBeenCalledWith(['b'], 'trashed'))
    expect(activeName()).toBe('Cara')
  })

  it('falls back to the previous draft when trashing the last one', async () => {
    await open()
    key('j'); key('j')
    await waitFor(() => expect(activeName()).toBe('Cara'))
    key('x')
    await waitFor(() => expect(bulkStatusSpy).toHaveBeenCalledWith(['c'], 'trashed'))
    expect(activeName()).toBe('Bob')
  })

  it('works with Caps Lock on', async () => {
    /* event.key becomes 'J'/'X', so every letter shortcut silently died with
       nothing on screen to explain it. */
    await open()
    key('J'); await waitFor(() => expect(activeName()).toBe('Bob'))
    key('K'); await waitFor(() => expect(activeName()).toBe('Alice'))
    key('A')
    await waitFor(() => expect(updateSpy).toHaveBeenCalledWith('a', { status: 'approved' }))
  })

  it('does not re-approve an already-approved draft', async () => {
    await open()
    key('j'); key('j')
    await waitFor(() => expect(activeName()).toBe('Cara'))
    key('a')
    expect(updateSpy).not.toHaveBeenCalled()
  })

  const openTab = async (label) => {
    await waitFor(() => expect(document.querySelectorAll('.segmented button').length)
      .toBeGreaterThan(0))
    fireEvent.click([...document.querySelectorAll('.segmented button')]
      .find((b) => b.textContent.startsWith(label)))
    await waitFor(() => expect(document.querySelectorAll('.email-row').length).toBe(1))
  }

  it('never acts on a delivered email', async () => {
    /* Deliberately a row that is delivered by message id while still labelled
       'draft'. A fixture with status:'sent' would be blocked by the status
       check alone, leaving the isDelivered half of both guards unexercised. */
    listSpy.mockResolvedValue({ data: [
      draft('s', 'Sent', { sent_at: '2026-02-01T10:00:00', gmail_message_id: 'gm1' }),
    ] })
    render(<Emails />)
    await openTab('Sent')
    key('a'); key('x')
    expect(bulkStatusSpy).not.toHaveBeenCalled()
    // Approve goes through emailsAPI.update, so watching bulkStatus alone left
    // the delivered half of the approve guard unexercised.
    expect(updateSpy).not.toHaveBeenCalled()
  })

  it('never acts on a legacy sent row that carries no Gmail id', async () => {
    /* The detail pane has always hidden its Trash button for these. A key
       gated only on isDelivered offered to trash them, the backend refused,
       and the user got a "trashed" toast and an Undo for nothing. */
    listSpy.mockResolvedValue({ data: [
      draft('L', 'Legacy', { status: 'sent', sent_at: null, gmail_message_id: null }),
    ] })
    render(<Emails />)
    await openTab('Sent')
    key('x'); key('a')
    expect(bulkStatusSpy).not.toHaveBeenCalled()
    expect(updateSpy).not.toHaveBeenCalled()
  })

  it('does nothing to an email already in the trash', async () => {
    listSpy.mockResolvedValue({ data: [draft('t', 'Trashed', { status: 'trashed' })] })
    render(<Emails />)
    await openTab('Trash')
    key('x'); key('a')
    expect(bulkStatusSpy).not.toHaveBeenCalled()
    expect(updateSpy).not.toHaveBeenCalled()
  })

  it('trashes once even if a second keystroke lands before the reload', async () => {
    /* Without an in-flight guard the second press saw the unchanged active
       email and repeated the whole action — a duplicate request and a second
       Undo toast that undoes nothing.

       Dispatched raw rather than through fireEvent: fireEvent wraps each call
       in act(), which flushes React and moves the cursor on, so the presses
       land on different emails and the duplicate can never occur. The real
       browser does not flush between two events in the same task, and neither
       does this. */
    await open()
    const press = () => document.body.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'x', bubbles: true }))
    press(); press(); press()

    await waitFor(() => expect(bulkStatusSpy).toHaveBeenCalledWith(['a'], 'trashed'))
    expect(bulkStatusSpy.mock.calls.filter(
      ([ids, status]) => ids[0] === 'a' && status === 'trashed')).toHaveLength(1)
  })

  it('undo puts an approved draft back as approved, not as a draft', async () => {
    /* Restoring everything to 'draft' quietly demoted an email that had
       already been reviewed — a second change the user never asked for,
       inside the button whose whole job is reversing one. */
    const toast = (await import('react-hot-toast')).default
    await open()
    key('j'); key('j')
    await waitFor(() => expect(activeName()).toBe('Cara'))   // status: approved
    key('x')
    await waitFor(() => expect(toast).toHaveBeenCalled())

    render(toast.mock.calls.at(-1)[0]({ id: 't1' }))
    fireEvent.click(screen.getByText('Undo'))
    await waitFor(() =>
      expect(bulkStatusSpy).toHaveBeenCalledWith(['c'], 'approved'))
  })

  it('does not open the help while the user is typing', async () => {
    /* "?" is handled ahead of the main guard, so it is the one key whose
       editor check is its own — and the help popped open mid-sentence. */
    await open()
    const body = detail().querySelector('.email-body-input')
    body.focus()
    fireEvent.keyDown(body, { key: '?', shiftKey: true })
    expect(document.body.textContent).not.toMatch(/Keyboard review/)
  })

  it('scrolls the newly selected row into view', async () => {
    /* The data-email-row attribute exists only for this lookup. Without it the
       list silently stops following the selection, and on a forty-draft review
       j walks the highlight off the bottom of the screen. */
    const spy = vi.fn()
    Element.prototype.scrollIntoView = spy
    await open()
    key('j')
    await waitFor(() => expect(activeName()).toBe('Bob'))
    expect(spy).toHaveBeenCalledWith({ block: 'nearest' })
    delete Element.prototype.scrollIntoView
  })

  it('trashes with an undo rather than a bare confirmation', async () => {
    const toast = (await import('react-hot-toast')).default
    await open()
    key('x')
    await waitFor(() => expect(bulkStatusSpy).toHaveBeenCalledWith(['a'], 'trashed'))
    // a custom toast (a render function), not the plain success string — the
    // undo is the point
    expect(toast).toHaveBeenCalled()
    expect(typeof toast.mock.calls.at(-1)[0]).toBe('function')
    expect(toast.success).not.toHaveBeenCalledWith(expect.stringMatching(/trashed/))
  })

  it('restores from the undo in the toast', async () => {
    const toast = (await import('react-hot-toast')).default
    await open()
    key('x')
    await waitFor(() => expect(toast).toHaveBeenCalled())

    const node = toast.mock.calls.at(-1)[0]({ id: 't1' })
    render(node)
    fireEvent.click(screen.getByText('Undo'))
    await waitFor(() =>
      expect(bulkStatusSpy).toHaveBeenCalledWith(['a'], 'draft'))
    expect(toast.dismiss).toHaveBeenCalledWith('t1')
  })

  it('does nothing at all while the body has focus', async () => {
    /* The single worst failure available here: "x" is a letter. */
    await open()
    const body = detail().querySelector('.email-body-input')
    body.focus()
    fireEvent.keyDown(body, { key: 'x' })
    fireEvent.keyDown(body, { key: 'a' })
    fireEvent.keyDown(body, { key: 'j' })
    expect(bulkStatusSpy).not.toHaveBeenCalled()
    expect(activeName()).toBe('Alice')
  })

  it('focuses the body on e, and Escape gives the keyboard back', async () => {
    await open()
    key('e')
    const body = detail().querySelector('.email-body-input')
    await waitFor(() => expect(document.activeElement).toBe(body))

    fireEvent.keyDown(body, { key: 'Escape' })
    await waitFor(() => expect(document.activeElement).not.toBe(body))
    key('j')
    await waitFor(() => expect(activeName()).toBe('Bob'))
  })

  it('leaves browser combinations alone', async () => {
    await open()
    key('a', { metaKey: true })
    key('x', { ctrlKey: true })
    expect(bulkStatusSpy).not.toHaveBeenCalled()
  })

  it('respects the unsaved-changes guard when moving', async () => {
    await open()
    const body = detail().querySelector('.email-body-input')
    fireEvent.change(body, { target: { value: 'work in progress' } })
    vi.stubGlobal('confirm', vi.fn(() => false))

    key('j')
    expect(window.confirm).toHaveBeenCalled()
    expect(activeName()).toBe('Alice')     // stayed put

    window.confirm.mockReturnValue(true)
    key('j')
    await waitFor(() => expect(activeName()).toBe('Bob'))
  })

  it('has no shortcut that opens the send flow, let alone sends', async () => {
    /* Deliberate and permanent: every other action here is reversible and a
       delivered email is not.

       The first version of this test watched emailsAPI.send, which is two
       clicks past the point of no return — wiring `case 's': setSendModal(...)`
       passed it cleanly. The send dialog opening at all is the thing a
       keystroke must never cause, so that is what is asserted. */
    await open()
    for (const k of ['s', 'S', 'Enter', 'd', 'y', 'Return', ' ', 'v']) key(k)
    expect(document.querySelector('.overlay')).toBeNull()
    expect(document.body.textContent).not.toMatch(/Send \d+ email/i)
    expect(sendSpy).not.toHaveBeenCalled()
  })

  it('lists no send key, and says the omission is deliberate', async () => {
    // Structural: the help is the contract, so an added key has to be added
    // here too, where it would fail.
    expect(SHORTCUTS.map(([k]) => k))
      .toEqual(['j  /  ↓', 'k  /  ↑', 'e', 'Esc', 'a', 'x', '?'])
    await open()
    key('?')
    await waitFor(() => expect(document.body.textContent).toMatch(/Keyboard review/))
    // every listed shortcut is actually rendered, so emptying the list fails
    expect(document.querySelectorAll('.modal-body .kbd').length).toBe(SHORTCUTS.length)
  })

  it('opens and closes the help on ?', async () => {
    await open()
    key('?')
    await waitFor(() => expect(document.body.textContent).toMatch(/Keyboard review/))
    expect(document.body.textContent).toMatch(/no shortcut for sending/i)

    key('Escape')
    await waitFor(() =>
      expect(document.body.textContent).not.toMatch(/Keyboard review/))
  })

  it('acts on nothing while the help is open', async () => {
    await open()
    key('?')
    await waitFor(() => expect(document.body.textContent).toMatch(/Keyboard review/))
    key('x'); key('a'); key('j')
    expect(bulkStatusSpy).not.toHaveBeenCalled()
    expect(activeName()).toBe('Alice')
  })

  it('offers the help where someone would look for it', async () => {
    await open()
    fireEvent.click(screen.getByTitle('Keyboard shortcuts'))
    await waitFor(() => expect(document.body.textContent).toMatch(/Keyboard review/))
  })

  it('survives an empty list without throwing', async () => {
    listSpy.mockResolvedValue({ data: [] })
    render(<Emails />)
    await waitFor(() => expect(document.body.textContent).toMatch(/No drafts/))
    key('j'); key('x'); key('a'); key('e')
    expect(bulkStatusSpy).not.toHaveBeenCalled()
  })
})
