"""FastAPI server over the higgsfield web app.

The routes mirror the official `higgs` CLI's verbs rather than any third-party schema:

    GET  /health
    GET  /v1/models                      list models (incl. which are free/unlimited)
    GET  /v1/account                     credit balance
    POST /v1/generate                    create (+ optionally wait) a generation
    GET  /v1/jobs                        recent jobs
    GET  /v1/jobs/{job_id}               one job (status + result urls)
    POST /v1/jobs/{job_id}/wait          poll until finished
    POST /v1/upload                      upload a reference image (multipart)
    GET  /v1/media                       previously uploaded references
    GET  /files/{job_id}.png             proxy-download a finished image

Run:  higgsfield-web-api        (uvicorn on :8200)
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from .clerk import AuthError, ClerkAuth
from .higgsfield import HiggsfieldClient, HiggsfieldError, TurnstileRequired
from .models import ASPECT_RATIOS, DEFAULT_MODEL

app = FastAPI(title="higgsfield-web-api",
              description="Unofficial API over the higgsfield.ai web app "
                          "(exposes the free `unlimited` path the CLI does not)")

_client: HiggsfieldClient | None = None


def client() -> HiggsfieldClient:
    global _client
    if _client is None:
        try:
            _client = HiggsfieldClient(ClerkAuth())
        except AuthError as e:
            raise HTTPException(401, str(e)) from e
    return _client


@app.exception_handler(TurnstileRequired)
async def _turnstile_handler(_req, exc: TurnstileRequired):
    return JSONResponse(status_code=429, content={"error": "turnstile_required", "detail": str(exc)})


@app.exception_handler(HiggsfieldError)
async def _hf_handler(_req, exc: HiggsfieldError):
    return JSONResponse(status_code=502, content={"error": "higgsfield_error", "detail": str(exc)})


@app.exception_handler(AuthError)
async def _auth_handler(_req, exc: AuthError):
    return JSONResponse(status_code=401, content={"error": "auth_error", "detail": str(exc)})


# ---- schemas --------------------------------------------------------------
class GenerateRequest(BaseModel):
    prompt: str
    model: str = DEFAULT_MODEL
    aspect_ratio: str = Field("3:4", description=f"one of {ASPECT_RATIOS}")
    batch_size: int = Field(1, ge=1, le=4)
    use_unlim: bool = Field(True, description="free 'unlimited' generation (web-only feature)")
    width: int = 1024
    height: int = 1024
    references: list[Any] | None = Field(
        None, description="reference images: uploaded media ids, {id,type} dicts, or server-side paths")
    seed: int | None = None
    style_id: str | None = None
    extra_params: dict | None = None
    wait: bool = Field(True, description="block until the images are ready")
    timeout: float = 300.0


# ---- routes ---------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    try:
        auth = ClerkAuth()
        return {"status": "ok", "authenticated": auth.has_cookies()}
    except Exception as e:  # pragma: no cover
        return {"status": "degraded", "detail": str(e)}


@app.get("/v1/models")
def list_models() -> dict:
    return {"models": [
        {"slug": m.slug, "name": m.name, "unlimited": m.unlimited,
         "verified": m.verified, "params": list(m.params), "notes": m.notes}
        for m in client().list_models()
    ], "aspect_ratios": list(ASPECT_RATIOS), "default": DEFAULT_MODEL}


@app.get("/v1/account")
def account() -> dict:
    return client().account()


@app.post("/v1/generate")
def generate(req: GenerateRequest) -> dict:
    hf = client()
    kw = dict(aspect_ratio=req.aspect_ratio, batch_size=req.batch_size, use_unlim=req.use_unlim,
              width=req.width, height=req.height, references=req.references,
              seed=req.seed, style_id=req.style_id, extra_params=req.extra_params)
    if not req.wait:
        resp = hf.create(req.prompt, req.model, **kw)
        return {"job_ids": hf.job_ids(resp), "waited": False}
    results = hf.generate(req.prompt, req.model, timeout=req.timeout, **kw)
    return {"results": results, "waited": True}


@app.get("/v1/jobs")
def list_jobs(limit: int = Query(20, ge=1, le=100)) -> dict:
    return {"jobs": client().list_jobs(limit=limit)}


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    hf = client()
    job = hf.get(job_id)
    return {"job_id": job_id, "status": job.get("status"), "results": hf.result_urls(job), "job": job}


@app.post("/v1/jobs/{job_id}/wait")
def wait_job(job_id: str, timeout: float = Body(300.0, embed=True)) -> dict:
    hf = client()
    job = hf.wait(job_id, timeout=timeout)
    return {"job_id": job_id, "status": job.get("status"), "results": hf.result_urls(job)}


@app.post("/v1/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    import tempfile

    hf = client()
    suffix = os.path.splitext(file.filename or "")[1] or ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        return hf.upload(tmp_path, filename=file.filename or "upload.png")
    finally:
        os.unlink(tmp_path)


@app.get("/v1/media")
def media(limit: int = Query(20, ge=1, le=100)) -> dict:
    return {"items": client().list_media(limit=limit)}


@app.get("/files/{job_id}.png")
def file_bytes(job_id: str) -> Response:
    """Download a finished job's image through the server (handy for local tooling)."""
    hf = client()
    urls = hf.result_urls(hf.get(job_id))
    url = urls.get("raw") or urls.get("min")
    if not url:
        raise HTTPException(404, f"job {job_id} has no result yet")
    return Response(content=hf.download(url), media_type="image/png")


def run() -> None:
    import uvicorn

    uvicorn.run(app,
                host=os.environ.get("HIGGSFIELD_API_HOST", "127.0.0.1"),
                port=int(os.environ.get("HIGGSFIELD_API_PORT", "8200")))


if __name__ == "__main__":
    run()
