import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from models import GeneratedEmail


class EmailStorage:
    """Persistent storage for generated emails"""
    
    def __init__(self, storage_path: str = "data/generated_emails.json"):
        self.storage_path = storage_path
        self._ensure_data_dir()
        self._emails = self._load()
    
    def _ensure_data_dir(self):
        """Ensure data directory exists"""
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
    
    def _load(self) -> Dict[str, GeneratedEmail]:
        """Load emails from disk"""
        if not os.path.exists(self.storage_path):
            return {}
        
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            emails = {}
            for email_id, email_data in data.items():
                # Convert datetime strings back to datetime objects
                if 'created_at' in email_data and email_data['created_at']:
                    email_data['created_at'] = datetime.fromisoformat(email_data['created_at'])
                if 'sent_at' in email_data and email_data['sent_at']:
                    email_data['sent_at'] = datetime.fromisoformat(email_data['sent_at'])
                if 'response_date' in email_data and email_data['response_date']:
                    email_data['response_date'] = datetime.fromisoformat(email_data['response_date'])
                if 'follow_up_generated_at' in email_data and email_data['follow_up_generated_at']:
                    email_data['follow_up_generated_at'] = datetime.fromisoformat(email_data['follow_up_generated_at'])
                if 'follow_up_sent_at' in email_data and email_data['follow_up_sent_at']:
                    email_data['follow_up_sent_at'] = datetime.fromisoformat(email_data['follow_up_sent_at'])
                
                emails[email_id] = GeneratedEmail(**email_data)
            
            return emails
        except Exception as e:
            print(f"Error loading email storage: {e}")
            return {}
    
    def _save_to_disk(self):
        """Write emails to JSON file"""
        try:
            data = {}
            for email_id, email in self._emails.items():
                email_dict = email.model_dump()
                # Convert datetime objects to ISO format strings
                if email_dict.get('created_at'):
                    email_dict['created_at'] = email.created_at.isoformat()
                if email_dict.get('sent_at'):
                    email_dict['sent_at'] = email.sent_at.isoformat()
                if email_dict.get('response_date'):
                    email_dict['response_date'] = email.response_date.isoformat()
                if email_dict.get('follow_up_generated_at'):
                    email_dict['follow_up_generated_at'] = email.follow_up_generated_at.isoformat()
                if email_dict.get('follow_up_sent_at'):
                    email_dict['follow_up_sent_at'] = email.follow_up_sent_at.isoformat()
                data[email_id] = email_dict
            
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving email storage: {e}")
    
    def save(self, email: GeneratedEmail):
        """Save or update an email"""
        if not email.id:
            raise ValueError("Email must have an id")
        self._emails[email.id] = email
        self._save_to_disk()
    
    def get(self, email_id: str) -> Optional[GeneratedEmail]:
        """Get an email by ID"""
        return self._emails.get(email_id)
    
    def get_by_contact_id(self, contact_id: str) -> Optional[GeneratedEmail]:
        """Get email for a contact (most recent)"""
        matching = [e for e in self._emails.values() if e.contact_id == contact_id]
        if not matching:
            return None
        # Return most recent by created_at
        return max(matching, key=lambda e: e.created_at or datetime.min)
    
    def get_all(self) -> List[GeneratedEmail]:
        """Get all emails"""
        return list(self._emails.values())
    
    def get_follow_up_candidates(self) -> List[GeneratedEmail]:
        """Get emails sent 1+ weeks ago with no response"""
        one_week_ago = datetime.now() - timedelta(days=7)
        
        candidates = []
        for email in self._emails.values():
            if (email.status == "sent" and 
                email.sent_at and 
                email.sent_at < one_week_ago and
                not email.has_response):
                candidates.append(email)
        return candidates
    
    def delete(self, email_id: str) -> bool:
        """Delete an email"""
        if email_id in self._emails:
            del self._emails[email_id]
            self._save_to_disk()
            return True
        return False
