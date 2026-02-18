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
    <div className="app">
      <Toaster
        position="top-center"
        toastOptions={{
          success: {
            style: {
              background: '#ecfdf5',
              color: '#065f46',
              border: '1px solid rgba(6, 95, 70, 0.2)',
            },
            iconTheme: {
              primary: '#065f46',
              secondary: '#ecfdf5',
            },
          },
          error: {
            style: {
              background: '#fef2f2',
              color: '#991b1b',
              border: '1px solid rgba(153, 27, 27, 0.2)',
            },
            iconTheme: {
              primary: '#991b1b',
              secondary: '#fef2f2',
            },
          },
          duration: 4000,
        }}
      />
      <header className="app-header">
        <h1>AI Cold Emailer</h1>
        <nav className="nav-tabs">
          <button
            className={activeTab === 'contacts' ? 'active' : ''}
            onClick={() => setActiveTab('contacts')}
          >
            Contacts
          </button>
          <button
            className={activeTab === 'review' ? 'active' : ''}
            onClick={() => setActiveTab('review')}
          >
            Review Emails
          </button>
        </nav>
      </header>
      <main className="app-main">
        {activeTab === 'contacts' && <CSVManager />}
        {activeTab === 'review' && <EmailReview />}
      </main>
    </div>
  )
}

export default App
