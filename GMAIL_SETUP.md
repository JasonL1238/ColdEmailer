# Gmail API Setup Guide

## Step-by-Step Instructions

### 1. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cxloud.google.com/)
2. Click on the project dropdown at the top
3. Click "New Project"
4. Enter project name: "Cold Emailer" (or any name)
5. Click "Create"

### 2. Enable Gmail API

1. In your project, go to "APIs & Services" → "Library"
2. Search for "Gmail API"
3. Click on "Gmail API"
4. Click "Enable"

### 3. Create OAuth2 Credentials

1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. If prompted, configure OAuth consent screen:
   - User Type: "External" (unless you have Google Workspace)
   - App name: "Cold Emailer"
   - User support email: jason.ye.li.7@gmail.com
   - Developer contact: jason.ye.li.7@gmail.com
   - Click "Save and Continue"
   - Scopes: Click "Add or Remove Scopes"
     - Search for "gmail.send"
     - Check "https://www.googleapis.com/auth/gmail.send"
     - Click "Update" → "Save and Continue"
   - Test users: Add jason.ye.li.7@gmail.com
   - Click "Save and Continue" → "Back to Dashboard"
4. Create OAuth Client ID:
   - Application type: "Desktop app"
   - Name: "Cold Emailer Desktop"
   - Click "Create"
5. Download credentials:
   - Click "Download JSON"
   - Save the file
   - Rename it to `credentials.json`
   - Move it to: `/Users/jasonli/Documents/GitHub/ColdEmailer/credentials.json`

### 4. First Time Authentication

1. Start the backend server
2. Try to send an email (or the app will authenticate on first email send)
3. A browser window will open
4. Sign in with your Gmail account
5. Click "Allow" to grant permissions
6. A `token.json` file will be created automatically

### 5. Verify Setup

Check that these files exist in project root:
- ✅ `credentials.json` (from Google Cloud Console)
- ✅ `token.json` (created after first authentication)

## Troubleshooting

**"Credentials file not found" error:**
- Make sure `credentials.json` is in the project root
- Check the filename is exactly `credentials.json` (not `credentials (1).json`)

**"Access denied" error:**
- Make sure you added your email as a test user in OAuth consent screen
- If app is in "Testing" mode, only test users can authenticate

**"Invalid credentials" error:**
- Delete `token.json` and re-authenticate
- Make sure OAuth consent screen is configured

**Want to use a different Gmail account?**
- Delete `token.json`
- Re-run the app and authenticate with the new account

## Security Note

- Never commit `credentials.json` or `token.json` to git
- These files are already in `.gitignore`
- Keep them secure and don't share them
