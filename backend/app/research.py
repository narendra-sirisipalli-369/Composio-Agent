"""Tavily retrieval, parallel LangGraph research, and evidence verification."""
import asyncio
import operator
import re
import time
from datetime import datetime, timezone
from typing import Annotated, Literal, TypedDict
from urllib.parse import urlparse

import httpx
from google import genai
from google.genai import types
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

from .config import TAVILY_API_KEY, GEMINI_API_KEY, GEMINI_MODEL, GEMINI_REQUEST_INTERVAL_SECONDS

DIMENSIONS = ("auth", "credentials", "api", "mcp")
QUERIES = {
    "auth": "authentication OAuth API key developer documentation",
    "credentials": "create developer app obtain API credentials pricing approval partner access",
    "api": "public REST GraphQL API reference endpoints webhooks SDK",
    "mcp": "official MCP server Model Context Protocol integration",
}
SALESFORCE_QUERIES = {
    "auth": "Salesforce platform REST API OAuth 2.0 external client app connected app authentication guide",
    "credentials": "Salesforce create external client app OAuth consumer key secret developer setup",
    "api": "Salesforce platform REST API resources reference sobjects query developer documentation",
    "mcp": "Salesforce official MCP server developer documentation",
}

class DraftClaim(BaseModel):
    text: str = Field(description="One precise factual claim")
    tag: Literal["description", "oauth2", "api_key", "basic", "token", "jwt", "self_serve", "trial", "free_access", "paid_plan", "admin_approval", "partner_gated", "rest", "graphql", "public_api", "webhooks", "sdk", "official_mcp", "mcp_other", "blocker", "other"]
    source_url: str
    evidence: str = Field(description="Exact short excerpt copied from the source text")

class DraftClaims(BaseModel):
    claims: list[DraftClaim] = Field(default_factory=list, max_length=8)

class IndexedVerification(BaseModel):
    index: int
    status: Literal["supported", "unsupported", "contradicted", "uncertain"]
    reason: str

class VerificationBatch(BaseModel):
    results: list[IndexedVerification]

ALLOWED_TAGS = {
    "auth": {"oauth2", "api_key", "basic", "token", "jwt", "other"},
    "credentials": {"self_serve", "trial", "free_access", "paid_plan", "admin_approval", "partner_gated", "blocker", "other"},
    "api": {"description", "rest", "graphql", "public_api", "webhooks", "sdk", "blocker", "other"},
    "mcp": {"official_mcp", "mcp_other", "blocker", "other"},
}
_generation_lock = asyncio.Lock()
_last_generation = 0.0

class GraphState(TypedDict, total=False):
    app: dict
    sources: list[dict]
    claims: Annotated[list[dict], operator.add]
    verified: list[dict]
    errors: Annotated[list[str], operator.add]
    progress: str

def configured() -> bool:
    return bool(TAVILY_API_KEY and GEMINI_API_KEY)

def provider_error(exc: Exception) -> str:
    message = str(exc)
    if "GenerateRequestsPerDay" in message:
        return f"Gemini daily request quota exhausted for {GEMINI_MODEL}"
    if "GenerateRequestsPerMinute" in message:
        return f"Gemini minute request quota exhausted for {GEMINI_MODEL}"
    if "503" in message:
        return f"Gemini temporarily unavailable for {GEMINI_MODEL}"
    if "404" in message:
        return f"Gemini model {GEMINI_MODEL} unavailable to this key"
    return f"{type(exc).__name__}: {message[:180]}"

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

def official_root(hint: str) -> str | None:
    host = urlparse(f"https://{hint}").hostname or ""
    if "." not in host:
        return None
    parts = host.split(".")
    return ".".join(parts[-2:])

def official_url(url: str, root: str | None) -> bool:
    if not root:
        return True
    host = urlparse(url).hostname or ""
    return host == root or host.endswith("." + root)

def relevant_product_url(app: dict, url: str) -> bool:
    if app["name"] == "Salesforce" and "/commerce/" in urlparse(url).path.casefold():
        return False
    return True

