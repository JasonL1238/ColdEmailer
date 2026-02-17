# How to Run the Application

## ⚠️ Important: Python Version Issue

You're using Python 3.13, which is too new. Some packages don't have wheels built for it yet.

**Solution: Use Python 3.11 or 3.12**

### Option 1: Install Python 3.12 (Recommended)

```bash
# Install Python 3.12 using Homebrew
brew install python@3.12

# Create venv with Python 3.12
cd backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Option 2: Use pyenv (if you have it)

```bash
pyenv install 3.12.0
pyenv local 3.12.0
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Quick Start (After Python 3.12 Setup)

### 1. Install Backend Dependencies
```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Install Frontend Dependencies
```bash
cd frontend
npm install
```

### 3. Make sure Ollama model is installed
```bash
ollama pull llama3.2
```

### 4. Run the Application

**Open 2 terminal windows:**

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

### 5. Open Browser
Go to: **http://localhost:5173**

## First Steps

1. **Add your resume**: Place `resume.pdf` in project root, update `RESUME_PATH` in `.env`

2. **Set up Gmail API** (for sending):
   - Go to https://console.cloud.google.com/
   - Create project → Enable Gmail API → Create OAuth2 credentials
   - Download as `credentials.json` in project root

3. **Upload contacts**: Use "Contacts" tab → "Upload CSV"

4. **Configure**: Go to "Review Emails" → "⚙️ Settings" (your name/email are pre-filled)

## Troubleshooting

**Backend won't start?**
- Make sure you're using Python 3.11 or 3.12
- Activate venv: `source venv/bin/activate`
- Check: `python --version` should show 3.11 or 3.12

**Port already in use?**
- Kill process: `lsof -ti:8000 | xargs kill` (backend)
- Kill process: `lsof -ti:5173 | xargs kill` (frontend)

**Ollama errors?**
- Check: `ollama list` (should show llama3.2)
- If not: `ollama pull llama3.2`
