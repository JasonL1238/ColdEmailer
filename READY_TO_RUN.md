# ✅ Ready to Run!

## Setup Complete!

Everything is now installed and ready. Here's how to run:

### Option 1: Use the Start Script (Easiest)

```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer
./START.sh
```

This will start both backend and frontend automatically.

### Option 2: Manual Start (2 Terminals)

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

### Then Open Browser

Go to: **http://localhost:5173**

## What's Ready

✅ Backend dependencies installed  
✅ Frontend dependencies installed  
✅ Python 3.12 virtual environment  
✅ .env file configured  
✅ Ollama model (llama3.2) ready  

## Optional Setup (for full functionality)

- **Gmail API**: Only needed when you want to send emails
  - Get `credentials.json` from Google Cloud Console
  - Place in project root
  
- **Resume file**: Only needed for email attachments
  - Place `resume.pdf` in project root
  - Or update `RESUME_PATH` in `.env`

## First Steps After Starting

1. **Upload Contacts**: Go to "Contacts" tab → "Upload CSV"
2. **Configure Settings**: Go to "Review Emails" → "⚙️ Settings"
   - Your name/email are pre-filled
   - Add your background
3. **Generate Emails**: Click "Generate Emails"
4. **Review & Send**: Accept/trash emails, then send

Enjoy! 🚀
