import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { Toaster } from 'react-hot-toast'
import {
  LayoutDashboard, Compass, Database, FileText, Mail, Microscope,
  Settings as SettingsIcon, Send, BarChart3, KanbanSquare, Layers, UserSearch,
} from 'lucide-react'
import { settingsAPI } from './api'
import Dashboard from './pages/Dashboard'
import Analytics from './pages/Analytics'
import Pipeline from './pages/Pipeline'
import Campaigns from './pages/Campaigns'
import Discover from './pages/Discover'
import DeepDive from './pages/DeepDive'
import FindPerson from './pages/FindPerson'
import DatabasePage from './pages/DatabasePage'
import Resumes from './pages/Resumes'
import Emails from './pages/Emails'
import Settings from './pages/Settings'

const AppContext = createContext(null)
export const useApp = () => useContext(AppContext)

const PAGES = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard, section: null },
  { id: 'discover', label: 'Discover', icon: Compass, section: 'Pipeline' },
  { id: 'deep-dive', label: 'Deep Dive', icon: Microscope, section: 'Pipeline' },
  { id: 'find-person', label: 'Find Person', icon: UserSearch, section: 'Pipeline' },
  { id: 'database', label: 'Database', icon: Database, section: 'Pipeline' },
  { id: 'emails', label: 'Emails', icon: Mail, section: 'Pipeline' },
  { id: 'pipeline', label: 'Pipeline', icon: KanbanSquare, section: 'Pipeline' },
  { id: 'campaigns', label: 'Campaigns', icon: Layers, section: 'Pipeline' },
  { id: 'analytics', label: 'Analytics', icon: BarChart3, section: 'Pipeline' },
  { id: 'resumes', label: 'Resumes', icon: FileText, section: 'Assets' },
  { id: 'settings', label: 'Settings', icon: SettingsIcon, section: 'Assets' },
]

function currentHashPage() {
  const h = (window.location.hash || '').replace(/^#\/?/, '').split('?')[0]
  return PAGES.some((p) => p.id === h) ? h : 'dashboard'
}

export default function App() {
  const [page, setPage] = useState(currentHashPage)
  const [settings, setSettings] = useState(null)

  const refreshSettings = useCallback(async () => {
    try {
      const { data } = await settingsAPI.get()
      setSettings({ ...data, unavailable: false })
      return true
    } catch {
      // Don't fabricate "AI not configured / Gmail not connected" — that
      // silently disables Rewrite and shows wrong status. Mark it unknown and
      // keep whatever we last knew to be true.
      setSettings((s) => s
        ? { ...s, unavailable: true }
        : { profile: {}, llm_provider: null, gmail_connected: false, unavailable: true })
      return false
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    const attempt = async (tries) => {
      const ok = await refreshSettings()
      if (!ok && tries > 0 && !cancelled) {
        setTimeout(() => attempt(tries - 1), 3000)
      }
    }
    attempt(3)
    return () => { cancelled = true }
  }, [refreshSettings])

  useEffect(() => {
    const onHash = () => setPage(currentHashPage())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const navigate = useCallback((id) => {
    window.location.hash = `/${id}`
    setPage(id)
  }, [])

  const ctx = { settings, refreshSettings, navigate }

  return (
    <AppContext.Provider value={ctx}>
      <Toaster
        position="bottom-right"
        toastOptions={{
          className: 'toast-base',
          style: {
            background: '#171b22', color: '#eceae4', borderRadius: 12,
            border: '1px solid #2a3140',
            boxShadow: '0 12px 40px rgba(0,0,0,0.45)', maxWidth: 420,
          },
          success: { iconTheme: { primary: '#b8e62e', secondary: '#0b0d10' } },
          error: { iconTheme: { primary: '#f87171', secondary: '#0b0d10' }, duration: 5000 },
        }}
      />
      <div className="shell">
        <aside className="sidebar">
          <div className="brand">
            <div className="brand-mark"><Send size={15} strokeWidth={2.4} /></div>
            <div>
              <div className="brand-name">Reach</div>
              <div className="brand-sub">cold outreach studio</div>
            </div>
          </div>
          <nav className="nav">
            {PAGES.map((p, i) => (
              <div key={p.id} style={{ display: 'contents' }}>
                {p.section && PAGES[i - 1]?.section !== p.section && (
                  <div className="nav-section">{p.section}</div>
                )}
                <button
                  className={`nav-item ${page === p.id ? 'active' : ''}`}
                  onClick={() => navigate(p.id)}
                >
                  <p.icon size={16} strokeWidth={2.1} />
                  <span>{p.label}</span>
                </button>
              </div>
            ))}
          </nav>
          <div className="nav-foot">
            {settings?.unavailable ? (
              <div title="Can't reach the backend — status unknown">
                <span className="dot warn" /> Backend unreachable
              </div>
            ) : (
              <>
                <div>
                  <span className={`dot ${settings?.llm_provider ? 'ok' : 'off'}`} />
                  AI: {settings?.llm_provider ? settings.llm_provider : 'not configured'}
                </div>
                <div>
                  <span className={`dot ${settings?.gmail_connected ? 'ok' : 'warn'}`} />
                  Gmail: {settings?.gmail_connected ? 'connected' : 'not connected'}
                </div>
              </>
            )}
          </div>
        </aside>
        <main className="main">
          {page === 'dashboard' && <Dashboard />}
          {page === 'discover' && <Discover />}
          {page === 'deep-dive' && <DeepDive />}
          {page === 'find-person' && <FindPerson />}
          {page === 'database' && <DatabasePage />}
          {page === 'emails' && <Emails />}
          {page === 'pipeline' && <Pipeline />}
          {page === 'campaigns' && <Campaigns />}
          {page === 'analytics' && <Analytics />}
          {page === 'resumes' && <Resumes />}
          {page === 'settings' && <Settings />}
        </main>
      </div>
    </AppContext.Provider>
  )
}
