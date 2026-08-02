"""Manual end-to-end smoke test: bootstrap cookies, mint a token, generate one free
(use_unlim) nano_banana image, download it. Run:  uv run python tests/smoke.py
"""
import sys

from higgsfield_api.clerk import ClerkAuth, bootstrap
from higgsfield_api.higgsfield import HiggsfieldClient


def main() -> int:
    cdp = sys.argv[1] if len(sys.argv) > 1 else None
    print("bootstrap:", "CDP " + cdp if cdp else "local Chrome (browser-cookie3)")
    cookies = bootstrap(cdp_url=cdp)
    print(f"  harvested {len(cookies)} cookies:", sorted({c['name'] for c in cookies}))

    auth = ClerkAuth()
    tok = auth.get_token()
    print(f"  minted JWT: {len(tok)} chars, prefix {tok[:3]}")

    hf = HiggsfieldClient(auth)
    print("generate (use_unlim=True, free)...")
    results = hf.generate("a small potted cactus on a windowsill, morning light", aspect_ratio="1:1")
    for r in results:
        print("  job", r["job_id"], r["status"], list(r["results"]))
        raw = r["results"].get("raw")
        if raw:
            data = hf.download(raw)
            out = f"/tmp/higgsfield_smoke_{r['job_id'][:8]}.png"
            open(out, "wb").write(data)
            print(f"    downloaded {len(data)} bytes -> {out}")
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
