import uuid
from datetime import datetime
from typing import Optional

import ollama

from models import CompanyMetadata, Contact, GeneratedEmail
from personal_profile import PersonalProfile, load_personal_profile
from resume_analyzer import ResumeAnalyzer


class EmailGenerator:
    """Generate personalized cold emails using LLM."""

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://localhost:11434",
        resume_path: str = "resume.pdf",
        profile: Optional[PersonalProfile] = None,
    ):
        self.model = model
        self.base_url = base_url
        self.client = ollama.Client(host=base_url)
        self.resume_analyzer = ResumeAnalyzer(resume_path)
        self.profile = profile or load_personal_profile()

    def generate(
        self,
        contact: Contact,
        company_metadata: CompanyMetadata,
        user_name: Optional[str] = None,
        user_background: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> GeneratedEmail:
        """Generate a personalized cold email for a contact."""
        personalization = self._build_personalization(company_metadata)
        prompt = self._build_prompt(
            contact,
            company_metadata,
            personalization,
            user_name,
            user_background,
            user_email,
        )

        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.7,
                },
            )
            email_text = response.get("response", "").strip()
            subject, body = self._parse_email(email_text)

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
            )
        except Exception as e:
            print(f"Error generating email: {e}")
            return self._fallback_email(contact, company_metadata, user_name, user_email)

    def _build_personalization(self, metadata: CompanyMetadata) -> str:
        """Build personalization string from metadata."""
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

    def _resolve_background(
        self,
        metadata: CompanyMetadata,
        user_background: Optional[str],
    ) -> str:
        if user_background and len(user_background.strip()) >= 20:
            return user_background.strip()
        if self.profile.default_background:
            return self.profile.default_background
        return self.resume_analyzer.get_relevant_background(
            company_industry=metadata.industry,
            company_product=metadata.product,
        )

    def _resolve_user_email(self, user_email: Optional[str]) -> str:
        if user_email and user_email.strip():
            return user_email.strip()
        return self.profile.default_user_email

    def _build_prompt(
        self,
        contact: Contact,
        metadata: CompanyMetadata,
        personalization: str,
        user_name: Optional[str],
        user_background: Optional[str],
        user_email: Optional[str] = None,
    ) -> str:
        """Build the email generation prompt."""
        sender_name = self.profile.resolve_name(user_name)
        sender_email = self._resolve_user_email(user_email)
        sender_intro = self.profile.render_introduction(sender_name)
        sender_background = self._resolve_background(metadata, user_background)
        signature_block = self.profile.render_signature_block(sender_name)
        signature_block_for_prompt = "\n".join(
            [f"   {line}" for line in signature_block.splitlines()]
        )

        name_part = (
            f"My name is {sender_name}."
            if sender_name
            else "I am a student/engineer looking for an internship."
        )
        background_part = f"\nMy background: {sender_background}"
        email_part = f"\nMy email: {sender_email}" if sender_email else ""

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
2. Introduction: "{sender_intro}"
3. Company interest (2-3 sentences): "I came across {contact.company}, and was thoroughly fascinated by the work you're doing with [specific area - use actual details from company information]. I saw that {contact.company} is developing [specific project or technology - use actual details], which aligns closely with my interests in [relevant fields - be specific]."
4. Personal projects/background (2-3 sentences): "In my own time I've been working on [brief project or focus area from your background]. My background in [skills or coursework from your background] has given me a foundation to explore these ideas, and I believe working with your team would help me better understand how they operate in real systems."
5. Call to action: "I'd love to learn more about your team and hear what skills you value when preparing for this kind of role. If you're open to it, I would appreciate a quick conversation sometime in the next week."
6. Closing: End with this exact signature block:
{signature_block_for_prompt}

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
- DO end with this exact signature block:
{signature_block_for_prompt}
- DO replace any bracketed placeholders with actual information from the company metadata and your background provided

=== FORMAT ===
Format your response as:
SUBJECT: [subject line]

BODY:
[email body - MUST end with this signature block:
{signature_block_for_prompt}]