def search_domain(app: dict, hint: str, root: str | None) -> str | None:
    if app["name"] == "Salesforce":
        return "developer.salesforce.com"
    host = urlparse(f"https://{hint}").hostname or ""
    if host.split(".")[0] in {"docs", "developer", "developers", "api", "open", "learn", "help", "core"}:
        return host
    return root

def result_score(app: dict, dimension: str, item: dict) -> int:
    url = item.get("url", "").casefold()
    title = item.get("title", "").casefold()
    terms = {"auth": ("oauth", "authentication", "authorization"),
             "credentials": ("external client app", "connected app", "credential", "setup"),
             "api": ("rest api", "reference", "resources", "query"),
             "mcp": ("mcp", "server")}[dimension]
    score = sum(2 for term in terms if term in url or term in title)
    if "/docs/platform/" in url:
        score += 4
    if app["name"] == "Salesforce":
        if dimension in {"auth", "credentials", "api"} and "/platform/api-rest/" in url:
            score += 10
        if dimension == "mcp" and "/platform/hosted-mcp-servers/" in url:
            score += 10
        if "/ai/" in url and dimension != "mcp":
            score -= 5
    return score

def useful_content(content: str) -> bool:
    if len(content.strip()) < 100:
        return False
    if content.count("Cookies Details") >= 2 and "Accept All Cookies" in content:
        return False
    return True

def source_topics(base: set[str], content: str) -> list[str]:
    topics = set(base)
    lower = content.casefold()
    if "oauth 2.0" in lower:
        topics.add("auth")
    if "external client app" in lower and ("create" in lower or "configure" in lower):
        topics.add("credentials")
    if "mcp server" in lower:
        topics.add("mcp")
    return sorted(topics)

async def discover(state: GraphState) -> dict:
    app = state["app"]
    hint = app["hint"].split(" (")[0].strip()
    root = official_root(hint)
    domain = search_domain(app, hint, root)
    disambiguation = " -commerce -retail -B2C" if app["name"] == "Salesforce" else ""
    seed = f"https://{hint}" if "." in hint and " " not in hint else None
    searches = await asyncio.gather(*[
        tavily("search", {
            "query": f'{SALESFORCE_QUERIES[dimension]}{disambiguation}' if app["name"] == "Salesforce" else f'{app["name"]} {QUERIES[dimension]} {hint}',
            "search_depth": "basic", "max_results": 6,
            **({"include_domains": [domain], "include_domains_mode": "filter"} if domain else {}),
        }) for dimension in DIMENSIONS
    ], return_exceptions=True)
    urls: dict[str, dict] = {seed: {"title": f'{app["name"]} supplied hint', "topics": {"overview"}}} if seed and valid_url(seed) else {}
    errors = []
    for dimension, result in zip(DIMENSIONS, searches):
        if isinstance(result, Exception):
            errors.append(f"Tavily {dimension} search: {type(result).__name__}: {result}")
            continue
        added = 0
        ranked = sorted(result.get("results", []), key=lambda item: result_score(app, dimension, item), reverse=True)
        for item in ranked:
            url = item.get("url", "")
            if valid_url(url) and official_url(url, root) and relevant_product_url(app, url):
                urls.setdefault(url, {"title": item.get("title", url), "topics": set()})["topics"].add(dimension)
                added += 1
            if added >= 2:
                break
    if len(urls) <= (1 if seed else 0):
        try:
            fallback = await tavily("search", {"query": f'{app["name"]} official developer API authentication documentation',
                                                "search_depth": "basic", "max_results": 5,
                                                **({"include_domains": [domain], "include_domains_mode": "filter"} if domain else {})})
            for item in fallback.get("results", []):
                url = item.get("url", "")
                if valid_url(url) and official_url(url, root) and relevant_product_url(app, url):
                    urls.setdefault(url, {"title": item.get("title", url), "topics": {"auth", "api"}})
                    if len(urls) >= 4:
                        break
        except Exception as exc:
            errors.append(f"Tavily fallback search: {type(exc).__name__}: {exc}")
    if not urls:
        return {"sources": [], "errors": errors or ["No source URLs found"], "progress": "No documentation found"}
    try:
        extracted = await tavily("extract", {"urls": list(urls)[:10], "extract_depth": "basic"})
    except Exception as exc:
        return {"sources": [], "errors": errors + [f"Tavily extract: {type(exc).__name__}: {exc}"], "progress": "Extraction failed"}
    sources = []
    for item in extracted.get("results", []):
        url = item.get("url", "")
        content = item.get("raw_content") or ""
        if url in urls and useful_content(content):
            sources.append({"url": url, "title": urls[url]["title"], "topics": source_topics(urls[url]["topics"], content),
                            "content": content[:16000], "retrieved_at": datetime.now(timezone.utc).isoformat()})
    covered = {dimension for source in sources for dimension in source["topics"]}
    missing = [dimension for dimension in DIMENSIONS if dimension not in covered]
    retry_urls = [url for url, meta in urls.items() if any(dimension in meta["topics"] for dimension in missing)
                  and url not in {source["url"] for source in sources}][:4]
    if retry_urls:
        try:
            second = await tavily("extract", {"urls": retry_urls, "extract_depth": "advanced"})
            for item in second.get("results", []):
                url = item.get("url", "")
                content = item.get("raw_content") or ""
                if url in urls and useful_content(content):
                    sources.append({"url": url, "title": urls[url]["title"], "topics": source_topics(urls[url]["topics"], content),
                                    "content": content[:16000], "retrieved_at": datetime.now(timezone.utc).isoformat()})
        except Exception as exc:
            errors.append(f"Tavily advanced extract: {type(exc).__name__}: {exc}")
    return {"sources": sources, "errors": errors, "progress": "Sources extracted"}

