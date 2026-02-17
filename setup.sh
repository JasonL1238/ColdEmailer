#!/bin/bash

echo "Setting up AI Cold Emailer..."

# Backend setup
echo "Setting up backend..."
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cd ..

# Frontend setup
echo "Setting up frontend..."
cd frontend
npm install
cd ..

echo "Setup complete!"
echo ""
echo "Next steps:"
echo "1. Install Ollama: https://ollama.ai"
echo "2. Pull a model: ollama pull llama3.2"
echo "3. Set up Gmail API credentials (see README.md)"
echo "4. Copy .env.example to .env and configure"
echo ""
echo "To run:"
echo "  Backend: cd backend && source venv/bin/activate && uvicorn main:app --reload"
echo "  Frontend: cd frontend && npm run dev"
