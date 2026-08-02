"""Measure how long each phase of a reference-based generation actually takes."""
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from higgsfield_api.clerk import ClerkAuth
from higgsfield_api.higgsfield import HiggsfieldClient

REF = sys.argv[1] if len(sys.argv) > 1 else "/tmp/higgsfield_smoke_a48e3400.png"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3

hf = HiggsfieldClient(ClerkAuth())
size_mb = pathlib.Path(REF).stat().st_size / 1e6
print(f"reference: {pathlib.Path(REF).name} ({size_mb:.1f} MB)\n")

t = time.monotonic(); rec = hf.upload(REF); t_up = time.monotonic() - t
print(f"upload            : {t_up:5.1f}s  (once; reuse the id afterwards)")

for i in range(N):
    t = time.monotonic()
    resp = hf.create(f"same subject, scene variation {i+1}, natural light",
                     "nano_banana", aspect_ratio="1:1", references=[rec["id"]])
    jid = hf.job_ids(resp)[0]
    t_create = time.monotonic() - t

    t = time.monotonic()
    job = hf.wait(jid, timeout=600, interval=1.0)
    t_gen = time.monotonic() - t

    t = time.monotonic()
    hf.download(hf.result_urls(job)["raw"], f"/tmp/hf_timing_{i}.png")
    t_dl = time.monotonic() - t

    print(f"run {i+1}: create {t_create:4.1f}s | generate {t_gen:5.1f}s | "
          f"download {t_dl:4.1f}s | TOTAL {t_create+t_gen+t_dl:5.1f}s")
