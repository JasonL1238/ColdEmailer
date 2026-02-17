import pytest
import os
import tempfile
import shutil
from pathlib import Path
from backend.csv_processor import CSVProcessor
from backend.models import Contact


class TestCSVProcessor:
    """Test CSVProcessor with edge cases and normal behavior"""
    
    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for test CSV files"""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path)
    
    @pytest.fixture
    def csv_path(self, temp_dir):
        """Path to test CSV file"""
        return os.path.join(temp_dir, "test_contacts.csv")
    
    @pytest.fixture
    def processor(self, csv_path):
        """Create CSVProcessor instance"""
        return CSVProcessor(csv_path=csv_path)
    
    def test_read_contacts_empty_file(self, processor):
        """Test reading from non-existent file returns empty list"""
        contacts = processor.read_contacts()
        assert contacts == []
        assert isinstance(contacts, list)
    
    def test_read_contacts_valid_data(self, processor):
        """Test reading valid CSV data"""
        # Create test CSV
        contact = Contact(
            id="test-123",
            name="John Doe",
            company="Acme Corp",
            email="john@acme.com",
            status="pending"
        )
        processor.add_contact(contact)
        
        # Read back
        contacts = processor.read_contacts()
        assert len(contacts) == 1
        assert contacts[0].id == "test-123"
        assert contacts[0].name == "John Doe"
        assert contacts[0].email == "john@acme.com"
    
    def test_read_contacts_missing_id_generates_uuid(self, processor, csv_path):
        """Test that missing ID generates UUID"""
        import pandas as pd
        df = pd.DataFrame([{
            'name': 'Test User',
            'company': 'Test Co',
            'email': 'test@test.com',
            'status': 'pending'
        }])
        df.to_csv(csv_path, index=False)
        
        contacts = processor.read_contacts()
        assert len(contacts) == 1
        assert contacts[0].id is not None
        assert len(contacts[0].id) > 0
        assert contacts[0].name == "Test User"
    
    def test_add_contact_with_id(self, processor):
        """Test adding contact with existing ID"""
        contact = Contact(
            id="custom-id",
            name="Jane Doe",
            company="Tech Inc",
            email="jane@tech.com",
            status="pending"
        )
        result = processor.add_contact(contact)
        assert result.id == "custom-id"
        
        contacts = processor.read_contacts()
        assert len(contacts) == 1
        assert contacts[0].id == "custom-id"
    
    def test_add_contact_without_id_generates_uuid(self, processor):
        """Test adding contact without ID generates UUID"""
        contact = Contact(
            name="Bob Smith",
            company="Startup Co",
            email="bob@startup.com",
            status="pending"
        )
        result = processor.add_contact(contact)
        assert result.id is not None
        assert len(result.id) > 0
        
        contacts = processor.read_contacts()
        assert len(contacts) == 1
        assert contacts[0].id == result.id
    
    def test_add_contact_null_name(self, processor):
        """Test adding contact with null name - should raise validation error"""
        with pytest.raises(Exception):  # Pydantic validation error
            contact = Contact(
                name=None,
                company="Test Co",
                email="test@test.com",
                status="pending"
            )
    
    def test_add_contact_empty_string(self, processor):
        """Test adding contact with empty strings - email validation should fail"""
        with pytest.raises(Exception):  # Pydantic validation error for empty email
            contact = Contact(
                name="",
                company="",
                email="",  # Empty email is invalid
                status="pending"
            )
    
    def test_update_contact_existing(self, processor):
        """Test updating existing contact"""
        contact = Contact(
            name="Original Name",
            company="Original Co",
            email="original@test.com",
            status="pending"
        )
        added = processor.add_contact(contact)
        
        updated = processor.update_contact(added.id, {"name": "Updated Name", "status": "sent"})
        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.status == "sent"
        assert updated.company == "Original Co"  # Unchanged
        
        contacts = processor.read_contacts()
        assert contacts[0].name == "Updated Name"
    
    def test_update_contact_nonexistent(self, processor):
        """Test updating non-existent contact returns None"""
        result = processor.update_contact("nonexistent-id", {"name": "New Name"})
        assert result is None
    
    def test_update_contact_empty_updates(self, processor):
        """Test updating with empty dict"""
        contact = Contact(
            name="Test",
            company="Co",
            email="test@test.com",
            status="pending"
        )
        added = processor.add_contact(contact)
        result = processor.update_contact(added.id, {})
        assert result is not None
        assert result.name == "Test"  # Unchanged
    
    def test_delete_contact_existing(self, processor):
        """Test deleting existing contact"""
        contact = Contact(
            name="To Delete",
            company="Co",
            email="delete@test.com",
            status="pending"
        )
        added = processor.add_contact(contact)
        
        success = processor.delete_contact(added.id)
        assert success is True
        
        contacts = processor.read_contacts()
        assert len(contacts) == 0
    
    def test_delete_contact_nonexistent(self, processor):
        """Test deleting non-existent contact returns False"""
        success = processor.delete_contact("nonexistent-id")
        assert success is False
    
    def test_delete_contact_empty_id(self, processor):
        """Test deleting with empty string ID"""
        success = processor.delete_contact("")
        assert success is False
    
    def test_get_contact_existing(self, processor):
        """Test getting existing contact"""
        contact = Contact(
            name="Get Me",
            company="Co",
            email="get@test.com",
            status="pending"
        )
        added = processor.add_contact(contact)
        
        found = processor.get_contact(added.id)
        assert found is not None
        assert found.id == added.id
        assert found.name == "Get Me"
    
    def test_get_contact_nonexistent(self, processor):
        """Test getting non-existent contact returns None"""
        result = processor.get_contact("nonexistent-id")
        assert result is None
    
    def test_get_pending_contacts(self, processor):
        """Test filtering pending contacts"""
        processor.add_contact(Contact(name="Pending", company="Co", email="p@test.com", status="pending"))
        processor.add_contact(Contact(name="Sent", company="Co", email="s@test.com", status="sent"))
        processor.add_contact(Contact(name="Trashed", company="Co", email="t@test.com", status="trashed"))
        
        pending = processor.get_pending_contacts()
        assert len(pending) == 1
        assert pending[0].status == "pending"
        assert pending[0].name == "Pending"
    
    def test_get_pending_contacts_empty(self, processor):
        """Test getting pending contacts when none exist"""
        processor.add_contact(Contact(name="Sent", company="Co", email="s@test.com", status="sent"))
        pending = processor.get_pending_contacts()
        assert len(pending) == 0
    
    def test_remove_sent_contacts(self, processor):
        """Test removing multiple sent contacts"""
        c1 = processor.add_contact(Contact(name="C1", company="Co", email="c1@test.com", status="sent"))
        c2 = processor.add_contact(Contact(name="C2", company="Co", email="c2@test.com", status="pending"))
        c3 = processor.add_contact(Contact(name="C3", company="Co", email="c3@test.com", status="sent"))
        
        processor.remove_sent_contacts([c1.id, c3.id])
        
        contacts = processor.read_contacts()
        assert len(contacts) == 1
        assert contacts[0].id == c2.id
    
    def test_remove_sent_contacts_empty_list(self, processor):
        """Test removing with empty list does nothing"""
        processor.add_contact(Contact(name="Test", company="Co", email="test@test.com", status="pending"))
        processor.remove_sent_contacts([])
        
        contacts = processor.read_contacts()
        assert len(contacts) == 1
    
    def test_multiple_operations_consistency(self, processor):
        """Test multiple operations maintain consistency"""
        # Add multiple
        c1 = processor.add_contact(Contact(name="One", company="Co", email="one@test.com", status="pending"))
        c2 = processor.add_contact(Contact(name="Two", company="Co", email="two@test.com", status="pending"))
        c3 = processor.add_contact(Contact(name="Three", company="Co", email="three@test.com", status="pending"))
        
        assert len(processor.read_contacts()) == 3
        
        # Update one
        processor.update_contact(c2.id, {"status": "sent"})
        assert processor.get_contact(c2.id).status == "sent"
        
        # Delete one
        processor.delete_contact(c1.id)
        assert len(processor.read_contacts()) == 2
        
        # Verify remaining
        contacts = processor.read_contacts()
        assert c2.id in [c.id for c in contacts]
        assert c3.id in [c.id for c in contacts]
        assert c1.id not in [c.id for c in contacts]
