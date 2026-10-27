# /// script
# dependencies = ["httpx"]
# ///
"""Dump every read-only recipe the Higgsfield MCP server serves: the preset/slash-command catalogue
(get_preset_instructions), /content-factory with all its reference parts, and each bundled workflow's
SKILL.md plus its whole resource folder. Nothing here generates or spends credits.

    HF_TOKEN="$(higgs auth token)" uv run scripts/mcp_dump_recipes.py OUT

Lineage: owner request 2026-10-27 to reverse-engineer Higgsfield Content Factory.
"""
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from mcp_probe import Session  # noqa: E402

out = pathlib.Path(sys.argv[1])
s = Session()


def call(name: str, args: dict) -> str:
    res = s.rpc("tools/call", {"name": name, "arguments": args}).get("result") or {}
    return "\n\n".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")


def save(rel: str, text: str) -> None:
    p = out / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    print(f"{len(text):>7}  {rel}")


# 1. presets / slash commands, then /content-factory and every reference path it names (followed recursively)
save("presets/_catalogue.md", call("get_preset_instructions", {}))
seen, todo = set(), ["/content-factory"]
while todo:
    ref = todo.pop(0)
    if ref in seen:
        continue
    seen.add(ref)
    text = call("get_preset_instructions", {"preset": ref})
    save("presets/" + ref.strip("/").replace("/", "__") + ".md", text)
    todo += [m for m in re.findall(r"/content-factory/[\w./-]+", text) if m.rstrip(".") not in seen]

# 2. every bundled workflow + its whole folder
listing = call("get_workflow_instructions", {})
for wf in re.findall(r"^- ([\w-]+):", listing, re.M):
    save(f"workflows/{wf}/SKILL.md", call("get_workflow_instructions", {"workflow": wf}))
    for d in ("references", "scripts", "templates", "assets", "."):
        txt = call("get_workflow_bundle_file", {"workflow": wf, "path": d, "include_contents": True})
        if txt and "not" not in txt[:60].lower():
            save(f"workflows/{wf}/bundle-{d.strip('.') or 'root'}.md", txt)
            if d == ".":
                break
