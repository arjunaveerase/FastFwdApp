from __future__ import annotations

import json
import os
from typing import Optional, Dict, Any
from urllib.parse import urlencode

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request


# Scopes needed for operations
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


def _get_redirect_uri() -> str:
    return os.getenv("GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/auth/google/callback").strip()


def build_google_auth_url(state: Optional[str] = None) -> str:
    """
    FIX: Replaced Flow class with direct parameter query string mapping.
    This bypasses local file errors and seamlessly returns the redirection URL to Next.js.
    """
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    redirect_uri = _get_redirect_uri()

    if not client_id:
        raise ValueError("Missing GOOGLE_CLIENT_ID in environment variables.")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "include_granted_scopes": "true",
        "prompt": "consent",
    }
    if state:
        params["state"] = state

    return f"https://accounts.google.com/o/oauth2/auth?{urlencode(params)}"


def credentials_from_dict(token_data: Dict[str, Any]) -> Credentials:
    creds = Credentials(
        token=token_data.get("token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri=token_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=token_data.get("client_id") or os.getenv("GOOGLE_CLIENT_ID"),
        client_secret=token_data.get("client_secret") or os.getenv("GOOGLE_CLIENT_SECRET"),
        scopes=token_data.get("scopes") or SCOPES,
    )
    return creds


# -------------------------
# Automated Session Hooks
# -------------------------

def save_user_credentials(user_email: str, token_data: Dict[str, Any]) -> None:
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tokens")
    os.makedirs(base, exist_ok=True)

    safe = user_email.replace("@", "_at_").replace(".", "_")
    path = os.path.join(base, f"{safe}.json")

    with open(path, "w", encoding="utf-8") as f:
        json.dump(token_data, f)


def load_user_credentials(user_email: str) -> Optional[Credentials]:
    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tokens")
    safe = user_email.replace("@", "_at_").replace(".", "_")
    path = os.path.join(base, f"{safe}.json")

    if not os.path.exists(path):
        return None

    with open(path, "r", encoding="utf-8") as f:
        token_data = json.load(f)

    creds = credentials_from_dict(token_data)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_data["token"] = creds.token
        save_user_credentials(user_email, token_data)

    return creds


def build_credentials_from_tokens(access_token: str, refresh_token: str | None = None):
    token_data = {
        "token": access_token,
        "refresh_token": refresh_token,
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "scopes": SCOPES,
    }

    creds = credentials_from_dict(token_data)

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return creds