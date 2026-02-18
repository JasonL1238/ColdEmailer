import ollama
from typing import Dict, Optional
from models import Contact, CompanyMetadata, GeneratedEmail
from datetime import datetime
import uuid
from resume_analyzer import ResumeAnalyzer


class EmailGenerator:
    """Generate personalized cold emails using LLM"""
    
    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434", resume_path: str = "resume.pdf"):
        self.model = model
        self.base_url = base_url
        self.client = ollama.Client(host=base_url)
        self.resume_analyzer = ResumeAnalyzer(resume_path)
    
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
        
        # Use resume-analyzed background if user_background is not provided or is generic
        if not user_background or len(user_background.strip()) < 20:
            background_from_resume = self.resume_analyzer.get_relevant_background(
                company_industry=metadata.industry,
                company_product=metadata.product
            )
            background_part = f"\nMy background: {background_from_resume}"
        else:
            background_part = f"\nMy background: {user_background}"
        
        email_part = f"\nMy email: {user_email}" if user_email else ""
        
        prompt = f"""Write a professional, direct cold email for an internship opportunity.

=== RECIPIENT INFORMATION ===
Name: {contact.name}
Company: {contact.company}
Email: {contact.email}

=== YOUR INFORMATION ===
{name_part}{background_part}{email_part}

=== COMPANY INFORMATION ===
{personalization}

=== EMAIL REQUIREMENTS ===

Length: 200-300 words (professional and comprehensive)

Structure:
1. Greeting: "Hi [Name],"
2. Introduction: "I'm Jason Li, a student at the University of Pennsylvania studying Computer Science and Math/Statistics graduating in 2028."
3. Company interest (2-3 sentences): "I came across {contact.company}, and was thoroughly fascinated by the work you're doing with [specific area - use actual details from company information]. I saw that {contact.company} is developing [specific project or technology - use actual details], which aligns closely with my interests in [relevant fields - be specific]."
4. Personal projects/background (2-3 sentences): "In my own time I've been working on [brief project or focus area from your background]. My background in [skills or coursework from your background] has given me a foundation to explore these ideas, and I believe working with your team would help me better understand how they operate in real systems."
5. Call to action: "I'd love to learn more about your team and hear what skills you value when preparing for this kind of role. If you're open to it, I would appreciate a quick conversation sometime in the next week."
6. Closing: "Thanks so much, Jason Li" followed by:
   University of Pennsylvania
   Phone: 847-907-0871
   Email: li59@seas.upenn.edu
   Website: https://personal-site-sooty-ten.vercel.app/

Subject line: Create a professional subject line (max 60 characters), e.g., "Summer 2026 Internship Inquiry - CS Student at UPenn"

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
Professional and direct (not overly casual)
- Confident but respectful
- Specific about your experiences and skills
- Show genuine interest in the company's work
- Avoid placeholder text like "[specific area]" or "[industry/market]" - be concrete
- No corporate buzzwords unless naturally fitting
- Each sentence should be specific and meaningful

=== WRITING STYLE ===
- Short clear sentences
- Prefer concrete observations over adjectives
- Show interest in what they are building before talking about yourself
- Your background should appear only as context for why you are reaching out
- The call to action should feel low pressure and easy to say yes to
- Clear, professional sentences
- Be specific about your experiences and skills
- Reference actual projects and achievements from your background
- Show you've researched the company
- Professional closing with your name


=== CRITICAL RULES ===
- DO NOT use placeholder text like "[specific area]", "[specific project or technology]", "[relevant fields]", "[brief project or focus area]", "[skills or coursework]" - REPLACE ALL PLACEHOLDERS with actual concrete information
- DO use actual details from your background and the company information provided
- DO include relevant experiences and qualifications from the background provided - mention specific organizations, projects, or achievements that demonstrate your qualifications
- DO mention specific technical skills when relevant
- DO end with "Thanks so much, Jason Li" followed by:
  University of Pennsylvania
  Phone: 847-907-0871
  Email: li59@seas.upenn.edu
  Website: https://personal-site-sooty-ten.vercel.app/
- DO replace any bracketed placeholders with actual information from the company metadata and your background provided

=== FORMAT ===
Format your response as:
SUBJECT: [subject line]

BODY:
[email body - MUST end with "Thanks so much, Jason Li" followed by:
University of Pennsylvania
Phone: 847-907-0871
Email: li59@seas.upenn.edu
Website: https://personal-site-sooty-ten.vercel.app/]

"""

        return prompt
    
    def _strip_subject_from_body(self, body: str) -> str:
        """Remove any leading 'Subject: ...' line from body so subject only appears in the email header."""
        if not body:
            return body
        lines = body.split('\n')
        # Strip leading blank lines
        while lines and not lines[0].strip():
            lines.pop(0)
        # If first line looks like "Subject: ...", remove it
        if lines and lines[0].strip().lower().startswith('subject:'):
            lines.pop(0)
            # Strip any single blank line after subject
            while lines and not lines[0].strip():
                lines.pop(0)
        return '\n'.join(lines).strip()

    def _parse_email(self, email_text: str) -> tuple[str, str]:
        """Parse email text into subject and body. Ensures subject is not duplicated in body."""
        # Look for SUBJECT: and BODY: markers
        if "SUBJECT:" in email_text and "BODY:" in email_text:
            parts = email_text.split("BODY:")
            subject_part = parts[0].replace("SUBJECT:", "").strip()
            body = parts[1].strip() if len(parts) > 1 else email_text
            body = self._strip_subject_from_body(body)
            return subject_part, body

        # If no markers, try to extract first line as subject
        lines = email_text.split('\n')
        if len(lines) > 1:
            subject = lines[0].strip()
            body = '\n'.join(lines[1:]).strip()
            body = self._strip_subject_from_body(body)
            return subject, body

        # Fallback
        return "Internship Opportunity", self._strip_subject_from_body(email_text)
    
    def _fallback_email(self, contact: Contact, metadata: CompanyMetadata, 
                       user_name: Optional[str], user_email: Optional[str] = None) -> GeneratedEmail:
        """Generate a basic fallback email if LLM fails"""
        subject = f"Internship Opportunity at {contact.company}"
        signature = user_name or "Student"
        if user_email:
            signature += f"\n{user_email}"
        
        body = f"""Hi {contact.name},

I'm Jason Li, a student at the University of Pennsylvania studying Computer Science and Math/Statistics graduating in 2028. I came across {contact.company}, and was thoroughly fascinated by the work you're doing. I saw that {contact.company} is developing {metadata.product or 'innovative solutions'}, which aligns closely with my interests.

In my own time I've been working on various projects involving AI, machine learning, and software development. My background in computer science and mathematics has given me a foundation to explore these ideas, and I believe working with your team would help me better understand how they operate in real systems.

I'd love to learn more about your team and hear what skills you value when preparing for this kind of role. If you're open to it, I would appreciate a quick conversation sometime in the next week.

Thanks so much,
Jason Li
University of Pennsylvania
Phone: 847-907-0871
Email: li59@seas.upenn.edu
Website: https://personal-site-sooty-ten.vercel.app/"""

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
- MUST end with "Thanks so much, Jason Li" followed by:
  University of Pennsylvania
  Phone: 847-907-0871
  Email: li59@seas.upenn.edu
  Website: https://personal-site-sooty-ten.vercel.app/

=== FORMAT ===
Format your response as:
SUBJECT: [subject line - can reference original or be new]

BODY:
[email body - MUST end with "Thanks so much, Jason Li" followed by:
University of Pennsylvania
Phone: 847-907-0871
Email: li59@seas.upenn.edu
Website: https://personal-site-sooty-ten.vercel.app/]
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
                original_email_id=original_email.id,
                is_follow_up=True,
                follow_up_generated_at=datetime.now()
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
        
        body = f"""Hi {contact.name},

I wanted to follow up on my email from {sent_date_str} regarding internship opportunities at {contact.company}.

I remain very interested in the possibility of contributing to your team and would welcome the opportunity to discuss this further.

I'd love to learn more about your team and hear what skills you value when preparing for this kind of role. If you're open to it, I would appreciate a quick conversation sometime in the next week.

Thanks so much,
Jason Li
University of Pennsylvania
Phone: 847-907-0871
Email: li59@seas.upenn.edu
Website: https://personal-site-sooty-ten.vercel.app/"""

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
            original_email_id=original_email.id,
            is_follow_up=True,
            follow_up_generated_at=datetime.now()
        )
