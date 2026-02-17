# AI Cold Emailer System

A web-based AI cold emailer that processes CSV spreadsheets, generates personalized emails using AI and company research, provides a review interface, and sends emails in batch.

## Features

- **CSV Management**: Easy-to-use interface for viewing, editing, and adding contacts
- **Company Research**: Automatic company enrichment with web scraping and AI extraction
- **AI Email Generation**: Personalized emails using local LLM (Ollama)
- **Email Review**: Review and accept/trash emails before sending
- **Batch Sending**: Send all accepted emails at once with rate limiting
- **100% Free**: Uses only free services (Ollama, Gmail API, web scraping)

## Quick Start

Everything is already set up! Just run:

```bash
./START.sh
```

Or see `FULL_SETUP.md` for complete setup instructions.

## Setup

### Prerequisites

1. **Python 3.12** (3.13 has compatibility issues)
2. **Node.js 18+**
3. **Ollama** (for local AI): https://ollama.ai
   ```bash
   ollama pull llama3.2
   ```

### Installation

**Already done!** All dependencies are installed. See `FULL_SETUP.md` for details.

### Gmail API Setup (Required for sending emails)

See `GMAIL_SETUP.md` for detailed step-by-step instructions.

**Quick steps:**
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create project → Enable Gmail API
3. Create OAuth2 credentials (Desktop app)
4. Download as `credentials.json` to project root
5. First email send will authenticate automatically

### Running the Application

1. **Start Ollama** (if not running as service)
   ```bash
   ollama serve
   ```

2. **Start Backend**
   ```bash
   cd backend
   source venv/bin/activate
   uvicorn main:app --reload --port 8000
   ```

3. **Start Frontend** (in another terminal)
   ```bash
   cd frontend
   npm run dev
   ```

4. **Open Browser**
   - Navigate to http://localhost:5173

## Usage

1. **Import Contacts**: Upload a CSV file with columns: `name`, `company`, `email`
2. **Manage Contacts**: Edit, add, or delete contacts in the CSV Manager
3. **Generate Emails**: Click "Generate Emails" to create personalized emails
4. **Review Emails**: Accept or trash each email
5. **Send Emails**: Send all accepted emails in batch

## CSV Format

Input CSV should have these columns:
- `name`: Contact's name
- `company`: Company name
- `email`: Contact's email address

## Architecture

- **Backend**: FastAPI (Python)
- **Frontend**: React + Vite
- **AI**: Ollama (local LLM)
- **Email**: Gmail API (OAuth2)
- **Scraping**: requests + BeautifulSoup + trafilatura + Playwright (fallback)

## License

MIT
