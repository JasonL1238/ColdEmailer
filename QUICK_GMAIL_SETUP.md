# Quick Gmail API Setup (5 minutes)

I can't do this automatically, but here's the fastest way:

## 🚀 Quick Steps

### 1. Go to Google Cloud Console
Open: https://console.cloud.google.com/

### 2. Create Project (if you don't have one)
- Click project dropdown (top left)
- Click "New Project"
- Name: "Cold Emailer"
- Click "Create"

### 3. Enable Gmail API
- In the search bar, type "Gmail API"
- Click "Gmail API"
- Click "Enable" (blue button)

### 4. Create OAuth Credentials
- Click "Credentials" in left sidebar
- Click "+ CREATE CREDENTIALS" → "OAuth client ID"
- If asked about consent screen:
  - User Type: "External"
  - App name: "Cold Emailer"
  - Your email: jason.ye.li.7@gmail.com
  - Click "Save and Continue"
  - Scopes: Click "Add or Remove Scopes"
    - Search "gmail.send"
    - Check it
    - Click "Update" → "Save and Continue"
  - Test users: Add jason.ye.li.7@gmail.com
  - Click "Save and Continue" → "Back to Dashboard"
- Application type: "Desktop app"
- Name: "Cold Emailer"
- Click "Create"
- Click "Download JSON" (downloads credentials file)

### 5. Move Credentials File
```bash
# Move the downloaded file to project root
# Rename it to: credentials.json
mv ~/Downloads/*.json /Users/jasonli/Documents/GitHub/ColdEmailer/credentials.json
```

Or manually:
- Rename downloaded file to `credentials.json`
- Move to: `/Users/jasonli/Documents/GitHub/ColdEmailer/`

### 6. Done!
The app will authenticate automatically on first email send.

## ✅ Verify Setup

Run this to check:
```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer
test -f credentials.json && echo "✅ Ready!" || echo "❌ credentials.json missing"
```

## 🎯 That's It!

Once `credentials.json` is in place, the app will handle authentication automatically when you try to send your first email.
