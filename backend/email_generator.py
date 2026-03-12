import os
import re
from typing import Dict, Optional, Any
from models import Contact, CompanyMetadata, GeneratedEmail
from datetime import datetime
import uuid
from resume_analyzer import ResumeAnalyzer

try:
    from llm_client import complete as cloud_llm_complete, get_cloud_llm_provider
except ImportError:
    cloud_llm_complete = None
    get_cloud_llm_provider = lambda: None


def _ollama_response_text(response: Any) -> str:
    """Get generated text from ollama generate() response (dict or Pydantic model)."""
    if hasattr(response, "response"):
        return (response.response or "").strip()
    if isinstance(response, dict):
        return (response.get("response") or "").strip()
    return ""


def _default_skills_path() -> str:
    """Project root is parent of backend directory."""
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(backend_dir)
    return os.path.join(project_root, "skills.md")


class EmailGenerator:
    """Generate personalized cold emails using cloud LLM or template fallback."""

    def __init__(self, model: str = "llama3.2", base_url: str = "http://localhost:11434", resume_path: str = "resume.pdf", skills_path: Optional[str] = None):
        self.model = model
        self.base_url = base_url
        self.resume_analyzer = ResumeAnalyzer(resume_path)
        self.skills_path = skills_path or os.getenv("SKILLS_FILE_PATH") or _default_skills_path()
        self._skills_content: Optional[str] = None
        self._ollama_client: Any = None

    def _get_ollama_client(self) -> Any:
        """Lazy init Ollama client only when needed (e.g. follow-up with provider=ollama)."""
        if self._ollama_client is None:
            try:
                import ollama
                self._ollama_client = ollama.Client(host=self.base_url)
            except Exception:
                pass
        return self._ollama_client

    def _load_skills(self) -> str:
        """Load background/qualifications from skills.md (cached). Use this so the model does not hallucinate."""
        if self._skills_content is not None:
            return self._skills_content
        if not os.path.exists(self.skills_path):
            self._skills_content = ""
            return ""
        try:
            with open(self.skills_path, "r", encoding="utf-8") as f:
                self._skills_content = f.read().strip()
            return self._skills_content
        except Exception as e:
            print(f"Error reading skills file {self.skills_path}: {e}")
            self._skills_content = ""
            return ""

    def _get_experience_sentence(self) -> str:
        """One sentence for the email from skills.md: '## Email one-liner' line, or first sentence of first Experience block. Fallback if missing."""
        content = self._load_skills()
        if not content:
            return "I'm a student at the University of Pennsylvania studying CS and Math with experience in Python and AI/ML."
        if "## Email one-liner" in content:
            after = content.split("## Email one-liner", 1)[-1].strip()
            if "##" in after:
                after = after.split("##")[0].strip()
            for line in after.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line.split(". ")[0].strip() + "." if "." in line else line
        if "## Experience" in content:
            exp_section = content.split("## Experience", 1)[-1].split("##")[0].strip()
            for line in exp_section.splitlines():
                line = line.strip()
                if line.startswith("###"):
                    continue
                if line and len(line) > 20:
                    first_sentence = line.split(". ")[0].strip()
                    if first_sentence.endswith("."):
                        return first_sentence
                    return first_sentence + "."
        return "I'm a student at the University of Pennsylvania studying CS and Math with experience in Python and AI/ML."

    def _build_company_details(self, metadata: CompanyMetadata, company_name: str) -> str:
        """2–3 sentences about the company using 'your'/'you', from metadata. Fallback if missing."""
        parts = []
        if metadata.summary and metadata.summary.strip():
            s = (metadata.summary or "").strip()
            if not s.endswith("."):
                s += "."
            parts.append(s)
        if metadata.product and metadata.product.strip() and (not parts or metadata.product not in (parts[-1] or "")):
            parts.append(f"Your work on {metadata.product.strip()} is especially interesting.")
        if metadata.industry and metadata.industry.strip():
            industry = (metadata.industry or "").strip()
            if not any(industry.lower() in (p or "").lower() for p in parts):
                parts.append(f"I've been curious about the {industry} space and what you're building.")
        if not parts:
            return f"I've been curious about what you're building at {company_name} and wanted to reach out."
        return " ".join(parts[:3])

    def _get_broad_field(self, metadata: CompanyMetadata) -> str:
        """Broad field for opener (e.g. 'biotech', 'robotics'). From metadata or fallback."""
        if metadata.industry and metadata.industry.strip():
            industry = (metadata.industry or "").strip().lower()
            if "bio" in industry or "health" in industry or "med" in industry:
                return "biotech"
            if "robot" in industry or "auto" in industry:
                return "robotics"
            if "ml" in industry or "ai" in industry or "nlp" in industry:
                return "machine learning"
            return industry.split(",")[0].strip() if "," in industry else industry
        return "tech"

    def _build_body_from_template(
        self,
        contact: Contact,
        metadata: CompanyMetadata,
        experience_sentence: str,
    ) -> str:
        """Build email body from fixed skeleton. Only placeholders: company name, details, experience sentence, broad field."""
        company_name = contact.company or "your company"
        contact_name = contact.name or "there"
        company_details = self._build_company_details(metadata, company_name)
        broad_field = self._get_broad_field(metadata)
        return f"""Hi {contact_name},

I'll keep this short: I was reading through a {broad_field} article and came across {company_name} — spent the last few days going deep on what you're building and had to reach out.

{company_details}

{experience_sentence} That's what drew me to your work.

I'm not coming in with a big ask — I'm trying to understand how people at the frontier of this space think, and whether there's a way I could contribute and learn, even in a limited capacity.

I'll be wrapping up my semester in May and have the summer open. I'd love to find a chance to talk about a potential opportunity at {company_name} — whether that's a summer role, internship, or just a conversation. If you'd be up for even a 15-minute call, I'd make it worth your time.

My resume and website are attached for your convenience.

Thanks so much,
Jason Li
University of Pennsylvania
Phone: 847-907-0871
Email: li59@seas.upenn.edu
Website: {self._get_website_url()}"""

    def _get_background_text(self, user_background: Optional[str], metadata: CompanyMetadata) -> str:
        """Return background/qualifications from request, skills.md, or resume (avoids hallucination)."""
        if user_background and len(user_background.strip()) >= 20:
            return user_background.strip()
        skills_text = self._load_skills()
        if skills_text:
            return skills_text
        return self.resume_analyzer.get_relevant_background(
            company_industry=metadata.industry,
            company_product=metadata.product
        )

    def _get_website_url(self) -> str:
        """Personal website URL from env; default from plan."""
        return (os.getenv("PERSONAL_WEBSITE_URL") or "https://personal-site-sooty-ten.vercel.app/").strip()

    def _get_resume_link(self) -> str:
        """Resume URL from env, or 'Resume attached' when no URL."""
        url = (os.getenv("RESUME_URL") or "").strip()
        return url if url else ""

    def _get_projects_text(self) -> str:
        """Relevant projects from skills.md (Projects section or Experience one-liners)."""
        content = self._load_skills()
        if not content:
            return ""
        if "## Projects" in content:
            section = content.split("## Projects", 1)[-1].split("##")[0].strip()
            if section:
                return section
        if "## Experience" in content:
            section = content.split("## Experience", 1)[-1].split("##")[0].strip()
            lines = []
            for line in section.splitlines():
                line = line.strip()
                if line.startswith("###") or (line and len(line) > 30):
                    lines.append(line)
            if lines:
                return "\n".join(lines[:5])
        return ""

    def _build_cold_email_prompt(self, contact: Contact, metadata: CompanyMetadata, user_background: Optional[str]) -> str:
        """Build the structured cold email prompt (user-provided)."""
        recipient_name = contact.name or "there"
        recipient_role = getattr(contact, "role", None) or "N/A"
        company_name = contact.company or "the company"
        company_description = (metadata.summary or "").strip() or "N/A"
        company_product = (metadata.product or "").strip() or "N/A"
        recent_company_update = (getattr(metadata, "recent_news", None) or metadata.hook_sentence or "").strip() or "N/A"
        recipient_background = "N/A"  # Optional; not in contact model
        my_background = self._get_background_text(user_background, metadata) or "N/A"
        my_projects = self._get_projects_text() or "N/A"
        website_link = self._get_website_url()
        resume_link = self._get_resume_link()
        resume_line = f"Resume: {resume_link}" if resume_link else ""
        return f"""
Before writing the email, silently identify the single strongest reason I am a fit for this company based on my background and projects, and make that reason explicit in the body. You are an expert startup recruiter and cold email strategist.

Your task is to write a highly personalized cold email to a senior person at a startup that maximizes reply probability.

The email must feel warm, natural, and genuinely excited from the very first line. Do not jump straight into a dry company summary. After the greeting, open in first person, like a real student reaching out.

The email should make three things clear:
1. I am genuinely interested in this company's work
2. I am a strong fit for this company specifically
3. This is an internship inquiry at {company_name}
4. If the recipient is too busy, they can point me in the right direction

INPUTS
Recipient name: {recipient_name}
Recipient role: {recipient_role}
Company name: {company_name}
Company description: {company_description}
Company product or technology: {company_product}
Recent news, launch, or feature: {recent_company_update}
Recipient background: {recipient_background}
My background: {my_background}
Relevant projects: {my_projects}
Website link: {website_link}
Resume link: {resume_link}

GOAL
Write a short cold email that is an internship inquiry at {company_name}. Ask for a short conversation or opportunity such as a summer internship, project contribution, or collaboration. Frame the entire email as an internship inquiry at this company.

OPENING RULES
The first sentence must feel warm and human.
Do not start with a dry summary of the company.
Do not start with patterns like:
- {company_name} has been doing...
- I came across your company and...
- Your company is working on...
- {company_name} recently launched...
- I am passionate about...

Instead, start in this style:
"Hi [Name], I was reading about [Company]'s work and was fascinated by [specific product, launch, technical detail, or direction]."

STYLE RULES
- Length must be 85 to 125 words
- Tone should be warm, curious, technical, and confident
- Sound like a real college student, not a recruiter
- Use simple natural language
- Do not use em dashes or any dash characters
- Do not use markdown (no asterisks in subject or body)
- Avoid generic praise
- Every sentence should feel specific to this company
- Frame the email as an internship inquiry at {company_name}
- Always write "the University of Pennsylvania", never "Penn" or "UPenn"
- Format the email body with line breaks: blank line after the greeting and between paragraphs

CONTENT RULES
- Include exactly one specific observation about the company, product, recent launch, or recipient's work
- Include exactly one relevant project or experience that shows fit
- Explicitly explain why my background makes me a strong fit for this company specifically
- Make the fit feel concrete, not vague
- Do not write a standalone intro sentence like "My name is Jason Li, a Computer Science and Mathematics student at the University of Pennsylvania." Instead, combine the intro with why your experiences apply: e.g. "My name is Jason Li, a CS and Math student at the University of Pennsylvania, and [relevant experience or project] has given me [specific skill/insight] that connects to [their work]." The name-and-school part must flow directly into how your background applies; never let it stand alone.
- Offer to help with something practical
- Mention that my website and resume are attached below for convenience
- End with a low pressure ask
- If appropriate, include a line like: "If you're swamped with work or feel someone else is better suited, I would greatly appreciate it if you could point me in the right direction."

STRUCTURE
Sentence 1: Greeting plus warm first person opening that shows genuine interest
Sentence 2: Intro and fit combined: introduce my full name (Jason Li) and the University of Pennsylvania in the same breath as why my background or project experience makes me a strong fit for this company (do not let the intro stand alone; it must flow into applicability).
Sentence 3: Offer to contribute in a practical way
Sentence 4: Mention that my website and resume are attached below for convenience
Sentence 5: Ask for a short 10 to 15 minute chat about an internship or opportunity at {company_name}, and add that if they are swamped, I would appreciate being pointed in the right direction

OUTPUT FORMAT

Subject: Internship inquiry at {company_name}
(or another specific, simple subject that makes clear this is an internship inquiry at {company_name})

Email:
[email body]

Signature
Jason Li
Website: {website_link}
{resume_line}

QUALITY CHECK
Before returning the answer, verify:
- The email starts warmly in first person after the greeting
- The first sentence does not sound like a company summary
- The intro (name, school) is never standalone; it is combined with why your background fits this company in one flowing sentence or thought
- The email clearly explains why I am a strong fit for this specific company
- The email includes a low pressure fallback ask if the recipient is too busy
- The email could not be sent unchanged to another company
- The email is framed as an internship inquiry at this company
- The body is under 125 words
- There are no dash characters
- The email explicitly states that my website and resume are attached below for convenience
- The email introduces my full name (Jason Li) in the body
- Complete email including sign-off; do not stop mid-email or mid-sentence.
Fallback sentence to use when appropriate: If you're swamped with work or feel someone else is better suited, I would greatly appreciate it if you could point me in the right direction.
"""

    def _parse_cold_email_output(self, text: str) -> tuple[str, str]:
        """Parse model output in 'Subject: ...' and 'Email:' format. Returns (subject, body)."""
        if not text:
            return "A CS student genuinely excited about what you're doing", ""
        text = text.strip()
        fallback_subject = "A CS student genuinely excited about what you're doing"
        if "Subject:" in text and "Email:" in text:
            try:
                after_subject = text.split("Subject:", 1)[-1]
                subject_line = after_subject.split("\n", 1)[0].strip()
                # Split only on "Email:" at line start (section delimiter), not "Email: li59@..." in signature
                parts = re.split(r"(?m)^Email:\s*\n", text, maxsplit=1, flags=re.IGNORECASE)
                if len(parts) >= 2:
                    after_email = parts[-1].strip()
                else:
                    after_email = text.split("Email:", 1)[-1].strip()
                body = self._strip_subject_from_body(after_email)
                if subject_line and "===" not in subject_line:
                    return subject_line, body
                return fallback_subject, body
            except Exception:
                pass
        if "SUBJECT:" in text and "BODY:" in text:
            return self._parse_email(text)
        lines = text.split("\n")
        if len(lines) >= 2 and lines[0].strip().lower().startswith("subject:"):
            subject_line = lines[0].replace("Subject:", "").replace("subject:", "").strip()
            body = "\n".join(lines[1:]).strip()
            return subject_line or fallback_subject, body
        return fallback_subject, self._strip_subject_from_body(text)

    def _remove_em_dashes(self, text: str) -> str:
        """Replace em dashes for style rules."""
        if not text:
            return text
        return text.replace("\u2014", ", ").replace("\u2013", ", ").strip()

    def _clean_subject(self, subject: str) -> str:
        """Remove markdown asterisks from subject line."""
        if not subject:
            return subject
        return subject.replace("**", "").replace("*", "").strip()

    def _format_email_body(self, body: str) -> str:
        """Remove markdown asterisks and ensure paragraph breaks for email display."""
        if not body:
            return body
        # Strip markdown bold/italic
        body = body.replace("**", "").replace("*", "")
        # If no paragraph breaks and body is long, insert after sentence endings (period + space + capital)
        if "\n\n" not in body and len(body) > 150:
            body = re.sub(r"\.\s+([A-Z])", r".\n\n\1", body)
        # Ensure greeting is followed by blank line: "Hi X," or "Hi X,\n" -> "Hi X,\n\n" (avoid double if already)
        body = re.sub(r"(Hi\s+[^,\n]+,\s*)(?:\n\n)?\n*", r"\1\n\n", body, count=1, flags=re.IGNORECASE)
        return body.strip()

    def generate(self, contact: Contact, company_metadata: CompanyMetadata,
                 user_name: Optional[str] = None, user_background: Optional[str] = None,
                 user_email: Optional[str] = None,
                 use_template_only: bool = False) -> GeneratedEmail:
        """Generate a cold email using cloud LLM when configured, else template fallback."""
        if not use_template_only and get_cloud_llm_provider() and cloud_llm_complete:
            prompt = self._build_cold_email_prompt(contact, company_metadata, user_background)
            out = cloud_llm_complete(prompt=prompt, system=None, max_tokens=4096)
            if out:
                subject, body = self._parse_cold_email_output(out)
                subject = self._clean_subject(subject)
                body = self._remove_em_dashes(body)
                body = self._format_email_body(body)
                if subject and body:
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
        experience_sentence = self._get_experience_sentence()
        try:
            subject = f"A CS student genuinely excited about what {contact.company} is doing"
            body = self._build_body_from_template(contact, company_metadata, experience_sentence)
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
                used_template_fallback=True,
                fallback_reason="user_requested" if use_template_only else "llm_unavailable",
            )
        except Exception as e:
            print(f"Error building email: {e}")
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
        
        background_text = self._get_background_text(user_background, metadata)
        background_part = f"\nMy background (use only this information, do not invent details):\n{background_text}" if background_text else ""

        email_part = f"\nMy email: {user_email}" if user_email else ""

        company_info = personalization.strip() if personalization.strip() else f"No detailed information available for {contact.company}."
        has_company_info = bool(personalization.strip())

        prompt = f"""Write a cold email closely following the TEMPLATE below. Fill in every blank using COMPANY INFORMATION and YOUR INFORMATION. Keep it concise. Do NOT use square brackets anywhere in the output.

=== RECIPIENT ===
Name: {contact.name}
Company: {contact.company}

=== YOUR INFORMATION ===
{name_part}{background_part}{email_part}

=== COMPANY INFORMATION (use ONLY this to describe {contact.company}) ===
{company_info}

=== TEMPLATE (follow this structure and tone exactly, filling in the blanks with real details) ===

Subject: A CS student genuinely excited about what {contact.company} is doing

Hi {contact.name},

I'll keep this short: I was reading through a [broad field] article and came across {contact.company} — spent the last few days going deep on what you're building and had to reach out.

[About the company — exactly 2 long sentences OR 3 short sentences. Address the recipient: say what you (the company) do, what makes you interesting, and why you're drawn to them. Use "your" and "you" (e.g. "your platform", "what you're building"). Include at least one concrete detail from COMPANY INFORMATION. If company info is thin, write 2 sentences e.g. that you've been curious about what you're building and would like to learn more.]

I'm a student at the University of Pennsylvania studying CS and Math, and I've been [one sentence only: one relevant project or experience from YOUR INFORMATION that fits their field, with specific tools — e.g. "working on computer vision for zebrafish behavior with OpenCV and YOLO"]. That's what drew me to [your work / your space — use "your", not "their"].

I'm not coming in with a big ask — I'm trying to understand how people at the frontier of this space think, and whether there's a way I could contribute and learn, even in a limited capacity.

I'll be wrapping up my semester in May and have the summer open. If you'd be up for even a 20-minute call, I'd make it worth your time.

Thanks so much,
Jason Li
University of Pennsylvania
Phone: 847-907-0871
Email: li59@seas.upenn.edu
Website: https://personal-site-sooty-ten.vercel.app/

=== RULES ===
- Follow the template wording as closely as possible. Only change what's in brackets.
- Opening line: Start with "I'll keep this short: " then the rest. Replace [broad field] with a general category so it reads like "I was reading through a [X] article" — e.g. "biotech", "immunotherapy", "robotics", "machine learning", "NLP". Use the broad field from COMPANY INFORMATION (e.g. "a biotech article", "a robotics article"). Keep it broad. Never leave brackets.
- BE SPECIFIC when filling blanks:
  * Company (critical): The "why them" paragraph must be exactly 2 long sentences OR 3 short sentences — more about them than about you. Include at least one concrete detail from COMPANY INFORMATION (product name, problem they solve, approach). Never leave it generic or empty.{" If company info is thin, write 2 short sentences that you're curious about what you're building." if not has_company_info else ""}
  * You: Exactly ONE sentence about yourself — one relevant project or experience from YOUR INFORMATION that fits their field, with specific tools. No second sentence listing another project. Keep "why you" short so "why them" gets more space.
- You are writing TO the recipient (someone at the company). Use "your" and "you" when referring to the company — e.g. "what you're building", "your platform", "your approach", "your team". Never use "their" or "they" for the company (not "their innovative approach" or "what they're building").
- The tone is casual-confident, genuine, slightly understated. Not salesy, not formal.
- ZERO square brackets in the output. Every blank must be filled with real words.
- Do NOT invent company details. Only use what is in COMPANY INFORMATION.
- Do NOT describe the company using phrases from YOUR INFORMATION.
- Keep the same length as the template — do not make it longer.

=== FORMAT ===
Your reply must contain ONLY these two parts — nothing else. Do not repeat any instructions, recipient block, or template.
Start your reply with this line:
SUBJECT: <your subject line>
Then on the next line:
BODY:
Then the email body (the actual email text).

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

    def _fill_brackets(self, body: str, metadata: CompanyMetadata) -> str:
        """Remove any [bracketed] placeholders, replacing with company metadata when available."""
        if not body or '[' not in body:
            return body

        snippets = {
            'summary': (metadata.summary or '').strip(),
            'product': (metadata.product or '').strip(),
            'industry': (metadata.industry or '').strip(),
            'hook': (metadata.hook_sentence or '').strip(),
        }

        used_snippets: set[str] = set()

        def pick_snippet(inner: str) -> str:
            inner_lower = inner.lower()
            # Try to match bracket content to the right metadata field
            if any(w in inner_lower for w in ['product', 'project', 'technology', 'develop', 'build']):
                if snippets['product'] and 'product' not in used_snippets:
                    used_snippets.add('product')
                    return snippets['product']
            if any(w in inner_lower for w in ['company', 'description', 'about', 'does', 'work']):
                if snippets['summary'] and 'summary' not in used_snippets:
                    used_snippets.add('summary')
                    return snippets['summary']
            if any(w in inner_lower for w in ['industry', 'sector', 'field', 'area', 'interest']):
                if snippets['industry'] and 'industry' not in used_snippets:
                    used_snippets.add('industry')
                    return snippets['industry']
            # Fallback: use first available unused snippet
            for key in ['summary', 'product', 'industry', 'hook']:
                if snippets[key] and key not in used_snippets:
                    used_snippets.add(key)
                    return snippets[key]
            # Nothing available — remove the bracket entirely
            return ''

        def repl(m: re.Match) -> str:
            inner = m.group(0)[1:-1]
            return pick_snippet(inner)

        result = re.sub(r'\[[^\]]*\]', repl, body)
        # Clean up artifacts: double spaces, trailing commas before periods
        result = re.sub(r'  +', ' ', result)
        result = re.sub(r' ,', ',', result)
        result = re.sub(r' \.', '.', result)
        return result

    def _strip_prompt_leakage(self, text: str) -> str:
        """If the model echoed the prompt, keep only the part that is the actual reply (SUBJECT: + BODY:)."""
        if not text or "SUBJECT:" not in text:
            return text
        # Model sometimes echoes the whole prompt; the real reply is at the end. Use last SUBJECT:.
        subject_marker = "SUBJECT:"
        last_idx = text.rfind(subject_marker)
        if last_idx >= 0:
            return text[last_idx:].strip()
        return text

    def _parse_email(self, email_text: str) -> tuple[str, str]:
        """Parse email text into subject and body. Strips any echoed prompt, then extracts SUBJECT and BODY."""
        # Strip prompt leakage (echoed instructions / recipient block / template)
        email_text = self._strip_prompt_leakage(email_text)

        # Look for SUBJECT: and BODY: markers
        if "SUBJECT:" in email_text and "BODY:" in email_text:
            parts = email_text.split("BODY:", 1)  # split once so body can contain "BODY:"
            subject_part = parts[0].replace("SUBJECT:", "").strip()
            body = parts[1].strip() if len(parts) > 1 else email_text
            body = self._strip_subject_from_body(body)
            # If subject still looks like prompt (headers/instructions), use fallback
            if "===" in subject_part or "RECIPIENT" in subject_part or subject_part.startswith("Name:"):
                subject_part = "A CS student genuinely excited about what they're doing"
            return subject_part, body

        # If no markers, try to extract first line as subject
        lines = email_text.split('\n')
        if len(lines) > 1:
            subject = lines[0].strip()
            body = '\n'.join(lines[1:]).strip()
            body = self._strip_subject_from_body(body)
            if "===" in subject or "RECIPIENT" in subject or subject.startswith("Name:"):
                subject = "A CS student genuinely excited about what they're doing"
            return subject, body

        # Fallback
        return "A CS student genuinely excited about what they're doing", self._strip_subject_from_body(email_text)
    
    def _fallback_email(self, contact: Contact, metadata: CompanyMetadata, 
                       user_name: Optional[str], user_email: Optional[str] = None) -> GeneratedEmail:
        """Fallback when template build fails; use same template with default experience sentence."""
        experience_sentence = self._get_experience_sentence()
        subject = f"A CS student genuinely excited about what {contact.company} is doing"
        body = self._build_body_from_template(contact, metadata, experience_sentence)
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
        
        prompt = f"""Generate a professional follow-up email about a summer work opportunity (e.g. summer internship).

