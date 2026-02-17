"""Pytest configuration and shared fixtures"""
import sys
import os
from pathlib import Path

# Add backend to path
project_root = Path(__file__).parent.parent
backend_path = project_root / "backend"
sys.path.insert(0, str(backend_path))
sys.path.insert(0, str(project_root))

# Set PYTHONPATH for subprocesses
os.environ['PYTHONPATH'] = f"{backend_path}:{project_root}:{os.environ.get('PYTHONPATH', '')}"
