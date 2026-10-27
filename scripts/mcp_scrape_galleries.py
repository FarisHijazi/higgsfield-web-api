# /// script
# dependencies = ["httpx"]
# ///
"""Scrape every idea/preset gallery the Higgsfield MCP server exposes, paginated to the end, plus each preset's
detail view. Read-only: list/describe tools only, nothing generates or spends credits.

    HF_TOKEN="$(higgs auth token)" uv run scripts/mcp_scrape_galleries.py OUT [--details] [--thumbs]

Writes OUT/<gallery>.jsonl (one item per line), OUT/raw/<gallery>/page-N.json, OUT/details/<gallery>/<id>.json,
OUT/thumbs/<gallery>/<id>.<ext> and OUT/SUMMARY.tsv. Re-running skips details/thumbs already on disk.

Lineage: owner 2026-10-27, "write scrapers to scrape all of those ideas on higgsfield and get inspired, scrape
everything!!" (during the Content Factory reverse-engineering).
"""
import json
import re
import pathlib
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from mcp_probe import Session  # noqa: E402

# gallery name -> (tool, fixed args, page-size arg, detail tool or None)
GALLERIES = {
    **{f"presets-{s}": ("get_presets", {"source": s}, ("limit", 50), "get_presets")
       for s in ("viral", "marketing_studio", "soul", "what_if")},
    **{f"presets-{c}": ("get_presets", {"source": "genjutsu", "category": c}, ("limit", 50), "get_presets")
       for c in ("genjutsu", "genjutsu-trending", "genjutsu-new", "genjutsu-styles")},
    "presets-katana-higgsfield": ("get_presets", {"source": "katana", "katana_source": "higgsfield"}, ("limit", 20), "get_preset_instructions"),
    "presets-katana-community": ("get_presets", {"source": "katana", "katana_source": "community"}, ("limit", 20), "get_preset_instructions"),
    "shorts-studio": ("shorts_studio_list_presets", {}, None, None),
    "explainer": ("get_explainer_presets", {}, None, None),
    "visual-effects": ("visual_effects_list", {}, ("size", 50), "visual_effects_samples"),
    "animation-actions": ("animation_actions", {}, ("limit", 100), None),
    "ai-influencer": ("ai_influencer_list_presets", {}, ("size", 100), None),
    "character-sheets": ("ai_influencer_character_sheet_presets", {}, ("size", 100), None),
    "marketing-studio-v2": ("marketing_studio_v2_presets", {"category": "all"}, ("size", 100), None),
    "marketplace-apps": ("apps_search", {}, ("limit", 100), None),
    "models": ("models_explore", {"action": "list"}, ("limit", 100), None),
}
CURSOR_IN = {"animation_actions": "after", "models_explore": "after"}

out = pathlib.Path(sys.argv[1])
want_details, want_thumbs = "--details" in sys.argv, "--thumbs" in sys.argv
s = Session()
http = httpx.Client(timeout=60, follow_redirects=True)


def call(tool: str, args: dict) -> dict:
    for attempt in range(3):
        r = s.rpc("tools/call", {"name": tool, "arguments": args}).get("result") or {}
        if r.get("structuredContent") is not None or not r.get("isError"):
            return r
        time.sleep(2 * (attempt + 1))
    return r


def items_of(sc: dict, text: str = "") -> list[dict]:
    for v in sc.values():  # some tools nest one level: {"presets": {"items": [...], "cursor": 2}}
        if isinstance(v, dict) and items_of(v):
            return items_of(v)
    if not sc and "/katana/" in text:  # Katana answers in text only: "Name (/katana/slug), ..."
        return [{"name": n.strip(), "id": f"/katana/{s}"} for n, s in re.findall(r"([^,:(]+) \(/katana/([\w-]+)\)", text)]
    lists = [v for v in sc.values() if isinstance(v, list) and v and isinstance(v[0], dict) and len(v[0]) > 2]
    return max(lists, key=len) if lists else []


def next_of(sc: dict, text: str = ""):
    for k in ("next_cursor", "next_page_token", "nextCursor", "cursor"):
        if sc.get(k) not in (None, "", False):
            return sc[k]
    for v in sc.values():
        if isinstance(v, dict) and (n := next_of(v)) is not None:
            return n
    m = re.search(r"Next cursor: (\S+?)\.?$", text, re.M)
    return None if not m or m.group(1) == "null" else m.group(1)


summary = []
for name, (tool, fixed, size, detail_tool) in GALLERIES.items():
    raw = out / "raw" / name
    raw.mkdir(parents=True, exist_ok=True)
    rows, cursor, page, seen = [], None, 0, set()
    while True:
        args = dict(fixed)
        if size:
            args[size[0]] = size[1]
        if cursor is not None:
            args[CURSOR_IN.get(tool, "cursor")] = cursor
        res = call(tool, args)
        sc = res.get("structuredContent") or {}
        text = "\n".join(c.get("text", "") for c in res.get("content", []))
        (raw / f"page-{page}.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
        new = [i for i in items_of(sc, text) if json.dumps(i, sort_keys=True) not in seen]
        seen.update(json.dumps(i, sort_keys=True) for i in new)
        rows += new
        cursor, page = next_of(sc, text), page + 1
        if not new or cursor is None or page > 400:
            break
        time.sleep(0.3)
    with open(out / f"{name}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"{name:28} {len(rows):5} items  {page} pages", flush=True)
    summary.append((name, tool, len(rows), page))

    ids = [r.get("id") or r.get("preset_id") for r in rows]
    if want_details and detail_tool:
        d = out / "details" / name
        d.mkdir(parents=True, exist_ok=True)
        for pid in filter(None, ids):
            p = d / f"{pid.replace('/', '_').strip('_')}.json"
            if p.exists():
                continue
            a = {"preset": pid} if detail_tool == "get_preset_instructions" else {"preset_id": pid}
            if detail_tool == "get_presets":
                a |= {"source": fixed["source"], "category": "all"}
            p.write_text(json.dumps(call(detail_tool, a), indent=1, ensure_ascii=False))
            time.sleep(0.3)
    if want_thumbs:
        t = out / "thumbs" / name
        t.mkdir(parents=True, exist_ok=True)
        for r in rows:
            url = r.get("thumbnail_url") or r.get("thumbnailUrl") or r.get("preview_url") or ""
            pid = r.get("id") or r.get("preset_id")
            if not (pid and url.startswith("http")) or url.endswith((".mp4", ".webm", ".mov")):
                continue
            p = t / f"{pid}{pathlib.Path(url.split('?')[0]).suffix or '.img'}"
            if p.exists():
                continue
            try:
                p.write_bytes(http.get(url).content)
            except httpx.HTTPError as e:
                print("thumb fail", url, e)

(out / "SUMMARY.tsv").write_text("gallery\ttool\titems\tpages\n" + "".join(f"{a}\t{b}\t{c}\t{d}\n" for a, b, c, d in summary))
