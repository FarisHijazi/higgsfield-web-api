"""Client for higgsfield.ai's web backend (`fnf-api-gw.higgsfield.ai`).

Mirrors the verbs of the official `higgs` CLI (create / get / wait / list / upload /
account) but hits the *web* app's API, which exposes things the CLI does not — above all
the **`use_unlim`** toggle that generates for free.

Job lifecycle:
  create ->  POST /fnf/jobs/<model-with-dashes>  {params, use_unlim, client_meta}
  poll   ->  GET  /fnf/jobs/<job_id>             -> status, results
  result ->  results.raw.url  (public CloudFront image — plain GET, unsigned)

Reference images:
  upload ->  POST /fnf/media                      -> {id, url, upload_url}
             PUT  <upload_url>  (raw bytes)       -> then use {"id": id, "type": "media_input"}
  reuse  ->  {"id": <previous job id>, "type": "<model>_job"}
"""
from __future__ import annotations

import mimetypes
import pathlib
import time
from typing import Any, Iterable

import httpx

from .clerk import ClerkAuth
from .models import ASPECT_RATIOS, DEFAULT_MODEL, MODELS, Model, resolve

API_BASE = "https://fnf-api-gw.higgsfield.ai"

_TERMINAL = {"completed", "failed", "canceled", "cancelled", "nsfw", "error"}


class HiggsfieldError(RuntimeError):
    pass


class TurnstileRequired(HiggsfieldError):
    """Higgsfield is demanding human verification for this account.

    Triggered by generation volume/rate. It applies to the *account session*, not just this
    client — the website itself is blocked in the same way until it is cleared. It cannot be
    solved programmatically (and we do not try to).
    """

    def __init__(self) -> None:
        super().__init__(
            "higgsfield returned turnstile_required — the account must pass a human check.\n"
            "Open https://higgsfield.ai/ai/image?model=nano_banana in your logged-in Chrome, "
            "generate once and complete the verification prompt, then re-run "
            "`higgsfield-web-api-cli bootstrap` to pick up the refreshed cookies.\n"
            "Generating more slowly (fewer requests per minute) avoids re-triggering it."
        )


