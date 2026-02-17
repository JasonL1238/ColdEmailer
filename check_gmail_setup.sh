#!/bin/bash

# Check Gmail API setup status

echo "🔍 Checking Gmail API Setup..."
echo ""

cd "$(dirname "$0")"

if [ -f "credentials.json" ]; then
    echo "✅ credentials.json found"
    
    # Check if it's valid JSON
    if python3 -m json.tool credentials.json > /dev/null 2>&1; then
        echo "✅ credentials.json is valid JSON"
        
        # Check for required fields
        if grep -q "client_id" credentials.json && grep -q "client_secret" credentials.json; then
            echo "✅ Contains required OAuth2 fields"
        else
            echo "⚠️  Missing required OAuth2 fields"
        fi
    else
        echo "❌ credentials.json is not valid JSON"
    fi
else
    echo "❌ credentials.json NOT FOUND"
    echo ""
    echo "📝 To set up:"
    echo "   1. Go to https://console.cloud.google.com/"
    echo "   2. Create project → Enable Gmail API"
    echo "   3. Create OAuth2 credentials (Desktop app)"
    echo "   4. Download as credentials.json"
    echo "   5. Place in: $(pwd)/credentials.json"
    echo ""
    echo "   See QUICK_GMAIL_SETUP.md for detailed steps"
fi

echo ""

if [ -f "token.json" ]; then
    echo "✅ token.json found (already authenticated)"
else
    echo "⚠️  token.json not found (will be created on first email send)"
fi

echo ""
echo "📚 Documentation:"
echo "   - QUICK_GMAIL_SETUP.md - Fast setup guide"
echo "   - GMAIL_SETUP.md - Detailed setup guide"
