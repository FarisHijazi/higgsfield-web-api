# Build notes — how this was reverse-engineered, and what bit

Written 2026-08-02, while building the tool against the live site. Everything here is
**observed**, not inferred: captured from a logged-in higgsfield.ai tab by wrapping
`window.fetch` / `XMLHttpRequest` and reading back the recorded calls.

The README covers *how to use* the tool. This file covers *why it is shaped the way it is* —
read it before changing the transport, the upload flow, or the timeouts.

> ⚠️ No tokens, cookies or signature values appear here — only parameter names and payload
> shapes.

## The point of the whole thing

The web app has an **`unlimited`** toggle the official `higgs` CLI never exposes. Jobs created
with `use_unlim=true` cost **nothing** — the wallet stayed flat at 45 credits across ~15
generations during the build. Image generation, **including img2img from a reference**, is
free; it is the same operation the paid CLI charges ~2 credits per frame for.

That is the entire reason this exists, which is why `use_unlim` defaults to `True` in the
client, the server schema and the CLI, and why four unit tests pin that default — so it cannot
silently regress into spending credits.

## The call graph

| Step | Call |
|---|---|
| auth | `POST clerk.higgsfield.ai/v1/client/sessions/<sid>/tokens` → short-lived JWT |
| create | `POST fnf-api-gw.higgsfield.ai/fnf/jobs/<model-with-dashes>` `{params, use_unlim}` |
| poll | `GET /fnf/jobs/<job_id>` → `status`, `results` |
| download | `results.raw.url` — **public unsigned CloudFront PNG**, plain GET |
| account | `GET /fnf/user` (plan + credits) · jobs list: `GET /fnf/jobs` |

**Auth is fully headless.** The Clerk token endpoint mints a fresh JWT from the cookie jar
alone, so there is no browser to keep open and no tab pool to babysit — `bootstrap` reads
Chrome's cookies once and everything after that is a plain HTTP client.

The `input_images` schema was recovered by sending deliberately-wrong shapes and reading the
validator's own error messages back: pydantic names every accepted `type` enum value for you.

## The three things that cost real debugging time

1. **Cloudflare + DataDome.** A plain `httpx` call gets a challenge page *even with a perfect
   cookie jar*. It needs [`curl_cffi`](https://github.com/lexiforest/curl_cffi) impersonating
   Chrome's TLS fingerprint **and** the browser's client-hint headers (`sec-ch-ua*`,
   `sec-fetch-*`). TLS alone was not enough — that combination is why `clerk.py` builds one
   shared session with a fixed header block instead of using `requests`.

2. **Reference upload is three calls, not two.**

   ```
   POST /fnf/media              -> {id, upload_url, content_type}
   PUT  <upload_url>            -> raw bytes, Content-Type exactly as the slot says
   POST /fnf/media/{id}/upload  -> {filename, force_nsfw_check, force_ip_check} -> "uploaded"
   ```

   Skipping the finalize leaves the media `not_ready`, and generation then fails with
   *"Media input not found"* — **while the PUT itself returns a healthy 200 and the bytes
   really are stored.** `PATCH /fnf/media/{id}` answers 204 and is a red herring. Found by
   injecting a synthetic `File` into the page's own uploader and capturing what the web app
   actually does. Isolated first by proving a known-good web-UI media id worked as a reference,
   which localised the fault to the upload leg rather than the generate leg.

3. **The 405 trap.** Unknown model slugs return `405 Method Not Allowed`, not 404 — the
   backend falls through to `GET /fnf/jobs/{job_id}`. So a wrong slug presents as a broken
   HTTP method. `model list` marks which slugs are verified against the live API.

## Timing — free jobs QUEUE, and it caused a real bug

Measured, not estimated (`tests/timing.py`):

| phase | time |
|---|---|
| upload a reference | 2–20 s — once; reuse the media id afterwards |
| create call | < 1 s |
| **queued** | **15 s – 8 min**, entirely load-dependent |
| generating (`in_progress`) | ~10–15 s |
| download | ~1 s |

Observed the same day: **~15–30 s end-to-end when quiet, 504 s when busy** (one timed run:
480 s `queued`, then 10 s generating). A queue-drain sample returned completions at 274 s,
494 s, 509 s and 503 s.

**The model is not what you wait on — the shared free queue is.** That exposed a genuine bug:
the default `--wait-timeout` was **300 s, shorter than a normal busy-hour job**, so `--wait`
aborted perfectly healthy queued jobs and reported a timeout. The fix:

- all timeouts 300 → **900 s** (CLI ×2, server ×2, client ×2);
- `wait()` gained `on_progress(status, elapsed)`, and the CLI prints the status every 15 s so a
  long `queued` never reads as a hang;
- the timeout message names the cause and points at `generate get <id>`.

**Batch rule: don't block.** Create every job first (no `--wait`), then collect with
`generate get` / `generate wait`. Serialising `--wait` per image multiplies the queue wait by
the batch size.

## Human verification is a real operational limit

Generating too quickly trips `turnstile_required` on the **account** — and the *website itself*
is blocked identically until it clears, so it is not a flaw in this tool. It is **not** bypassed
programmatically, and must not be. Recovery: open higgsfield.ai in the logged-in Chrome,
generate once, complete the check, re-run `bootstrap`.

So the batch rule above has a second half: **pace the creates** a few seconds apart. Firing a
whole batch at once is exactly what trips it.

## Things that look like bugs but aren't

- **There is no "quality" parameter.** The web UI's settings row is only batch / aspect ratio /
  unlimited / draw — resolution derives from `aspect_ratio`. Don't add a quality flag; there is
  nothing behind it.
- **Uploaded media reports `width`/`height` as `null`** even when `status` is `uploaded`. The
  values are filled in asynchronously and are not needed for generation.
- **`/health` returning ok does not prove auth works** — the client is constructed lazily. Test
  with a real call.

## Scope

**Images only.** Video models are not covered here and cost credits through the official CLI.

## Verified

- 15 offline unit tests: `uv run pytest tests/unit_test.py`.
- Live: free generation → real PNG bytes downloaded; upload → reference → generate
  (`tests/upload_ref_e2e.py`); img2img from a reference portrait at 9:16, identity and clothing
  preserved; every server route including multipart `/v1/upload` and `/files/{job_id}.png`;
  `uvx --from git+https://…` install straight from GitHub.
- Timing measured end to end (`tests/timing.py`); output verified as a real `PNG 768 x 1344`.
