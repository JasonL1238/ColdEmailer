# Current Setup Status

## ❌ NOT Ready to Run Yet

You still need to set up a few things:

### Required (to run the app):

1. **Python 3.12** - You have Python 3.13 which is incompatible
   ```bash
   brew install python@3.12
   ```

2. **Backend Dependencies** - Need to install after Python 3.12
   ```bash
   cd backend
   python3.12 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Frontend Dependencies** - Need to install
   ```bash
   cd frontend
   npm install
   ```

4. **Ollama Model** - Need to pull
   ```bash
   ollama pull llama3.2
   ```

5. **Environment File** - Need to create
   ```bash
   # Copy from example or create manually
   ```

### Optional (for full functionality):

- **Gmail API credentials** - Only needed when you want to send emails
- **Resume file** - Only needed for email attachments

## Quick Setup Commands

Run these in order:

```bash
# 1. Install Python 3.12
brew install python@3.12

# 2. Set up backend
cd /Users/jasonli/Documents/GitHub/ColdEmailer/backend
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Set up frontend
cd /Users/jasonli/Documents/GitHub/ColdEmailer/frontend
npm install

# 4. Pull Ollama model
ollama pull llama3.2

# 5. Create .env file
cd /Users/jasonli/Documents/GitHub/ColdEmailer
cat > .env << 'EOF'
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

After this, you can run the app! See `HOW_TO_RUN.md` for running instructions.
