# Quick Start Guide

## 🚀 Run the Application

### Option 1: Use the Start Script (Easiest)

```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer
./START.sh
```

This will:
- Start Ollama (if not running)
- Start the backend server on port 8000
- Start the frontend server on port 5173
- Show you the URLs and status

**Access the app:** http://localhost:5173

**To stop:** Press `Ctrl+C` in the terminal

---

### Option 2: Manual Start (Two Terminals)

**Terminal 1 - Backend:**
```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer/backend
source venv/bin/activate
uvicorn main:app --reload --port 8000
```

**Terminal 2 - Frontend:**
```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer/frontend
npm run dev
```

**Access the app:** http://localhost:5173

---

## ✅ Prerequisites Check

Before running, make sure:

1. **Ollama is installed and running:**
   ```bash
   ollama list  # Should show llama3.2
   # If not: ollama pull llama3.2
   ```

2. **Python 3.12 is being used:**
   ```bash
   cd backend
   source venv/bin/activate
   python --version  # Should show 3.12.x
   ```

3. **Dependencies are installed:**
   - Backend: Already installed in `backend/venv/`
   - Frontend: Already installed in `frontend/node_modules/`

---

## 📋 First Time Setup

1. **Gmail API** (for sending emails):
   - ✅ `credentials.json` already exists
   - First email send will prompt for authentication

2. **Resume file:**
   - Check `.env` file for `RESUME_PATH`
   - Should point to your resume PDF

3. **User info:**
   - Pre-filled: Name "Jason Li", Email "jason.ye.li.7@gmail.com"
   - Can be changed in Email Review → Settings

---

## 🎯 Using the App

1. **Add Contacts:**
   - Go to "Contacts" tab
   - Click "+ Add Contact" or "Upload CSV"
   - Contacts are auto-saved

2. **Generate Emails:**
   - Go to "Review Emails" tab
   - Click "Generate Emails"
   - Review and accept/trash emails

3. **Send Emails:**
   - In "Review Emails", click "Send Accepted Emails"
   - Emails are sent with your resume attached

4. **Track Responses:**
   - Go to "Contacts" → "Emailed" tab
   - See sent dates and response status
   - Follow-up reminders appear after 1 week

---

## 🐛 Troubleshooting

**Backend won't start?**
```bash
cd backend
source venv/bin/activate
python --version  # Should be 3.12
pip install -r requirements.txt  # Reinstall if needed
```

**Frontend won't start?**
```bash
cd frontend
npm install  # Reinstall if needed
npm run dev
```

**Port already in use?**
```bash
# Kill backend on port 8000
lsof -ti:8000 | xargs kill

# Kill frontend on port 5173
lsof -ti:5173 | xargs kill
```

**Ollama errors?**
```bash
ollama serve  # Start Ollama server
ollama pull llama3.2  # Download model if needed
```

---

## 📊 New Features

- ✅ **Three-section contact organization:**
  - Emailed (with tracking)
  - Emails Generated - Not Sent
  - No Emails Generated

- ✅ **Email tracking:**
  - Sent dates
  - Response detection
  - Follow-up reminders (1 week)

- ✅ **Auto-save:**
  - Contacts saved immediately when added/edited
  - No need to click "Save Changes"

- ✅ **Follow-up emails:**
  - AI-generated follow-ups
  - Easy one-click generation

---

## 🔗 URLs

- **Frontend:** http://localhost:5173
- **Backend API:** http://localhost:8000
- **API Docs:** http://localhost:8000/docs
