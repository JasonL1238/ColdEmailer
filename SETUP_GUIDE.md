# Setup Guide

## Prerequisites

1. **Python 3.9+** - Check with `python3 --version`
2. **Node.js 18+** - Check with `node --version`
3. **Ollama** - Download from https://ollama.ai

## Step 1: Install Ollama

1. Download and install Ollama from https://ollama.ai
2. Pull a model:
   ```bash
   ollama pull llama3.2
   ```
   Or use another model like `mistral` or `llama2`

## Step 2: Set Up Gmail API

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or use existing)
3. Enable the Gmail API:
   - Go to "APIs & Services" > "Library"
   - Search for "Gmail API"
   - Click "Enable"
4. Create OAuth2 credentials:
   - Go to "APIs & Services" > "Credentials"
   - Click "Create Credentials" > "OAuth client ID"
   - Choose "Desktop app" as application type
   - Download the credentials file
   - Rename it to `credentials.json` and place it in the project root
5. On first run, the app will open a browser for OAuth authentication

## Step 3: Install Dependencies

### Option A: Use Setup Script
```bash
./setup.sh
```

### Option B: Manual Setup

**Backend:**
```bash
cd backend
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**Frontend:**
```bash
cd frontend
npm install
```

## Step 4: Configure Environment

1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` if needed (defaults should work):
   - `OLLAMA_MODEL` - Change if using different model
   - `OLLAMA_BASE_URL` - Change if Ollama is on different host
   - `RESUME_PATH` - Path to your resume PDF (e.g., `resume.pdf`)
   - Rate limiting settings can be adjusted

3. **Add Your Resume File**:
   - Place your resume PDF in the project root (or specify full path)
   - Update `RESUME_PATH` in `.env` to match the file location
   - Example: If resume is at project root, set `RESUME_PATH=resume.pdf`

## Step 5: Run the Application

**Terminal 1 - Backend:**
```bash
cd backend
source venv/bin/activate  # On Windows: venv\Scripts\activate
uvicorn main:app --reload --port 8000
```

**Terminal 2 - Frontend:**
```bash
cd frontend
npm run dev
```

**Terminal 3 - Ollama (if not running as service):**
```bash
ollama serve
```

## Step 6: Access the Application

Open your browser to: http://localhost:5173

## First Time Setup

1. **Upload Contacts**: 
   - Go to "Contacts" tab
   - Click "Upload CSV" 
   - Use the sample file: `backend/data/sample_contacts.csv` as a template
   - Or create your own CSV with columns: `name`, `company`, `email`

2. **Configure Your Info**:
   - Go to "Review Emails" tab
   - Click "⚙️ Settings"
   - Your name is pre-filled as "Jason Li"
   - Your email is pre-filled as "jason.ye.li.7@gmail.com"
   - Enter your background/qualifications
   - Enter the path to your resume file (e.g., `resume.pdf`)
   - Also add `RESUME_PATH=resume.pdf` to your `.env` file
   - Click "Save"

3. **Generate Emails**:
   - Click "Generate Emails"
   - Review each email (Accept or Trash)
   - Click "Send All Accepted" when ready

## Troubleshooting

### Ollama Connection Error
- Make sure Ollama is running: `ollama serve`
- Check `OLLAMA_BASE_URL` in `.env`
- Verify model is installed: `ollama list`

### Gmail Authentication Error
- Make sure `credentials.json` is in project root
- Delete `token.json` and re-authenticate
- Check OAuth2 credentials are set up correctly

### Port Already in Use
- Change ports in `.env` file
- Or kill the process using the port

### Import Errors
- Make sure virtual environment is activated
- Reinstall dependencies: `pip install -r requirements.txt`

## CSV Format

Your CSV file should have these columns:
- `name`: Contact's full name
- `company`: Company name
- `email`: Contact's email address
- `status`: (optional) pending, trashed, or sent

Example:
```csv
name,company,email,status
John Smith,OpenAI,john.smith@openai.com,pending
Sarah Johnson,Anthropic,sarah.j@anthropic.com,pending
```
