"""Offline unit tests — no network, no credentials. Run: uv run pytest tests/unit_test.py"""
import base64
import json
import time

import pytest

from higgsfield_api import clerk, models
from higgsfield_api.higgsfield import HiggsfieldClient, TurnstileRequired


# ---- models ---------------------------------------------------------------
def test_route_converts_underscores_to_dashes():
    assert models.resolve("nano_banana").route == "/fnf/jobs/nano-banana"
    assert models.resolve("nano-banana").route == "/fnf/jobs/nano-banana"


def test_unknown_slug_passes_through():
    m = models.resolve("some_future_model")
    assert m.slug == "some_future_model" and "unknown" in m.notes


def test_unlimited_models_are_flagged():
    slugs = {m.slug for m in models.unlimited_models()}
    assert "nano_banana" in slugs
    assert all(m.unlimited for m in models.unlimited_models())


# ---- jwt handling ---------------------------------------------------------
def test_jwt_exp_parses_payload():
    exp = time.time() + 60
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    assert clerk._jwt_exp(f"header.{payload}.sig") == pytest.approx(exp)


def test_jwt_exp_bad_token_is_zero():
    assert clerk._jwt_exp("not-a-jwt") == 0.0


# ---- job helpers ----------------------------------------------------------
def test_job_ids_flattens_job_sets():
    resp = {"job_sets": [{"jobs": [{"id": "a"}, {"id": "b"}]}, {"jobs": [{"id": "c"}]}]}
    assert HiggsfieldClient.job_ids(resp) == ["a", "b", "c"]


def test_result_urls_extracts_raw_and_min():
    job = {"results": {"raw": {"url": "R"}, "min": {"url": "M"}, "junk": None}}
    assert HiggsfieldClient.result_urls(job) == {"raw": "R", "min": "M"}


def test_result_urls_empty_when_pending():
    assert HiggsfieldClient.result_urls({"results": None}) == {}


# ---- validation -----------------------------------------------------------
class _FakeAuth:
    session = None

    def get_token(self, force=False):
        return "t"


def test_create_rejects_bad_aspect_ratio():
    hf = HiggsfieldClient.__new__(HiggsfieldClient)
    hf.auth = _FakeAuth()
    with pytest.raises(ValueError, match="aspect_ratio"):
        hf.create("p", aspect_ratio="7:13")


def test_create_rejects_bad_batch_size():
    hf = HiggsfieldClient.__new__(HiggsfieldClient)
    hf.auth = _FakeAuth()
    with pytest.raises(ValueError, match="batch_size"):
        hf.create("p", batch_size=9)


def test_turnstile_message_is_actionable():
    msg = str(TurnstileRequired())
    assert "bootstrap" in msg and "higgsfield.ai" in msg
