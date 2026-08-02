# higgsfield-web-api

Unofficial CLI + HTTP API over the **higgsfield.ai web app**, built to expose what the
official `higgs` CLI does not — most importantly the web-only **`unlimited` toggle**, which
generates images **for free** (no credits spent).

Runs fully **headless**: it authenticates with the Clerk session cookies already in your
logged-in Chrome, so there is no browser to keep open and no API key to buy.

```bash
GW="uvx --from git+https://github.com/FarisHijazi/higgsfield-web-api"

$GW higgsfield-web-api-cli bootstrap                       # one time: read Chrome's cookies
$GW higgsfield-web-api-cli generate create nano_banana \
      --prompt "a golden retriever in a sunny meadow" --wait -o out.png
```

## Why this exists

| | official `higgs` CLI | this |
|---|---|---|
| free `unlimited` generation | ✗ not exposed | ✅ `use_unlim` (default on) |
| auth | API token | your existing browser login |
| reference images | upload ids | ✅ local paths auto-uploaded |
| server | ✗ | ✅ REST API on `:8200` |

## Install / run

Requires `uv`, and **Chrome logged in to higgsfield.ai**.

```bash
uvx --from git+https://github.com/FarisHijazi/higgsfield-web-api higgsfield-web-api-cli --help
uvx --from git+https://github.com/FarisHijazi/higgsfield-web-api higgsfield-web-api        # server on :8200
```

### 1. Bootstrap (once)

```bash
higgsfield-web-api-cli bootstrap             # reads the local Chrome profile
higgsfield-web-api-cli bootstrap --cdp-url http://localhost:9222   # or a DevTools Chrome
```

Cookies are saved to `~/.config/higgsfield-web-api/clerk_cookies.json` (mode 600). From then
on the server mints short-lived Clerk JWTs itself. Re-run it if you log out or hit a
turnstile (below).

## CLI — mirrors the official `higgs` verbs

```bash
higgsfield-web-api-cli model list                    # all models; --unlimited for free ones
higgsfield-web-api-cli account status                # plan + credit balances

# generate (unlimited/free by default)
higgsfield-web-api-cli generate create nano_banana \
    --prompt "a cat astronaut" --aspect-ratio 9:16 --batch-size 2 --wait -o ./out/

# reference images — pass local files, they are uploaded automatically
higgsfield-web-api-cli generate create nano_banana \
    --prompt "same woman, in a Riyadh cafe" --image ./persona.png --wait -o cafe.png

higgsfield-web-api-cli generate get  <job_id>
higgsfield-web-api-cli generate wait <job_id> -o out.png
higgsfield-web-api-cli generate list

higgsfield-web-api-cli upload create ./ref.png       # -> media id, reusable via --image
higgsfield-web-api-cli upload list

higgsfield-web-api-cli serve --port 8200
```

Useful flags: `--no-unlim` (spend credits instead of using the free path), `--seed`,
`--style-id`, `--extra-params '{"...":...}'`, `--json`.

## HTTP API

```
GET  /health
GET  /v1/models                 list models (+ which are free)
GET  /v1/account                plan and credits
POST /v1/generate               create (and optionally wait)
GET  /v1/jobs                   recent jobs
GET  /v1/jobs/{id}              status + result urls
POST /v1/jobs/{id}/wait         poll until done
POST /v1/upload                 multipart reference upload
GET  /v1/media                  previously uploaded references
GET  /files/{job_id}.png        download a finished image
```

```bash
curl -X POST localhost:8200/v1/generate -H 'Content-Type: application/json' -d '{
  "prompt": "a red fox in the snow", "model": "nano_banana",
  "aspect_ratio": "16:9", "batch_size": 1, "use_unlim": true, "wait": true}'

MID=$(curl -s -F file=@persona.png localhost:8200/v1/upload | jq -r .id)
curl -X POST localhost:8200/v1/generate -H 'Content-Type: application/json' \
  -d "{\"prompt\":\"same woman in a cafe\",\"references\":[\"$MID\"],\"wait\":true}"
```

## Parameters

