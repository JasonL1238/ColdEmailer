#!/bin/bash

# Start script for AI Cold Emailer
# Run this to start both backend and frontend

echo "🚀 Starting AI Cold Emailer..."
echo ""

# Check if Ollama is running
if ! pgrep -x "ollama" > /dev/null; then
    echo "⚠️  Starting Ollama..."
    ollama serve > /dev/null 2>&1 &
    sleep 2
fi

# Get absolute script directory first (before any cd commands)
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Start backend
echo "📦 Starting backend server..."
cd "$SCRIPT_DIR/backend"
source venv/bin/activate
uvicorn main:app --reload --port 8000 > /tmp/coldemailer_backend.log 2>&1 &
BACKEND_PID=$!
echo "✅ Backend started (PID: $BACKEND_PID)"
echo "   API: http://localhost:8000"
echo ""

# Wait for backend to start
sleep 3

# Start frontend
echo "🎨 Starting frontend server..."
FRONTEND_DIR="$SCRIPT_DIR/frontend"
(cd "$FRONTEND_DIR" && npm run dev > /tmp/coldemailer_frontend.log 2>&1) &
FRONTEND_PID=$!
echo "✅ Frontend started (PID: $FRONTEND_PID)"
echo "   App: http://localhost:5173"
echo ""

echo "✨ Application is running!"
echo ""
echo "📝 Access the app at: http://localhost:5173"
echo ""
echo "📋 Setup Checklist:"
echo "   ✅ Backend running on port 8000"
echo "   ✅ Frontend running on port 5173"
# Check files from project root, not backend directory
if [ -f "$SCRIPT_DIR/credentials.json" ]; then
    echo "   ✅ Gmail credentials found"
else
    echo "   ⚠️  Gmail credentials missing (see GMAIL_SETUP.md)"
fi
if [ -f "$SCRIPT_DIR/resume.pdf" ] || [ -f "$SCRIPT_DIR/Current_Resume_2 12.43.21 PM.pdf" ] || [ -f "$SCRIPT_DIR/resume28.pdf" ] || [ -f "$SCRIPT_DIR/resume29.pdf" ]; then
    echo "   ✅ Resume file found"
else
    echo "   ⚠️  Resume file missing"
fi
echo ""
echo "📋 Logs:"
echo "   Backend: tail -f /tmp/coldemailer_backend.log"
echo "   Frontend: tail -f /tmp/coldemailer_frontend.log"
echo ""
echo "🛑 To stop: Press Ctrl+C or run: kill $BACKEND_PID $FRONTEND_PID"
echo ""

# Wait for user interrupt
trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
