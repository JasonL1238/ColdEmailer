#!/bin/sh
# Pre-commit hook: block commits that look like secrets.
# Install: cp scripts/pre-commit-secrets.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

# Block committing known secret filenames
for f in $(git diff --cached --name-only 2>/dev/null); do
  case "$f" in
    *.env|.env|.env.*|credentials.json|token.json|*.pem|*.key|*.p12|*.pfx|*.jks) 
      echo "ERROR: Refusing to commit secret or env file: $f"
      exit 1
      ;;
  esac
done

# Block obvious secret patterns in staged diff (baseline; consider gitleaks for more)
if git diff --cached 2>/dev/null | grep -qE 'sk-[a-zA-Z0-9]{20,}'; then
  echo "ERROR: Possible API key (sk-...) in staged changes. Abort or remove."
  exit 1
fi
if git diff --cached 2>/dev/null | grep -qE 'ghp_[a-zA-Z0-9]{36}'; then
  echo "ERROR: Possible GitHub token (ghp_...) in staged changes. Abort or remove."
  exit 1
fi
if git diff --cached 2>/dev/null | grep -qE 'AKIA[0-9A-Z]{16}'; then
  echo "ERROR: Possible AWS key (AKIA...) in staged changes. Abort or remove."
  exit 1
fi
if git diff --cached 2>/dev/null | grep -qE '-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----'; then
  echo "ERROR: Possible private key in staged changes. Abort or remove."
  exit 1
fi

exit 0
