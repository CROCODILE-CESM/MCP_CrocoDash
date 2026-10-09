"""Background worker for process_forcings — spawned by subprocess.Popen or qsub.

The parent (tools/case.py::process_forcings) resolves which process components
to run and passes them here as a JSON {flag_name: bool} dict, so this worker
never needs to know which forcing types exist.
"""
import argparse
import json
import os
import sys
import traceback
from pathlib import Path

STATUS_FILE = "process_forcings_status.json"


def _write_status(extract_dir: Path, payload: dict) -> None:
    (extract_dir / STATUS_FILE).write_text(json.dumps(payload, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_dir")
    parser.add_argument("--flags", required=True, help="JSON {flag_name: bool}")
    parser.add_argument("--preview", action="store_true")
    args = parser.parse_args()

    case_dir_path = Path(args.case_dir)
    extract_dir = case_dir_path / "extract_forcings"
    config_path = extract_dir / "config.json"
    flags = json.loads(args.flags)

    _write_status(extract_dir, {"status": "running", "pid": os.getpid()})

    try:
        from CrocoDash.forcing.driver import run_workflow

        timings = run_workflow(config_path=config_path, preview=args.preview, **flags)

        _write_status(
            extract_dir,
            {
                "status": "done",
                "processed": [name for name, enabled in flags.items() if enabled],
                "timings_s": timings or {},
            },
        )

    except Exception:
        _write_status(extract_dir, {"status": "failed", "error": traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    main()
