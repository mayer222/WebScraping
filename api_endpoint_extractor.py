#!/usr/bin/env python3
"""
api_endpoint_extractor.py
=========================

Discovers and extracts API endpoints from modern web applications by fusing
two independent capture channels:

  1. In-page instrumentation: `fetch`, `XMLHttpRequest.open/send`, and
     `setRequestHeader` are monkey-patched via `add_init_script` and events
     bridged to Python with `expose_function`. This channel sees the request
     exactly as the app constructs it (headers the app sets, body before
     serialization quirks, etc.).

  2. Playwright network events (`request`/`response`): this channel sees the
     authoritative on-the-wire view, including final headers added by the
     browser (cookies, User-Agent, sec-*) and response bodies + status codes.

The two streams are merged per-request by keying on the Playwright Request
object identity; JS-captured metadata enriches the on-the-wire record.

Outputs a deduplicated, normalized, classified endpoint catalogue with
detected patterns (pagination, auth, GraphQL ops), curl equivalents, and
domain/path grouping.

Example
-------
    from api_endpoint_extractor import APIEndpointExtractor
    import asyncio

    async def main():
        ex = APIEndpointExtractor(headless=True)
        result = await ex.extract(
            "https://example.com/app",
            auto_scroll=True,
            click_buttons=True,
        )
        print(result["total_endpoints"], "endpoints")

    asyncio.run(main())

CLI:
    python api_endpoint_extractor.py https://example.com -o out.json --scroll --click
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import shlex
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse, parse_qsl, urlunparse

try:
    from playwright.async_api import (
        async_playwright,
        Page,
        Request,
        Response,
        BrowserContext,
        Error as PlaywrightError,
    )
except ImportError as e:
    sys.stderr.write(
        "Playwright is not installed. Install with:\n"
        "    pip install playwright\n"
        "    playwright install chromium\n"
    )
    raise

log = logging.getLogger("api_endpoint_extractor")


# ============================================================================
# Configuration constants
# ============================================================================

# Allow-list of resource types we keep. API extraction targets xhr/fetch only —
# scripts, images, stylesheets, documents (HTML pages), fonts, media, etc. are
# noise. The previous deny-list approach let `script` through (the bug that
# polluted the catalogue with .js files). Callers can widen this (e.g. add
# "document" for HTML-returning APIs) via `allowed_resource_types`.
DEFAULT_ALLOWED_RESOURCE_TYPES = frozenset({"xhr", "fetch"})

PAGINATION_PARAM_NAMES = frozenset({
    "page", "p", "pagenum", "page_num", "pagenumber",
    "offset", "start", "skip", "from",
    "limit", "per_page", "pagesize", "page_size", "size", "count", "take",
    "cursor", "after", "before", "next", "prev",
    "pagetoken", "page_token", "continuationtoken", "continuation",
})

AUTH_HEADER_NAMES = frozenset({
    "authorization", "proxy-authorization", "cookie",
    "x-api-key", "apikey", "api-key", "x-auth-token", "x-access-token",
    "x-csrf-token", "x-xsrf-token", "csrf-token", "x-csrftoken",
    "x-session-token", "x-session", "x-token",
})

SENSITIVE_COOKIE_HINTS = re.compile(
    r"(session|sess|auth|token|jwt|csrf|xsrf|sid|_sid|connect\.sid)",
    re.IGNORECASE,
)

# Path-segment patterns for URL normalization
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_OBJECTID_RE = re.compile(r"^[0-9a-f]{24}$", re.I)
_NUMERIC_RE = re.compile(r"^\d+$")
_HEX_LONG_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)
_TOKEN_LIKE_RE = re.compile(r"^[A-Za-z0-9_\-]{20,}$")


# ============================================================================
# JavaScript instrumentation payload
# ============================================================================
#
# Installed via add_init_script so it runs before *any* page script on every
# frame/navigation, including SPA route changes that keep the same document.
# Communicates with Python via the bridge function exposed through
# BrowserContext.expose_function.
# ----------------------------------------------------------------------------

_JS_INSTRUMENTATION = r"""
(() => {
  if (window.__apiCaptureInstalled) return;
  window.__apiCaptureInstalled = true;

  const send = (payload) => {
    try {
      if (typeof window.__apiCaptureBridge === 'function') {
        // Fire-and-forget; do not block the request.
        window.__apiCaptureBridge(payload);
      }
    } catch (e) { /* swallow */ }
  };

  const serializeBody = (body) => {
    if (body == null) return null;
    try {
      if (typeof body === 'string') return body;
      if (body instanceof FormData) {
        const obj = {};
        for (const [k, v] of body.entries()) {
          obj[k] = (v instanceof File) ? ('<File:' + v.name + ':' + v.size + '>') : v;
        }
        return JSON.stringify(obj);
      }
      if (body instanceof URLSearchParams) return body.toString();
      if (body instanceof ArrayBuffer) return '<binary:arraybuffer:' + body.byteLength + '>';
      if (body instanceof Blob) return '<binary:blob:' + body.size + '>';
      if (ArrayBuffer.isView && ArrayBuffer.isView(body)) return '<binary:view:' + body.byteLength + '>';
      return JSON.stringify(body);
    } catch (e) {
      try { return String(body); } catch (_) { return '<unserializable>'; }
    }
  };

  // --- fetch() patch ---------------------------------------------------
  const origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function(resource, init) {
      init = init || {};
      let url = '', method = 'GET';
      const headers = {};
      try {
        if (typeof resource === 'string') {
          url = resource;
        } else if (resource && typeof resource === 'object') {
          url = resource.url || String(resource);
          if (resource.method) method = resource.method;
          if (resource.headers && typeof resource.headers.forEach === 'function') {
            resource.headers.forEach((v, k) => { headers[k] = v; });
          }
        }
        if (init.method) method = init.method;
        if (init.headers) {
          if (init.headers instanceof Headers) {
            init.headers.forEach((v, k) => { headers[k] = v; });
          } else if (Array.isArray(init.headers)) {
            for (const [k, v] of init.headers) headers[k] = v;
          } else {
            for (const k of Object.keys(init.headers)) headers[k] = init.headers[k];
          }
        }
        send({
          source: 'js-fetch',
          url: url,
          method: method.toUpperCase(),
          headers: headers,
          body: serializeBody(init.body),
          ts: Date.now(),
        });
      } catch (e) { /* swallow */ }
      return origFetch.apply(this, arguments);
    };
  }

  // --- XMLHttpRequest patch --------------------------------------------
  const XHR = window.XMLHttpRequest;
  if (XHR && XHR.prototype) {
    const P = XHR.prototype;
    const origOpen = P.open;
    const origSend = P.send;
    const origSetHeader = P.setRequestHeader;

    P.open = function(method, url) {
      this.__capMethod = method;
      this.__capUrl = url;
      this.__capHeaders = {};
      return origOpen.apply(this, arguments);
    };
    P.setRequestHeader = function(name, value) {
      if (!this.__capHeaders) this.__capHeaders = {};
      this.__capHeaders[name] = value;
      return origSetHeader.apply(this, arguments);
    };
    P.send = function(body) {
      try {
        send({
          source: 'js-xhr',
          url: this.__capUrl || '',
          method: (this.__capMethod || 'GET').toUpperCase(),
          headers: this.__capHeaders || {},
          body: serializeBody(body),
          ts: Date.now(),
        });
      } catch (e) { /* swallow */ }
      return origSend.apply(this, arguments);
    };
  }

  // --- sendBeacon (bonus: often used for analytics APIs) ---------------
  if (navigator && navigator.sendBeacon) {
    const origBeacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = function(url, data) {
      try {
        send({
          source: 'js-beacon',
          url: url,
          method: 'POST',
          headers: {},
          body: serializeBody(data),
          ts: Date.now(),
        });
      } catch (e) { /* swallow */ }
      return origBeacon(url, data);
    };
  }
})();
"""


# ============================================================================
# Data model
# ============================================================================


@dataclass
class CapturedRequest:
    url: str
    method: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    timestamp: float = 0.0
    source: str = "playwright"
    resource_type: str | None = None


@dataclass
class CapturedResponse:
    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    body_truncated: bool = False
    content_type: str | None = None
    timestamp: float = 0.0


@dataclass
class Endpoint:
    method: str
    normalized_url: str
    sample_url: str
    domain: str
    path: str
    query_keys: list[str] = field(default_factory=list)
    classification: str = "rest"  # 'rest' | 'graphql'
    request_count: int = 0
    status_codes: list[int] = field(default_factory=list)
    content_types: list[str] = field(default_factory=list)
    headers_sample: dict[str, str] = field(default_factory=dict)
    request_sample: Any = None
    response_sample: Any = None
    detected_patterns: dict[str, Any] = field(default_factory=dict)
    graphql_operations: list[dict[str, Any]] = field(default_factory=list)
    requires_auth: bool = False
    curl_equivalent: str = ""


# ============================================================================
# Normalization & classification
# ============================================================================


def normalize_path_segment(seg: str) -> str:
    if not seg:
        return seg
    if _UUID_RE.match(seg):
        return "{uuid}"
    if _OBJECTID_RE.match(seg):
        return "{id}"
    if _NUMERIC_RE.match(seg):
        return "{id}"
    if _HEX_LONG_RE.match(seg):
        return "{hash}"
    # Token-like: long alphanumeric with at least one digit (avoids eating real
    # route names like "subscriptions" or "authenticated").
    if _TOKEN_LIKE_RE.match(seg) and any(c.isdigit() for c in seg):
        return "{token}"
    return seg


def normalize_url(url: str) -> str:
    """Return a canonical URL with dynamic path params replaced and the query
    string stripped. Query key inventory is preserved separately on the
    Endpoint record."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    norm_segments = [normalize_path_segment(s) for s in p.path.split("/")]
    return urlunparse((p.scheme, p.netloc, "/".join(norm_segments), p.params, "", ""))


