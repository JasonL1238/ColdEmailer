import pandas as pd
import os
from typing import List, Dict, Optional
from models import Contact
import uuid


class CSVProcessor:
    def __init__(self, csv_path: str = "data/contacts.csv"):
        self.csv_path = csv_path
        self._ensure_data_dir()
    
    def _ensure_data_dir(self):
        """Ensure data directory exists"""
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
    
    def read_contacts(self) -> List[Contact]:
        """Read all contacts from CSV"""
        if not os.path.exists(self.csv_path):
            return []
        
        try:
            df = pd.read_csv(self.csv_path)
            contacts = []
            for _, row in df.iterrows():
                contact = Contact(
                    id=str(uuid.uuid4()) if 'id' not in df.columns else str(row.get('id', uuid.uuid4())),
                    name=str(row['name']),
                    company=str(row['company']),
                    email=str(row['email']),
                    status=str(row.get('status', 'pending'))
                )
                contacts.append(contact)
            return contacts
        except Exception as e:
            print(f"Error reading CSV: {e}")
            return []
    
    def write_contacts(self, contacts: List[Contact]):
        """Write contacts to CSV"""
        self._ensure_data_dir()
        
        data = []
        for contact in contacts:
            data.append({
                'id': contact.id,
                'name': contact.name,
                'company': contact.company,
                'email': contact.email,
                'status': contact.status
            })
        
        df = pd.DataFrame(data)
        df.to_csv(self.csv_path, index=False)
    
    def add_contact(self, contact: Contact) -> Contact:
        """Add a new contact"""
        # #region agent log
        import json
        import time
        log_data = {
            "location": "csv_processor.py:add_contact:start",
            "message": "Adding contact",
            "data": {
                "contact_id": contact.id,
                "has_name": bool(contact.name),
                "has_company": bool(contact.company),
                "has_email": bool(contact.email),
            },
            "timestamp": int(time.time() * 1000),
            "runId": "add-contact",
            "hypothesisId": "I"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data) + '\n')
        except: pass
        # #endregion
        contacts = self.read_contacts()
        if not contact.id:
            contact.id = str(uuid.uuid4())
        contacts.append(contact)
        self.write_contacts(contacts)
        # #region agent log
        log_data2 = {
            "location": "csv_processor.py:add_contact:success",
            "message": "Contact added to CSV",
            "data": {
                "contact_id": contact.id,
                "total_contacts": len(contacts),
            },
            "timestamp": int(time.time() * 1000),
            "runId": "add-contact",
            "hypothesisId": "J"
        }
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps(log_data2) + '\n')
        except: pass
        # #endregion
        return contact
    
    def update_contact(self, contact_id: str, updates: Dict) -> Optional[Contact]:
        """Update an existing contact"""
        contacts = self.read_contacts()
        for i, contact in enumerate(contacts):
            if contact.id == contact_id:
                for key, value in updates.items():
                    if hasattr(contact, key):
                        setattr(contact, key, value)
                self.write_contacts(contacts)
                return contact
        return None
    
    def delete_contact(self, contact_id: str) -> bool:
        """Delete a contact"""
        # #region agent log
        import json
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"csv_processor.py:delete_contact:start","message":"Delete contact called","data":{"contact_id":contact_id},"runId":"run1","hypothesisId":"C"}) + '\n')
        except: pass
        # #endregion
        contacts = self.read_contacts()
        original_count = len(contacts)
        # #region agent log
        import json
        try:
            contact_ids = [c.id for c in contacts]
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"csv_processor.py:delete_contact:beforeFilter","message":"Before filtering","data":{"contact_id":contact_id,"original_count":original_count,"existing_ids":contact_ids},"runId":"run1","hypothesisId":"C"}) + '\n')
        except: pass
        # #endregion
        contacts = [c for c in contacts if c.id != contact_id]
        new_count = len(contacts)
        # #region agent log
        import json
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"csv_processor.py:delete_contact:afterFilter","message":"After filtering","data":{"contact_id":contact_id,"original_count":original_count,"new_count":new_count,"deleted":original_count > new_count},"runId":"run1","hypothesisId":"C"}) + '\n')
        except: pass
        # #endregion
        if len(contacts) < original_count:
            self.write_contacts(contacts)
            # #region agent log
            import json
            try:
                with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                    f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"csv_processor.py:delete_contact:success","message":"Contact deleted successfully","data":{"contact_id":contact_id},"runId":"run1","hypothesisId":"C"}) + '\n')
            except: pass
            # #endregion
            return True
        # #region agent log
        import json
        try:
            with open('/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug.log', 'a') as f:
                f.write(json.dumps({"id":f"log_{int(__import__('time').time()*1000)}","timestamp":int(__import__('time').time()*1000),"location":"csv_processor.py:delete_contact:notFound","message":"Contact not found","data":{"contact_id":contact_id},"runId":"run1","hypothesisId":"C"}) + '\n')
        except: pass
        # #endregion
        return False
    
    def get_contact(self, contact_id: str) -> Optional[Contact]:
        """Get a single contact by ID"""
        contacts = self.read_contacts()
        for contact in contacts:
            if contact.id == contact_id:
                return contact
        return None
    
    def get_pending_contacts(self) -> List[Contact]:
        """Get all pending contacts"""
        contacts = self.read_contacts()
        return [c for c in contacts if c.status == 'pending']
    
    def remove_sent_contacts(self, contact_ids: List[str]):
        """Remove sent contacts from CSV"""
        contacts = self.read_contacts()
        contacts = [c for c in contacts if c.id not in contact_ids]
        self.write_contacts(contacts)
