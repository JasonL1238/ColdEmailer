#!/bin/bash
# Start Reach — backend (FastAPI :8000) + frontend (Vite :5173)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_LOG=/tmp/reach_backend.log
FRONTEND_LOG=/tmp/reach_frontend.log

echo "🚀 Starting Reach…"
echo ""

# --- backend ---
cd "$SCRIPT_DIR/backend"
if [ ! -d venv ]; then
    echo "📦 Creating Python venv (first run)…"
    python3.12 -m venv venv 2>/dev/null || python3 -m venv venv
    ./venv/bin/pip install -q --upgrade pip
    ./venv/bin/pip install -q -r requirements.txt
fi
# Bind IPv4 explicitly — macOS localhost can be ::1 while the Vite proxy
# targets 127.0.0.1, which would otherwise make every /api call fail.
./venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 > "$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

# --- frontend ---
cd "$SCRIPT_DIR/frontend"
if [ ! -d node_modules ]; then
    echo "📦 Installing frontend dependencies (first run)…"
    npm install --silent
fi
npm run dev -- --host 127.0.0.1 --port 5173 > "$FRONTEND_LOG" 2>&1 &
FRONTEND_PID=$!

# --- wait for backend to answer ---
printf "⏳ Waiting for backend"
for _ in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8000/ > /dev/null 2>&1; then break; fi
    printf "."
    sleep 1
done
echo ""

echo ""
echo "✨ Reach is running"
echo "   App:      http://127.0.0.1:5173"
echo "   API docs: http://127.0.0.1:8000/docs"
echo ""

# --- setup checklist ---
if [ -f "$SCRIPT_DIR/credentials.json" ]; then
    echo "   ✅ Gmail credentials found"
else
    echo "   ⚠️  No credentials.json — sending is disabled until you add it (see README)"
fi
if grep -qE '^(GOOGLE_AI_API_KEY|OPENAI_API_KEY|OPENROUTER_API_KEY)=.+' "$SCRIPT_DIR/.env" 2>/dev/null; then
    echo "   ✅ AI provider key found"
else
    echo "   ⚠️  No AI key in .env — discovery and AI writing are disabled (template mode only)"
fi
echo ""
echo "   Logs: tail -f $BACKEND_LOG   |   tail -f $FRONTEND_LOG"
echo "   Stop: Ctrl+C"
echo ""

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
