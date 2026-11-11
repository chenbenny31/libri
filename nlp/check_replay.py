"""Exercise the public CLI against both original v0.11 default-text baselines."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parent


def stable_digest(data):
    raw = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main():
    manifest = json.loads((ROOT / "fixtures/replay.sha256.json").read_text())
    with TemporaryDirectory() as workspace:
        for cohort, expected in manifest.items():
            files = ROOT / "fixtures" / cohort
            out = Path(workspace) / f"{cohort}.json"
            command = [
                sys.executable, "-B", "-E", "-S", "-m", "libri.b_pipeline.run",
                "--input", str(files / "inputs.json"),
                "--observed", str(files / "observed.plain.json"), "--output", str(out),
            ]
            subprocess.run(command, cwd=ROOT, check=True)
            bundle = json.loads(out.read_text())
            for field in ("input_file_sha256", "observed_file_sha256"):
                if bundle.pop(field) != expected[field]:
                    raise SystemExit(f"{cohort}: {field} differs from the frozen fixture")
            del bundle["created_at_utc"], bundle["extract_seconds_total"]
            if len(bundle["records"]) != expected["n"]:
                raise SystemExit(f"{cohort}: wrong number of records")
            if stable_digest(bundle) != expected["stable_bundle_sha256"]:
                raise SystemExit(f"{cohort}: records or stable metadata differ from baseline")
            print(f"{cohort}: {expected['n']} records and stable metadata match")


if __name__ == "__main__":
    main()
