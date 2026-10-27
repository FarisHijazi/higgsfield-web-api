# /// script
# dependencies = ["httpx"]
# ///
"""Log into the Higgsfield MCP server (OAuth auth-code + PKCE, or HF_TOKEN="$(higgs auth token)") and dump its tools, prompts and resources.

    uv run scripts/mcp_probe.py login      # prints an authorize URL; open it where higgsfield.ai is signed in
    uv run scripts/mcp_probe.py finish 'http://localhost:8765/callback?code=...'
    uv run scripts/mcp_probe.py dump OUT   # writes tools.json / prompts.json / resources.json + each prompt body
    uv run scripts/mcp_probe.py call TOOL '{"arg": 1}'

Lineage: owner request 2026-10-27 to reverse-engineer Higgsfield Content Factory, whose "Generate in
Claude" button only hands Claude `/content-factory` on this MCP server.
"""
import base64
import hashlib
import json
import os
import pathlib
import secrets
import sys
import urllib.parse

import httpx

AS = "https://clerk.higgsfield.ai"
MCP = "https://mcp.higgsfield.ai/mcp"
STATE = pathlib.Path.home() / ".config/higgsfield-web-api/mcp_oauth.json"
http = httpx.Client(timeout=60)


def save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=1))
    STATE.chmod(0o600)


REDIRECT = "http://localhost:8765/callback"


def login() -> None:
    """Step 1: register a public client and print the authorize URL. Open it in any browser signed in
    to higgsfield.ai; the redirect to localhost fails to load there, which is fine: copy its URL."""
    reg = http.post(f"{AS}/oauth/register", json={
        "client_name": "claude-code", "redirect_uris": [REDIRECT], "grant_types": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_method": "none", "scope": "openid email offline_access"}).json()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    save({"client_id": reg["client_id"], "verifier": verifier})
    q = urllib.parse.urlencode({"response_type": "code", "client_id": reg["client_id"], "redirect_uri": REDIRECT,
                                "scope": "openid email offline_access", "code_challenge": challenge,
                                "code_challenge_method": "S256", "state": secrets.token_urlsafe(8),
                                "resource": MCP, "prompt": "consent"})
    print(f"{AS}/oauth/authorize?{q}")


def finish(redirected_url: str) -> None:
    """Step 2: exchange the code from the redirected localhost URL."""
    st = json.loads(STATE.read_text())
    code = urllib.parse.parse_qs(urllib.parse.urlparse(redirected_url).query)["code"][0]
    tok = http.post(f"{AS}/oauth/token", data={"grant_type": "authorization_code", "code": code,
                                               "redirect_uri": REDIRECT, "client_id": st["client_id"],
                                               "code_verifier": st["verifier"], "resource": MCP}).json()
    if "access_token" not in tok:
        sys.exit(f"token exchange failed: {tok}")
    save({"client_id": st["client_id"], **tok})
    print("LOGGED IN")


def token() -> str:
    if t := os.environ.get("HF_TOKEN"):  # e.g. `higgs auth token`; the CLI and MCP share the Clerk issuer
        return t
    st = json.loads(STATE.read_text())
    if st.get("refresh_token"):
        tok = http.post(f"{AS}/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": st["refresh_token"], "client_id": st["client_id"]}).json()
        if "access_token" in tok:
            st.update(tok)
            save(st)
    return st["access_token"]


class Session:
    def __init__(self) -> None:
        self.h = {"Authorization": f"Bearer {token()}", "Accept": "application/json, text/event-stream"}
        self.n = 0
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                    "clientInfo": {"name": "claude-code", "version": "1"}}, raw=True)
        if sid := r.headers.get("mcp-session-id"):
            self.h["mcp-session-id"] = sid
        http.post(MCP, headers=self.h, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def rpc(self, method: str, params: dict | None = None, raw: bool = False):
        self.n += 1
        r = http.post(MCP, headers=self.h, json={"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        if raw:
            return r
        body = r.text
        if "text/event-stream" in r.headers.get("content-type", ""):
            parts = [l[5:].removeprefix(" ") for l in body.splitlines() if l.startswith("data:")]
            try:
                return json.loads("\n".join(parts))
            except json.JSONDecodeError:  # big payloads arrive split mid-string across data: lines
                return json.loads("".join(parts))
        return json.loads(body)


def dump(out: str) -> None:
    o = pathlib.Path(out)
    o.mkdir(parents=True, exist_ok=True)
    s = Session()
    for kind in ("tools", "prompts", "resources", "resources/templates"):
        res = s.rpc(f"{kind}/list")
        (o / f"{kind.replace('/', '_')}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
        print(kind, len((res.get("result") or {}).get(kind.split("/")[-1].replace("templates", "resourceTemplates"), [])))
    prompts = (s.rpc("prompts/list").get("result") or {}).get("prompts", [])
    (o / "prompt-bodies").mkdir(exist_ok=True)
    for p in prompts:
        args = {a["name"]: "What if the Moon had a city" for a in p.get("arguments", []) or []}
        body = s.rpc("prompts/get", {"name": p["name"], "arguments": args})
        (o / "prompt-bodies" / f"{p['name']}.json").write_text(json.dumps(body, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "login":
        login()
    elif cmd == "finish":
        finish(sys.argv[2])
    elif cmd == "dump":
        dump(sys.argv[2])
    elif cmd == "call":
        print(json.dumps(Session().rpc("tools/call", {"name": sys.argv[2], "arguments": json.loads(sys.argv[3])}),
                         indent=1, ensure_ascii=False))
