import os
import json
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from google.auth.transport.requests import Request
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
        # #region agent log
        import json
        log_data = {
            "location": "email_sender.py:__init__",
            "message": "EmailSender initialization",
            "data": {
                "credentials_path_input": credentials_path,
                "token_path_input": token_path,
                "project_root": project_root,
                "cwd": os.getcwd(),
                "credentials_path_abs": os.path.abspath(credentials_path) if credentials_path else None,
                "token_path_abs": os.path.abspath(token_path) if token_path else None,
                "credentials_exists": os.path.exists(credentials_path) if credentials_path else False,
                "token_exists": os.path.exists(token_path) if token_path else False,
            },
            "timestamp": int(time.time() * 1000),
            "runId": "init",
            "hypothesisId": "A"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data) + '\n')
        except: pass
        # #endregion
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.service = None
        self.send_delay = int(os.getenv('EMAIL_SEND_DELAY_SECONDS', 3))
        resume_path = os.getenv('RESUME_PATH', None)
        # Default to resume.pdf in project root if not specified
        if not resume_path and project_root:
            resume_path = 'resume.pdf'
        # #region agent log
        log_data_resume = {
            "location": "email_sender.py:__init__",
            "message": "Resolving resume path",
            "data": {
                "resume_path_env": resume_path,
                "project_root": project_root,
                "cwd": os.getcwd(),
                "is_absolute": os.path.isabs(resume_path) if resume_path else None,
            },
            "timestamp": int(time.time() * 1000),
            "runId": "init",
            "hypothesisId": "F"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data_resume) + '\n')
        except: pass
        # #endregion
        # Resolve resume path relative to project root if it's a relative path
        if resume_path and not os.path.isabs(resume_path) and project_root:
            resume_path = os.path.join(project_root, resume_path)
        
        # If specified resume path doesn't exist, try to auto-detect PDF in project root
        if resume_path and not os.path.exists(resume_path) and project_root:
            # #region agent log
            log_data_autodetect = {
                "location": "email_sender.py:__init__",
                "message": "Resume file not found, attempting auto-detect",
                "data": {
                    "specified_path": resume_path,
                    "project_root": project_root,
                },
                "timestamp": int(time.time() * 1000),
                "runId": "init",
                "hypothesisId": "F"
            }
            try:
                with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps(log_data_autodetect) + '\n')
            except: pass
            # #endregion
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
                    # #region agent log
                    log_data_autodetect2 = {
                        "location": "email_sender.py:__init__",
                        "message": "Auto-detected resume file",
                        "data": {
                            "detected_path": resume_path,
                            "exists": os.path.exists(resume_path),
                        },
                        "timestamp": int(time.time() * 1000),
                        "runId": "init",
                        "hypothesisId": "F"
                    }
                    try:
                        with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps(log_data_autodetect2) + '\n')
                    except: pass
                    # #endregion
            except Exception as e:
                # #region agent log
                log_data_autodetect_err = {
                    "location": "email_sender.py:__init__",
                    "message": "Auto-detect failed",
                    "data": {"error": str(e)},
                    "timestamp": int(time.time() * 1000),
                    "runId": "init",
                    "hypothesisId": "F"
                }
                try:
                    with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps(log_data_autodetect_err) + '\n')
                except: pass
                # #endregion
                pass  # Keep original path even if auto-detect fails
        
        # #region agent log
        log_data_resume2 = {
            "location": "email_sender.py:__init__",
            "message": "Resume path resolved",
            "data": {
                "resume_path_final": resume_path,
                "resume_path_abs": os.path.abspath(resume_path) if resume_path else None,
                "resume_exists": os.path.exists(resume_path) if resume_path else False,
                "cwd": os.getcwd(),
            },
            "timestamp": int(time.time() * 1000),
            "runId": "init",
            "hypothesisId": "F"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data_resume2) + '\n')
        except: pass
        # #endregion
        self.resume_path = resume_path
    
    def authenticate(self):
        """Authenticate with Gmail API"""
        # #region agent log
        import json
        log_data = {
            "location": "email_sender.py:authenticate",
            "message": "Starting authentication",
            "data": {
                "credentials_path": self.credentials_path,
                "token_path": self.token_path,
                "cwd": os.getcwd(),
                "credentials_path_abs": os.path.abspath(self.credentials_path),
                "token_path_abs": os.path.abspath(self.token_path),
                "credentials_exists": os.path.exists(self.credentials_path),
                "token_exists": os.path.exists(self.token_path),
                "credentials_isfile": os.path.isfile(self.credentials_path) if os.path.exists(self.credentials_path) else False,
                "credentials_readable": os.access(self.credentials_path, os.R_OK) if os.path.exists(self.credentials_path) else False,
            },
            "timestamp": int(time.time() * 1000),
            "runId": "auth",
            "hypothesisId": "B"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data) + '\n')
        except: pass
        # #endregion
        creds = None
        
        # Load existing token
        if os.path.exists(self.token_path):
            # #region agent log
            log_data2 = {
                "location": "email_sender.py:authenticate",
                "message": "Loading existing token",
                "data": {"token_path": self.token_path, "token_exists": True},
                "timestamp": int(time.time() * 1000),
                "runId": "auth",
                "hypothesisId": "C"
            }
            try:
                with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps(log_data2) + '\n')
            except: pass
            # #endregion
            creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)
        
        # If no valid credentials, get new ones
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                # #region agent log
                log_data3 = {
                    "location": "email_sender.py:authenticate",
                    "message": "Refreshing expired token",
                    "data": {"has_refresh_token": True},
                    "timestamp": int(time.time() * 1000),
                    "runId": "auth",
                    "hypothesisId": "D"
                }
                try:
                    with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps(log_data3) + '\n')
                except: pass
                # #endregion
                creds.refresh(Request())
            else:
                # #region agent log
                log_data4 = {
                    "location": "email_sender.py:authenticate",
                    "message": "Checking credentials file before OAuth flow",
                    "data": {
                        "credentials_path": self.credentials_path,
                        "credentials_exists": os.path.exists(self.credentials_path),
                        "credentials_path_abs": os.path.abspath(self.credentials_path),
                    },
                    "timestamp": int(time.time() * 1000),
                    "runId": "auth",
                    "hypothesisId": "E"
                }
                try:
                    with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                        f.write(json.dumps(log_data4) + '\n')
                except: pass
                # #endregion
                if not os.path.exists(self.credentials_path):
                    # #region agent log
                    log_data5 = {
                        "location": "email_sender.py:authenticate",
                        "message": "Credentials file not found - raising error",
                        "data": {
                            "credentials_path": self.credentials_path,
                            "credentials_path_abs": os.path.abspath(self.credentials_path),
                            "cwd": os.getcwd(),
                        },
                        "timestamp": int(time.time() * 1000),
                        "runId": "auth",
                        "hypothesisId": "E"
                    }
                    try:
                        with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                            f.write(json.dumps(log_data5) + '\n')
                    except: pass
                    # #endregion
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
        
        # #region agent log
        log_data_send = {
            "location": "email_sender.py:send_email",
            "message": "Checking resume file before sending",
            "data": {
                "resume_path": resume_path,
                "resume_path_abs": os.path.abspath(resume_path) if resume_path else None,
                "resume_exists": os.path.exists(resume_path) if resume_path else False,
                "cwd": os.getcwd(),
            },
            "timestamp": int(time.time() * 1000),
            "runId": "send",
            "hypothesisId": "G"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data_send) + '\n')
        except: pass
        # #endregion
        
        try:
            # Create message
            message = MIMEMultipart()
            message['to'] = email.contact_email
            message['subject'] = email.subject
            message.attach(MIMEText(email.body, 'plain'))
            
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
