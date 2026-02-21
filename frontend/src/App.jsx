import { useState } from 'react'
import { Toaster } from 'react-hot-toast'
import CSVManager from './CSVManager'
import EmailReview from './EmailReview'
import { sendTelemetry } from './config'
import './App.css'

sendTelemetry('App.jsx:init', 'App component loading', { windowLocation: typeof window !== 'undefined' ? window.location.href : '' })

function App() {
  sendTelemetry('App.jsx:render', 'App rendering', {})
  const [activeTab, setActiveTab] = useState('contacts')

  return (
    <div className="app app--spotify">
      <Toaster
        position="top-right"
        toastOptions={{
          success: {
            style: {
              background: '#282828',
              color: '#1db954',
              border: '1px solid #404040',
            },
            iconTheme: {
              primary: '#1db954',
              secondary: '#282828',
            },
          },
          error: {
            style: {
              background: '#282828',
              color: '#e91429',
              border: '1px solid #404040',
            },
            iconTheme: {
              primary: '#e91429',
              secondary: '#282828',
            },
          },
          duration: 4000,
        }}
      />
      <aside className="sidebar">
        <div className="sidebar-brand">
          <span className="sidebar-logo">Cold Emailer</span>
        </div>
        <nav className="sidebar-nav">
          <button
            type="button"
            className={`sidebar-nav-item ${activeTab === 'contacts' ? 'active' : ''}`}
            onClick={() => setActiveTab('contacts')}
          >
            <span className="sidebar-nav-label">Contacts</span>
          </button>
          <button
            type="button"
            className={`sidebar-nav-item ${activeTab === 'review' ? 'active' : ''}`}
            onClick={() => setActiveTab('review')}
          >
            <span className="sidebar-nav-label">Review Emails</span>
          </button>
        </nav>
      </aside>
      <main className="app-main">
        <div key={activeTab} className="app-main-content">
          {activeTab === 'contacts' && <CSVManager />}
          {activeTab === 'review' && <EmailReview />}
        </div>
      </main>
    </div>
  )
}

export default App
