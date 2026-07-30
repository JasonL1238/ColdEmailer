import os
import base64
import html
import re
from email.charset import QP, Charset
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from typing import List, Dict, Optional
import time

# One plain address only: no commas/semicolons/angle-brackets/whitespace.
# RFC 5322 treats a comma as a recipient separator, so 'a@x.com, b@y.com'
# stored as one contact email would silently send to two recipients.
_RECIPIENT_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)+")


# UTF-8 with quoted-printable bodies. The default for a utf-8 MIMEText is
# base64, whose 76-char lines are fine, but 7bit/8bit text left long
# paragraphs as single over-length lines (RFC 5322 caps a line at 998 octets)
# which some relays reject or rewrap mid-word. QP wraps safely and keeps the
# body readable in raw form.
_QP_UTF8 = Charset('utf-8')
_QP_UTF8.body_encoding = QP


def _refused_by_gmail(exc: Exception) -> bool:
    """True only when Gmail definitely rejected the message.

    A 4xx is a verdict: the request was understood and turned down, so nothing
    was queued and the draft is safe to retry. Anything else — a read timeout, a
    connection reset, a 5xx — happened at a point where Gmail may already have
    accepted and queued the message, and retrying would deliver a second copy.
    408/429 are "come back later", which does not rule out the first attempt
    having landed.
    """
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    try:
        status = int(status)
    except (TypeError, ValueError):
        return False
    return 400 <= status < 500 and status not in (408, 429)


def _sanitize_header(value: str) -> str:
    """Collapse CR/LF out of a header value.

    Subjects come from LLM output and user edits. A newline followed by
    'Bcc: someone@evil.example' would be parsed as a real header, silently
    copying the message to a third party.
    """
    return re.sub(r"[\r\n]+", " ", str(value or "")).strip()