def extract_path_params(sample_url: str, normalized_url: str) -> dict[str, str]:
    """Pair dynamic path placeholders with the values they replaced.

    Used as a final fallback for request_sample on GETs with no body and no
    query string: if the URL encoded its inputs as path segments (e.g. a UUID
    or numeric id) we surface them rather than emitting null for a request
    that clearly carries data.
    """
    try:
        orig_segs = urlparse(sample_url).path.split("/")
        norm_segs = urlparse(normalized_url).path.split("/")
    except ValueError:
        return {}
    if len(orig_segs) != len(norm_segs):
        return {}
    params: dict[str, str] = {}
    counters: dict[str, int] = defaultdict(int)
    for orig, norm in zip(orig_segs, norm_segs):
        if len(norm) >= 2 and norm[0] == "{" and norm[-1] == "}" and orig != norm:
            key = norm[1:-1]
            counters[key] += 1
            name = key if counters[key] == 1 else f"{key}{counters[key]}"
            params[name] = orig
    return params


def _parse_body(raw: str | None) -> Any:
    if raw is None:
        return None
    if not isinstance(raw, str):
        return raw
    stripped = raw.strip()
    if not stripped:
        return None
    if stripped[0] in "{[\"":
        try:
            return json.loads(stripped)
        except (ValueError, TypeError):
            pass
    return raw


