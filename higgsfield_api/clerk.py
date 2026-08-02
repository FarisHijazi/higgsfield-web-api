"""Clerk authentication for higgsfield.ai — headless Bearer-token minting.

higgsfield.ai authenticates every `fnf-api-gw.higgsfield.ai` call with a short-lived
(~60s) Clerk session JWT. In the browser that JWT comes from
`window.Clerk.session.getToken()`. Headlessly we reproduce the same refresh Clerk's own
JS does: POST the Clerk session-token endpoint with the account's Clerk cookies. As long
as the cookie jar holds a valid `__client`/`__session` for clerk.higgsfield.ai, we can
mint fresh JWTs forever — no live browser, no per-request automation.

Cookie sources, tried in order:
  1. a saved cookies file (`~/.config/higgsfield-web-api/clerk_cookies.json`)
  2. the local Chrome profile, read with browser-cookie3 (default; no browser relaunch)
  3. a Chrome DevTools endpoint (`HIGGSFIELD_CDP_URL`), via `Storage.getCookies`

Run `higgsfield-web-api-cli bootstrap` once to harvest and persist the cookies.
"""
from __future__ import annotations

import base64
import json
import os
import pathlib
import time
from typing import Iterable

import httpx
from curl_cffi import requests as cffi

CLERK_BASE = "https://clerk.higgsfield.ai"
CLERK_JS_VERSION = os.environ.get("HIGGSFIELD_CLERK_JS_VERSION", "6.25.10")
COOKIE_DOMAINS = ("clerk.higgsfield.ai", "higgsfield.ai")

# The web app runs Chrome on Linux; DataDome/Cloudflare bind cf_clearance & datadome to
# this exact UA + a Chrome TLS fingerprint, so the server must impersonate Chrome and send
# the same UA + client hints the browser did. Override the UA only if your Chrome differs.
USER_AGENT = os.environ.get(
    "HIGGSFIELD_USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.0.0 Safari/537.36",
)
IMPERSONATE = os.environ.get("HIGGSFIELD_IMPERSONATE", "chrome")
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://higgsfield.ai",
    "Referer": "https://higgsfield.ai/",
    "sec-ch-ua": '"Chromium";v="150", "Not?A_Brand";v="24", "Google Chrome";v="150"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
}


def make_session() -> "cffi.Session":
    """A curl_cffi session that looks like the real Chrome to Cloudflare/DataDome."""
    return cffi.Session(impersonate=IMPERSONATE, headers=dict(BROWSER_HEADERS), timeout=60.0)

CONFIG_DIR = pathlib.Path(
    os.environ.get("HIGGSFIELD_CONFIG_DIR", os.path.expanduser("~/.config/higgsfield-web-api"))
)
COOKIES_FILE = CONFIG_DIR / "clerk_cookies.json"


class AuthError(RuntimeError):
    """No usable Clerk credentials — run `higgsfield-web-api-cli bootstrap`."""