class EmailSender:
    """Gmail API integration for sending emails"""

    SCOPES = [
        'https://www.googleapis.com/auth/gmail.send',
        'https://www.googleapis.com/auth/gmail.readonly'  # To check for replies
    ]

    def __init__(self, credentials_path: str = "credentials.json", token_path: str = "token.json", project_root: Optional[str] = None):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        self.send_delay = int(os.getenv('EMAIL_SEND_DELAY_SECONDS', 3))

    def get_thread_context(self, gmail_message_id: str) -> Dict[str, Optional[str]]:
        """Fetch what's needed to thread a reply onto an existing message.

        Returns {'message_id': <RFC 5322 Message-ID header>, 'thread_id': ...}.
        The stored gmail_message_id is Gmail's *API* id, which is not the
        Message-ID header — putting it in In-Reply-To matches nothing, so the
        header has to be read back from the message itself.
        """
        empty = {"message_id": None, "thread_id": None}
        if not gmail_message_id or not self.service:
            return empty
        try:
            msg = self.service.users().messages().get(
                userId="me", id=gmail_message_id, format="metadata",
                metadataHeaders=["Message-ID"],
            ).execute()
            headers = {h["name"].lower(): h.get("value")
                       for h in msg.get("payload", {}).get("headers", [])}
            return {"message_id": headers.get("message-id"),
                    "thread_id": msg.get("threadId")}
        except Exception as e:
            print(f"[threading] could not read thread context for {gmail_message_id}: {e}")
            return empty

    def is_connected(self) -> bool:
        """True when a saved token exists (may still need refresh on next send)."""
        return os.path.exists(self.token_path)
    
    def authenticate(self):
        """Authenticate with Gmail API"""
        creds = None
        
        # Load existing token
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)
        
        # If no valid credentials, get new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except (RefreshError, Exception) as e:
                    if "invalid_grant" in str(e).lower():
                        try:
                            if os.path.exists(self.token_path):
                                os.remove(self.token_path)
                        except OSError:
                            pass
                        raise RuntimeError(
                            "Gmail sign-in expired. The saved token was removed. "
                            "Try sending again; your browser will open to sign in to Google."
                        ) from e
                    raise
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(
                        f"Credentials file not found: {self.credentials_path}\n"
                        "Please download credentials.json from Google Cloud Console"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, self.SCOPES)
                creds = flow.run_local_server(port=0)
            
            # Save credentials for next run
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())
        
        self.service = build('gmail', 'v1', credentials=creds)
        return self.service

    def disconnect(self) -> bool:
        """Remove saved token and clear service so next send triggers OAuth. Returns True if token was removed."""
        removed = False
        if os.path.exists(self.token_path):
            try:
                os.remove(self.token_path)
                removed = True
            except OSError:
                pass
        self.service = None
        return removed

    def send_email(self, email: Dict, from_email: str, resume_path: Optional[str] = None) -> Dict[str, any]:
        """
        Send a single email via Gmail API with optional resume attachment.
        email: dict with keys id, contact_email, subject, body.
        Returns: {success: bool, message_id/thread_id or error: str, email_id}
        """
        if not self.service:
            self.authenticate()

        try:
            # Refuse anything that isn't exactly one plain address
            to_addr = str(email.get('contact_email') or '').strip()
            if not _RECIPIENT_RE.fullmatch(to_addr):
                return {
                    'success': False,
                    'error': f"Invalid recipient address: {to_addr!r}",
                    'email_id': email['id']
                }

            # Create message. Subject is header-sanitized: a newline in scraped
            # or edited text would otherwise inject extra headers (Bcc:).
            message = MIMEMultipart()
            message['to'] = to_addr
            message['subject'] = _sanitize_header(email['subject'])
            if from_email and from_email != 'me' and '@' in str(from_email):
                message['From'] = from_email

            # Thread follow-ups onto the original conversation. Without these a
            # "Re: ..." subject opens a brand-new thread with no quoted
            # context, which reads as a spam pattern to both people and filters.
            # Already an RFC Message-ID including angle brackets.
            in_reply_to = email.get('reply_to_message_id')
            if in_reply_to:
                bracketed = in_reply_to if in_reply_to.startswith('<') else f"<{in_reply_to}>"
                message['In-Reply-To'] = bracketed
                message['References'] = bracketed

            # Convert plain text to HTML with proper formatting
            # Replace newlines with <br> and wrap in proper HTML structure
            # Split body into paragraphs (double newlines) for better formatting
            paragraphs = email['body'].split('\n\n')
            html_paragraphs = []
            for para in paragraphs:
                if para.strip():
                    # Escape first: the body carries scraped company text and
                    # the user's own signature, none of which is markup. Without
                    # this, a scraped "<a href=...>" becomes a live link in the
                    # sent mail and "Jason Li <me@x.com>" silently disappears —
                    # the delivered HTML part would differ from the plain-text
                    # part the user actually reviewed.
                    para_html = html.escape(para.strip()).replace('\n', '<br>')
                    html_paragraphs.append(f'<p style="margin: 0 0 1em 0;">{para_html}</p>')
            
            html_body = '\n'.join(html_paragraphs)
            
            html_body = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{
            font-family: Arial, Helvetica, sans-serif;
            font-size: 14px;
            line-height: 1.6;
            color: #333;
            margin: 0;
            padding: 0;
            width: 100%;
            max-width: 100%;
        }}
        .email-container {{
            width: 100%;
            max-width: 100%;
            margin: 0;
            padding: 20px;
        }}
        p {{
            margin: 0 0 1em 0;
        }}
    </style>
