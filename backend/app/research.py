"""Tavily retrieval, parallel LangGraph research, and evidence verification."""
import asyncio
import operator
from datetime import datetime, timezone
from typing import Annotated, Literal, TypedDict
from urllib.parse import urlparse

import httpx
from google import genai
from google.genai import types
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

from .config import TAVILY_API_KEY, GEMINI_API_KEY, GEMINI_MODEL

DIMENSIONS = ("auth", "credentials", "api", "mcp")
QUERIES = {
    "auth": "authentication OAuth API key developer documentation",
    "credentials": "create developer app obtain API credentials pricing approval partner access",
    "api": "public REST GraphQL API reference endpoints webhooks SDK",
    "mcp": "official MCP server Model Context Protocol integration",
}

class DraftClaim(BaseModel):
    text: str = Field(description="One precise factual claim")
    tag: Literal["description", "oauth2", "api_key", "basic", "token", "jwt", "self_serve", "trial", "free_access", "paid_plan", "admin_approval", "partner_gated", "rest", "graphql", "public_api", "webhooks", "sdk", "official_mcp", "mcp_other", "blocker", "other"]
    source_url: str
    evidence: str = Field(description="Exact short excerpt copied from the source text")

class DraftClaims(BaseModel):
    claims: list[DraftClaim] = Field(default_factory=list, max_length=8)

class Verification(BaseModel):
    status: Literal["supported", "unsupported", "contradicted", "uncertain"]
    reason: str

class GraphState(TypedDict, total=False):
    app: dict
    sources: list[dict]
    claims: Annotated[list[dict], operator.add]
    verified: list[dict]
    errors: Annotated[list[str], operator.add]
    progress: str

def configured() -> bool:
    return bool(TAVILY_API_KEY and GEMINI_API_KEY)

async def tavily(endpoint: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=50) as client:
        response = await client.post(
            f"https://api.tavily.com/{endpoint}",
            headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
            json=payload,
        )
        response.raise_for_status()
        return response.json()

def valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)

async def discover(state: GraphState) -> dict:
    app = state["app"]
    hint = app["hint"].split(" (")[0].strip()
    seed = f"https://{hint}" if "." in hint and " " not in hint else None
    searches = await asyncio.gather(*[
        tavily("search", {
            "query": f'{app["name"]} {query} site:{hint}' if "/" not in hint else f'{app["name"]} {query} {hint}',
            "search_depth": "basic", "max_results": 4,
        }) for query in QUERIES.values()
    ], return_exceptions=True)
    urls: dict[str, str] = {seed: f'{app["name"]} supplied hint' } if seed and valid_url(seed) else {}
    errors = []
    for result in searches:
        if isinstance(result, Exception):
            errors.append(f"Tavily search: {type(result).__name__}: {result}")
            continue
        for item in result.get("results", []):
            url = item.get("url", "")
            if valid_url(url):
                urls[url] = item.get("title", url)
            if len(urls) >= 12:
                break
    if len(urls) <= (1 if seed else 0):
        try:
            fallback = await tavily("search", {"query": f'{app["name"]} official developer API authentication documentation', "search_depth": "basic", "max_results": 5})
            for item in fallback.get("results", []):
                if valid_url(item.get("url", "")):
                    urls[item["url"]] = item.get("title", item["url"])
        except Exception as exc:
            errors.append(f"Tavily fallback search: {type(exc).__name__}: {exc}")
    if not urls:
        return {"sources": [], "errors": errors or ["No source URLs found"], "progress": "No documentation found"}
    try:
        extracted = await tavily("extract", {"urls": list(urls)[:12], "extract_depth": "basic"})
    except Exception as exc:
        return {"sources": [], "errors": errors + [f"Tavily extract: {type(exc).__name__}: {exc}"], "progress": "Extraction failed"}
    sources = []
    for item in extracted.get("results", []):
        url = item.get("url", "")
        content = item.get("raw_content") or ""
        if url in urls and len(content.strip()) >= 100:
            sources.append({"url": url, "title": urls[url], "content": content[:16000], "retrieved_at": datetime.now(timezone.utc).isoformat()})
    return {"sources": sources, "errors": errors, "progress": "Sources extracted"}

async def generate(schema, prompt: str):
    client = genai.Client(api_key=GEMINI_API_KEY)
    try:
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0),
        )
        return schema.model_validate_json(response.text)
    finally:
        await client.aio.aclose()

async def specialist(state: GraphState, dimension: str) -> dict:
    sources = state.get("sources", [])
    if not sources:
        return {"claims": [], "errors": [f"{dimension}: no extracted sources"]}
    evidence = "\n\n".join(f'URL: {s["url"]}\nCONTENT:\n{s["content"][:9000]}' for s in sources[:8])
    prompt = (
        f'For {state["app"]["name"]}, research only the {dimension} dimension. '
        f'Focus: {QUERIES[dimension]}. Use only the supplied extracted source text. '
        + ("Also include one concise claim about what this application does, tagged description, only if directly supported. " if dimension == "api" else "") +
        "Return at most 6 precise claims. Every evidence field must be an exact contiguous quote from its source. "
        "Use exact source URLs from the input. If there is no direct evidence, return an empty claim list. "
        "Never infer absence of an API, MCP, or access path from silence.\n\n" + evidence
    )
    try:
        output = await generate(DraftClaims, prompt)
        return {"claims": [{**claim.model_dump(), "dimension": "overview" if claim.tag == "description" else dimension} for claim in output.claims], "errors": []}
    except Exception as exc:
        return {"claims": [], "errors": [f"{dimension}: {type(exc).__name__}: {exc}"]}

