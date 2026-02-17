from typing import List, Optional
from datetime import datetime
from models import GeneratedEmail


class ResponseChecker:
    """Check Gmail for email responses/replies"""
    
    def __init__(self, service):
        self.service = service
    
    def check_response(self, email: GeneratedEmail) -> bool:
        """Check if there's a reply to this email"""
        if not email.gmail_message_id:
            return False
        
        try:
            # Get the original message thread
            original_message = self.service.users().messages().get(
                userId='me',
                id=email.gmail_message_id
            ).execute()
            
            thread_id = original_message['threadId']
            
            # Get all messages in the thread
            thread = self.service.users().threads().get(
                userId='me',
                id=thread_id
            ).execute()
            
            # Check if there are replies (messages after the original)
            messages = thread.get('messages', [])
            if len(messages) > 1:
                # There are replies
                # Find the most recent reply
                replies = [m for m in messages if m['id'] != email.gmail_message_id]
                if replies:
                    latest_reply = max(replies, key=lambda m: int(m['internalDate']))
                    email.has_response = True
                    email.response_date = datetime.fromtimestamp(int(latest_reply['internalDate']) / 1000)
                    return True
            
            return False
        except Exception as e:
            print(f"Error checking response: {e}")
            return False
    
    def check_all_responses(self, emails: List[GeneratedEmail]) -> List[GeneratedEmail]:
        """Check responses for multiple emails"""
        updated = []
        for email in emails:
            if self.check_response(email):
                updated.append(email)
        return updated
