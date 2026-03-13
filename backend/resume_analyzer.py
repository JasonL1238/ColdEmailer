"""Extract relevant background information from resume based on company context"""
from typing import Optional
import os
from pypdf import PdfReader


class ResumeAnalyzer:
    """Analyze resume and extract relevant background for email personalization"""
    
    def __init__(self, resume_path: str = "resume.pdf"):
        self.resume_path = resume_path
        self._resume_text = None
    
    def _load_resume(self) -> str:
        """Load and extract text from resume PDF"""
        if self._resume_text:
            return self._resume_text
        
        if not os.path.exists(self.resume_path):
            return ""
        
        try:
            reader = PdfReader(self.resume_path)
            self._resume_text = ''.join([page.extract_text() for page in reader.pages])
            return self._resume_text
        except Exception as e:
            print(f"Error reading resume: {e}")
            return ""
    
    def get_relevant_background(self, company_industry: Optional[str] = None, 
                                company_product: Optional[str] = None) -> str:
        """
        Extract relevant background from resume based on company context.
        Returns detailed background information for email generation.
        """
        resume_text = self._load_resume()
        if not resume_text:
            return "Computer Science student with experience in Python and AI/ML"
        
        # Extract specific experiences and skills
        background_parts = []
        
        # Penn Aerial Robotics
        if any(term in resume_text.lower() for term in ['penn aerial', 'px4', 'ros2', 'uav', 'drone', 'autonomous', 'flight-control']):
            background_parts.append("I currently work with Penn Aerial Robotics, where I contribute to autonomous UAV systems and related software")
        
        # Campbell Labs research
        if any(term in resume_text.lower() for term in ['campbell', 'yolo', 'opencv', 'computer vision', 'object detection', 'neural', 'ai research']):
            background_parts.append("I have conducted significant AI and computer vision research at Campbell Labs at Penn")
        
        # IEEE paper
        if any(term in resume_text.lower() for term in ['ieee', 'published', 'co-authored']):
            background_parts.append("I've published an AI paper in IEEE")
        
        # Technical skills
        skills = []
        if 'python' in resume_text.lower():
            skills.append('Python')
        if 'c#' in resume_text.lower():
            skills.append('C#')
        if any(term in resume_text.lower() for term in ['ros', 'ros2']):
            skills.append('ROS')
        if 'opencv' in resume_text.lower():
            skills.append('OpenCV')
        if 'pytorch' in resume_text.lower():
            skills.append('PyTorch')
        if 'tensorflow' in resume_text.lower():
            skills.append('TensorFlow')
        if 'react' in resume_text.lower():
            skills.append('React')
        
        skills_text = ""
        if skills:
            skills_text = f" These experiences have helped me build strong skills in {', '.join(skills)}."
        
        # Combine background
        if background_parts:
            background = ". ".join(background_parts) + "." + skills_text
        else:
            background = "Computer Science student with experience in Python, React, and AI/ML" + skills_text
        
        return background