def source_for_claim(claim: dict, sources: list[dict]):
    return next((source for source in sources if source["url"] == claim["source_url"]), None)

def exact_quote_present(quote: str, content: str) -> bool:
    return " ".join(quote.split()).casefold() in " ".join(content.split()).casefold()

async def verify_one(claim: dict, sources: list[dict]) -> dict:
    source = source_for_claim(claim, sources)
    if not source:
        return {**claim, "status": "unsupported", "reason": "Source URL was not extracted"}
    if not claim["evidence"].strip() or not exact_quote_present(claim["evidence"], source["content"]):
        return {**claim, "status": "unsupported", "reason": "Evidence quote was not found in the extracted source"}
    prompt = (
        "Judge whether the claim follows from this exact source excerpt. Do not use outside knowledge. "
        "Choose supported only if the excerpt directly entails the entire claim; contradicted if it says the opposite; "
        "unsupported if irrelevant; uncertain if partially supported or ambiguous.\n"
        f'Claim: {claim["text"]}\nSource URL: {source["url"]}\nExact excerpt: {claim["evidence"]}'
    )
    try:
        verdict = await generate(Verification, prompt)
        return {**claim, **verdict.model_dump()}
    except Exception as exc:
        return {**claim, "status": "uncertain", "reason": f"Verification unavailable: {type(exc).__name__}"}

async def verify(state: GraphState) -> dict:
    claims = state.get("claims", [])
    verified = await asyncio.gather(*(verify_one(claim, state.get("sources", [])) for claim in claims))
    retry_dim = next((dimension for dimension in DIMENSIONS
                      if any(c["dimension"] == dimension and c["status"] != "supported" for c in verified)
                      and not any(c["dimension"] == dimension and c["status"] == "supported" for c in verified)), None)
    if not retry_dim:
        return {"verified": verified, "progress": "Claims verified"}
    app = state["app"]
    try:
        found = await tavily("search", {"query": f'{app["name"]} official {QUERIES[retry_dim]} documentation',
                                         "search_depth": "basic", "max_results": 3})
        prior = {source["url"] for source in state.get("sources", [])}
        urls = [item["url"] for item in found.get("results", []) if valid_url(item.get("url", "")) and item["url"] not in prior][:2]
        if not urls:
            return {"verified": verified, "progress": "Claims verified"}
        extracted = await tavily("extract", {"urls": urls, "extract_depth": "basic"})
        added = [{"url": item["url"], "title": item["url"], "content": item.get("raw_content", "")[:16000],
                  "retrieved_at": datetime.now(timezone.utc).isoformat()} for item in extracted.get("results", [])
                 if item.get("url") in urls and len(item.get("raw_content", "").strip()) >= 100]
        if not added:
            return {"verified": verified, "progress": "Claims verified"}
        retry = await specialist({"app": app, "sources": added}, retry_dim)
        retry_claims = await asyncio.gather(*(verify_one(claim, added) for claim in retry.get("claims", [])))
        return {"sources": state.get("sources", []) + added, "verified": verified + retry_claims,
                "errors": retry.get("errors", []), "progress": "Claims verified after targeted retry"}
    except Exception as exc:
        return {"verified": verified, "errors": [f"Targeted retry: {type(exc).__name__}: {exc}"], "progress": "Claims verified"}

def build_graph():
    graph = StateGraph(GraphState)
    graph.add_node("discover", discover)
    for dimension in DIMENSIONS:
        async def research_dimension(state: GraphState, d=dimension):
            return await specialist(state, d)
        graph.add_node(dimension, research_dimension)
    graph.add_node("verify", verify)
    graph.add_edge(START, "discover")
    for dimension in DIMENSIONS:
        graph.add_edge("discover", dimension)
    graph.add_edge(list(DIMENSIONS), "verify")
    graph.add_edge("verify", END)
    return graph.compile()

GRAPH = build_graph()

def assess_buildability(claims: list[dict]) -> tuple[str, str | None]:
    supported = [claim for claim in claims if claim["status"] == "supported"]
    tags = {claim["tag"].casefold() for claim in supported}
    blocker = next((c["text"] for c in supported if c["tag"].casefold() in {"blocker", "partner_gated"}), None)
    if blocker:
        return "Blocked", blocker
    has_api = bool(tags & {"rest", "graphql", "public_api", "api"})
    has_auth = bool(tags & {"oauth2", "api_key", "basic", "token", "jwt"})
    has_access = bool(tags & {"self_serve", "trial", "free_access"})
    if has_api and has_auth and has_access:
        return "Possible", None
    access_limit = next((c["text"] for c in supported if c["tag"].casefold() in {"paid_plan", "admin_approval"}), None)
    if access_limit:
        return "Unclear", access_limit
    return "Unclear", "Insufficient verified evidence for API, authentication, or credential access"
