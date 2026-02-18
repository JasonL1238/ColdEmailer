import { useState } from 'react'
import { Toaster } from 'react-hot-toast'
import CSVManager from './CSVManager'
import EmailReview from './EmailReview'
import './App.css'

// #region agent log
fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.jsx:init','message':'App component loading','data':{windowLocation:window.location.href},timestamp:Date.now(),runId:'run1',hypothesisId:'B'})}).catch(()=>{});
// #endregion

function App() {
  // #region agent log
  fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'App.jsx:render','message':'App rendering','data':{},timestamp:Date.now(),runId:'run1',hypothesisId:'B'})}).catch(()=>{});
  // #endregion
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
