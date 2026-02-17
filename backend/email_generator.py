import ollama
from typing import Dict, Optional
from models import Contact, CompanyMetadata, GeneratedEmail
from datetime import datetime
import uuid


class EmailGenerator:
    """Generate personalized cold emails using LLM"""
    
    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url
        self.client = ollama.Client(host=base_url)
    
    def generate(self, contact: Contact, company_metadata: CompanyMetadata, 
                 user_name: Optional[str] = None, user_background: Optional[str] = None,
                 user_email: Optional[str] = None) -> GeneratedEmail:
        """
        Generate a personalized cold email for a contact.
        
        Args:
            contact: Contact information
            company_metadata: Enriched company metadata
            user_name: Your name (for email signature)
            user_background: Your background/qualifications
        """
        # Build personalization context
        personalization = self._build_personalization(company_metadata)
        
        # Build prompt
        prompt = self._build_prompt(contact, company_metadata, personalization, user_name, user_background, user_email)
        
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.7,  # Slightly creative but professional
                }
            )
            
            # Ollama response structure: {'model': ..., 'response': '...', 'done': ...}
            email_text = response.get('response', '').strip()
            
            # Extract subject and body if they're separated
            subject, body = self._parse_email(email_text)
            
            # Generate email object
            email = GeneratedEmail(
                id=str(uuid.uuid4()),
                contact_id=contact.id,
                contact_name=contact.name,
                contact_email=contact.email,
                company=contact.company,
                subject=subject,
                body=body,
                status="pending",
                created_at=datetime.now()
            )
            
            return email
            
        except Exception as e:
            print(f"Error generating email: {e}")
            # Return a basic email on error
            return self._fallback_email(contact, company_metadata, user_name, user_email)
    
    def _build_personalization(self, metadata: CompanyMetadata) -> str:
        """Build personalization string from metadata"""
        parts = []
        
        if metadata.hook_sentence:
            parts.append(f"Hook: {metadata.hook_sentence}")
        
        if metadata.summary:
            parts.append(f"Company: {metadata.summary}")
        
        if metadata.industry:
            parts.append(f"Industry: {metadata.industry}")
        
        if metadata.product:
            parts.append(f"Product: {metadata.product}")
        
        if metadata.why_engineers_care:
            parts.append(f"Why engineers care: {metadata.why_engineers_care}")
        
        return "\n".join(parts)
    
    def _build_prompt(self, contact: Contact, metadata: CompanyMetadata, 
                     personalization: str, user_name: Optional[str], 
                     user_background: Optional[str], user_email: Optional[str] = None) -> str:
        """Build the email generation prompt"""
        
        name_part = f"My name is {user_name}." if user_name else "I am a student/engineer looking for an internship."
        background_part = f"\nMy background: {user_background}" if user_background else ""
        email_part = f"\nMy email: {user_email}" if user_email else ""
        
        prompt = f"""Write a professional, personalized cold email for an internship opportunity.

=== RECIPIENT INFORMATION ===
Name: {contact.name}
Company: {contact.company}
Email: {contact.email}

=== YOUR INFORMATION ===
{name_part}{background_part}{email_part}

=== COMPANY INFORMATION ===
{personalization}

=== EMAIL REQUIREMENTS ===

Length: 150-250 words (concise and to the point)

Structure:
1. Brief introduction with your name, class of UPenn 2028 studying CS and Math/Stats, and purpose (seeking internship)
2. Brief background/qualifications (1-2 sentences)
3. Specific interest in the company (use the hook sentence naturally: "I saw {contact.company} is [hook] — that kind of [why_engineers_care] is exactly what I've been building around...")
4. Call to action (request for conversation/meeting)
5. Closing: MUST end with "Thanks so much, Jason" (no other signature needed)

Subject line: Create a compelling subject line (max 60 characters)

=== TONE GUIDELINES ===
- Natural and conversational but still respectful
- Curious rather than self promotional
- Specific rather than impressive sounding
- Confident but slightly humble
- No corporate buzzwords or exaggerated praise
- Do not sound like marketing copy or LinkedIn influencer writing
- Avoid generic phrases like "I am passionate about", "cutting edge", "leverage my skills", or "perfect fit"
- Avoid long lists of achievements
- Each sentence should feel like a real person had a reason to write it

=== WRITING STYLE ===
- Short clear sentences
- Prefer concrete observations over adjectives
- Show interest in what they are building before talking about yourself
- Your background should appear only as context for why you are reaching out
- The call to action should feel low pressure and easy to say yes to

=== GOAL ===
The reader should think "this person actually looked at what we do and seems interesting to talk to"

The email should feel like a thoughtful message from a curious student, not a sales pitch and not a formal cover letter. The goal is to start a conversation, not convince immediately.

=== FORMAT ===
Format your response as:
SUBJECT: [subject line]

BODY:
[email body]

"""

        return prompt
    
    def _parse_email(self, email_text: str) -> tuple[str, str]:
        """Parse email text into subject and body"""
        # Look for SUBJECT: and BODY: markers
        if "SUBJECT:" in email_text and "BODY:" in email_text:
            parts = email_text.split("BODY:")
            subject_part = parts[0].replace("SUBJECT:", "").strip()
            body = parts[1].strip() if len(parts) > 1 else email_text
            return subject_part, body
        
        # If no markers, try to extract first line as subject
        lines = email_text.split('\n')
        if len(lines) > 1:
            subject = lines[0].strip()
            body = '\n'.join(lines[1:]).strip()
            return subject, body
        
        # Fallback
        return "Internship Opportunity", email_text
    
    def _fallback_email(self, contact: Contact, metadata: CompanyMetadata, 
                       user_name: Optional[str], user_email: Optional[str] = None) -> GeneratedEmail:
        """Generate a basic fallback email if LLM fails"""
        subject = f"Internship Opportunity at {contact.company}"
        signature = user_name or "Student"
        if user_email:
            signature += f"\n{user_email}"
        
        body = f"""Dear {contact.name},

I hope this email finds you well. I am reaching out to express my interest in internship opportunities at {contact.company}.

{metadata.summary or f"I am impressed by {contact.company}'s work"} and would love to contribute to your team.

I would welcome the opportunity to discuss how I might be able to contribute to {contact.company}.

Thanks so much,
Jason"""

        return GeneratedEmail(
            id=str(uuid.uuid4()),
            contact_id=contact.id,
            contact_name=contact.name,
            contact_email=contact.email,
            company=contact.company,
            subject=subject,
            body=body,
            status="pending",
            created_at=datetime.now()
        )
    
    def generate_follow_up(self, contact: Contact, company_metadata: CompanyMetadata,
                           original_email: GeneratedEmail,
                           user_name: Optional[str] = None,
                           user_background: Optional[str] = None,
                           user_email: Optional[str] = None) -> GeneratedEmail:
        """Generate a follow-up email referencing the original"""
        
        sent_date_str = original_email.sent_at.strftime('%B %d, %Y') if original_email.sent_at else "recently"
        
        prompt = f"""Generate a professional follow-up email for a cold email outreach.

=== ORIGINAL EMAIL ===
Sent: {sent_date_str}
Subject: {original_email.subject}
Body: {original_email.body}

=== CONTACT INFORMATION ===
Name: {contact.name}
Company: {contact.company}

=== YOUR INFORMATION ===
Name: {user_name or 'Jason Li'}
Background: {user_background or ''}
Email: {user_email or 'jason.ye.li.7@gmail.com'}

=== COMPANY CONTEXT ===
{company_metadata.summary or ''}

=== REQUIREMENTS ===
- Length: 100-150 words (brief and professional)
- References the original email politely (e.g., "I wanted to follow up on my email from {sent_date_str}")
- Shows continued interest
- Includes a clear call to action
- Does not sound pushy or desperate
- MUST end with "Thanks so much, Jason" (no other signature needed)

=== FORMAT ===
Format your response as:
SUBJECT: [subject line - can reference original or be new]

BODY:
[email body - MUST end with "Thanks so much, Jason"]
"""
        
        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.7,
                }
            )
            
            email_text = response.get('response', '').strip()
            subject, body = self._parse_email(email_text)
            
            # Generate email object
            follow_up = GeneratedEmail(
                id=str(uuid.uuid4()),
                contact_id=contact.id,
                contact_name=contact.name,
                contact_email=contact.email,
                company=contact.company,
                subject=subject,
                body=body,
                status="pending",
                created_at=datetime.now(),
                original_email_id=original_email.id
            )
            
            return follow_up
            
        except Exception as e:
            print(f"Error generating follow-up email: {e}")
            # Return a basic follow-up on error
            return self._fallback_follow_up(contact, original_email, user_name, user_email)
    
    def _fallback_follow_up(self, contact: Contact, original_email: GeneratedEmail,
                           user_name: Optional[str], user_email: Optional[str] = None) -> GeneratedEmail:
        """Generate a basic fallback follow-up email if LLM fails"""
        sent_date_str = original_email.sent_at.strftime('%B %d, %Y') if original_email.sent_at else "recently"
        subject = f"Re: {original_email.subject}"
        signature = user_name or "Student"
        if user_email:
            signature += f"\n{user_email}"
        
        body = f"""Dear {contact.name},

I wanted to follow up on my email from {sent_date_str} regarding internship opportunities at {contact.company}.

I remain very interested in the possibility of contributing to your team and would welcome the opportunity to discuss this further.

Please let me know if you have any questions or if there's a convenient time for a brief conversation.

Thanks so much,
Jason"""

        return GeneratedEmail(
            id=str(uuid.uuid4()),
            contact_id=contact.id,
            contact_name=contact.name,
            contact_email=contact.email,
            company=contact.company,
            subject=subject,
            body=body,
            status="pending",
            created_at=datetime.now(),
            original_email_id=original_email.id
        )
