# Complete Setup for Full Functionality

## ✅ What's Already Done

- ✅ Backend dependencies installed
- ✅ Frontend dependencies installed  
- ✅ Python 3.12 virtual environment
- ✅ Ollama model (llama3.2) ready
- ✅ Resume file configured
- ✅ .env file configured

## 📋 Remaining Setup (for full functionality)

### 1. Gmail API Setup (Required for sending emails)

Follow the detailed guide in `GMAIL_SETUP.md`:

**Quick steps:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create project → Enable Gmail API
3. Create OAuth2 credentials (Desktop app)
4. Download as `credentials.json`
5. Place in project root: `/Users/jasonli/Documents/GitHub/ColdEmailer/credentials.json`
6. First email send will open browser for authentication

**Detailed instructions:** See `GMAIL_SETUP.md`

### 2. Resume File (Already configured!)

Your resume is set up:
- File: `Current_Resume_2 12.43.21 PM.pdf` or `resume.pdf`
- Path configured in `.env`: `RESUME_PATH=Current_Resume_2 12.43.21 PM.pdf`
- Will be automatically attached to all emails

## 🚀 Running the Application

### Quick Start
```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer
./START.sh
```

### Manual Start (2 terminals)

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

### Open Browser
Go to: **http://localhost:5173**

## 📝 First Time Usage

1. **Upload Contacts**
   - Go to "Contacts" tab
   - Click "Upload CSV"
   - Use `backend/data/sample_contacts.csv` as template
   - Or create your own with columns: `name`, `company`, `email`

2. **Configure Settings**
   - Go to "Review Emails" tab
   - Click "⚙️ Settings"
   - Your name: "Jason Li" (pre-filled)
   - Your email: "jason.ye.li.7@gmail.com" (pre-filled)
   - Add your background/qualifications
   - Resume path is already configured

3. **Generate Emails**
   - Click "Generate Emails"
   - Wait for AI to generate personalized emails
   - Review each email

4. **Review & Send**
   - Accept emails you like (press `A` or click "Accept")
   - Trash emails you don't like (press `T` or click "Trash")
   - Click "Send All Accepted" when ready
   - **Note:** First send will open browser for Gmail authentication

## ✅ Verification Checklist

Before sending emails, verify:

- [ ] Gmail API credentials (`credentials.json`) in project root
- [ ] Resume file exists (check `.env` for `RESUME_PATH`)
- [ ] Contacts uploaded in CSV format
- [ ] Settings configured (name, email, background)
- [ ] Ollama running (model llama3.2 available)

## 🎯 What Works Without Gmail Setup

You can test everything except sending:
- ✅ Upload and manage contacts
- ✅ Generate emails with AI
- ✅ Review and accept/trash emails
- ✅ See email previews
- ❌ Send emails (requires Gmail API)

## 🆘 Troubleshooting

**Resume not attaching?**
- Check `RESUME_PATH` in `.env` matches actual filename
- Make sure file exists in project root
- Check file permissions

**Gmail authentication fails?**
- Make sure `credentials.json` is in project root
- Delete `token.json` and re-authenticate
- Check OAuth consent screen is configured

**Can't generate emails?**
- Check Ollama is running: `ollama list`
- Verify model: `ollama pull llama3.2`
- Check backend logs for errors

## 📚 Additional Documentation

- `GMAIL_SETUP.md` - Detailed Gmail API setup
- `RESUME_SETUP.md` - Resume attachment setup
- `HOW_TO_RUN.md` - Running instructions
- `READY_TO_RUN.md` - Quick start guide