=== ORIGINAL EMAIL ===
Sent: {sent_date_str}
Subject: {original_email.subject}
Body: {original_email.body}

=== CONTACT INFORMATION ===
Name: {contact.name}
Company: {contact.company}

=== YOUR INFORMATION ===
Name: {user_name or 'Jason Li'}
Background (use only this, do not invent): {self._get_background_text(user_background, company_metadata)}
Email: {user_email or 'jason.ye.li.7@gmail.com'}

=== COMPANY CONTEXT ===
{company_metadata.summary or ''}

=== REQUIREMENTS ===
- Length: 100-150 words (brief and professional)
- Mention that you're interested in a summer work opportunity / summer internship.
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
SUBJECT: your follow-up subject line here

BODY:
your follow-up email body here — MUST end with "Thanks so much, Jason Li" followed by:
University of Pennsylvania
Phone: 847-907-0871
Email: li59@seas.upenn.edu
Website: https://personal-site-sooty-ten.vercel.app/
"""
        
        try:
            email_text = None
            if get_cloud_llm_provider() and cloud_llm_complete:
                email_text = cloud_llm_complete(prompt=prompt, system=None, max_tokens=1024)
            if not email_text:
                client = self._get_ollama_client()
                if client:
                    response = client.generate(model=self.model, prompt=prompt, options={"temperature": 0.7})
                    email_text = _ollama_response_text(response)
            if email_text:
                subject, body = self._parse_email(email_text)
                body = self._fill_brackets(body, company_metadata)
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
            return self._fallback_follow_up(contact, original_email, user_name, user_email)
        except Exception as e:
            print(f"Error generating follow-up email: {e}")
            return self._fallback_follow_up(contact, original_email, user_name, user_email)
    
    def _fallback_follow_up(self, contact: Contact, original_email: GeneratedEmail,
                           user_name: Optional[str], user_email: Optional[str] = None) -> GeneratedEmail:
        """Generate a basic fallback follow-up email if LLM fails"""
        sent_date_str = original_email.sent_at.strftime('%B %d, %Y') if original_email.sent_at else "recently"
        subject = f"Re: {original_email.subject}"
        
        body = f"""Hi {contact.name},

I wanted to follow up on my email from {sent_date_str} regarding a summer work opportunity at {contact.company}.

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
