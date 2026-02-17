# Quick Start Guide

## 🚀 Fast Setup (5 minutes)

### Step 1: Install Backend Dependencies
```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..
```

### Step 2: Install Frontend Dependencies
```bash
cd frontend
npm install
cd ..
```

### Step 3: Set Up Environment
```bash
# Create .env file (if it doesn't exist)
cat > .env << EOF
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
CSV_FILE_PATH=backend/data/contacts.csv
COMPANY_CACHE_PATH=backend/data/company_cache.json
MAX_EMAILS_PER_DAY=50
MAX_EMAIL_GENERATIONS_PER_MINUTE=10
MAX_COMPANY_RESEARCH_PER_MINUTE=5
EMAIL_SEND_DELAY_SECONDS=3
RESUME_PATH=resume.pdf
EOF
```

### Step 4: Pull Ollama Model (if not already done)
```bash
ollama pull llama3.2
```

### Step 5: Run the Application

**Open 3 terminal windows:**

**Terminal 1 - Backend:**
```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer/backend
source venv/bin/activate
uvicorn main:app --reload --port 8000
```
You should see: `Uvicorn running on http://127.0.0.1:8000`

**Terminal 2 - Frontend:**
```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer/frontend
npm run dev
```
You should see: `Local: http://localhost:5173`

**Terminal 3 - Ollama (if not running):**
```bash
ollama serve
```

### Step 6: Open in Browser
Go to: **http://localhost:5173**

## 📝 First Time Setup

1. **Add your resume**: Place `resume.pdf` in the project root, or update `RESUME_PATH` in `.env`

2. **Set up Gmail API** (for sending emails):
   - Go to https://console.cloud.google.com/
   - Create project → Enable Gmail API → Create OAuth2 credentials
   - Download as `credentials.json` and place in project root
   - First email send will open browser for authentication

3. **Upload contacts**: 
   - Go to "Contacts" tab
   - Click "Upload CSV" 
   - Use `backend/data/sample_contacts.csv` as template

4. **Configure settings**:
   - Go to "Review Emails" tab → Click "⚙️ Settings"
   - Your name/email are pre-filled
   - Add your background and resume path

## 🎯 Quick Commands Reference

**Start everything:**
```bash
# Terminal 1
cd backend && source venv/bin/activate && uvicorn main:app --reload

# Terminal 2  
cd frontend && npm run dev

# Terminal 3 (if needed)
ollama serve
```

**Stop everything:**
- Press `Ctrl+C` in each terminal

**Check if running:**
- Backend: http://localhost:8000 (should show JSON)
- Frontend: http://localhost:5173 (should show the app)

## ⚠️ Troubleshooting

**Backend won't start?**
- Make sure venv is activated: `source venv/bin/activate`
- Check if port 8000 is free: `lsof -i :8000`

**Frontend won't start?**
- Make sure you're in frontend directory: `cd frontend`
- Try: `rm -rf node_modules && npm install`

**Ollama errors?**
- Check if model is installed: `ollama list`
- Pull model: `ollama pull llama3.2`

**Gmail API issues?**
- Make sure `credentials.json` is in project root
- Delete `token.json` to re-authenticate
