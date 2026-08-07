"""Background worker for process_forcings — spawned by subprocess.Popen or qsub."""
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
    parser.add_argument("--no-ic", action="store_true")
    parser.add_argument("--no-vt", action="store_true")
    parser.add_argument("--no-bgc", action="store_true")
    parser.add_argument("--no-tides", action="store_true")
    parser.add_argument("--no-chl", action="store_true")
    parser.add_argument("--no-runoff", action="store_true")
    parser.add_argument("--no-bgc-river", action="store_true")
    parser.add_argument("--no-cice", action="store_true")
    parser.add_argument("--no-ww3", action="store_true")
    args = parser.parse_args()

    case_dir_path = Path(args.case_dir)
    extract_dir = case_dir_path / "extract_forcings"
    config_path = extract_dir / "config.json"

    _write_status(extract_dir, {"status": "running", "pid": os.getpid()})

    try:
        with open(config_path) as f:
            config = json.load(f)

        from CrocoDash.extract_forcings.driver import run_workflow

        process_ic = not args.no_ic
        process_vt = not args.no_vt
        process_bgc = not args.no_bgc
        process_tides = not args.no_tides
        process_chl = not args.no_chl
        process_runoff = not args.no_runoff
        process_bgc_river = not args.no_bgc_river
        process_cice = not args.no_cice
        process_ww3 = not args.no_ww3

        run_workflow(
            config_path=config_path,
            ic=process_ic,
            bc=process_vt,
            bgcic="bgcic" in config and process_bgc,
            bgcironforcing="bgcironforcing" in config and process_bgc,
            tides="tides" in config and process_tides,
            chl_="chl" in config and process_chl,
            runoff="runoff" in config and process_runoff,
            bgcrivernutrients="bgcrivernutrients" in config and process_bgc_river,
            cice="cice" in config and process_cice,
            ww3="ww3" in config and process_ww3,
        )

        ran = ["conditions"]
        for key, flag in (
            ("bgcironforcing", process_bgc),
            ("bgcic", process_bgc),
            ("tides", process_tides),
            ("chl", process_chl),
            ("runoff", process_runoff),
            ("bgcrivernutrients", process_bgc_river),
            ("cice", process_cice),
            ("ww3", process_ww3),
        ):
            if key in config and flag:
                ran.append(key)

        _write_status(extract_dir, {"status": "done", "processed": ran})

    except Exception:
        _write_status(extract_dir, {"status": "failed", "error": traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    main()