async def generate(schema, prompt: str):
    global _last_generation
    for attempt in range(3):
        async with _generation_lock:
            delay = GEMINI_REQUEST_INTERVAL_SECONDS - (time.monotonic() - _last_generation)
            if delay > 0:
                await asyncio.sleep(delay)
            _last_generation = time.monotonic()
        client = genai.Client(api_key=GEMINI_API_KEY)
        try:
            response = await client.aio.models.generate_content(
                model=GEMINI_MODEL, contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema, temperature=0),
            )
            return schema.model_validate_json(response.text)
        except Exception as exc:
            message = str(exc)
            if attempt == 2 or "PerDay" in message or not ("429" in message or "503" in message):
                raise
            retry = re.search(r"retry in ([\d.]+)s", message, re.I)
            await asyncio.sleep(max(float(retry.group(1)) if retry else 15, 15))
        finally:
            await client.aio.aclose()

async def specialist(state: GraphState, dimension: str) -> dict:
    sources = [source for source in state.get("sources", [])
               if dimension in source.get("topics", []) or (dimension == "api" and "overview" in source.get("topics", []))]
    if not sources:
        return {"claims": [], "errors": [f"{dimension}: no relevant extracted source"]}
    evidence = "\n\n".join(f'URL: {s["url"]}\nCONTENT:\n{s["content"][:10000]}' for s in sources[:3])
    prompt = (
        f'For {state["app"]["name"]}, research only the {dimension} dimension. '
        f'Focus: {QUERIES[dimension]}. Use only the supplied extracted source text. '
        + ("Also include one concise claim about what this application does, tagged description, only if directly supported. " if dimension == "api" else "") +
        f'Use only these tags for this dimension: {", ".join(sorted(ALLOWED_TAGS[dimension]))}. '
        "Return at most 6 precise claims. Every evidence field must be an exact contiguous quote from its source. "
        "Use exact source URLs from the input. If there is no direct evidence, return an empty claim list. "
        "Never infer absence of an API, MCP, or access path from silence.\n\n" + evidence
    )
    try:
        output = await generate(DraftClaims, prompt)
        return {"claims": [{**claim.model_dump(), "dimension": "overview" if claim.tag == "description" else dimension}
                           for claim in output.claims if claim.tag in ALLOWED_TAGS[dimension]], "errors": []}
    except Exception as exc:
        return {"claims": [], "errors": [f"{dimension}: {provider_error(exc)}"]}

def source_for_claim(claim: dict, sources: list[dict]):
    return next((source for source in sources if source["url"] == claim["source_url"]), None)

def exact_quote_present(quote: str, content: str) -> bool:
    return " ".join(quote.split()).casefold() in " ".join(content.split()).casefold()

