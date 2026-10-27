# /// script
# dependencies = ["httpx", "beautifulsoup4"]
# ///
"""Scrape every public page in higgsfield.ai's sitemaps (English only) into one markdown file per page:
title, URL, meta description, media URLs (og/video/img on cdn.higgsfield.ai) and the visible text.

    uv run scripts/web_scrape_sitemap.py OUT [--only motion,effects]

OUT/<section>/<slug>.md and OUT/INDEX.tsv. Re-running skips pages already saved. ~2 requests/s.

Lineage: owner 2026-10-27, "write scrapers to scrape all of those ideas on higgsfield and get inspired, scrape
everything!!". For internal research only, not for republishing.
"""
import pathlib
import re
import sys
import time

import httpx
from bs4 import BeautifulSoup

ROOT = "https://higgsfield.ai"
out = pathlib.Path(sys.argv[1])
only = next((a.split("=", 1)[1].split(",") for a in sys.argv if a.startswith("--only=")), None)
http = httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (research scraper)"})


def locs(xml_url: str) -> list[str]:
    return re.findall(r"<loc>([^<]+)</loc>", http.get(xml_url).text)


def section_of(url: str) -> str:
    path = url.removeprefix(ROOT).strip("/")
    first = path.split("/")[0] or "home"
    return "projects" if first.startswith("@") else first if "/" in path else "marketing"


pages = []
for sm in locs(f"{ROOT}/sitemap.xml"):
    if re.search(r"sitemap-(es|ko)\.xml", sm) or "help-center" in sm:
        continue
    pages += locs(sm)
pages = [u for u in dict.fromkeys(pages) if not re.search(r"higgsfield\.ai/(es|ko)(/|$)", u)]
if only:
    pages = [u for u in pages if section_of(u) in only]
print(len(pages), "pages", flush=True)

index = []
for i, url in enumerate(pages):
    sec = section_of(url)
    slug = re.sub(r"[^\w.-]+", "_", url.removeprefix(ROOT).strip("/"))[:150] or "home"
    p = out / sec / f"{slug}.md"
    if not p.exists():
        try:
            r = http.get(url)
        except httpx.HTTPError as e:
            print("fail", url, e, flush=True)
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        meta = {m.get("property") or m.get("name"): m.get("content") for m in soup.find_all("meta") if m.get("content")}
        media = sorted(set(re.findall(r"https://(?:cdn|static-public-media)\.higgsfield\.ai/[^\s\"'<>)\\]+\.(?:mp4|webm|webp|jpg|jpeg|png)", r.text)))
        for t in soup(["script", "style", "noscript", "svg"]):
            t.decompose()
        text = re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n", strip=True))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {soup.title.string.strip() if soup.title and soup.title.string else slug}\n\nurl: {url}\n"
                     f"description: {meta.get('description') or meta.get('og:description') or ''}\n\n"
                     f"## media\n" + "".join(f"- {m}\n" for m in media[:60]) + f"\n## text\n{text[:20000]}\n")
        time.sleep(0.5)
    index.append((sec, url, str(p.relative_to(out))))
    if i % 100 == 0:
        print(i, sec, url, flush=True)

(out / "INDEX.tsv").write_text("section\turl\tfile\n" + "".join("\t".join(r) + "\n" for r in index))