</head>
<body>
<div class="email-container">
{html_body}
</div>
</body>
</html>"""
            
            # Plain and HTML as multipart/alternative so the client shows only
            # one (no duplicate body). Explicit quoted-printable keeps every
            # line under the RFC 5322 998-octet limit — 7bit/8bit encoding left
            # long paragraphs and base64 HTML as over-length lines, which some
            # relays reject or silently rewrap mid-word.
            alternative = MIMEMultipart('alternative')
            alternative.attach(MIMEText(email['body'], 'plain', _charset=_QP_UTF8))
            alternative.attach(MIMEText(html_body, 'html', _charset=_QP_UTF8))
            message.attach(alternative)

            # Attach resume if provided and file exists
            if resume_path and os.path.exists(resume_path):
                try:
                    with open(resume_path, 'rb') as f:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        
                        # Let the email package quote/encode the filename.
                        # Hand-built 'filename= x.pdf' left an unquoted value
                        # with a leading space, which some clients render as a
                        # missing or mis-named attachment.
                        filename = os.path.basename(resume_path)
                        part.add_header('Content-Disposition', 'attachment',
                                        filename=filename)
                        message.attach(part)
                except Exception as e:
                    print(f"Warning: Could not attach resume: {e}")
            
            # Encode message
            raw_message = base64.urlsafe_b64encode(
                message.as_bytes()
            ).decode('utf-8')
            
            # Send. threadId keeps a follow-up inside the original Gmail
            # conversation instead of starting a second one.
            send_body = {'raw': raw_message}
            if email.get('reply_to_thread_id'):
                send_body['threadId'] = email['reply_to_thread_id']
            # Only the call itself can leave delivery in doubt: everything above
            # happens before Gmail sees anything, so a failure there is
            # unambiguously "not sent".
            try:
                send_message = self.service.users().messages().send(
                    userId='me', body=send_body
                ).execute()
            except Exception as e:
                return {
                    'success': False,
                    'error': str(e),
                    # "We don't know" is not the same as "Gmail said no", and
                    # only the first one makes a retry dangerous.
                    'delivery_unknown': not _refused_by_gmail(e),
                    'email_id': email['id']
                }

            return {
                'success': True,
                'message_id': send_message.get('id'),
                'gmail_message_id': send_message.get('id'),  # For tracking responses
                'gmail_thread_id': send_message.get('threadId'),
                'email_id': email['id']
            }

        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'email_id': email['id']
            }

    def find_delivered_message(self, to_email: str, subject: str,
                               sent_after: Optional[str] = None) -> Optional[Dict]:
        """Look for the message *this attempt* left in the Sent folder.

        Reconciles an attempt whose response was lost: if Gmail did accept it,
        the message is sitting in the Sent folder and the row can be marked
        delivered instead of sent a second time. Degrades quietly — a failed
        lookup just means we still don't know.

        Identity matters, because the answer marks a row delivered and blocks it
        from ever being sent again. Template subjects are deterministic
        ("Internship inquiry at {company}", "Re: {original subject}"), so an
        older copy to the same person matches the same search — and Gmail's
        `subject:` is a phrase match, not an equality test. So: bound the search
        to the attempt with `sent_after` (the row's send_attempted_at), re-check
        every candidate's internalDate against it, and require the Subject header
        to match exactly. Anything we cannot positively identify is treated as
        "not found", which leads to the honest "check Gmail before retrying"
        prompt rather than a fabricated delivery record.
        """
        to_addr = str(to_email or '').strip()
        subject = _sanitize_header(subject)
        if not self.service or not _RECIPIENT_RE.fullmatch(to_addr) or not subject:
            return None
        # A minute of slack absorbs clock skew between us and Gmail.
        floor_epoch = None
        if sent_after:
            try:
                from datetime import datetime as _dt
                floor_epoch = int(_dt.fromisoformat(str(sent_after)).timestamp()) - 60
            except (TypeError, ValueError):
                floor_epoch = None
        query = f'in:sent to:{to_addr} subject:"{subject}"'
        if floor_epoch is not None:
            query += f' after:{floor_epoch}'
        try:
            listing = self.service.users().messages().list(
                userId='me', maxResults=5, q=query,
            ).execute()
            messages = listing.get('messages') or []
            for candidate in messages:
                if self._is_this_message(candidate.get('id'), subject, floor_epoch):
                    return {'gmail_message_id': candidate.get('id'),
                            'gmail_thread_id': candidate.get('threadId')}
            return None
        except Exception as e:
            print(f"[send] could not check the Sent folder for {to_addr}: {e}")
            return None

    def _is_this_message(self, message_id: Optional[str], subject: str,
                         floor_epoch: Optional[int]) -> bool:
        """Confirm a Sent-folder hit really is the message we just tried to send."""
        if not message_id:
            return False
        try:
            msg = self.service.users().messages().get(
                userId='me', id=message_id, format='metadata',
                metadataHeaders=['Subject'],
            ).execute()
            headers = {h.get('name', '').lower(): h.get('value', '')
                       for h in (msg.get('payload', {}) or {}).get('headers', [])}
            if _sanitize_header(headers.get('subject')) != subject:
                return False
            internal = int(msg.get('internalDate', 0)) // 1000
        except Exception as e:
            # Cannot establish identity => not proof. Better to ask the user to
            # check Gmail than to record a delivery that may never have happened.
            print(f"[send] could not identify sent message {message_id}: {e}")
            return False
        return floor_epoch is None or internal >= floor_epoch

    def send_batch(self, emails: List[Dict], from_email: str,
                   resume_paths: Optional[List[Optional[str]]] = None) -> List[Dict]:
        """
        Send multiple emails with rate limiting. resume_paths aligns with emails
        (per-email attachment path, or None for no attachment).
        Returns: List of {success, message_id/error, email_id}
        """
        results = []

        for i, email in enumerate(emails):
            # Rate limiting between sends
            if i > 0:
                time.sleep(self.send_delay)

            resume_path = resume_paths[i] if resume_paths and i < len(resume_paths) else None
            result = self.send_email(email, from_email, resume_path)
            results.append(result)

            if not result['success']:
                print(f"Failed to send email to {email.get('contact_email')}: {result.get('error')}")

        return results
