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
                'status': contact.status,
                'role': getattr(contact, 'role', None) or '',
            })
        
        df = pd.DataFrame(data)
        df.to_csv(self.csv_path, index=False)
    
    def add_contact(self, contact: Contact) -> Contact:
        """Add a new contact"""
        contacts = self.read_contacts()
        if not contact.id:
            contact.id = str(uuid.uuid4())
        contacts.append(contact)
        self.write_contacts(contacts)
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
        contacts = self.read_contacts()
        original_count = len(contacts)
        contacts = [c for c in contacts if c.id != contact_id]
        if len(contacts) < original_count:
            self.write_contacts(contacts)
            return True
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
