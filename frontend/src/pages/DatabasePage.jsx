/* Database: unified view of scraped companies and their contacts */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import toast from 'react-hot-toast'
import {
  Search, Plus, Upload, Download, Trash2, RefreshCw, Globe, Building2,
  Users, Sparkles, ExternalLink, AlertTriangle, Archive, ArchiveRestore,
  ChevronLeft, ChevronRight, MessageSquare, Copy, Check,
} from 'lucide-react'
import { companiesAPI, contactsAPI, errMessage } from '../api'
import {
  Button, Chip, Drawer, EmptyState, Modal, Segmented,
  initials, timeAgo, contactStatusMeta, EMAIL_TYPE_META,
} from '../ui'
import { useApp } from '../App'
import ComposeModal from './ComposeModal'

const SCRAPE_STATUS = {
  scraped: { label: 'Contacts found', tone: 'green' },
  pending: { label: 'Not researched', tone: 'gray' },
  scraping: { label: 'Researching…', tone: 'accent' },
  no_emails_found: { label: 'No contacts found', tone: 'amber' },
  no_website: { label: 'No website', tone: 'gray' },
  wrong_site: { label: 'Wrong site found', tone: 'amber' },
  scrape_failed: { label: 'Research failed', tone: 'red' },
}

const PAGE_SIZE_OPTIONS = [10, 15, 25, 50]

