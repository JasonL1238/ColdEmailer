#!/bin/sh
# Pre-commit hook: block commits that look like secrets.
# Install: cp scripts/pre-commit-secrets.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

# Block committing known secret filenames.
# Committed-on-purpose templates are allowed first: ".env.*" otherwise matches
# .env.example, which is tracked deliberately and blocks every real commit
# that touches it.
for f in $(git diff --cached --name-only 2>/dev/null); do
  case "$f" in
    *.env.example|*.env.sample|*.env.template|.env.example|.env.sample|.env.template)
      continue
      ;;
    *.env|.env|.env.*|credentials.json|token.json|*.pem|*.key|*.p12|*.pfx|*.jks)
      echo "ERROR: Refusing to commit secret or env file: $f"
      exit 1
      ;;
  esac
done

# Block obvious secret patterns in the staged diff.
# Patterns are passed with -e: a pattern starting with "-" (e.g. the PEM
# header) is otherwise parsed as grep options and the check silently fails,
# which is worse than having no check at all.
staged=$(git diff --cached 2>/dev/null)

check() {
  if printf '%s' "$staged" | grep -qE -e "$1"; then
    echo "ERROR: $2 in staged changes. Abort or remove."
    exit 1
  fi
}

check 'sk-[a-zA-Z0-9]{20,}'                              'Possible API key (sk-...)'
check 'ghp_[a-zA-Z0-9]{36}'                              'Possible GitHub token (ghp_...)'
check 'AKIA[0-9A-Z]{16}'                                 'Possible AWS key (AKIA...)'
check 'AIza[0-9A-Za-z_-]{35}'                            'Possible Google API key (AIza...)'
check '-----BEGIN (RSA|EC|OPENSSH|PGP|DSA)? ?PRIVATE KEY' 'Possible private key'
check '"?refresh_token"?\s*[:=]\s*"1//'                   'Possible Google OAuth refresh token'

exit 0
