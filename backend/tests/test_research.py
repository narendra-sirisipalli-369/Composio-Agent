import pytest
import asyncio
from fastapi.testclient import TestClient

from app.dataset import APPS, resolve, mentioned_app
from app.research import exact_quote_present, assess_buildability
from app.main import app, question_focus
from app import research

def test_dataset_and_resolver():
    assert len(APPS) == 100
    assert len({a["id"] for a in APPS}) == 100
    assert resolve("salesforce")["id"] == 1
    assert resolve("Salesforse")["id"] == 1
    assert resolve("Notion")["id"] == 71
    assert resolve("Figma") is None
    assert mentioned_app("How does Slack authentication work?")["name"] == "Slack"
    assert mentioned_app("Tell me about Salesforce Commerce Cloud")["id"] == 44

def test_quote_and_buildability_guardrails():
    assert exact_quote_present("OAuth 2.0  authorization", "Uses OAuth 2.0 authorization for apps")
    assert not exact_quote_present("API keys are free", "API keys require approval")
    assert assess_buildability([])[0] == "Unclear"
    claims = [{"status": "supported", "tag": tag, "text": tag} for tag in ("rest", "oauth2", "self_serve")]
    assert assess_buildability(claims)[0] == "Possible"
    claims[2]["status"] = "uncertain"
    assert assess_buildability(claims)[0] == "Unclear"
    assert question_focus("How do I get credentials?") == "credentials"
    assert question_focus("Does it support MCP?") == "mcp"

def test_scope_and_context():
    with TestClient(app) as client:
        apps = client.get("/api/apps").json()
        assert len(apps) == 100
        first = client.post("/api/chat", json={"message": "Tell me about Salesforce"}).json()
        assert first["app"]["id"] == 1
        followup = client.post("/api/chat", json={"message": "How do I get credentials?", "session_id": first["session_id"]}).json()
        assert followup["app"]["id"] == 1
        switched = client.post("/api/chat", json={"message": "Now tell me about HubSpot", "session_id": first["session_id"]}).json()
        assert switched["app"]["id"] == 2
        outside = client.post("/api/chat", json={"message": "Tell me about Figma", "session_id": first["session_id"]}).json()
        assert outside["type"] == "not_found"
        weather = client.post("/api/chat", json={"message": "What is the weather today?", "session_id": first["session_id"]}).json()
        assert weather["type"] == "out_of_scope"

def test_graph_fanout_and_verification(monkeypatch):
    async def fake_tavily(endpoint, payload):
        if endpoint == "search":
            return {"results": [{"url": "https://example.com/docs", "title": "Example docs"}]}
        return {"results": [{"url": "https://example.com/docs", "raw_content": "Applications use OAuth 2.0 authorization for developer access. " * 5}]}
    async def fake_generate(schema, prompt):
        if schema is research.DraftClaims:
            return research.DraftClaims(claims=[research.DraftClaim(
                text="Applications use OAuth 2.0 authorization", tag="oauth2",
                source_url="https://example.com/docs", evidence="Applications use OAuth 2.0 authorization")])
        return research.Verification(status="supported", reason="The excerpt directly says this")
    monkeypatch.setattr(research, "tavily", fake_tavily)
    monkeypatch.setattr(research, "generate", fake_generate)
    result = asyncio.run(research.GRAPH.ainvoke({"app": APPS[0], "sources": [], "claims": [], "verified": [], "errors": []}))
    assert len(result["verified"]) == 4
    assert all(c["status"] == "supported" for c in result["verified"])

def test_targeted_retry_for_bad_evidence(monkeypatch):
    async def fake_tavily(endpoint, payload):
        if endpoint == "search":
            return {"results": [{"url": "https://example.com/new", "title": "New docs"}]}
        return {"results": [{"url": "https://example.com/new", "raw_content": "The API uses OAuth 2.0 authorization. " * 5}]}
    async def fake_generate(schema, prompt):
        if schema is research.DraftClaims:
            return research.DraftClaims(claims=[research.DraftClaim(
                text="The API uses OAuth 2.0 authorization", tag="oauth2",
                source_url="https://example.com/new", evidence="The API uses OAuth 2.0 authorization")])
        return research.Verification(status="supported", reason="Direct support")
    monkeypatch.setattr(research, "tavily", fake_tavily)
    monkeypatch.setattr(research, "generate", fake_generate)
    state = {"app": APPS[0], "sources": [{"url": "https://example.com/old", "content": "No relevant evidence", "title": "Old"}],
             "claims": [{"dimension": "auth", "text": "OAuth is used", "tag": "oauth2", "source_url": "https://example.com/old", "evidence": "OAuth is used"}]}
    result = asyncio.run(research.verify(state))
    assert [c["status"] for c in result["verified"]] == ["unsupported", "supported"]
    assert len(result["sources"]) == 2
