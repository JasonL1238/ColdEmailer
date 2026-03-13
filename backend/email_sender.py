import os
import base64
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
from models import GeneratedEmail
import time


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
        resume_path = os.getenv('RESUME_PATH', None)
        # Default to resume.pdf in project root if not specified
        if not resume_path and project_root:
            resume_path = 'resume.pdf'
        # Resolve resume path relative to project root if it's a relative path
        if resume_path and not os.path.isabs(resume_path) and project_root:
            resume_path = os.path.join(project_root, resume_path)
        
        # If specified resume path doesn't exist, try to auto-detect PDF in project root
        if resume_path and not os.path.exists(resume_path) and project_root:
            try:
                # Look for PDF files in project root
                pdf_files = [f for f in os.listdir(project_root) if f.lower().endswith('.pdf')]
                if pdf_files:
                    # Prefer files with "resume" in the name, otherwise use first PDF
                    resume_candidates = [f for f in pdf_files if 'resume' in f.lower()]
                    if resume_candidates:
                        resume_path = os.path.join(project_root, resume_candidates[0])
                    else:
                        resume_path = os.path.join(project_root, pdf_files[0])
            except Exception as e:
                pass  # Keep original path even if auto-detect fails

        self.resume_path = resume_path
    
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

    def send_email(self, email: GeneratedEmail, from_email: str, resume_path: Optional[str] = None) -> Dict[str, any]:
        """
        Send a single email via Gmail API with optional resume attachment.
        Returns: {success: bool, message_id: str or error: str}
        """
        if not self.service:
            self.authenticate()
        
        # Use instance resume_path if not provided
        if not resume_path:
            resume_path = self.resume_path

        try:
            # Create message
            message = MIMEMultipart()
            message['to'] = email.contact_email
            message['subject'] = email.subject
            if from_email and from_email != 'me' and '@' in str(from_email):
                message['From'] = from_email

            # Convert plain text to HTML with proper formatting
            # Replace newlines with <br> and wrap in proper HTML structure
            # Split body into paragraphs (double newlines) for better formatting
            paragraphs = email.body.split('\n\n')
            html_paragraphs = []
            for para in paragraphs:
                if para.strip():
                    # Replace single newlines within paragraph with <br>
                    para_html = para.strip().replace('\n', '<br>')
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
            
            # Plain and HTML as multipart/alternative so the client shows only one (no duplicate body)
            alternative = MIMEMultipart('alternative')
            alternative.attach(MIMEText(email.body, 'plain'))
            alternative.attach(MIMEText(html_body, 'html'))
            message.attach(alternative)

            # Attach resume if provided and file exists
            if resume_path and os.path.exists(resume_path):
                try:
                    with open(resume_path, 'rb') as f:
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        
                        # Get filename from path
                        filename = os.path.basename(resume_path)
                        part.add_header(
                            'Content-Disposition',
                            f'attachment; filename= {filename}'
                        )
                        message.attach(part)
                except Exception as e:
                    print(f"Warning: Could not attach resume: {e}")
            
            # Encode message
            raw_message = base64.urlsafe_b64encode(
                message.as_bytes()
            ).decode('utf-8')
            
            # Send
            send_message = self.service.users().messages().send(
                userId='me',
                body={'raw': raw_message}
            ).execute()
            
            return {
                'success': True,
                'message_id': send_message.get('id'),
                'gmail_message_id': send_message.get('id'),  # For tracking responses
                'email_id': email.id
            }
            
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
                'email_id': email.id
            }
    
    def send_batch(self, emails: List[GeneratedEmail], from_email: str, resume_path: Optional[str] = None) -> List[Dict]:
        """
        Send multiple emails with rate limiting and resume attachment.
        Returns: List of {success, message_id/error, email_id}
        """
        results = []
        
        for email in emails:
            # Rate limiting
            time.sleep(self.send_delay)
            
            result = self.send_email(email, from_email, resume_path)
            results.append(result)
            
            if not result['success']:
                print(f"Failed to send email to {email.contact_email}: {result.get('error')}")
        
        return results