"""
        return prompt

    def _strip_subject_from_body(self, body: str) -> str:
        """Remove any leading subject line from body text."""
        if not body:
            return body

        lines = body.split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)

        if lines and lines[0].strip().lower().startswith("subject:"):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)

        return "\n".join(lines).strip()

    def _parse_email(self, email_text: str) -> tuple[str, str]:
        """Parse email text into subject and body."""
        if "SUBJECT:" in email_text and "BODY:" in email_text:
            parts = email_text.split("BODY:")
            subject_part = parts[0].replace("SUBJECT:", "").strip()
            body = parts[1].strip() if len(parts) > 1 else email_text
            body = self._strip_subject_from_body(body)
            return subject_part, body

        lines = email_text.split("\n")
        if len(lines) > 1:
            subject = lines[0].strip()
            body = "\n".join(lines[1:]).strip()
            body = self._strip_subject_from_body(body)
            return subject, body

        return "Internship Opportunity", self._strip_subject_from_body(email_text)

    def _fallback_email(
        self,
        contact: Contact,
        metadata: CompanyMetadata,
        user_name: Optional[str],
        user_email: Optional[str] = None,
    ) -> GeneratedEmail:
        """Generate a basic fallback email if LLM fails."""
        sender_name = self.profile.resolve_name(user_name)
        signature_block = self.profile.render_signature_block(sender_name)
        intro_line = self.profile.render_introduction(sender_name)
        sender_email = self._resolve_user_email(user_email)

        subject = f"Internship Opportunity at {contact.company}"
        body = f"""Hi {contact.name},

{intro_line} I came across {contact.company}, and was thoroughly fascinated by the work you're doing. I saw that {contact.company} is developing {metadata.product or 'innovative solutions'}, which aligns closely with my interests.

In my own time I've been working on various projects involving AI, machine learning, and software development. My background in computer science and mathematics has given me a foundation to explore these ideas, and I believe working with your team would help me better understand how they operate in real systems.

I'd love to learn more about your team and hear what skills you value when preparing for this kind of role. If you're open to it, I would appreciate a quick conversation sometime in the next week.
{f"\n\nYou can also reach me at {sender_email}." if sender_email else ""}

{signature_block}"""

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
        )

    def generate_follow_up(
        self,
        contact: Contact,
        company_metadata: CompanyMetadata,
        original_email: GeneratedEmail,
        user_name: Optional[str] = None,
        user_background: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> GeneratedEmail:
        """Generate a follow-up email referencing the original."""
        sender_name = self.profile.resolve_name(user_name)
        sender_email = self._resolve_user_email(user_email)
        sender_background = user_background or self.profile.default_background
        signature_block = self.profile.render_signature_block(sender_name)
        signature_block_for_prompt = "\n".join(
            [f"  {line}" for line in signature_block.splitlines()]
        )
        sent_date_str = (
            original_email.sent_at.strftime("%B %d, %Y")
            if original_email.sent_at
            else "recently"
        )

        prompt = f"""Generate a professional follow-up email for a cold email outreach.

=== ORIGINAL EMAIL ===
Sent: {sent_date_str}
Subject: {original_email.subject}
Body: {original_email.body}

=== CONTACT INFORMATION ===
Name: {contact.name}
Company: {contact.company}

=== YOUR INFORMATION ===
Name: {sender_name}
Background: {sender_background}
Email: {sender_email}

=== COMPANY CONTEXT ===
{company_metadata.summary or ''}

=== REQUIREMENTS ===
- Length: 100-150 words (brief and professional)
- References the original email politely (e.g., "I wanted to follow up on my email from {sent_date_str}")
- Shows continued interest
- Includes a clear call to action
- Does not sound pushy or desperate
- MUST end with this exact signature block:
{signature_block_for_prompt}

=== FORMAT ===
Format your response as:
SUBJECT: [subject line - can reference original or be new]

BODY:
[email body - MUST end with this signature block:
{signature_block_for_prompt}]
"""

        try:
            response = self.client.generate(
                model=self.model,
                prompt=prompt,
                options={
                    "temperature": 0.7,
                },
            )

            email_text = response.get("response", "").strip()
            subject, body = self._parse_email(email_text)

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
                follow_up_generated_at=datetime.now(),
            )
        except Exception as e:
            print(f"Error generating follow-up email: {e}")
            return self._fallback_follow_up(contact, original_email, user_name, user_email)

    def _fallback_follow_up(
        self,
        contact: Contact,
        original_email: GeneratedEmail,
        user_name: Optional[str],
        user_email: Optional[str] = None,
    ) -> GeneratedEmail:
        """Generate a basic fallback follow-up email if LLM fails."""
        sender_name = self.profile.resolve_name(user_name)
        signature_block = self.profile.render_signature_block(sender_name)
        sender_email = self._resolve_user_email(user_email)
        sent_date_str = (
            original_email.sent_at.strftime("%B %d, %Y")
            if original_email.sent_at
            else "recently"
        )
        subject = f"Re: {original_email.subject}"

        body = f"""Hi {contact.name},

I wanted to follow up on my email from {sent_date_str} regarding internship opportunities at {contact.company}.

I remain very interested in the possibility of contributing to your team and would welcome the opportunity to discuss this further.

I'd love to learn more about your team and hear what skills you value when preparing for this kind of role. If you're open to it, I would appreciate a quick conversation sometime in the next week.
{f"\n\nYou can also reach me at {sender_email}." if sender_email else ""}

{signature_block}"""

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
            follow_up_generated_at=datetime.now(),
        )
