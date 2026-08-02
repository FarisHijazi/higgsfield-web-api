"""Model registry for the higgsfield **web** app.

The web app exposes models the official `higgs` CLI does not price the same way — most
importantly a set marked **UNLIMITED**, which generate for free via `use_unlim=True`.

There is no public models endpoint (`/fnf/models` 404s), so this table is captured from the
web app's model picker plus the job-type vocabulary the API's own validator returns. Any
slug may still be passed straight through — `verified` only records what has been exercised
against the live API here.

Route rule: the API path is the job type with underscores turned into dashes,
e.g. `nano_banana` -> `POST /fnf/jobs/nano-banana`. A slug with no POST route answers
405 (FastAPI falls through to `GET /fnf/jobs/{job_id}`).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Model:
    slug: str                      # job type, e.g. "nano_banana"
    name: str                      # display name in the web UI
    unlimited: bool = False        # can run free with use_unlim=True
    verified: bool = False         # POST route exercised against the live API
    params: tuple[str, ...] = field(default=())
    notes: str = ""

    @property
    def route(self) -> str:
        return "/fnf/jobs/" + self.slug.replace("_", "-")


_COMMON = ("prompt", "aspect_ratio", "batch_size", "width", "height", "input_images", "use_unlim")

MODELS: tuple[Model, ...] = (
    # --- free / unlimited -------------------------------------------------
    Model("nano_banana", "Nano Banana", unlimited=True, verified=True, params=_COMMON,
          notes="Default. Free with use_unlim=True; verified end-to-end."),
    Model("seedream_v5_lite", "Seedream 5.0 lite", unlimited=True, params=_COMMON),
    Model("seedream_v4_5", "Seedream 4.5", unlimited=True, params=_COMMON),
    Model("flux_2_pro", "FLUX.2 Pro", unlimited=True, params=_COMMON),
    Model("gpt_image", "GPT Image", unlimited=True, params=_COMMON),
    # --- credit-based -----------------------------------------------------
    Model("nano_banana_2", "Nano Banana 2", verified=True, params=_COMMON,
          notes="Route confirmed (reached the anti-bot gate, i.e. routing succeeded)."),
    Model("nano_banana_pro", "Nano Banana Pro", params=_COMMON),
    Model("nano_banana_2_lite", "Nano Banana 2 Lite", params=_COMMON),
    Model("nano_banana_flash", "Nano Banana (flash)", params=_COMMON),
    Model("seedream_v5", "Seedream 5.0 Pro", params=_COMMON),
    Model("gpt_image_2", "GPT Image 2", params=_COMMON),
    Model("grok_image", "Grok Imagine", params=_COMMON),
    Model("recraft_v4_1", "Recraft V4.1", params=_COMMON),
    Model("openai_hazel", "OpenAI Hazel", params=_COMMON),
    Model("z_image", "Z Image", params=_COMMON),
    Model("text2image_soul_v2", "Higgsfield Soul 2.0", verified=True,
          params=("prompt", "width", "height", "batch_size", "seed", "style_id"),
          notes="Requires seed and style_id (server-validated)."),
    Model("soul_cinematic", "Higgsfield Soul Cinema", params=_COMMON),
    Model("flux_kontext", "Flux Kontext", verified=True,
          params=("prompt", "width", "height", "input_images", "seed"),
          notes="Image-editing model: input_images and seed are required."),
    Model("outpaint", "Outpaint", params=("prompt", "input_images", "width", "height")),
    Model("nano_banana_2_upscale", "Nano Banana 2 Upscale", params=("input_images",)),
    Model("nano_banana_2_skin_enhancer", "Skin Enhancer", params=("input_images",)),
    Model("nano_banana_2_relight", "Relight", params=("prompt", "input_images")),
    Model("nano_banana_2_ai_stylist", "AI Stylist", params=("prompt", "input_images")),
    Model("character_swap_v2", "Character Swap", params=("input_images",)),
)

BY_SLUG = {m.slug: m for m in MODELS}
DEFAULT_MODEL = "nano_banana"

# Aspect ratios accepted by the image models (matches `higgs model get nano_banana`).
ASPECT_RATIOS = ("1:1", "3:2", "2:3", "4:3", "3:4", "4:5", "5:4", "9:16", "16:9", "21:9")

# input_images[].type — from the API validator's own enum. `media_input` is an uploaded
# file; every `*_job` value references the output of a previous job of that model.
INPUT_IMAGE_TYPES = (
    "media_input", "image_job", "text2image_job", "text2image_gpt_job", "flux_kontext_job",
    "nano_banana_job", "seedream_job", "seedream_v4_5_job", "seedream_v5_job", "canvas_job",
    "canvas_soul_job", "text2image_soul_job", "text2image_soul_v2_job", "soul_cinematic_job",
    "z_image_job", "nano_banana_2_skin_enhancer_job", "skin_enhancer_job",
    "nano_banana_2_relight_job", "nano_banana_2_ai_stylist_job", "character_swap_v2_job",
    "nano_banana_2_upscale_job", "nano_banana_2_shots_job", "nano_banana_2_zooms_job",
    "nano_banana_flash_job", "openai_hazel_job", "openai_hazel_mini_job",
    "video_frame_extractor_job", "ai_influencer_job", "headshot_job", "outpaint_job",
    "grok_job", "grok_image_job", "image_auto_job",
)


def resolve(slug: str) -> Model:
    """Look up a model, or synthesise a passthrough entry for an unknown slug."""
    slug = slug.replace("-", "_")
    return BY_SLUG.get(slug) or Model(slug, slug, notes="unknown slug — passed through")


def unlimited_models() -> list[Model]:
    return [m for m in MODELS if m.unlimited]
