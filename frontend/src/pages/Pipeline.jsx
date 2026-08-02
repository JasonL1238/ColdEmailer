/* Pipeline: where every contact actually stands.

   The question this answers is the one you ask before deciding what to do
   next — how many people are waiting on me, and how many am I waiting on.
   Two numbers, and then the names behind them.

   The stages come from the backend, derived from the emails that exist rather
   than from `contacts.status`. That distinction is the whole point: the column
   is written from four places and reset from none, so a contact whose draft
   was deleted stays marked `drafted` forever. A board that believed it would
   file people under "handled" and they would never be written to again.

   Read-only on purpose. Dragging a card between columns would have to mean
   something — moving someone into "Sent" without sending is a lie, and moving
   them out of "Replied" discards a verified fact — so the columns are a view
   of the evidence, and the actions that change the evidence live where they
   already are. */
import { useCallback, useEffect, useState } from 'react'
import {
  KanbanSquare, AlertTriangle, MailX, Inbox, Send, PenLine,
  Archive, CornerUpLeft,
} from 'lucide-react'
import { pipelineAPI, errMessage } from '../api'
import { Button, Chip, EmptyState, timeAgo } from '../ui'
import { useApp } from '../App'

const STAGE_ICONS = {
  ready: PenLine, no_address: MailX, drafted: Inbox, awaiting: Send,
  replied: CornerUpLeft, bounced: AlertTriangle, archived: Archive,
}

// Only the stages where a card is a live prospect get colour. Bounced and
// archived are exits, and painting them like progress reads as progress.
const STAGE_TONE = {
  ready: 'gray', no_address: 'amber', drafted: 'sky', awaiting: 'accent',
  replied: 'green', bounced: 'red', archived: 'gray',
}

export default function Pipeline() {
  const { navigate } = useApp()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    setError(null)
    pipelineAPI.get()
      .then(({ data: d }) => setData(d))
      .catch((e) => setError(errMessage(e, 'Could not load the pipeline')))
  }, [])

  useEffect(() => { load() }, [load])

  if (error) {
    return (
      <div className="page wide">
        <Head />
        <div className="card">
          <EmptyState icon={AlertTriangle} title="Pipeline unavailable" desc={error}
            action={<Button onClick={load}>Try again</Button>} />
        </div>
      </div>
    )
  }
  if (!data) {
    return <div className="page wide"><div className="skeleton" style={{ height: 420, marginTop: 40 }} /></div>
  }
  if (data.total === 0) {
    return (
      <div className="page wide">
        <Head />
        <div className="card">
          <EmptyState icon={KanbanSquare} title="No contacts yet"
            desc="Find some companies on Discover and this fills in as you research them."
            action={<Button variant="primary" onClick={() => navigate('discover')}>Go to Discover</Button>} />
        </div>
      </div>
    )
  }

  return (
    <div className="page wide">
      <Head />

      <div className="row" style={{ gap: 14, flexWrap: 'wrap', marginBottom: 16 }}>
        <Tally label="Waiting on you" value={data.waiting_on_you}
          sub="ready to write, or drafted and unsent" />
        <Tally label="Waiting on them" value={data.waiting_on_them}
          sub="delivered, no reply yet" />
        {/* Queued mail is in neither of the two above: the scheduler will send
            it without you, and it has not reached anyone yet. Left out of both
            it would simply vanish, so it gets said here. */}
        <Tally label={data.queued > 0 ? 'Queued to send' : 'Contacts'}
          value={data.queued > 0 ? data.queued : data.total}
          sub={data.queued > 0
            ? 'armed by the send window — no action needed'
            : 'everyone in the database'} />
      </div>

      {data.status_drift > 0 && (
        <div className="card card-pad row mb-16" style={{ background: 'var(--surface-2)', gap: 10 }}>
          <AlertTriangle size={16} style={{ color: 'var(--text-2)', flexShrink: 0 }} />
          <span className="small muted">
            {data.status_drift} contact{data.status_drift === 1 ? '' : 's'} {data.status_drift === 1 ? 'is' : 'are'} filed
            here by what their emails show, not by the status stored against them —
            that field is never reset when a draft is deleted. The columns below are
            the accurate ones; nothing was changed to make them agree.
          </span>
        </div>
      )}

      <div className="board">
        {data.stages.map((stage) => (
          <Column key={stage.key} stage={stage} onOpen={() => navigate('database')} />
        ))}
      </div>
    </div>
  )
}