def classify_endpoint(url: str, parsed_body: Any) -> tuple[str, dict | None]:
    """Return ('rest' | 'graphql', graphql_op_info_or_None)."""
    try:
        path = urlparse(url).path.lower()
    except ValueError:
        path = ""

    looks_graphql_url = "graphql" in path or path.endswith("/gql")

    if isinstance(parsed_body, dict) and isinstance(parsed_body.get("query"), str):
        q = parsed_body["query"]
        if re.search(r"\b(query|mutation|subscription)\b", q):
            return "graphql", _extract_graphql(parsed_body)

    # Batched GraphQL (array of ops)
    if isinstance(parsed_body, list) and parsed_body and all(
        isinstance(x, dict) and isinstance(x.get("query"), str) for x in parsed_body
    ):
        return "graphql", {"batched": True, "operations": [_extract_graphql(op) for op in parsed_body]}

    if looks_graphql_url:
        return "graphql", _extract_graphql(parsed_body) if isinstance(parsed_body, dict) else None

    return "rest", None


def _extract_graphql(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    q = body.get("query")
    if not isinstance(q, str):
        return None
    op_type_match = re.match(r"\s*(query|mutation|subscription)\b", q)
    op_type = op_type_match.group(1) if op_type_match else "query"
    op_name = body.get("operationName")
    if not op_name:
        m = re.search(r"\b(?:query|mutation|subscription)\s+(\w+)", q)
        op_name = m.group(1) if m else None
    return {
        "operationName": op_name,
        "operationType": op_type,
        "query": q if len(q) <= 600 else q[:600] + "…",
        "variables": body.get("variables"),
    }


def detect_pagination(url: str, parsed_body: Any) -> dict[str, Any] | None:
    try:
        p = urlparse(url)
    except ValueError:
        return None
    query = dict(parse_qsl(p.query, keep_blank_values=True))
    q_hits = {k: v for k, v in query.items() if k.lower() in PAGINATION_PARAM_NAMES}
    b_hits: dict[str, Any] = {}
    if isinstance(parsed_body, dict):
        b_hits = {k: v for k, v in parsed_body.items() if k.lower() in PAGINATION_PARAM_NAMES}
    if not q_hits and not b_hits:
        return None
    styles = set()
    for k in list(q_hits.keys()) + list(b_hits.keys()):
        kl = k.lower()
        if kl in {"page", "p", "pagenum", "page_num", "pagenumber"}:
            styles.add("page-number")
        elif kl in {"offset", "start", "skip", "from"}:
            styles.add("offset")
        elif kl in {"cursor", "after", "before", "next", "prev",
                    "pagetoken", "page_token", "continuationtoken", "continuation"}:
            styles.add("cursor")
    return {
        "styles": sorted(styles),
        "query_params": q_hits or None,
        "body_params": b_hits or None,
    }


def detect_auth(headers: dict[str, str], cookies_present: bool = False) -> dict[str, Any] | None:
    lower = {k.lower(): v for k, v in headers.items()}
    mechanisms: list[dict[str, Any]] = []

    if "authorization" in lower:
        v = lower["authorization"] or ""
        scheme = v.split(" ", 1)[0] if " " in v else v.split(":", 1)[0] if v else ""
        mechanisms.append({"type": "authorization", "scheme": scheme or "custom"})

    if "cookie" in lower:
        cookie_val = lower["cookie"] or ""
        names = [c.split("=", 1)[0].strip() for c in cookie_val.split(";") if "=" in c]
        looks_sessiony = any(SENSITIVE_COOKIE_HINTS.search(n) for n in names)
        mechanisms.append({
            "type": "cookie",
            "cookie_names": names[:10],
            "session_like": looks_sessiony,
        })

    for name in ("x-api-key", "apikey", "api-key"):
        if name in lower:
            mechanisms.append({"type": "api-key", "header": name})
            break

    for name in ("x-csrf-token", "x-xsrf-token", "csrf-token", "x-csrftoken"):
        if name in lower:
            mechanisms.append({"type": "csrf", "header": name})
            break

    for name in ("x-access-token", "x-session-token", "x-auth-token"):
        if name in lower:
            mechanisms.append({"type": "custom-token", "header": name})
            break

    return {"mechanisms": mechanisms} if mechanisms else None


# ============================================================================
# curl export
# ============================================================================


def to_curl(req: CapturedRequest, *, max_body: int = 4000) -> str:
    parts: list[str] = ["curl", "-X", req.method.upper(), shlex.quote(req.url)]
    for k, v in req.headers.items():
        # Drop HTTP/2 pseudo-headers and noisy headers that curl re-derives.
        if k.startswith(":"):
            continue
        if k.lower() in {"content-length", "host"}:
            continue
        parts += ["-H", shlex.quote(f"{k}: {v}")]
    if req.body:
        body = req.body
        if len(body) > max_body:
            body = body[:max_body] + "..."
        parts += ["--data-raw", shlex.quote(body)]
    return " ".join(parts)


# ============================================================================
# Merger
# ============================================================================


class _Merger:
    """Correlates JS-captured events with Playwright Request objects.

    Playwright's Request object is the source of truth for a single on-the-wire
    request; JS events are matched to it by (method, URL) with a small time
    window, and the best remaining JS match is consumed exactly once.
    """

    def __init__(self) -> None:
        self.js_captures: list[dict[str, Any]] = []
        self.pw_requests: dict[int, CapturedRequest] = {}
        self.pw_responses: dict[int, CapturedResponse] = {}

    def add_js_capture(self, payload: dict[str, Any]) -> None:
        self.js_captures.append(payload)

    def add_pw_request(self, rid: int, req: CapturedRequest) -> None:
        self.pw_requests[rid] = req

    def add_pw_response(self, rid: int, resp: CapturedResponse) -> None:
        self.pw_responses[rid] = resp

    def merge(self) -> list[tuple[CapturedRequest, CapturedResponse | None]]:
        used: set[int] = set()
        merged: list[tuple[CapturedRequest, CapturedResponse | None]] = []

        for rid, req in self.pw_requests.items():
            best_i: int | None = None
            best_delta = float("inf")
            for i, js in enumerate(self.js_captures):
                if i in used:
                    continue
                if (js.get("method") or "").upper() != req.method.upper():
                    continue
                if not self._urls_match(js.get("url", ""), req.url):
                    continue
                delta = abs((js.get("ts", 0) / 1000.0) - req.timestamp)
                if delta < best_delta and delta < 5.0:
                    best_delta = delta
                    best_i = i

            if best_i is not None:
                used.add(best_i)
                js = self.js_captures[best_i]
                # Playwright headers are authoritative (browser-added headers
                # like Cookie, User-Agent, sec-*), but JS-captured headers may
                # include app-level ones Playwright might not expose on some
                # transports — so union with PW taking precedence.
                js_headers = js.get("headers") or {}
                merged_headers = {**js_headers, **req.headers}
                merged_req = CapturedRequest(
                    url=req.url,
                    method=req.method,
                    headers=merged_headers,
                    # JS body is often richer (e.g. for FormData/URLSearchParams
                    # which Playwright may render as binary).
                    body=js.get("body") or req.body,
                    timestamp=req.timestamp,
                    source=f"merged:{js.get('source', 'js')}+playwright",
                    resource_type=req.resource_type,
                )
            else:
                merged_req = req

            merged.append((merged_req, self.pw_responses.get(rid)))

        return merged

    @staticmethod
    def _urls_match(u1: str, u2: str) -> bool:
        if not u1 or not u2:
            return False
        if u1 == u2:
            return True
        try:
            p1, p2 = urlparse(u1), urlparse(u2)
        except ValueError:
            return False
        if p1.path != p2.path:
            return False
        if p1.netloc and p2.netloc and p1.netloc != p2.netloc:
            return False
        return dict(parse_qsl(p1.query)) == dict(parse_qsl(p2.query))


# ============================================================================
# Main extractor
# ============================================================================


class APIEndpointExtractor:
    """Reusable API discovery pipeline. Instantiate once, call .extract()
    against any number of URLs (each launches its own fresh browser context)."""

    def __init__(
        self,
        *,
        headless: bool = True,
        allowed_resource_types: set[str] | frozenset[str] | None = None,
        only_json: bool = True,
        max_body_size: int = 100_000,
        idle_timeout_ms: int = 5_000,
        nav_timeout_ms: int = 30_000,
        body_timeout_s: float = 5.0,
        retries: int = 2,
        user_agent: str | None = None,
        extra_http_headers: dict[str, str] | None = None,
    ) -> None:
        self.headless = headless
        self.allowed_resource_types = (
            frozenset(allowed_resource_types)
            if allowed_resource_types is not None
            else DEFAULT_ALLOWED_RESOURCE_TYPES
        )
        self.only_json = only_json
        self.max_body_size = max_body_size
        self.idle_timeout_ms = idle_timeout_ms
        self.nav_timeout_ms = nav_timeout_ms
        self.body_timeout_s = body_timeout_s
        self.retries = retries
        self.user_agent = user_agent
        self.extra_http_headers = extra_http_headers or {}

    # ---- public API --------------------------------------------------------

    async def extract(
        self,
        url: str,
        *,
        auto_scroll: bool = False,
        click_buttons: bool = False,
        interact: Callable[[Page], Awaitable[None]] | None = None,
        settle_seconds: float = 0.75,
    ) -> dict[str, Any]:
        """Load `url`, optionally run automation, and return the endpoint
        catalogue.

        `interact` is an optional user-provided async callback that receives
        the Page after initial load and can drive custom flows (login, forms,
        deep navigation). It runs *after* auto_scroll/click_buttons.
        """
        # Playwright's internal IPC futures error with TargetClosedError when
        # the browser shuts down with requests still in flight. Those futures
        # aren't ours to await, so asyncio's default handler logs them as
        # "Future exception was never retrieved" noise. Swallow them here,
        # restore the previous handler on exit.
        loop = asyncio.get_running_loop()
        prev_handler = loop.get_exception_handler()

        def _handler(lp: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            exc = context.get("exception")
            if isinstance(exc, PlaywrightError):
                log.debug("suppressed post-shutdown playwright error: %s", exc)
                return
            if prev_handler is not None:
                prev_handler(lp, context)
            else:
                lp.default_exception_handler(context)

        loop.set_exception_handler(_handler)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=self.headless)
                try:
                    ctx = await browser.new_context(
                        user_agent=self.user_agent,
                        extra_http_headers=self.extra_http_headers,
                        ignore_https_errors=True,
                    )

                    merger = _Merger()
                    response_tasks: list[asyncio.Task] = []
                    # Single-element list so closures can mutate it.
                    stopping: list[bool] = [False]

                    def _bridge(payload: dict[str, Any]) -> None:
                        merger.add_js_capture(payload)

                    await ctx.expose_function("__apiCaptureBridge", _bridge)
                    await ctx.add_init_script(_JS_INSTRUMENTATION)

                    page = await ctx.new_page()
                    self._wire_listeners(page, merger, response_tasks, stopping)

                    await self._goto_with_retry(page, url)

                    if auto_scroll:
                        await self._auto_scroll(page)
                    if click_buttons:
                        await self._click_visible_buttons(page)
                    if interact is not None:
                        try:
                            await interact(page)
                        except Exception as e:
                            log.warning("interact() raised: %s", e)

                    try:
                        await page.wait_for_load_state(
                            "networkidle", timeout=self.idle_timeout_ms,
                        )
                    except PlaywrightError:
                        log.debug("network did not fully idle within timeout; proceeding")

                    await asyncio.sleep(settle_seconds)

                    # Drain pending response body fetches. Without this, fast
                    # extractions return before `await response.body()` finishes
                    # and response_sample is null for every endpoint.
                    if response_tasks:
                        try:
                            await asyncio.wait_for(
                                asyncio.gather(*response_tasks, return_exceptions=True),
                                timeout=max(10.0, self.body_timeout_s * 2),
                            )
                        except asyncio.TimeoutError:
                            pending = [t for t in response_tasks if not t.done()]
                            log.warning(
                                "%d/%d response body tasks still pending; cancelling",
                                len(pending), len(response_tasks),
                            )
                            for t in pending:
                                t.cancel()
                            # Await the cancellations so their CancelledError /
                            # TargetClosedError are consumed, not logged as
                            # "Future exception was never retrieved".
                            await asyncio.gather(*pending, return_exceptions=True)

                    # Stop accepting new response-body tasks during shutdown;
                    # events fired after this point (as the browser closes)
                    # are ignored instead of producing dangling tasks.
                    stopping[0] = True

                    return self._build_result(merger, source_url=url)
                finally:
                    await browser.close()
        finally:
            loop.set_exception_handler(prev_handler)

    # ---- listener wiring ---------------------------------------------------

    def _wire_listeners(
        self,
        page: Page,
        merger: _Merger,
        response_tasks: list[asyncio.Task],
        stopping: list[bool],
    ) -> None:
        def on_request(request: Request) -> None:
            if stopping[0]:
                return
            try:
                if request.resource_type not in self.allowed_resource_types:
                    return
                merger.add_pw_request(
                    id(request),
                    CapturedRequest(
                        url=request.url,
                        method=request.method,
                        headers=dict(request.headers),
                        body=request.post_data,
                        timestamp=time.time(),
                        source="playwright",
                        resource_type=request.resource_type,
                    ),
                )
            except Exception as e:
                log.debug("on_request error: %s", e)

        def on_response(response: Response) -> None:
            if stopping[0]:
                return
            # Filter early so we don't spawn body-fetch tasks for assets.
            try:
                if response.request.resource_type not in self.allowed_resource_types:
                    return
            except Exception:
                return
            task = asyncio.create_task(self._on_response(response, merger))
            response_tasks.append(task)

        page.on("request", on_request)
        page.on("response", on_response)

        # SPA resilience: re-inject nothing (init script auto-applies), but log
        # navigations for diagnostic purposes.
        page.on("framenavigated", lambda f: log.debug("framenavigated: %s", f.url))

    async def _on_response(self, response: Response, merger: _Merger) -> None:
        try:
            req = response.request
            if req.resource_type not in self.allowed_resource_types:
                return
            body: str | None = None
            truncated = False
            content_type = response.headers.get("content-type")
            try:
                # Bound the body fetch — closed/navigated pages can make this
                # hang, blocking the gather() drain at the end of extract().
                raw = await asyncio.wait_for(
                    response.body(), timeout=self.body_timeout_s,
                )
                if raw:
                    if len(raw) > self.max_body_size:
                        truncated = True
                        raw = raw[: self.max_body_size]
                    try:
                        body = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        body = f"<binary:{len(raw)}>"
            except asyncio.TimeoutError:
                log.debug("response.body() timed out for %s", response.url)
            except PlaywrightError as e:
                log.debug("response.body() failed for %s: %s", response.url, e)
            merger.add_pw_response(
                id(req),
                CapturedResponse(
                    url=response.url,
                    status=response.status,
                    headers=dict(response.headers),
                    body=body,
                    body_truncated=truncated,
                    content_type=content_type,
                    timestamp=time.time(),
                ),
            )
        except Exception as e:
            log.debug("on_response error: %s", e)

    # ---- navigation & automation ------------------------------------------

    async def _goto_with_retry(self, page: Page, url: str) -> None:
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                await page.goto(url, wait_until="domcontentloaded",
                                timeout=self.nav_timeout_ms)
                return
            except PlaywrightError as e:
                last_err = e
                log.warning("navigation attempt %d/%d failed: %s",
                            attempt + 1, self.retries + 1, e)
                await asyncio.sleep(1.0 + attempt)
        raise last_err  # type: ignore[misc]

    async def _auto_scroll(self, page: Page, max_steps: int = 25, pause: float = 0.7) -> None:
        try:
            prev_height = -1
            for _ in range(max_steps):
                height = await page.evaluate("document.body ? document.body.scrollHeight : 0")
                if height == prev_height:
                    break
                await page.evaluate(f"window.scrollTo(0, {height})")
                await asyncio.sleep(pause)
                prev_height = height
            # Return to top so subsequent button discovery isn't biased
            # toward footer-only buttons.
            await page.evaluate("window.scrollTo(0, 0)")
        except PlaywrightError as e:
            log.debug("auto_scroll stopped: %s", e)

    async def _click_visible_buttons(self, page: Page, *, max_clicks: int = 5) -> None:
        """Click a bounded number of visible, low-risk buttons to trigger
        additional API calls. Skips anything that looks destructive or
        auth-exiting."""
        danger = re.compile(
            r"\b(logout|sign[ -]?out|delete|remove|cancel\s+account|"
            r"close\s+account|deactivate|unsubscribe|pay(\s+now)?|checkout|purchase)\b",
            re.IGNORECASE,
        )
        try:
            locator = page.locator(
                'button, a[role="button"], [role="button"], input[type="button"], input[type="submit"]'
            )
            total = await locator.count()
        except PlaywrightError as e:
            log.debug("button enumeration failed: %s", e)
            return

        clicked = 0
        for i in range(min(total, 60)):
            if clicked >= max_clicks:
                break
            btn = locator.nth(i)
            try:
                if not await btn.is_visible():
                    continue
                if not await btn.is_enabled():
                    continue
                try:
                    text = (await btn.inner_text(timeout=500)).strip()
                except PlaywrightError:
                    text = ""
                if not text:
                    # Try aria-label / title as a fallback
                    text = (await btn.get_attribute("aria-label") or "") + \
                           " " + (await btn.get_attribute("title") or "")
                    text = text.strip()
                if danger.search(text):
                    continue
                await btn.click(timeout=2_000, no_wait_after=True)
                clicked += 1
                await asyncio.sleep(0.6)
                try:
                    await page.wait_for_load_state("networkidle", timeout=2_000)
                except PlaywrightError:
                    pass
            except PlaywrightError as e:
                log.debug("skip button %d: %s", i, e)

    # ---- result assembly ---------------------------------------------------

    def _build_result(self, merger: _Merger, *, source_url: str) -> dict[str, Any]:
        merged_pairs = merger.merge()
        endpoints: dict[tuple[str, str], Endpoint] = {}

        for req, resp in merged_pairs:
            parsed_body = _parse_body(req.body)
            classification, gql = classify_endpoint(req.url, parsed_body)
            norm_url = normalize_url(req.url)
            key = (req.method.upper(), norm_url)

            if key not in endpoints:
                p = urlparse(req.url)
                endpoints[key] = Endpoint(
                    method=req.method.upper(),
                    normalized_url=norm_url,
                    sample_url=req.url,
                    domain=p.netloc,
                    path=urlparse(norm_url).path,
                    query_keys=sorted({k for k, _ in parse_qsl(p.query)}),
                    classification=classification,
                )
            ep = endpoints[key]
            ep.request_count += 1

            if resp is not None:
                if resp.status and resp.status not in ep.status_codes:
                    ep.status_codes.append(resp.status)
                ep.status_codes.sort()
                if resp.content_type:
                    ct = resp.content_type.split(";", 1)[0].strip().lower()
                    if ct and ct not in ep.content_types:
                        ep.content_types.append(ct)

            if not ep.headers_sample:
                ep.headers_sample = self._redact_headers(req.headers)

            # Populate request_sample: prefer parsed body, fall back to query
            # params, then to path params extracted from dynamic segments.
            # For GETs the query string IS the client's payload; for RESTful
            # GETs that encode inputs in the path (/users/{id}) the path
            # values are the payload.
            if ep.request_sample is None:
                sample: Any = parsed_body
                if sample is None:
                    qs = dict(parse_qsl(urlparse(req.url).query, keep_blank_values=True))
                    if qs:
                        sample = qs
                if sample is None:
                    path_params = extract_path_params(req.url, norm_url)
                    if path_params:
                        sample = path_params
                if sample is not None:
                    ep.request_sample = self._cap_value(sample)

            # Populate response_sample from any iteration with a body — not
            # just the first. If we later see a dict/list parse while the
            # current sample is raw text, upgrade it.
            if resp is not None and resp.body:
                parsed_resp = _parse_body(resp.body)
                if ep.response_sample is None:
                    ep.response_sample = self._cap_value(parsed_resp)
                elif (not isinstance(ep.response_sample, (dict, list))
                      and isinstance(parsed_resp, (dict, list))):
                    ep.response_sample = self._cap_value(parsed_resp)

            # Patterns
            pagination = detect_pagination(req.url, parsed_body)
            if pagination:
                ep.detected_patterns.setdefault("pagination", pagination)

            auth = detect_auth(req.headers)
            if auth and auth["mechanisms"]:
                ep.detected_patterns.setdefault("auth", auth)
                ep.requires_auth = True

            if gql:
                ep.classification = "graphql"
                if "operations" in gql:  # batched
                    for op in gql["operations"]:
                        self._add_gql_op(ep, op)
                else:
                    self._add_gql_op(ep, gql)

            if not ep.curl_equivalent:
                ep.curl_equivalent = to_curl(req)

        endpoint_list = list(endpoints.values())

        # JSON-only filter: keep anything that looks like a data API —
        # JSON content-type, JSON-parseable body, GraphQL, or a JSON request.
        filtered_out = 0
        if self.only_json:
            kept: list[Endpoint] = []
            for ep in endpoint_list:
                if self._is_json_endpoint(ep):
                    kept.append(ep)
                else:
                    filtered_out += 1
                    log.debug("filtered non-JSON endpoint: %s %s (content-types=%s)",
                              ep.method, ep.normalized_url, ep.content_types)
            endpoint_list = kept

        endpoint_list.sort(key=lambda e: (e.domain, e.path, e.method))

        grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for ep in endpoint_list:
            grouped[ep.domain][ep.path].append(asdict(ep))

        return {
            "source_url": source_url,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "total_endpoints": len(endpoint_list),
            "filtered_non_json": filtered_out,
            "auth_required_count": sum(1 for ep in endpoint_list if ep.requires_auth),
            "graphql_count": sum(1 for ep in endpoint_list if ep.classification == "graphql"),
            "endpoints": [asdict(ep) for ep in endpoint_list],
            "grouped_by_domain": {d: dict(paths) for d, paths in grouped.items()},
        }

    @staticmethod
    def _is_json_endpoint(ep: Endpoint) -> bool:
        """True if any evidence points to this being a JSON data API.
        Order matters: GraphQL first, then wire-level content-type, then
        parsed samples (handles servers that return JSON with wrong
        content-type headers)."""
        if ep.classification == "graphql":
            return True
        for ct in ep.content_types:
            if "json" in ct:
                return True
        if isinstance(ep.response_sample, (dict, list)):
            return True
        # A JSON *body* is strong evidence, but for GET/HEAD the request_sample
        # now comes from query params (which are a dict too) — those aren't
        # enough on their own to call something a JSON API.
        if (ep.method not in ("GET", "HEAD", "OPTIONS")
                and isinstance(ep.request_sample, (dict, list))):
            return True
        return False

    # ---- small helpers -----------------------------------------------------

    @staticmethod
    def _add_gql_op(ep: Endpoint, op: dict[str, Any] | None) -> None:
        if not op:
            return
        key = (op.get("operationName"), op.get("operationType"))
        for existing in ep.graphql_operations:
            if (existing.get("operationName"), existing.get("operationType")) == key:
                return
        ep.graphql_operations.append(op)

    @staticmethod
    def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
        """Preserve which auth mechanisms are in play without leaking secrets."""
        out: dict[str, str] = {}
        for k, v in headers.items():
            kl = k.lower()
            if kl == "authorization":
                scheme = v.split(" ", 1)[0] if v and " " in v else "custom"
                out[k] = f"{scheme} <redacted>"
            elif kl == "cookie":
                names = [c.split("=", 1)[0].strip() for c in (v or "").split(";") if "=" in c]
                out[k] = "; ".join(f"{n}=<redacted>" for n in names if n)
            elif kl in AUTH_HEADER_NAMES:
                out[k] = "<redacted>"
            else:
                out[k] = v
        return out

    @staticmethod
    def _cap_value(value: Any, *, max_str: int = 1_000, max_items: int = 20) -> Any:
        if isinstance(value, str):
            return value if len(value) <= max_str else value[:max_str] + "…"
        if isinstance(value, list):
            capped = [APIEndpointExtractor._cap_value(v) for v in value[:max_items]]
            if len(value) > max_items:
                capped.append(f"<truncated {len(value) - max_items} more items>")
            return capped
        if isinstance(value, dict):
            return {k: APIEndpointExtractor._cap_value(v) for k, v in value.items()}
        return value


# ============================================================================
# CLI
# ============================================================================


async def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Discover and extract API endpoints from a web application.",
    )
    parser.add_argument("url", help="Target URL to instrument")
    parser.add_argument("-o", "--output", type=Path,
                        help="Write JSON catalogue to this file (default: stdout)")
    parser.add_argument("--headed", action="store_true",
                        help="Run with a visible browser window")
    parser.add_argument("--scroll", action="store_true",
                        help="Auto-scroll to trigger lazy-loaded requests")
    parser.add_argument("--click", action="store_true",
                        help="Click visible non-destructive buttons")
    parser.add_argument("--idle-timeout", type=int, default=5_000,
                        help="networkidle timeout in ms (default: 5000)")
    parser.add_argument("--nav-timeout", type=int, default=30_000,
                        help="Navigation timeout in ms (default: 30000)")
    parser.add_argument("--retries", type=int, default=2,
                        help="Navigation retries (default: 2)")
    parser.add_argument("--user-agent", help="Override User-Agent")
    parser.add_argument("--max-body", type=int, default=100_000,
                        help="Max response body bytes to capture (default: 100000)")
    parser.add_argument("--include-non-json", action="store_true",
                        help="Include endpoints that aren't JSON APIs (default: drop them)")
    parser.add_argument("--body-timeout", type=float, default=5.0,
                        help="Per-response body fetch timeout in seconds (default: 5.0)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    extractor = APIEndpointExtractor(
        headless=not args.headed,
        idle_timeout_ms=args.idle_timeout,
        nav_timeout_ms=args.nav_timeout,
        body_timeout_s=args.body_timeout,
        retries=args.retries,
        user_agent=args.user_agent,
        max_body_size=args.max_body,
        only_json=not args.include_non_json,
    )

    result = await extractor.extract(
        args.url,
        auto_scroll=args.scroll,
        click_buttons=args.click,
    )

    payload = json.dumps(result, indent=2, default=str, ensure_ascii=False)
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
        print(
            f"Wrote {args.output} — "
            f"{result['total_endpoints']} endpoints kept "
            f"({result['auth_required_count']} auth-gated, "
            f"{result['graphql_count']} GraphQL, "
            f"{result.get('filtered_non_json', 0)} non-JSON dropped)",
            file=sys.stderr,
        )
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(_cli()))
    except KeyboardInterrupt:
        print()