def _jwt_exp(jwt: str) -> float:
    """Unix expiry from a JWT's payload; 0 if unreadable."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
    except Exception:
        return 0.0


class ClerkAuth:
    """Holds a Clerk cookie jar and mints/caches session JWTs from it."""

    def __init__(self, cookies: Iterable[dict] | None = None, cdp_url: str | None = None,
                 session: "cffi.Session | None" = None):
        # Shared with HiggsfieldClient so the (rotating) cookie jar stays in one place.
        self.session = session or make_session()
        self.cdp_url = cdp_url or os.environ.get("HIGGSFIELD_CDP_URL")
        self._sid: str | None = None
        self._token: str | None = None
        self._token_exp: float = 0.0

        if cookies is not None:
            self._seed(cookies)
        elif COOKIES_FILE.exists():
            self._seed(json.loads(COOKIES_FILE.read_text()))

    # ---- cookie loading ---------------------------------------------------
    def _seed(self, cookies: Iterable[dict]) -> None:
        for c in cookies:
            name, value = c.get("name"), c.get("value")
            if not name or value is None:
                continue
            # Force a dotted domain so subdomain hosts (fnf-api-gw, clerk) all receive it.
            self.session.cookies.set(name, value, domain=".higgsfield.ai", path=c.get("path", "/"))

    def has_cookies(self) -> bool:
        names = {c.name for c in self.session.cookies.jar}
        return "__client" in names or "__session" in names

    # ---- token minting ----------------------------------------------------
    def _session_id(self) -> str:
        if self._sid:
            return self._sid
        r = self.session.get(
            f"{CLERK_BASE}/v1/client",
            params={"_clerk_js_version": CLERK_JS_VERSION},
        )
        r.raise_for_status()
        resp = r.json().get("response") or {}
        sid = resp.get("last_active_session_id")
        if not sid:
            sessions = resp.get("sessions") or []
            sid = sessions[0]["id"] if sessions else None
        if not sid:
            raise AuthError("Clerk client has no active session — re-run bootstrap after logging in")
        self._sid = sid
        return sid

    def get_token(self, force: bool = False) -> str:
        """A valid Clerk session JWT, cached until ~10s before it expires."""
        now = time.time()
        if not force and self._token and now < self._token_exp - 10:
            return self._token
        if not self.has_cookies():
            raise AuthError(
                "no Clerk cookies loaded — run `higgsfield-web-api-cli bootstrap` "
                "(reads them from your logged-in Chrome)"
            )
        sid = self._session_id()
        r = self.session.post(
            f"{CLERK_BASE}/v1/client/sessions/{sid}/tokens",
            params={"_clerk_js_version": CLERK_JS_VERSION},
        )
        if r.status_code in (401, 403, 404):
            # stale session id or expired cookies — reset sid and surface clearly
            self._sid = None
            raise AuthError(
                f"Clerk refused token mint (HTTP {r.status_code}). Cookies likely expired — "
                "re-run `higgsfield-web-api-cli bootstrap`."
            )
        r.raise_for_status()
        jwt = r.json().get("jwt")
        if not jwt:
            raise AuthError("Clerk response had no jwt")
        self._token = jwt
        self._token_exp = _jwt_exp(jwt) or (now + 45)
        self._persist_cookies()  # Clerk rotates __client via Set-Cookie; keep disk in sync
        return jwt

    def _persist_cookies(self) -> None:
        if not COOKIES_FILE.exists():
            return
        try:
            save_cookies(list(self._dump_cookies()))
        except Exception:
            pass

    def _dump_cookies(self):
        for c in self.session.cookies.jar:
            if any(d in (c.domain or "") for d in COOKIE_DOMAINS):
                yield {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}

    def close(self) -> None:
        self.session.close()


# ---- bootstrap helpers ----------------------------------------------------
def save_cookies(cookies: list[dict]) -> pathlib.Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_FILE.write_text(json.dumps(cookies, indent=2))
    try:
        COOKIES_FILE.chmod(0o600)
    except OSError:
        pass
    return COOKIES_FILE


def harvest_from_browser_cookie3() -> list[dict]:
    """Read Clerk/higgsfield cookies from the local Chrome profile (no relaunch)."""
    import browser_cookie3

    out: list[dict] = []
    seen = set()
    for domain in COOKIE_DOMAINS:
        try:
            cj = browser_cookie3.chrome(domain_name=domain)
        except Exception:
            continue
        for c in cj:
            key = (c.name, c.domain)
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"})
    return out


def harvest_from_cdp(cdp_url: str) -> list[dict]:
    """Read cookies from a Chrome DevTools endpoint via Storage.getCookies."""
    import asyncio

    import websockets

    async def _grab() -> list[dict]:
        info = httpx.get(f"{cdp_url}/json/version", timeout=5).json()
        ws_url = info["webSocketDebuggerUrl"]
        async with websockets.connect(ws_url, max_size=None) as ws:
            await ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == 1:
                    cookies = msg.get("result", {}).get("cookies", [])
                    return [
                        {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c.get("path", "/")}
                        for c in cookies
                        if any(d in c["domain"] for d in COOKIE_DOMAINS)
                    ]

    return asyncio.run(_grab())


def bootstrap(cdp_url: str | None = None) -> list[dict]:
    """Harvest cookies (CDP if given, else local Chrome) and persist them."""
    cookies = harvest_from_cdp(cdp_url) if cdp_url else harvest_from_browser_cookie3()
    names = {c["name"] for c in cookies}
    if "__client" not in names and "__session" not in names:
        raise AuthError(
            "no Clerk session cookie (__client/__session) found for higgsfield.ai — "
            "log in at https://higgsfield.ai in Chrome first"
            + ("" if cdp_url else " (or pass --cdp-url for a DevTools Chrome)")
        )
    save_cookies(cookies)
    return cookies