function Head() {
  return (
    <div className="page-head">
      <div>
        <div className="page-title">Pipeline</div>
        <div className="page-desc">Where everyone stands, from what actually happened</div>
      </div>
    </div>
  )
}

function Tally({ label, value, sub }) {
  return (
    <div className="card card-pad" style={{ flex: '1 1 180px' }}>
      <span className="tiny muted">{label}</span>
      <div style={{ fontSize: 26, fontWeight: 700, marginTop: 2 }}>{value}</div>
      <div className="tiny muted">{sub}</div>
    </div>
  )
}

function Column({ stage, onOpen }) {
  const Icon = STAGE_ICONS[stage.key] || Inbox
  return (
    <div className="board-col">
      <div className="board-col-head" title={stage.hint}>
        <span className="row" style={{ gap: 6, minWidth: 0 }}>
          <Icon size={13} style={{ color: 'var(--text-2)', flexShrink: 0 }} />
          <b className="tiny">{stage.label}</b>
        </span>
        <Chip tone={stage.total ? STAGE_TONE[stage.key] : 'gray'}>{stage.total}</Chip>
      </div>
      {/* An empty column is information — "nothing is waiting on you" — so it
          says that rather than collapsing and making the user count. */}
      {stage.total === 0 ? (
        <div className="tiny muted board-empty">Nobody here.</div>
      ) : (
        stage.cards.map((c) => <Card key={c.id} card={c} onOpen={onOpen} />)
      )}
      {stage.hidden > 0 && (
        <div className="tiny muted board-empty">+{stage.hidden} more</div>
      )}
    </div>
  )
}

function Card({ card, onOpen }) {
  return (
    <button type="button" className="board-card" onClick={onOpen}
      title={card.email || 'No email address'}>
      <div className="tiny board-card-name">{card.name}</div>
      {card.company_name && <div className="tiny muted board-card-sub">{card.company_name}</div>}
      {card.role && <div className="tiny muted board-card-sub">{card.role}</div>}

      <div className="row" style={{ gap: 5, flexWrap: 'wrap', marginTop: 2 }}>
        {/* An unverified flag is not a reply, so it never promotes the card —
            it explains why someone with a reply flag is still in Awaiting. */}
        {card.reply_unverified && (
          <Chip tone="amber" title="Flagged by an older check that also counted bounces, auto-replies and your own messages. Re-verify on the Emails page.">
            reply unverified
          </Chip>
        )}
        {/* Otherwise "No address" looks like nothing has been done, when in
            fact there is finished writing with nowhere to send it. */}
        {card.blocked_draft && (
          <Chip tone="amber" title="A draft is written, but there is no address to send it to.">
            draft, no address
          </Chip>
        )}
        {/* A follow-up drafted for someone already emailed is the ordinary
            output of the cadence. It has to be visible as work, not folded
            into "you have written to this person". */}
        {card.drafts > 0 && card.sent > 0 && !card.blocked_draft && (
          <Chip tone="sky">{card.drafts} unsent</Chip>
        )}
        {card.queued > 0 && (
          <Chip tone="gray" title="Approved and armed. The send window will send it without you.">
            {card.queued} queued
          </Chip>
        )}
        {card.unconfirmed > 0 && (
          <Chip tone="amber" title="Handed to Gmail without a confirmation coming back. It may already have arrived, so it is not a safe retry.">
            send unconfirmed
          </Chip>
        )}
        {card.follow_ups_sent > 0 && (
          <Chip tone="gray">{card.follow_ups_sent} follow-up{card.follow_ups_sent === 1 ? '' : 's'}</Chip>
        )}
        {card.last_sent_at && (
          <span className="tiny muted">{timeAgo(card.last_sent_at)}</span>
        )}
      </div>
    </button>
  )
}
