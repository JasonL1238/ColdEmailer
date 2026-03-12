import pandas as pd
import os
import time
import json
from typing import List, Dict, Optional
from models import Contact
import uuid

DEBUG_LOG = "/Users/jasonli/Documents/GitHub/ColdEmailer/.cursor/debug-6c4284.log"

def _dlog(location: str, message: str, data: dict, hypothesis_id: str):
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(json.dumps({"sessionId": "6c4284", "timestamp": int(time.time() * 1000), "location": location, "message": message, "data": data, "hypothesisId": hypothesis_id}) + "\n")
    except Exception:
        pass


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
            _dlog("csv_processor.py:read_contacts", "CSV missing", {"csv_path": self.csv_path, "cwd": os.getcwd()}, "H4")
            return []

        try:
            df = pd.read_csv(self.csv_path)
            contacts = []
            for _, row in df.iterrows():
                raw_id = row.get("id") if "id" in df.columns else None
                if raw_id is None or (isinstance(raw_id, float) and (raw_id != raw_id or str(raw_id) == "nan")) or str(raw_id).strip() == "" or str(raw_id) == "nan":
                    raw_id = str(uuid.uuid4())
                else:
                    raw_id = str(raw_id)
                contact = Contact(
                    id=raw_id,
                    name=str(row["name"]) if pd.notna(row.get("name")) else "",
                    company=str(row["company"]) if pd.notna(row.get("company")) else "",
                    email=str(row["email"]) if pd.notna(row.get("email")) else "",
                    status=str(row.get("status", "pending")) if pd.notna(row.get("status")) else "pending",
                    role=str(row["role"]).strip() if "role" in df.columns and pd.notna(row.get("role")) and str(row.get("role")).strip() else None,
                )
                contacts.append(contact)
            ids = [c.id for c in contacts]
            nan_like = [i for i in ids if i == "nan" or (isinstance(i, str) and not i.strip())]
            _dlog("csv_processor.py:read_contacts", "Read contacts", {"csv_path": self.csv_path, "abs_path": os.path.abspath(self.csv_path), "cwd": os.getcwd(), "count": len(contacts), "ids_sample": ids[:10] if len(ids) <= 10 else ids[:5] + ["..."] + ids[-5:], "any_nan_or_empty": len(nan_like) > 0, "nan_count": len(nan_like)}, "H1" if nan_like else "H4")
            return contacts
        except Exception as e:
            _dlog("csv_processor.py:read_contacts", "Exception", {"csv_path": self.csv_path, "error": str(e)}, "H5")
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
                'status': contact.status,
                'role': getattr(contact, 'role', None) or '',
            })
        
        df = pd.DataFrame(data)
        df.to_csv(self.csv_path, index=False)
    
    def add_contact(self, contact: Contact) -> Contact:
        """Add a new contact"""
        # #region agent log
        _dlog("csv_processor.py:add_contact:start", "Adding contact", {"contact_id": contact.id}, "H3")
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
        _dlog("csv_processor.py:add_contact:success", "Contact added", {"contact_id": contact.id, "total_contacts": len(contacts)}, "H3")
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
        contact_ids = [c.id for c in contacts]
        _dlog("csv_processor.py:delete_contact:beforeFilter", "Delete contact", {"contact_id": contact_id, "existing_ids": contact_ids, "contact_id_in_list": contact_id in contact_ids}, "H2")
        # #region agent log
        import json
        try:
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
