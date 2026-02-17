# Email Limits Explained

## Why 50 Emails Per Day?

The 50 emails/day limit is a **safety feature** I added to:
- ✅ Prevent accidental mass sending
- ✅ Protect against bugs/loops that could send too many
- ✅ Give you control over your daily sending
- ✅ Keep you well within Gmail's free 500/day limit

## Gmail API Free Limits

- **Free tier**: 500 emails per day
- **Your current limit**: 50 emails per day
- **You can safely increase**: Up to 500 emails per day

## How to Change the Limit

### Option 1: Edit `.env` file

```bash
# Open .env file
nano .env
# or
open .env

# Change this line:
MAX_EMAILS_PER_DAY=50

# To whatever you want (up to 500):
MAX_EMAILS_PER_DAY=100
# or
MAX_EMAILS_PER_DAY=200
# or
MAX_EMAILS_PER_DAY=500  # Maximum free tier
```

### Option 2: Quick Command

```bash
cd /Users/jasonli/Documents/GitHub/ColdEmailer
sed -i '' 's/MAX_EMAILS_PER_DAY=.*/MAX_EMAILS_PER_DAY=100/' .env
```

## Recommended Limits

- **Conservative**: 50/day (current) - Safe for testing
- **Moderate**: 100-200/day - Good for regular use
- **Maximum**: 500/day - Gmail's free limit

## Other Rate Limits

Your app also has:
- **Email generations**: 10 per minute (prevents API spam)
- **Company research**: 5 per minute (prevents scraping spam)
- **Email sending**: 1 per 3 seconds (prevents spam filters)

These are separate from the daily limit and protect against:
- Rapid-fire API calls
- Getting blocked by websites
- Triggering spam filters

## Why These Limits Matter

1. **Spam Protection**: Sending too fast triggers Gmail spam filters
2. **API Protection**: Prevents hitting rate limits
3. **Cost Protection**: Even though it's free, limits prevent abuse
4. **Best Practices**: Professional email sending uses delays

## Summary

- **50/day is just a safety limit** - not a Gmail requirement
- **You can increase it** up to 500/day (Gmail's free limit)
- **Change in `.env` file**: `MAX_EMAILS_PER_DAY=100` (or whatever you want)
- **Restart the app** after changing

The limit is there to protect you, but you're free to adjust it!