- `model` — any slug; see `model list`. Free ones: `nano_banana`, `seedream_v5_lite`,
  `seedream_v4_5`, `flux_2_pro`, `gpt_image`.
- `aspect_ratio` — `1:1 3:2 2:3 4:3 3:4 4:5 5:4 9:16 16:9 21:9` (the server derives the
  pixel size, e.g. `3:4` → 864×1184).
- `batch_size` — 1–4.
- `use_unlim` — **free generation**; on by default.
- `references` / `--image` — local file paths (auto-uploaded), media ids, or explicit
  `{"id": ..., "type": "media_input"}` dicts. Repeatable, up to 8.
- `seed`, `style_id`, `extra_params` — passed straight through for models that take them.

## How it works

```
Clerk cookies (Chrome)  ->  POST clerk.higgsfield.ai/v1/client/sessions/<sid>/tokens  ->  JWT
JWT  ->  POST fnf-api-gw.higgsfield.ai/fnf/jobs/<model>   {params, use_unlim}
     ->  GET  /fnf/jobs/<job_id>  until status == completed
     ->  results.raw.url  (public CloudFront PNG — plain unauthenticated GET)
```

Reference upload is three calls, and **all three are required** (skipping the last leaves
the media `not_ready`, and generation then fails with *"Media input not found"*):

```
POST /fnf/media                 -> {id, upload_url, content_type}
PUT  <upload_url>               -> raw bytes, Content-Type exactly as the slot says
POST /fnf/media/{id}/upload     -> {filename, force_nsfw_check, force_ip_check} -> "uploaded"
```

The gateway sits behind **Cloudflare + DataDome**, so requests are made with
[`curl_cffi`](https://github.com/lexiforest/curl_cffi) impersonating Chrome's TLS
fingerprint and carrying the browser's `cf_clearance` / `datadome` cookies. A plain
`requests`/`httpx` call is rejected with a challenge page.

## How long does it take?

Free (`use_unlim`) jobs go into a **shared queue**, and the queue — not the model — is what
you actually wait on. Measured on `nano_banana`:

| phase | time |
|---|---|
| upload a reference | 2–20 s (once; reuse the id afterwards) |
| create call | < 1 s |
| **queued** | **15 s – 8 min**, entirely load-dependent |
| generating (`in_progress`) | ~10–15 s |
| download | ~1 s |

Observed the same day: **~15–30 s end-to-end when quiet, ~500 s when busy.** So budget a few
minutes, and don't read a long `queued` as a hang. `--wait` prints the status every 15 s;
the default `--wait-timeout` is 900 s.

For batches, don't block: create everything first, then collect.

```bash
higgsfield-web-api-cli generate create nano_banana --prompt "..." --image ref.png   # returns a job id
higgsfield-web-api-cli generate get  <job_id>          # check later
higgsfield-web-api-cli generate wait <job_id> -o out.png
```

## Known limits

- **Human verification (`turnstile_required`).** Generating too fast trips an anti-bot gate
  on the *account* — the website itself is blocked the same way until it clears. The client
  raises a clear error telling you to open higgsfield.ai, complete the check, and re-run
  `bootstrap`. It is not bypassed programmatically. Pace your requests to avoid it.
- **Cookie lifetime.** Clerk cookies eventually expire; re-run `bootstrap`.
- **Model coverage.** Only some slugs have a POST route; unknown ones answer HTTP 405.
  `model list` marks the ones verified against the live API.
- Uploaded media reports `width`/`height` as `null` via the API even when `uploaded` — the
  values are filled in asynchronously and are not needed for generation.

## Development

```bash
uv sync
uv run pytest tests/unit_test.py         # offline unit tests
uv run python tests/smoke.py             # live: bootstrap + free generation + download
uv run python tests/upload_ref_e2e.py    # live: upload -> reference -> generate
```

## Legal

Unofficial and unaffiliated with Higgsfield. It drives your own logged-in account through
the same endpoints the website uses; you remain bound by Higgsfield's terms. No credentials
are transmitted anywhere except to Higgsfield's own servers.
