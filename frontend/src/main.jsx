import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import './index.css'

// #region agent log
fetch('http://127.0.0.1:7243/ingest/2a1d9d6c-1d59-4b37-a463-932a5a4b92a4',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({location:'main.jsx:init','message':'Frontend app initializing','data':{port:5173},timestamp:Date.now(),runId:'run1',hypothesisId:'A'})}).catch(()=>{});
// #endregion

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
