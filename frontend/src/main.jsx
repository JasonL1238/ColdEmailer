import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'
import { sendTelemetry } from './config'
import './index.css'
import './shared.css'

sendTelemetry('main.jsx:init', 'Frontend app initializing', { port: 5173 })

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
