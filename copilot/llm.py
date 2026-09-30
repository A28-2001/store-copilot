"""Groq chat model with a fallback for rate limits, and where the key comes from.

Key order: a key typed in the app (session only) > Streamlit secrets > a local
.env file > the GROQ_API_KEY environment variable. The .env beats the shell on
purpose: a stale key exported in a shell profile should not silently win.
"""
from __future__ import annotations

import os

from copilot.config import ROOT

# The build spec named llama-3.3-70b-versatile / llama-3.1-8b-instant; Groq retired both in 2026
# (they now return 404 model_not_found), so the same strong + fast pairing uses gpt-oss.
PRIMARY_MODEL = os.environ.get("COPILOT_PRIMARY_MODEL", "openai/gpt-oss-120b")
FALLBACK_MODEL = os.environ.get("COPILOT_FALLBACK_MODEL", "openai/gpt-oss-20b")


def resolve_key(explicit: str | None = None) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    try:
        import streamlit as st
        if "GROQ_API_KEY" in st.secrets:
            return str(st.secrets["GROQ_API_KEY"]).strip()
    except Exception:  # no secrets file, or not running under Streamlit
        pass
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip().startswith("GROQ_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return os.environ.get("GROQ_API_KEY") or None


def make_llm(api_key: str, callbacks: list | None = None):
    """The strong model at temperature 0, falling back to the fast one on rate limits or errors."""
    from langchain_groq import ChatGroq

    def chat(model: str):
        return ChatGroq(model=model, temperature=0, api_key=api_key, max_retries=1, timeout=30, callbacks=callbacks)

    return chat(PRIMARY_MODEL).with_fallbacks([chat(FALLBACK_MODEL)])


def check_key(api_key: str) -> tuple[bool, str]:
    """Ask Groq to list models (free, no tokens). Returns (works, message for the sidebar)."""
    try:
        from groq import Groq
        ids = {m.id for m in Groq(api_key=api_key, timeout=8.0, max_retries=0).models.list().data}
    except Exception as err:  # 401 bad key, network down, ...
        status = getattr(err, "status_code", None)
        return False, "Groq rejected that key." if status in (401, 403) else f"Couldn't reach Groq ({type(err).__name__})."
    if PRIMARY_MODEL not in ids and FALLBACK_MODEL not in ids:
        return False, f"The key works, but {PRIMARY_MODEL} isn't available on it."
    return True, "Key works."