async def verify_claims(claims: list[dict], sources: list[dict]) -> tuple[list[dict], list[str]]:
    verified: list[dict | None] = [None] * len(claims)
    candidates = []
    for index, claim in enumerate(claims):
        source = source_for_claim(claim, sources)
        if not source:
            verified[index] = {**claim, "status": "unsupported", "reason": "Source URL was not extracted"}
        elif not claim["evidence"].strip() or not exact_quote_present(claim["evidence"], source["content"]):
            verified[index] = {**claim, "status": "unsupported", "reason": "Evidence quote was not found in the extracted source"}
        else:
            candidates.append({"index": index, "claim": claim["text"], "evidence": claim["evidence"], "url": source["url"]})
    if not candidates:
        return [item for item in verified if item is not None], []
    prompt = (
        "For every numbered claim below, decide whether its exact excerpt directly supports the entire claim. "
        "Use only the excerpt. Return one result per index. Supported means direct entailment; contradicted means "
        "the opposite; unsupported means irrelevant; uncertain means partial or ambiguous evidence. "
        "Do not let one excerpt support a different numbered claim.\n\n" +
        "\n\n".join(f'Index {item["index"]}\nClaim: {item["claim"]}\nSource: {item["url"]}\nExcerpt: {item["evidence"]}' for item in candidates)
    )
    errors = []
    try:
        batch = await generate(VerificationBatch, prompt)
        by_index = {item.index: item for item in batch.results}
        for candidate in candidates:
            index = candidate["index"]
            verdict = by_index.get(index)
            verified[index] = {**claims[index], "status": verdict.status if verdict else "uncertain",
                               "reason": verdict.reason if verdict else "Verifier omitted this claim"}
    except Exception as exc:
        errors.append(f"Verification: {provider_error(exc)}")
        for candidate in candidates:
            index = candidate["index"]
            verified[index] = {**claims[index], "status": "uncertain", "reason": f"Verification unavailable: {type(exc).__name__}"}
    return [item for item in verified if item is not None], errors

async def verify(state: GraphState) -> dict:
    claims = state.get("claims", [])
    verified, errors = await verify_claims(claims, state.get("sources", []))
    retry_dim = next((dimension for dimension in DIMENSIONS
                      if any(c["dimension"] == dimension and c["status"] in {"unsupported", "contradicted"} for c in verified)
                      and not any(c["dimension"] == dimension and c["status"] == "supported" for c in verified)), None)
    if not retry_dim:
        return {"verified": verified, "errors": errors, "progress": "Claims verified"}
    app = state["app"]
    try:
        found = await tavily("search", {"query": f'{app["name"]} official {QUERIES[retry_dim]} documentation',
                                         "search_depth": "basic", "max_results": 3})
        prior = {source["url"] for source in state.get("sources", [])}
        root = official_root(app["hint"].split(" (")[0].strip())
        urls = [item["url"] for item in found.get("results", [])
                if valid_url(item.get("url", "")) and official_url(item["url"], root)
                and relevant_product_url(app, item["url"]) and item["url"] not in prior][:2]
        if not urls:
            return {"verified": verified, "errors": errors, "progress": "Claims verified"}
        extracted = await tavily("extract", {"urls": urls, "extract_depth": "basic"})
        added = [{"url": item["url"], "title": item["url"], "content": item.get("raw_content", "")[:16000],
                  "topics": [retry_dim], "retrieved_at": datetime.now(timezone.utc).isoformat()} for item in extracted.get("results", [])
                 if item.get("url") in urls and len(item.get("raw_content", "").strip()) >= 100]
        if not added:
            return {"verified": verified, "errors": errors, "progress": "Claims verified"}
        retry = await specialist({"app": app, "sources": added}, retry_dim)
        retry_claims, retry_errors = await verify_claims(retry.get("claims", []), added)
        return {"sources": state.get("sources", []) + added, "verified": verified + retry_claims,
                "errors": errors + retry.get("errors", []) + retry_errors, "progress": "Claims verified after targeted retry"}
    except Exception as exc:
        return {"verified": verified, "errors": errors + [f"Targeted retry: {type(exc).__name__}: {exc}"], "progress": "Claims verified"}

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
