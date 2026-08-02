"""END-TO-END: upload a local image, use it as a reference, download the result.
This is the flow that matters most — run it after any change to upload().

  uv run python tests/upload_ref_e2e.py [image.png]
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from higgsfield_api.clerk import ClerkAuth
from higgsfield_api.higgsfield import HiggsfieldClient


def main() -> int:
    src = sys.argv[1] if len(sys.argv) > 1 else "/tmp/higgsfield_smoke_a48e3400.png"
    hf = HiggsfieldClient(ClerkAuth())

    print(f"1. upload {src}")
    rec = hf.upload(src)
    print(f"   id={rec['id']} status={rec['status']} bytes={rec['size_bytes']}")

    print("2. confirm the server sees it as uploaded")
    it = hf._request("GET", f"/fnf/media/{rec['id']}")
    print(f"   status={it.get('status')} {it.get('width')}x{it.get('height')} "
          f"name={it.get('filename')}")
    assert it.get("status") == "uploaded", f"media not uploaded: {it.get('status')}"

    print("3. generate WITH the reference (free, use_unlim)")
    results = hf.generate("add gentle falling snow, keep the subject identical",
                          "nano_banana", aspect_ratio="1:1", references=[rec["id"]])
    r = results[0]
    out = pathlib.Path("/tmp/higgsfield_upload_ref_result.png")
    hf.download(r["results"]["raw"], out)
    print(f"   job={r['job_id']} status={r['status']}")
    print(f"   saved {out} ({out.stat().st_size} bytes)")
    print("\nUPLOAD + REFERENCES WORK END-TO-END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
