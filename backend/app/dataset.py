import json
import re
from difflib import get_close_matches
from .config import ROOT

APPS = json.loads((ROOT / "data" / "apps.json").read_text())
assert len(APPS) == 100

def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()

ALIASES = {
    "monday": "Monday.com", "larksuite": "Lark (Larksuite)",
    "magento": "Magento (Adobe Commerce)", "threads": "Threads (Meta)",
    "otter": "Otter AI", "salesforce commerce": "Salesforce Commerce Cloud",
    "whatsapp": "WhatsApp Business", "youtube transcript": "YouTube Transcript",
}

def resolve(name: str):
    query = normalize(name)
    for app in APPS:
        if normalize(app["name"]) == query:
            return app
    if query in ALIASES:
        return resolve(ALIASES[query])
    names = {normalize(app["name"]): app for app in APPS}
    matches = get_close_matches(query, list(names), n=1, cutoff=0.86)
    return names[matches[0]] if matches else None

def mentioned_app(message: str):
    normal = f" {normalize(message)} "
    candidates = sorted(APPS, key=lambda app: len(normalize(app["name"])), reverse=True)
    for app in candidates:
        key = normalize(app["name"])
        if f" {key} " in normal:
            return app
    for alias, name in ALIASES.items():
        if f" {normalize(alias)} " in normal:
            return resolve(name)
    candidate = re.search(r"(?:about|research|investigate|for|on|to)\s+(.+?)(?:\?|\.|$)", message, re.I)
    if candidate:
        value = re.sub(r"\b(?:integration|credentials|authentication|auth|api|mcp)\b.*$", "", candidate.group(1), flags=re.I).strip()
        return resolve(value)
    return None

def explicit_subject(message: str):
    match = re.search(r"(?:tell me about|research|investigate|now about|look up)\s+(.+?)(?:\?|\.|$)", message, re.I)
    return match.group(1).strip() if match else None

def in_scope(message: str) -> bool:
    return bool(re.search(r"\b(auth|oauth|credentials?|tokens?|apis?|rest|graphql|webhooks?|mcp|integrations?|integrate|build|blockers?|sources?|documentation|research|support|it|this app|access|developer)\b", message, re.I))
