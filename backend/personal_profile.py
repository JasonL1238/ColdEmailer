from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

try:
    import yaml
except Exception:  # pragma: no cover - runtime fallback when PyYAML is unavailable
    yaml = None


DEFAULT_PROFILE: Dict[str, Any] = {
    "personal": {
        "full_name": "Your Name",
        "default_user_email": "you@example.com",
        "default_background": "",
        "introduction": "I'm {name}, and I'm reaching out about internship opportunities.",
        "signature": {
            "closing_format": "Thanks so much, {name}",
            "lines": [
                "Email: you@example.com",
            ],
        },
    }
}


@dataclass
class PersonalProfile:
    full_name: str
    default_user_email: str
    default_background: str
    introduction: str
    signature_closing_format: str
    signature_lines: List[str] = field(default_factory=list)

    def resolve_name(self, user_name: Optional[str] = None) -> str:
        if user_name and user_name.strip():
            return user_name.strip()
        return self.full_name.strip()

    def render_introduction(self, user_name: Optional[str] = None) -> str:
        name = self.resolve_name(user_name)
        try:
            return self.introduction.format(name=name).strip()
        except Exception:
            return self.introduction.strip()

    def render_signature_lines(self, user_name: Optional[str] = None) -> List[str]:
        name = self.resolve_name(user_name)
        rendered = []
        for line in self.signature_lines:
            try:
                value = str(line).format(name=name).strip()
            except Exception:
                value = str(line).strip()
            if value:
                rendered.append(value)
        return rendered

    def render_signature_block(self, user_name: Optional[str] = None) -> str:
        name = self.resolve_name(user_name)
        try:
            closing = self.signature_closing_format.format(name=name).strip()
        except Exception:
            closing = self.signature_closing_format.strip()
        parts = [closing] + self.render_signature_lines(user_name)
        return "\n".join([line for line in parts if line])

    def to_frontend_defaults(self) -> Dict[str, str]:
        return {
            "user_name": self.full_name,
            "user_background": self.default_background or "",
            "user_email": self.default_user_email or "",
        }


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def default_profile_path() -> str:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(project_root, "personal_profile.yaml")


def load_personal_profile(config_path: Optional[str] = None) -> PersonalProfile:
    path = config_path or os.getenv("PERSONAL_CONFIG_PATH") or default_profile_path()
    loaded: Dict[str, Any] = {}

    if os.path.exists(path):
        if yaml is None:
            print(
                f"Warning: PyYAML is not installed; cannot read profile config at {path}. "
                "Using default profile values."
            )
        else:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    parsed = yaml.safe_load(f) or {}
                    if isinstance(parsed, dict):
                        loaded = parsed
            except Exception as exc:
                print(f"Warning: failed to load profile config at {path}: {exc}")

    merged = _deep_merge(DEFAULT_PROFILE, loaded)
    personal = merged.get("personal", {})
    signature = personal.get("signature", {})

    return PersonalProfile(
        full_name=str(personal.get("full_name", "")).strip() or DEFAULT_PROFILE["personal"]["full_name"],
        default_user_email=str(personal.get("default_user_email", "")).strip(),
        default_background=str(personal.get("default_background", "")).strip(),
        introduction=str(personal.get("introduction", "")).strip() or DEFAULT_PROFILE["personal"]["introduction"],
        signature_closing_format=str(signature.get("closing_format", "")).strip()
        or DEFAULT_PROFILE["personal"]["signature"]["closing_format"],
        signature_lines=[str(line) for line in signature.get("lines", []) if str(line).strip()],
    )
