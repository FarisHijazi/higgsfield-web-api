---
name: higgsfield-web-api
description: Generate images through higgsfield.ai's web app for FREE (the web-only "unlimited" toggle the official higgs CLI doesn't expose), including reference/persona images. Use for any image generation where Higgsfield credits are low or when img2img from a locked persona/product photo is needed.
---

# higgsfield-web-api

Drive **higgsfield.ai** with the user's existing browser login — **no API key, and free**.

The web app has an **`unlimited`** toggle the official `higgs` CLI does not expose. Jobs
created with `use_unlim=true` cost **0 credits** (verified: balance unchanged across many
generations). This is the cheap path for images; the paid `higgs` CLI is the fallback.

Source: <https://github.com/FarisHijazi/higgsfield-web-api>

```bash
GW="uvx --from git+https://github.com/FarisHijazi/higgsfield-web-api"
```

**Requires:** `uv`, and **Chrome logged in to higgsfield.ai**.

## 1. One-time bootstrap

```bash
$GW higgsfield-web-api-cli bootstrap
```

Harvests the Clerk cookies from the local Chrome profile into
`~/.config/higgsfield-web-api/clerk_cookies.json`. Everything after that is **headless** —
no browser needs to stay open. Re-run it if auth fails or a turnstile appears (§5).

## 2. Generate (free by default)

```bash
$GW higgsfield-web-api-cli generate create nano_banana \
    --prompt "a golden retriever in a sunny meadow, photorealistic" \
    --aspect-ratio 9:16 --wait -o /tmp/out.png
```

- `--wait` blocks until done; `-o` saves the PNG (a directory if `--batch-size` > 1).
- Free unless you pass `--no-unlim`.
- `--batch-size 1..4`, `--seed`, `--json`.
- Aspect ratios: `1:1 3:2 2:3 4:3 3:4 4:5 5:4 9:16 16:9 21:9` (server picks the pixels).

## 3. Reference images (img2img / persona consistency) — **the important one**

Pass a local file to `--image`; it is uploaded automatically. Repeatable (up to 8).

```bash
$GW higgsfield-web-api-cli generate create nano_banana \
    --prompt "same woman, same face and same black embroidered abaya, sitting in a modern \
Riyadh cafe, candid vertical smartphone photo, natural window light" \
    --aspect-ratio 9:16 --image ./persona-summer-hijabi.png --wait -o /tmp/cafe.png
```

Verified: identity, clothing and styling carry over faithfully — this is a working
replacement for Higgsfield img2img persona shots at **zero credit cost**.

Reuse an already-uploaded reference by id (no re-upload):

```bash
$GW higgsfield-web-api-cli upload create ./ref.png      # -> media id
$GW higgsfield-web-api-cli upload list
$GW higgsfield-web-api-cli generate create nano_banana --prompt "..." --image <media_id> --wait
```

## 4. Models

```bash
$GW higgsfield-web-api-cli model list --unlimited     # the FREE ones
$GW higgsfield-web-api-cli model list                 # everything known
$GW higgsfield-web-api-cli account status             # plan + credits
```

Free (`unlimited`) models: **`nano_banana`** (default, verified end-to-end),
`seedream_v5_lite`, `seedream_v4_5`, `flux_2_pro`, `gpt_image`.
Credit-based ones (`nano_banana_pro`, `seedream_v5`, `gpt_image_2`, `flux_kontext`, …) work
too but spend credits — **ask the user before using those**.

Unknown/unsupported slugs answer HTTP 405; `model list` flags which are `verified`.

## 5. As a server

```bash
$GW higgsfield-web-api &        # :8200
curl -X POST localhost:8200/v1/generate -H 'Content-Type: application/json' \
  -d '{"prompt":"a red fox in snow","aspect_ratio":"16:9","use_unlim":true,"wait":true}'
MID=$(curl -s -F file=@persona.png localhost:8200/v1/upload | jq -r .id)
```

Routes: `/health`, `/v1/models`, `/v1/account`, `/v1/generate`, `/v1/jobs`,
`/v1/jobs/{id}`, `/v1/jobs/{id}/wait`, `/v1/upload`, `/v1/media`, `/files/{job_id}.png`.

## 6. Troubleshooting

**`turnstile_required` (HTTP 429 from the server).** Higgsfield demands human verification
when generations come too fast. It blocks the *account*, so the website is blocked the same
way — it is **not** a bug in this tool, and it must **not** be bypassed programmatically.
Fix: the user opens <https://higgsfield.ai/ai/image?model=nano_banana> in Chrome, generates
once and completes the check, then re-run `bootstrap`. **Pace generations** (a few seconds
apart, avoid long unattended bursts) to keep it from re-triggering.

**`auth error` / no Clerk cookies.** Chrome isn't logged in to higgsfield.ai on this
machine, or the cookies expired → log in, then `bootstrap`. On WSL the Linux Chrome profile
is read, not Windows Chrome; use `bootstrap --cdp-url http://localhost:9222` against a
DevTools Chrome instead.

**`Media input not found`.** The upload didn't finalize. `upload()` does POST `/fnf/media` →
PUT bytes → POST `/fnf/media/{id}/upload`; all three are required. Re-upload.

## Guidance for Claude

- **Prefer this over spending Higgsfield credits for images.** Check `model list --unlimited`
  and stay on those models; the repo's standing rule is *don't spend video/image credits
  without approval*, and `use_unlim` sidesteps that entirely.
- **Never claim an image was generated without verifying the bytes** (`-o` then check the
  file exists and is a real PNG, or `file`/size it).
- Do **not** attempt to solve or bypass the turnstile — surface it to the user.
- For persona/UGC work, always pass the account's **locked persona PNG** via `--image` so
  the face stays consistent; that is the whole point of this over text-only generation.
