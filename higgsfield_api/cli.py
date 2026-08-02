"""CLI for the higgsfield web API — verbs mirror the official `higgs` CLI.

    higgsfield-web-api-cli bootstrap                 # harvest Clerk cookies from Chrome
    higgsfield-web-api-cli model list
    higgsfield-web-api-cli account status
    higgsfield-web-api-cli generate create nano_banana --prompt "..." --wait
    higgsfield-web-api-cli generate get <job_id>
    higgsfield-web-api-cli generate wait <job_id>
    higgsfield-web-api-cli generate list
    higgsfield-web-api-cli upload create ./ref.png
    higgsfield-web-api-cli upload list
    higgsfield-web-api-cli serve
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .clerk import AuthError, ClerkAuth, bootstrap
from .higgsfield import HiggsfieldClient, HiggsfieldError
from .models import ASPECT_RATIOS, DEFAULT_MODEL


def _client() -> HiggsfieldClient:
    return HiggsfieldClient(ClerkAuth())


def _emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, default=str))


def cmd_bootstrap(a) -> int:
    cookies = bootstrap(cdp_url=a.cdp_url)
    names = sorted({c["name"] for c in cookies})
    print(f"saved {len(cookies)} cookies for higgsfield.ai")
    print("  session cookies present:", [n for n in names if n in ("__client", "__session")])
    tok = ClerkAuth().get_token()
    print(f"  minted a Clerk token OK ({len(tok)} chars) — you're ready to generate")
    return 0


def cmd_model_list(a) -> int:
    models = HiggsfieldClient.list_models()
    if a.json:
        _emit([m.__dict__ for m in models], True)
        return 0
    print(f"{'SLUG':30} {'NAME':24} {'FREE':5} {'VERIFIED'}")
    for m in models:
        if a.unlimited and not m.unlimited:
            continue
        print(f"{m.slug:30} {m.name:24} {'yes' if m.unlimited else '-':5} "
              f"{'yes' if m.verified else '-'}")
    print(f"\naspect ratios: {', '.join(ASPECT_RATIOS)}")
    print("free models generate at no credit cost with --unlim (the default)")
    return 0


def cmd_account(a) -> int:
    info = _client().account()
    if a.json:
        _emit(info, True)
    else:
        print("plan:            ", info.get("plan_type"))
        print("subscription cr.:", info.get("subscription_credits"))
        print("package credits: ", info.get("package_credits"))
        print("(free `unlimited` generations do not spend credits)")
    return 0


def cmd_create(a) -> int:
    hf = _client()
    refs = a.image or []
    resp = hf.create(
        a.prompt, a.model,
        aspect_ratio=a.aspect_ratio, batch_size=a.batch_size,
        use_unlim=not a.no_unlim, seed=a.seed, style_id=a.style_id,
        references=refs,
        extra_params=json.loads(a.extra_params) if a.extra_params else None,
    )
    job_ids = hf.job_ids(resp)
    balance = (resp.get("wallet") or {}).get("credits_balance")
    print(f"created {len(job_ids)} job(s) on {a.model}"
          + (f" | credits {balance}" if balance is not None else ""))
    for jid in job_ids:
        print("  ", jid)
    if not a.wait:
        _emit({"job_ids": job_ids}, a.json)
        return 0
    out = []
    for jid in job_ids:
        job = hf.wait(jid, timeout=a.wait_timeout)
        urls = hf.result_urls(job)
        out.append({"job_id": jid, "results": urls})
        print(f"  {jid} -> {urls.get('raw', '(no url)')}")
        if a.output:
            dest = pathlib.Path(a.output)
            dest = dest / f"{jid}.png" if dest.is_dir() or len(job_ids) > 1 else dest
            hf.download(urls["raw"], dest)
            print(f"     saved {dest}")
    _emit(out, a.json)
    return 0


def cmd_get(a) -> int:
    hf = _client()
    job = hf.get(a.job_id)
    urls = hf.result_urls(job)
    print(f"{a.job_id}: {job.get('status')}")
    for k, v in urls.items():
        print(f"  {k}: {v}")
    _emit(job, a.json)
    return 0


def cmd_wait(a) -> int:
    hf = _client()
    job = hf.wait(a.job_id, timeout=a.wait_timeout)
    urls = hf.result_urls(job)
    print(f"{a.job_id}: {job.get('status')} -> {urls.get('raw', '')}")
    if a.output:
        print("  saved", hf.download(urls["raw"], a.output))
    _emit(job, a.json)
    return 0


def cmd_list(a) -> int:
    jobs = _client().list_jobs(limit=a.size)
    for j in jobs:
        print(f"{j.get('id')}  {str(j.get('status')):10} {j.get('job_set_type')}")
    _emit(jobs, a.json)
    return 0


def cmd_upload_create(a) -> int:
    rec = _client().upload(a.path)
    print(f"uploaded {rec['filename']} ({rec['size_bytes']} bytes)")
    print("  id:", rec["id"], "(use as a reference with --image)")
    _emit(rec, a.json)
    return 0


def cmd_upload_list(a) -> int:
    items = _client().list_media(limit=a.size)
    for it in items:
        print(f"{it.get('id')}  {it.get('filename')}  {it.get('width')}x{it.get('height')}")
    _emit(items, a.json)
    return 0


def cmd_serve(a) -> int:
    import uvicorn

    from .server import app

    uvicorn.run(app, host=a.host, port=a.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="higgsfield-web-api-cli",
                                description="Drive higgsfield.ai's web app (free `unlimited` generations)")
    p.add_argument("--json", action="store_true", help="print raw JSON responses")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bootstrap", help="harvest Clerk cookies from your logged-in Chrome")
    b.add_argument("--cdp-url", help="use a Chrome DevTools endpoint instead of the profile on disk")
    b.set_defaults(func=cmd_bootstrap)

    m = sub.add_parser("model", help="list models").add_subparsers(dest="sub", required=True)
    ml = m.add_parser("list", help="list image models")
    ml.add_argument("--unlimited", action="store_true", help="only the free ones")
    ml.set_defaults(func=cmd_model_list)

    ac = sub.add_parser("account", help="credits").add_subparsers(dest="sub", required=True)
    ac.add_parser("status", help="show credit balance").set_defaults(func=cmd_account)

    g = sub.add_parser("generate", aliases=["gen"], help="create / inspect generations")
    gsub = g.add_subparsers(dest="sub", required=True)

    c = gsub.add_parser("create", help="create a generation job")
    c.add_argument("model", nargs="?", default=DEFAULT_MODEL, help=f"model slug (default {DEFAULT_MODEL})")
    c.add_argument("--prompt", required=True)
    c.add_argument("--aspect-ratio", default="3:4", choices=ASPECT_RATIOS)
    c.add_argument("--batch-size", type=int, default=1, choices=range(1, 5))
    c.add_argument("--image", action="append", metavar="PATH_OR_ID",
                   help="reference image: local file (auto-uploaded) or media id; repeatable")
    c.add_argument("--seed", type=int)
    c.add_argument("--style-id")
    c.add_argument("--extra-params", help="JSON of extra model params")
    c.add_argument("--no-unlim", action="store_true",
                   help="disable the free 'unlimited' path (spends credits)")
    c.add_argument("--wait", action="store_true", help="block until finished")
    c.add_argument("--wait-timeout", type=float, default=300.0)
    c.add_argument("-o", "--output", help="save the image(s) here (file or directory)")
    c.set_defaults(func=cmd_create)

    gg = gsub.add_parser("get", help="show one job")
    gg.add_argument("job_id")
    gg.set_defaults(func=cmd_get)

    gw = gsub.add_parser("wait", help="poll a job until it finishes")
    gw.add_argument("job_id")
    gw.add_argument("--wait-timeout", type=float, default=300.0)
    gw.add_argument("-o", "--output")
    gw.set_defaults(func=cmd_wait)

    gl = gsub.add_parser("list", help="recent jobs")
    gl.add_argument("--size", type=int, default=20)
    gl.set_defaults(func=cmd_list)

    u = sub.add_parser("upload", help="reference images").add_subparsers(dest="sub", required=True)
    uc = u.add_parser("create", help="upload a local image")
    uc.add_argument("path")
    uc.set_defaults(func=cmd_upload_create)
    ul = u.add_parser("list", help="list uploaded media")
    ul.add_argument("--size", type=int, default=20)
    ul.set_defaults(func=cmd_upload_list)

    s = sub.add_parser("serve", help="run the HTTP server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8200)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AuthError as e:
        print(f"auth error: {e}", file=sys.stderr)
        return 2
    except HiggsfieldError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