class HiggsfieldClient:
    def __init__(self, auth: ClerkAuth, timeout: float = 60.0):
        self.auth = auth
        self._session = auth.session  # shared curl_cffi session (Chrome TLS + cookies)
        self._timeout = timeout

    # ---- low level --------------------------------------------------------
    def _request(self, method: str, path: str, force_token: bool = False, **kw) -> Any:
        headers = {"Authorization": f"Bearer {self.auth.get_token(force=force_token)}"}
        r = self._session.request(method, API_BASE + path, headers=headers,
                                  timeout=self._timeout, **kw)
        if r.status_code == 401 and not force_token:  # token raced past expiry — retry once
            return self._request(method, path, force_token=True, **kw)
        if r.status_code == 403 and "turnstile" in r.text:
            raise TurnstileRequired()
        if r.status_code >= 400:
            raise HiggsfieldError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r.json()

    # ---- models -----------------------------------------------------------
    @staticmethod
    def list_models() -> tuple[Model, ...]:
        return MODELS

    # ---- media / references ----------------------------------------------
    def upload(self, path: str | pathlib.Path, *, filename: str | None = None,
               nsfw_check: bool = True, ip_check: bool = False) -> dict:
        """Upload a local image for use as a reference.

        Three steps, all required — skipping the last leaves the media `not_ready` and the
        API rejects it later with "Media input not found":
          1. POST /fnf/media                  -> {id, upload_url, content_type}
          2. PUT  <upload_url>                -> the raw bytes
          3. POST /fnf/media/{id}/upload      -> finalize; flips status to "uploaded"

        Returns a record whose ``id``/``type`` can go straight into ``input_images``.
        """
        p = pathlib.Path(path).expanduser()
        name = filename or p.name
        data = p.read_bytes()
        slot = self._request("POST", "/fnf/media", json={})
        upload_url = slot.get("upload_url")
        if not upload_url:
            raise HiggsfieldError(f"/fnf/media gave no upload_url: {list(slot)}")
        # PUT with exactly the content type the slot was signed for.
        ctype = slot.get("content_type") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        r = self._session.put(upload_url, data=data, headers={"Content-Type": ctype},
                              timeout=max(self._timeout, 300.0))
        if r.status_code >= 400:
            raise HiggsfieldError(f"upload PUT -> HTTP {r.status_code}: {r.text[:200]}")
        rec = self._request("POST", f"/fnf/media/{slot['id']}/upload",
                            json={"filename": name,
                                  "force_nsfw_check": nsfw_check,
                                  "force_ip_check": ip_check})
        if rec.get("status") != "uploaded":
            raise HiggsfieldError(
                f"upload finalize returned status={rec.get('status')!r} for {slot['id']}")
        return {"id": slot["id"], "type": "media_input", "url": slot.get("url"),
                "status": rec.get("status"), "filename": name, "size_bytes": len(data)}

    def list_media(self, limit: int = 20) -> list[dict]:
        return (self._request("GET", "/fnf/media", params={"limit": limit}) or {}).get("items", [])

    def _resolve_references(self, references: Iterable[Any] | None) -> list[dict]:
        """Accept file paths, media/job ids, or ready-made {id,type} dicts."""
        out: list[dict] = []
        for ref in references or []:
            if isinstance(ref, dict):
                out.append(ref)
            elif isinstance(ref, (str, pathlib.Path)) and pathlib.Path(str(ref)).expanduser().exists():
                up = self.upload(ref)
                out.append({"id": up["id"], "type": "media_input"})
            else:  # a bare id — assume an already-uploaded media input
                out.append({"id": str(ref), "type": "media_input"})
        return out

    # ---- jobs -------------------------------------------------------------
    def create(
        self,
        prompt: str,
        model: str = DEFAULT_MODEL,
        *,
        aspect_ratio: str = "3:4",
        batch_size: int = 1,
        use_unlim: bool = True,
        width: int = 1024,
        height: int = 1024,
        references: Iterable[Any] | None = None,
        seed: int | None = None,
        style_id: str | None = None,
        extra_params: dict | None = None,
    ) -> dict:
        """Create a generation job set. Returns the raw API response."""
        if aspect_ratio not in ASPECT_RATIOS:
            raise ValueError(f"aspect_ratio must be one of {ASPECT_RATIOS}")
        if not 1 <= batch_size <= 4:
            raise ValueError("batch_size must be between 1 and 4")
        m = resolve(model)
        params: dict[str, Any] = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio,
            "batch_size": batch_size,
            "use_unlim": use_unlim,
            "width": width,
            "height": height,
            "input_images": self._resolve_references(references),
        }
        if seed is not None:
            params["seed"] = seed
        if style_id is not None:
            params["style_id"] = style_id
        params.update(extra_params or {})
        body = {"params": params, "use_unlim": use_unlim, "client_meta": None}
        return self._request("POST", m.route, json=body)

    @staticmethod
    def job_ids(create_response: dict) -> list[str]:
        return [job["id"]
                for js in create_response.get("job_sets", [])
                for job in js.get("jobs", []) if job.get("id")]

    def get(self, job_id: str) -> dict:
        return self._request("GET", f"/fnf/jobs/{job_id}")

    def list_jobs(self, limit: int = 20) -> list[dict]:
        return (self._request("GET", "/fnf/jobs", params={"limit": limit}) or {}).get("jobs", [])

    def wait(self, job_id: str, timeout: float = 900.0, interval: float = 3.0,
             on_progress: "callable | None" = None) -> dict:
        """Poll a job until it reaches a terminal state or times out.

        Free (`use_unlim`) jobs sit in a shared queue, so this can legitimately take
        several minutes — `on_progress(status, elapsed)` is called each poll so callers
        can show that it is queued rather than hung.
        """
        start = time.monotonic()
        deadline = start + timeout
        while True:
            job = self.get(job_id)
            status = job.get("status")
            if status in _TERMINAL:
                if status != "completed":
                    raise HiggsfieldError(f"job {job_id} ended as '{status}'")
                return job
            if time.monotonic() >= deadline:
                raise HiggsfieldError(
                    f"job {job_id} still '{status}' after {timeout:.0f}s — free jobs queue; "
                    f"raise --wait-timeout, or poll later with `generate get {job_id}`")
            if on_progress:
                on_progress(status, time.monotonic() - start)
            time.sleep(interval)

    @staticmethod
    def result_urls(job: dict) -> dict[str, str]:
        """{'raw': <png url>, 'min': <webp thumb>} from a completed job."""
        results = job.get("results") or {}
        return {k: v["url"] for k, v in results.items()
                if isinstance(v, dict) and v.get("url")}

    def download(self, url: str, dest: str | pathlib.Path | None = None) -> bytes | pathlib.Path:
        """Download a result image. CloudFront URLs are public — no auth needed."""
        r = httpx.get(url, timeout=120.0, follow_redirects=True)
        r.raise_for_status()
        if dest is None:
            return r.content
        p = pathlib.Path(dest).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
        return p

    def account(self) -> dict:
        """Account: plan tier and credit balances (`GET /fnf/user`).

        Note free `use_unlim` generations do not touch these balances.
        """
        return self._request("GET", "/fnf/user")

    def workspaces(self) -> list[dict]:
        return self._request("GET", "/fnf/workspaces")

    # ---- convenience ------------------------------------------------------
    def generate(
        self,
        prompt: str,
        model: str = DEFAULT_MODEL,
        *,
        timeout: float = 900.0,
        **kw,
    ) -> list[dict]:
        """Create + wait for every job in the batch.
        Returns [{job_id, status, results:{raw,min}}]."""
        resp = self.create(prompt, model, **kw)
        out = []
        for jid in self.job_ids(resp):
            job = self.wait(jid, timeout=timeout)
            out.append({"job_id": jid, "status": job.get("status"),
                        "results": self.result_urls(job)})
        return out

    def close(self) -> None:
        self._session.close()