export default function DatabasePage() {
  const { navigate } = useApp()
  const [tab, setTab] = useState('companies')
  const [search, setSearch] = useState('')
  const [viewFilter, setViewFilter] = useState('all')
  const [sortBy, setSortBy] = useState('newest')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(15)
  const [companies, setCompanies] = useState(null)
  const [contacts, setContacts] = useState(null)
  const [selected, setSelected] = useState(new Set())   // contact ids
  const [drawerCompany, setDrawerCompany] = useState(null)
  const [showAddCompany, setShowAddCompany] = useState(false)
  const [showAddContact, setShowAddContact] = useState(false)
  const [composeIds, setComposeIds] = useState(null)
  const [linkedinContact, setLinkedinContact] = useState(null)

  const [loadError, setLoadError] = useState(null)

  const load = useCallback(async () => {
    try {
      const [co, ct] = await Promise.all([companiesAPI.list(), contactsAPI.list()])
      setCompanies(co.data)
      setContacts(ct.data)
      setLoadError(null)
    } catch (e) {
      const msg = errMessage(e, 'Failed to load database')
      toast.error(msg)
      // Never fall through to the empty state here — "no companies yet" would
      // tell the user their data is gone when the request merely failed.
      setLoadError(msg)
      setCompanies((c) => c ?? [])
      setContacts((c) => c ?? [])
    }
  }, [])

  useEffect(() => { load() }, [load])

  const q = search.trim().toLowerCase()
  const filteredCompanies = useMemo(() => {
    const rows = (companies || []).filter((c) => {
      const matchesSearch = !q ||
        [c.name, c.industry, c.summary, c.domain].some((v) => v?.toLowerCase().includes(q))
      const matchesFilter = viewFilter === 'all'
        || (viewFilter === 'researched' && c.scrape_status === 'scraped')
        || (viewFilter === 'needs_research' && c.scrape_status !== 'scraped')
        || (viewFilter === 'with_contacts' && Number(c.contact_count || 0) > 0)
        || (viewFilter === 'without_contacts' && Number(c.contact_count || 0) === 0)
      return matchesSearch && matchesFilter
    })
    return rows.sort((a, b) => {
      if (sortBy === 'name') return (a.name || '').localeCompare(b.name || '')
      if (sortBy === 'contacts') return Number(b.contact_count || 0) - Number(a.contact_count || 0)
      if (sortBy === 'research') {
        const rank = (c) => c.scrape_status === 'scraped' ? 1 : 0
        return rank(a) - rank(b) || (a.name || '').localeCompare(b.name || '')
      }
      return (Date.parse(b.created_at || '') || 0) - (Date.parse(a.created_at || '') || 0)
    })
  }, [companies, q, sortBy, viewFilter])
  const filteredContacts = useMemo(() => {
    const rows = (contacts || []).filter((c) => {
      const matchesSearch = !q ||
        [c.name, c.email, c.company_name, c.role, c.affinity, c.linkedin_url]
          .some((v) => v?.toLowerCase().includes(q))
      const matchesFilter = viewFilter === 'all'
        || (viewFilter === 'active' && c.status !== 'archived')
        || (viewFilter === 'uncontacted' && Number(c.email_count || 0) === 0)
        || (viewFilter === 'replied' && Number(c.verified_reply_count || 0) > 0)
        || (viewFilter === 'archived' && c.status === 'archived')
        || (viewFilter === 'verified' && Number(c.person_verified || 0) === 1)
        || (viewFilter === 'personal_email' && (
          c.email_kind === 'personal' || Number(c.email_verified || 0) === 1))
        || (viewFilter === 'has_linkedin' && !!c.linkedin_url)
        || (viewFilter === 'linkedin_verified' && Number(c.linkedin_verified || 0) === 1)
        || (viewFilter === 'affinity' && !!c.affinity)
        || (viewFilter === 'senior' && Number(c.seniority_rank ?? 99) <= 5)
      return matchesSearch && matchesFilter
    })
    return rows.sort((a, b) => {
      if (sortBy === 'name') return (a.name || a.email || '').localeCompare(b.name || b.email || '')
      if (sortBy === 'company') {
        return (a.company_name || '').localeCompare(b.company_name || '')
          || (a.name || a.email || '').localeCompare(b.name || b.email || '')
      }
      if (sortBy === 'status') return (a.status || '').localeCompare(b.status || '')
      if (sortBy === 'seniority') {
        return Number(a.seniority_rank ?? 99) - Number(b.seniority_rank ?? 99)
          || (a.name || '').localeCompare(b.name || '')
      }
      if (sortBy === 'verified') {
        return Number(b.person_verified || 0) - Number(a.person_verified || 0)
          || Number(b.email_verified || 0) - Number(a.email_verified || 0)
          || (a.name || '').localeCompare(b.name || '')
      }
      return (Date.parse(b.created_at || '') || 0) - (Date.parse(a.created_at || '') || 0)
    })
  }, [contacts, q, sortBy, viewFilter])

  const activeRows = tab === 'companies' ? filteredCompanies : filteredContacts
  const pageCount = Math.max(1, Math.ceil(activeRows.length / pageSize))
  const currentPage = Math.min(page, pageCount)
  const pageStart = (currentPage - 1) * pageSize
  const pagedCompanies = filteredCompanies.slice(pageStart, pageStart + pageSize)
  const pagedContacts = filteredContacts.slice(pageStart, pageStart + pageSize)

  useEffect(() => {
    if (page > pageCount) setPage(pageCount)
  }, [page, pageCount])

  const resetView = () => {
    setPage(1)
    setSelected(new Set())
  }

  const changeTab = (nextTab) => {
    setTab(nextTab)
    setSearch('')
    setViewFilter('all')
    setSortBy('newest')
    resetView()
  }

  const changePage = (nextPage) => {
    setPage(Math.max(1, Math.min(pageCount, nextPage)))
    setSelected(new Set())
  }

  const toggleContact = (id) => {
    setSelected((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }
  const allVisibleSelected = pagedContacts.length > 0 &&
    pagedContacts.every((c) => selected.has(c.id))
  const toggleAll = () => {
    setSelected(allVisibleSelected ? new Set() : new Set(pagedContacts.map((c) => c.id)))
  }

  // Only ever act on rows the user can actually see. Selecting contacts, then
  // typing a search, must not leave hidden contacts armed for Delete/Generate.
  const visibleIds = useMemo(() => new Set(pagedContacts.map((c) => c.id)), [pagedContacts])
  const actionable = useMemo(
    () => [...selected].filter((id) => visibleIds.has(id)), [selected, visibleIds])

  const deleteSelected = async () => {
    if (!window.confirm(`Delete ${actionable.length} contact(s)? Their drafts are removed too.`)) return
    try {
      const { data } = await contactsAPI.bulkDelete(actionable)
      const protectedCount = (data.protected || []).length
      if (protectedCount > 0) {
        toast.success(`Deleted ${data.deleted}. Kept ${protectedCount} with sent email history.`)
        if (window.confirm(
          `${protectedCount} contact(s) have sent emails. Deleting them erases that ` +
          `history from your tracking too.\n\nDelete them anyway?`)) {
          const { data: forced } = await contactsAPI.bulkDelete(actionable, true)
          toast.success(`Deleted ${forced.deleted} more`)
        }
      } else {
        toast.success(`Deleted ${data.deleted} contacts`)
      }
      setSelected(new Set())
      load()
    } catch (e) { toast.error(errMessage(e)) }
  }

  const toggleArchive = async (c) => {
    const next = c.status === 'archived' ? 'new' : 'archived'
    try {
      await contactsAPI.update(c.id, { status: next })
      toast.success(next === 'archived'
        ? `${c.name || c.email} archived — history kept, no new emails will be drafted`
        : `${c.name || c.email} is active again`)
      load()
    } catch (e) { toast.error(errMessage(e)) }
  }

  const importCsv = async (file) => {
    if (!file) return
    try {
      const { data } = await contactsAPI.import(file)
      const parts = []
      if (data.duplicates) parts.push(`${data.duplicates} already in your database`)
      if (data.invalid) {
        const sample = (data.invalid_samples || []).slice(0, 2).join(', ')
        parts.push(`${data.invalid} invalid${sample ? ` (e.g. ${sample})` : ''}`)
      }
      if (data.warnings) {
        parts.push(`${data.warnings} with LinkedIn dropped (name mismatch)`)
      }
      toast.success(
        `Imported ${data.added} contact${data.added === 1 ? '' : 's'}` +
        (parts.length ? `. Skipped/noted: ${parts.join('; ')}.` : ''),
        { duration: parts.length ? 6000 : 4000 }
      )
      load()
    } catch (e) { toast.error(errMessage(e, 'Import failed')) }
  }

  const loading = companies === null

  return (
    <div className="page wide database-page">
      <div className="page-head">
        <div>
          <div className="page-title">Database</div>
          <div className="page-desc">
            Everything you've discovered and imported — {companies?.length ?? '…'} companies, {contacts?.length ?? '…'} contacts
          </div>
        </div>
        <div className="page-actions">
          <label className="btn btn-secondary btn-sm" style={{ marginBottom: 0 }}
            title="Import a Reach contacts CSV or LinkedIn Connections export">
            <Upload size={14} /> Import CSV
            <input type="file" accept=".csv" hidden
              onChange={(e) => { importCsv(e.target.files[0]); e.target.value = '' }} />
          </label>
          <Button size="sm" icon={Download} onClick={() => window.open(contactsAPI.exportUrl(), '_blank')}>
            Export
          </Button>
          {tab === 'companies' ? (
            <Button size="sm" variant="primary" icon={Plus} onClick={() => setShowAddCompany(true)}>Add company</Button>
          ) : (
            <Button size="sm" variant="primary" icon={Plus} onClick={() => setShowAddContact(true)}>Add contact</Button>
          )}
        </div>
      </div>

      <div className="database-toolbar">
        <div className="database-toolbar-group">
          <Segmented
            value={tab}
            onChange={changeTab}
            options={[
              { value: 'companies', label: 'Companies', count: companies?.length ?? 0 },
              { value: 'contacts', label: 'Contacts', count: contacts?.length ?? 0 },
            ]}
          />
          <select
            className="select compact-select"
            aria-label={`Filter ${tab}`}
            value={viewFilter}
            onChange={(e) => { setViewFilter(e.target.value); resetView() }}
          >
            <option value="all">All {tab}</option>
            {tab === 'companies' ? (
              <>
                <option value="researched">Researched</option>
                <option value="needs_research">Needs research</option>
                <option value="with_contacts">Has contacts</option>
                <option value="without_contacts">No contacts</option>
              </>
            ) : (
              <>
                <option value="active">Active</option>
                <option value="uncontacted">Not contacted</option>
                <option value="replied">Replied</option>
                <option value="archived">Archived</option>
                <option value="verified">Person verified</option>
                <option value="personal_email">Personal email</option>
                <option value="has_linkedin">Has LinkedIn</option>
                <option value="linkedin_verified">LinkedIn verified</option>
                <option value="affinity">Warm match</option>
                <option value="senior">Senior (CEO/Founder/VP)</option>
              </>
            )}
          </select>
          <select
            className="select compact-select"
            aria-label={`Sort ${tab}`}
            value={sortBy}
            onChange={(e) => { setSortBy(e.target.value); resetView() }}
          >
            <option value="newest">Newest</option>
            <option value="name">A–Z</option>
            {tab === 'companies' ? (
              <>
                <option value="research">Needs research first</option>
                <option value="contacts">Most contacts</option>
              </>
            ) : (
              <>
                <option value="company">By company</option>
                <option value="status">By status</option>
                <option value="seniority">Most senior</option>
                <option value="verified">Verified first</option>
              </>
            )}
          </select>
        </div>
        <div className="database-toolbar-group">
          {tab === 'contacts' && actionable.length > 0 && (
            <>
              <span className="small muted">{actionable.length} selected</span>
              <Button size="sm" variant="danger" icon={Trash2} onClick={deleteSelected}>Delete</Button>
              <Button size="sm" variant="primary" icon={Sparkles} onClick={() => setComposeIds(actionable)}>
                Generate emails
              </Button>
            </>
          )}
          <div className="search-wrap database-search">
            <Search size={14} />
            <input className="input" placeholder={`Search ${tab}…`} value={search}
              onChange={(e) => { setSearch(e.target.value); resetView() }} />
          </div>
        </div>
      </div>

      {loading ? (
        <div className="card"><div className="card-pad stack">
          {[...Array(6)].map((_, i) => <div key={i} className="skeleton" style={{ height: 40 }} />)}
        </div></div>
      ) : loadError ? (
        <div className="card">
          <EmptyState
            icon={AlertTriangle}
            title="Couldn't load your database"
            desc={`${loadError} Your data is still there — this is a connection problem, not data loss.`}
            action={<Button variant="primary" icon={RefreshCw} onClick={load}>Try again</Button>}
          />
        </div>
      ) : (
        <div className="database-results">
          {tab === 'companies' ? (
            <CompaniesTable
              companies={pagedCompanies}
              onOpen={async (c) => {
                try { setDrawerCompany((await companiesAPI.get(c.id)).data) }
                catch (e) { toast.error(errMessage(e)) }
              }}
              onDiscover={() => navigate('discover')}
              anyData={(companies || []).length > 0}
            />
          ) : (
            <ContactsTable
              contacts={pagedContacts}
              selected={selected}
              onToggle={toggleContact}
              onToggleAll={toggleAll}
              allSelected={allVisibleSelected}
              onCompose={(id) => setComposeIds([id])}
              onLinkedIn={setLinkedinContact}
              onArchive={toggleArchive}
              onDelete={async (c) => {
                if (!window.confirm(`Delete ${c.name || c.email}?`)) return
                try {
                  await contactsAPI.delete(c.id)
                  toast.success('Contact deleted')
                  load()
                } catch (e) {
                  // 409 = has sent history; offer an explicit second confirmation
                  if (e?.response?.status === 409 && window.confirm(`${errMessage(e)}\n\nDelete anyway?`)) {
                    try {
                      await contactsAPI.delete(c.id, true)
                      toast.success('Contact and its email history deleted')
                      load()
                    } catch (e2) { toast.error(errMessage(e2)) }
                  } else if (e?.response?.status !== 409) {
                    toast.error(errMessage(e))
                  }
                }
              }}
              onDiscover={() => navigate('discover')}
              anyData={(contacts || []).length > 0}
            />
          )}
          <PaginationBar
            page={currentPage}
            pageCount={pageCount}
            pageSize={pageSize}
            total={activeRows.length}
            onPage={changePage}
            onPageSize={(size) => { setPageSize(size); resetView() }}
          />
        </div>
      )}

      {drawerCompany && (
        <CompanyDrawer
          company={drawerCompany}
          onClose={() => setDrawerCompany(null)}
          onChanged={async () => {
            load()
            try { setDrawerCompany((await companiesAPI.get(drawerCompany.id)).data) }
            catch { setDrawerCompany(null) }
          }}
          onDeleted={() => { setDrawerCompany(null); load() }}
          onCompose={(ids) => setComposeIds(ids)}
        />
      )}
      {showAddCompany && (
        <AddCompanyModal onClose={() => setShowAddCompany(false)} onAdded={() => { setShowAddCompany(false); load() }} />
      )}
      {showAddContact && (
        <AddContactModal companies={companies || []} onClose={() => setShowAddContact(false)}
          onAdded={() => { setShowAddContact(false); load() }} />
      )}
      {composeIds && (
        <ComposeModal contactIds={composeIds} onClose={() => setComposeIds(null)}
          onDone={() => { setSelected(new Set()); load() }} />
      )}
      {linkedinContact && (
        <LinkedInMessageModal
          contact={linkedinContact}
          onClose={() => setLinkedinContact(null)}
        />
      )}
    </div>
  )
}

/* ---------- companies table ---------- */

function CompaniesTable({ companies, onOpen, onDiscover, anyData }) {
  if (companies.length === 0) {
    return (
      <div className="card">
        <EmptyState
          icon={Building2}
          title={anyData ? 'No companies match your search' : 'No companies yet'}
          desc={anyData ? 'Try a different search or filter.' : 'Discover companies with AI search, or add one manually.'}
          action={!anyData && <Button variant="primary" onClick={onDiscover}>Discover companies</Button>}
        />
      </div>
    )
  }
  return (
    <div className="card database-table-card">
      <div className="database-table-scroll">
      <table className="table database-table companies-table">
        <thead>
          <tr>
            <th>Company</th><th>Industry</th><th>Research</th>
            <th>Contacts</th><th>Sent</th><th>Replies</th>
          </tr>
        </thead>
        <tbody>
          {companies.map((c) => {
            const st = SCRAPE_STATUS[c.scrape_status] || SCRAPE_STATUS.pending
            return (
              <tr key={c.id} className="clickable" onClick={() => onOpen(c)}>
                <td>
                  <div className="row" style={{ gap: 10 }}>
                    <div className="company-favicon compact-favicon">
                      {initials(c.name)}
                    </div>
                    <div>
                      <div className="td-main">{c.name}</div>
                      <div className="td-sub">{c.domain || '—'}</div>
                    </div>
                  </div>
                </td>
                <td className="td-dim truncate-cell">{c.industry || '—'}</td>
                <td><Chip tone={st.tone}>{st.label}</Chip></td>
                <td>{c.contact_count}</td>
                <td>{c.sent_count}</td>
                <td>
                  {c.reply_count > 0
                    ? <Chip tone="green">{c.reply_count}</Chip>
                    : c.unverified_reply_count > 0
                      // Flagged by the older reply check, never confirmed — an
                      // amber "?" instead of a green count that isn't backed up.
                      ? <Chip tone="amber" title="Flagged by an older reply check and not yet verified against Gmail">
                        {c.unverified_reply_count}?
                      </Chip>
                      : <span className="muted">0</span>}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      </div>
    </div>
  )
}

/* ---------- contacts table ---------- */

function ContactsTable({ contacts, selected, onToggle, onToggleAll, allSelected, onCompose, onLinkedIn, onDelete, onArchive, onDiscover, anyData }) {
  if (contacts.length === 0) {
    return (
      <div className="card">
        <EmptyState
          icon={Users}
          title={anyData ? 'No contacts match your search' : 'No contacts yet'}
          desc={anyData ? 'Try a different search or filter.' : 'Contacts appear when research finds an email or a LinkedIn profile on the company site.'}
          action={!anyData && <Button variant="primary" onClick={onDiscover}>Discover companies</Button>}
        />
      </div>
    )
  }
  return (
    <div className="card database-table-card">
      <div className="database-table-scroll">
      <table className="table database-table contacts-table">
        <thead>
          <tr>
            <th style={{ width: 34 }}>
              <input type="checkbox" className="checkbox" checked={allSelected} onChange={onToggleAll} />
            </th>
            <th>Contact</th><th>Company</th><th>Role</th><th>Verified</th><th>Status</th>
            <th style={{ width: 112 }}></th>
          </tr>
        </thead>
        <tbody>
          {contacts.map((c) => {
            const st = contactStatusMeta(c)
            const verifiedBits = []
            if (Number(c.person_verified || 0) === 1) verifiedBits.push('Person')
            else {
              if (Number(c.email_verified || 0) === 1) verifiedBits.push('Email')
              if (Number(c.linkedin_verified || 0) === 1) verifiedBits.push('LinkedIn')
            }
            return (
              <tr key={c.id} className={selected.has(c.id) ? 'selected' : ''}>
                <td>
                  <input type="checkbox" className="checkbox" checked={selected.has(c.id)}
                    onChange={() => onToggle(c.id)} />
                </td>
                <td>
                  <div className="td-main">
                    {c.name || <span className="muted">Unnamed</span>}
                    {c.notes && (
                      <span title={c.notes} style={{ marginLeft: 6, verticalAlign: -2 }}>
                        <AlertTriangle size={12} style={{ color: 'var(--amber)' }} />
                      </span>
                    )}
                  </div>
                  <div className="td-sub mono">{c.email || 'no email'}</div>
                  {c.linkedin_url && (
                    <div className="td-sub">
                      <a href={c.linkedin_url} target="_blank" rel="noreferrer"
                        onClick={(e) => e.stopPropagation()}>
                        LinkedIn
                      </a>
                      {Number(c.linkedin_verified || 0) === 1 ? ' · matched' : ''}
                    </div>
                  )}
                  {c.affinity && <div className="td-sub warm-match">{c.affinity}</div>}
                </td>
                <td className="td-dim truncate-cell">{c.company_name || '—'}</td>
                <td className="td-dim truncate-cell">{c.role || '—'}</td>
                <td>
                  {verifiedBits.length > 0
                    ? <Chip tone="green">{verifiedBits.join(' · ')}</Chip>
                    : c.email_kind === 'generic'
                      ? <Chip tone="amber">Company inbox</Chip>
                      : <span className="muted">—</span>}
                </td>
                <td><Chip tone={st.tone} title={st.title}>{st.label}</Chip></td>
                <td>
                  <div className="row" style={{ gap: 2, justifyContent: 'flex-end' }}>
                    {c.linkedin_url && (
                      <button className="icon-btn linkedin-action" title="Draft LinkedIn message"
                        onClick={() => onLinkedIn(c)}>
                        <MessageSquare size={14} />
                      </button>
                    )}
                    <button className="icon-btn" title={c.email ? 'Generate email' : 'No email address found'}
                      disabled={!c.email} onClick={() => onCompose(c.id)}>
                      <Sparkles size={14} />
                    </button>
                    <button className="icon-btn"
                      title={c.status === 'archived' ? 'Unarchive' : 'Archive (keeps history, stops outreach)'}
                      onClick={() => onArchive(c)}>
                      {c.status === 'archived' ? <ArchiveRestore size={14} /> : <Archive size={14} />}
                    </button>
                    <button className="icon-btn danger" title="Delete" onClick={() => onDelete(c)}>
                      <Trash2 size={14} />
                    </button>
                  </div>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
      </div>
    </div>
  )
}

function PaginationBar({ page, pageCount, pageSize, total, onPage, onPageSize }) {
  const start = total === 0 ? 0 : (page - 1) * pageSize + 1
  const end = Math.min(total, page * pageSize)
  return (
    <div className="database-pager">
      <span>{start}–{end} of {total}</span>
      <div className="database-pager-controls">
        <label className="database-page-size">
          Rows
          <select
            className="select compact-select"
            aria-label="Rows per page"
            value={pageSize}
            onChange={(e) => onPageSize(Number(e.target.value))}
          >
            {PAGE_SIZE_OPTIONS.map((size) => (
              <option key={size} value={size}>{size}</option>
            ))}
          </select>
        </label>
        <span>Page {page} of {pageCount}</span>
        <button className="icon-btn" aria-label="Previous page"
          disabled={page <= 1} onClick={() => onPage(page - 1)}>
          <ChevronLeft size={15} />
        </button>
        <button className="icon-btn" aria-label="Next page"
          disabled={page >= pageCount} onClick={() => onPage(page + 1)}>
          <ChevronRight size={15} />
        </button>
      </div>
    </div>
  )
}

/* ---------- company drawer ---------- */

function CompanyDrawer({ company, onClose, onChanged, onDeleted, onCompose }) {
  const [enriching, setEnriching] = useState(false)
  const refreshTimer = useRef(null)

  // Cancel the pending refresh if the drawer closes, or it would re-open the
  // drawer on top of whatever the user moved on to.
  useEffect(() => () => clearTimeout(refreshTimer.current), [])

  const enrich = async () => {
    setEnriching(true)
    try {
      await companiesAPI.enrich(company.id)
      toast.success('Research started — this takes about 30 seconds')
      clearTimeout(refreshTimer.current)
      refreshTimer.current = setTimeout(onChanged, 20000)
    } catch (e) { toast.error(errMessage(e)) }
    finally { setEnriching(false) }
  }

  const del = async () => {
    if (!window.confirm(`Delete ${company.name} and all its contacts + emails?`)) return
    try { await companiesAPI.delete(company.id); toast.success('Company deleted'); onDeleted() }
    catch (e) {
      // 409 = the cascade would erase delivered emails (and the reply history
      // that stops those people being emailed twice). Name the count and make
      // the user confirm, the way the contact delete does.
      if (e?.response?.status === 409 && window.confirm(`${errMessage(e)}\n\nDelete anyway?`)) {
        try {
          await companiesAPI.delete(company.id, true)
          toast.success('Company and its email history deleted')
          onDeleted()
        } catch (e2) { toast.error(errMessage(e2)) }
      } else if (e?.response?.status !== 409) {
        toast.error(errMessage(e))
      }
    }
  }

  const st = SCRAPE_STATUS[company.scrape_status] || SCRAPE_STATUS.pending
  const contactIds = (company.contacts || []).filter((c) => c.email).map((c) => c.id)

  return (
    <Drawer
      title={company.name}
      subtitle={
        <span className="row" style={{ gap: 8 }}>
          {company.url && (
            <a href={company.url} target="_blank" rel="noreferrer" className="row" style={{ gap: 4 }}>
              <Globe size={11} /> {company.domain || company.url} <ExternalLink size={10} />
            </a>
          )}
          <Chip tone={st.tone}>{st.label}</Chip>
        </span>
      }
      onClose={onClose}
      actions={
        <>
          <Button size="sm" icon={RefreshCw} onClick={enrich} disabled={enriching}>
            {company.scrape_status === 'scraped' ? 'Re-research' : 'Research'}
          </Button>
          <button className="icon-btn danger" title="Delete company" onClick={del}><Trash2 size={15} /></button>
        </>
      }
    >
      {Array.isArray(company.scrape_warnings) && company.scrape_warnings.length > 0 && (
        <div className="card card-pad" style={{ borderColor: 'var(--amber)' }}>
          <div className="row" style={{ gap: 6, marginBottom: 6 }}>
            <AlertTriangle size={14} style={{ color: 'var(--amber)' }} />
            <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Contact conflicts
            </div>
          </div>
          <div className="stack" style={{ gap: 4 }}>
            {company.scrape_warnings.map((w) => (
              <div key={w} className="small">{w}</div>
            ))}
          </div>
        </div>
      )}
      {company.summary ? (
        <div className="stack" style={{ gap: 10 }}>
          <Info label="What they do" value={company.summary} />
          <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
            {company.industry && <Chip tone="violet">{company.industry}</Chip>}
            {company.location && <Chip tone="gray">{company.location}</Chip>}
          </div>
          {company.product && <Info label="Product" value={company.product} />}
          {company.hook && <Info label="Personalization hook" value={company.hook} />}
          {company.recent_news && <Info label="Recent news" value={company.recent_news} />}
          <div className="card card-pad" style={{ background: 'var(--surface-hover)' }}>
            <div className="row-between" style={{ gap: 8 }}>
              <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Research coverage
              </div>
              <Chip tone={company.research_quality === 'high' ? 'green'
                : company.research_quality === 'medium' ? 'sky' : 'amber'}>
                {company.research_quality || 'legacy'}
              </Chip>
            </div>
            <div className="small" style={{ marginTop: 6 }}>
              {company.pages_scraped || 0} page{company.pages_scraped === 1 ? '' : 's'} read
              {company.pages_attempted
                ? ` from ${company.pages_attempted} attempted`
                : ''}
            </div>
            {Array.isArray(company.research_sources) && company.research_sources.length > 0 && (
              <div className="stack" style={{ gap: 3, marginTop: 7 }}>
                {company.research_sources.slice(0, 8).map((source) => (
                  <a key={source} href={source} target="_blank" rel="noreferrer"
                    className="tiny row" style={{ gap: 4, overflowWrap: 'anywhere' }}>
                    <ExternalLink size={10} /> {source}
                  </a>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="card card-pad small muted">
          Not researched yet. Hit <b>Research</b> to scrape their site for a description, hooks, and contact emails.
        </div>
      )}

      {/* The model's guess at why this matched the search. Deliberately kept
          out of the research block and out of emails — it is not evidence. */}
      {company.discovery_note && (
        <div className="card card-pad" style={{ background: 'var(--surface-hover)' }}>
          <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>
            Why the search matched
          </div>
          <div className="small" style={{ lineHeight: 1.55 }}>{company.discovery_note}</div>
          <div className="tiny" style={{ marginTop: 6, color: 'var(--text-3)' }}>
            Unverified — from the AI that suggested this company, not from their
            site. Never quoted in your emails.
          </div>
        </div>
      )}

      <div>
        <div className="row-between mb-16" style={{ marginBottom: 8 }}>
          <div style={{ fontWeight: 650, fontSize: 13.5 }}>
            Contacts ({(company.contacts || []).length})
          </div>
          {contactIds.length > 0 && (
            <Button size="sm" variant="primary" icon={Sparkles} onClick={() => onCompose(contactIds)}>
              Generate for all
            </Button>
          )}
        </div>
        {(company.contacts || []).length === 0 ? (
          <div className="small muted">No contacts. Research the company to find emails, or add one from the Contacts tab.</div>
        ) : (
          <div className="stack" style={{ gap: 8 }}>
            {company.contacts.map((c) => {
              const cst = contactStatusMeta(c)
              return (
                <div key={c.id} className="card card-pad row-between" style={{ padding: '10px 14px' }}>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: 13 }}>{c.name || 'Unnamed'}</div>
                    <div className="tiny mono">{c.email}</div>
                    <div className="row" style={{ gap: 5, marginTop: 2 }}>
                      {c.role && <div className="tiny">{c.role}</div>}
                      {c.notes?.startsWith('Same-school match:') && (
                        <Chip tone="violet">Penn affinity</Chip>
                      )}
                    </div>
                    {c.notes && (
                      <div className="tiny row" style={{ gap: 4, color: 'var(--amber)', marginTop: 2 }}>
                        <AlertTriangle size={11} /> {c.notes}
                      </div>
                    )}
                  </div>
                  <div className="row">
                    <Chip tone={cst.tone} title={cst.title}>{cst.label}</Chip>
                    <button className="icon-btn" title="Generate email" onClick={() => onCompose([c.id])}>
                      <Sparkles size={14} />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {(company.emails || []).length > 0 && (
        <div>
          <div style={{ fontWeight: 650, fontSize: 13.5, marginBottom: 8 }}>
            Email history ({company.emails.length})
          </div>
          <div className="stack" style={{ gap: 8 }}>
            {company.emails.slice(0, 6).map((e) => {
              const tm = EMAIL_TYPE_META[e.email_type] || EMAIL_TYPE_META.custom
              return (
                <div key={e.id} className="card card-pad" style={{ padding: '10px 14px' }}>
                  <div className="row-between">
                    <div style={{ fontWeight: 600, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {e.subject}
                    </div>
                    <Chip tone={tm.tone}>{tm.label}</Chip>
                  </div>
                  <div className="tiny mt-8" style={{ marginTop: 4 }}>
                    to {e.contact_email} · {e.status}
                    {e.has_response
                      ? (e.response_verified_at ? ' · replied ✓' : ' · replied (unverified)')
                      : ''} · {timeAgo(e.created_at)}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </Drawer>
  )
}

function Info({ label, value }) {
  return (
    <div>
      <div className="tiny" style={{ fontWeight: 650, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 3 }}>
        {label}
      </div>
      <div className="small" style={{ lineHeight: 1.55 }}>{value}</div>
    </div>
  )
}

/* ---------- add modals ---------- */

function AddCompanyModal({ onClose, onAdded }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [saving, setSaving] = useState(false)

  const save = async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      const { data } = await companiesAPI.create(name.trim(), url.trim() || null)
      toast.success(data.research_started
        ? `${name.trim()} added — researching it now`
        : `${name.trim()} added. ${data.research_skipped_reason || 'Research is rate-limited'} — use Research in the company drawer shortly.`,
        { duration: data.research_started ? 4000 : 6000 })
      onAdded()
    } catch (e) { toast.error(errMessage(e)) }
    finally { setSaving(false) }
  }

  return (
    <Modal title="Add company" onClose={onClose}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={save} disabled={saving || !name.trim()}>
          {saving ? 'Adding…' : 'Add & research'}
        </Button>
      </>}>
      <div className="field">
        <div className="field-label">Company name</div>
        <input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && save()} placeholder="e.g. Vercel" />
      </div>
      <div className="field">
        <div className="field-label">Website (optional)</div>
        <input className="input" value={url} onChange={(e) => setUrl(e.target.value)}
          placeholder="https:// — leave empty and we'll find it" />
        <div className="field-hint">We'll scrape the site for a description, personalization hooks, and contact emails.</div>
      </div>
    </Modal>
  )
}

function AddContactModal({ companies, onClose, onAdded }) {
  const [form, setForm] = useState({ name: '', email: '', linkedin_url: '', role: '', company_id: '', company_name: '' })
  const [saving, setSaving] = useState(false)
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }))

  const save = async () => {
    if (!form.email.trim() && !form.linkedin_url.trim() && !form.name.trim()) {
      toast.error('A contact needs a name, email, or LinkedIn profile')
      return
    }
    setSaving(true)
    try {
      const { data: created } = await contactsAPI.create({
        name: form.name.trim(),
        email: form.email.trim(),
        linkedin_url: form.linkedin_url.trim() || null,
        role: form.role.trim() || null,
        company_id: form.company_id || null,
        company_name: form.company_id ? null : (form.company_name.trim() || null),
      })
      if (created?.ingest_warning === 'linkedin_does_not_match_name') {
        toast.success('Contact added — LinkedIn URL dropped (did not match name)')
      } else {
        toast.success('Contact added')
      }
      onAdded()
    } catch (e) { toast.error(errMessage(e)) }
    finally { setSaving(false) }
  }

  return (
    <Modal title="Add contact" onClose={onClose}
      footer={<>
        <Button variant="ghost" onClick={onClose}>Cancel</Button>
        <Button variant="primary" onClick={save} disabled={saving}>{saving ? 'Adding…' : 'Add contact'}</Button>
      </>}>
      <div className="row" style={{ gap: 10 }}>
        <div className="field grow">
          <div className="field-label">Name</div>
          <input className="input" autoFocus value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="Jane Smith" />
        </div>
        <div className="field grow">
          <div className="field-label">Role</div>
          <input className="input" value={form.role} onChange={(e) => set('role', e.target.value)} placeholder="CTO" />
        </div>
      </div>
      <div className="field">
        <div className="field-label">Email</div>
        <input className="input" type="email" value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="jane@company.com" />
      </div>
      <div className="field">
        <div className="field-label">LinkedIn profile</div>
        <input className="input" type="url" value={form.linkedin_url}
          onChange={(e) => set('linkedin_url', e.target.value)}
          placeholder="https://www.linkedin.com/in/..." />
      </div>
      <div className="field">
        <div className="field-label">Company</div>
        <select className="select" value={form.company_id} onChange={(e) => set('company_id', e.target.value)}>
          <option value="">— New or none —</option>
          {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      {!form.company_id && (
        <div className="field">
          <div className="field-label">New company name (optional)</div>
          <input className="input" value={form.company_name} onChange={(e) => set('company_name', e.target.value)}
            placeholder="Creates the company and researches it later" />
        </div>
      )}
    </Modal>
  )
}

function LinkedInMessageModal({ contact, onClose }) {
  const [message, setMessage] = useState('')
  const [instructions, setInstructions] = useState('')
  const [loading, setLoading] = useState(true)
  const [copied, setCopied] = useState(false)

  const generate = useCallback(async () => {
    setLoading(true)
    try {
      const { data } = await contactsAPI.linkedinDraft(
        contact.id, instructions.trim() || null)
      setMessage(data.message)
    } catch (e) {
      toast.error(errMessage(e, 'Could not draft LinkedIn message'))
    } finally {
      setLoading(false)
    }
  }, [contact.id, instructions])

  useEffect(() => { generate() }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message)
      setCopied(true)
      toast.success('Message copied')
      window.setTimeout(() => setCopied(false), 1600)
    } catch {
      toast.error('Copy failed — select the message and copy it manually')
    }
  }

  const openProfile = () => {
    window.open(contact.linkedin_url, '_blank', 'noopener,noreferrer')
  }

  return (
    <Modal title={`Message ${contact.name || 'contact'} on LinkedIn`} onClose={onClose}
      footer={<>
        <Button icon={copied ? Check : Copy} onClick={copy} disabled={!message}>
          {copied ? 'Copied' : 'Copy message'}
        </Button>
        <Button variant="primary" icon={ExternalLink} onClick={openProfile}>
          Open profile to send
        </Button>
      </>}>
      <div className="linkedin-manual-note">
        This prepares the message and opens the verified profile. You review it and click Send in LinkedIn.
      </div>
      <div className="field">
        <div className="field-label">Optional direction</div>
        <div className="row" style={{ gap: 8 }}>
          <input className="input grow" value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="e.g. Ask about engineering internships" />
          <Button onClick={generate} disabled={loading}>
            {loading ? 'Drafting…' : 'Redraft'}
          </Button>
        </div>
      </div>
      <div className="field">
        <div className="field-label">Message</div>
        <textarea className="textarea linkedin-draft" value={message}
          onChange={(e) => setMessage(e.target.value)}
          placeholder={loading ? 'Drafting…' : 'Write a message…'} />
        <div className="field-hint">{message.length}/700 characters</div>
      </div>
      {(contact.affinity || contact.source_url) && (
        <div className="linkedin-evidence">
          {contact.affinity && <div><b>Warm match:</b> {contact.affinity}</div>}
          {contact.source_url && (
            <div><b>Found on:</b>{' '}
              <a href={contact.source_url} target="_blank" rel="noreferrer">
                company source <ExternalLink size={11} />
              </a>
            </div>
          )}
        </div>
      )}
    </Modal>
  )
}
