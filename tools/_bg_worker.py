"""Background worker for process_forcings — spawned by subprocess.Popen."""
import argparse
import importlib.util
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
    args = parser.parse_args()

    case_dir_path = Path(args.case_dir)
    extract_dir = case_dir_path / "extract_forcings"
    driver_path = extract_dir / "driver.py"
    config_path = extract_dir / "config.json"

    _write_status(extract_dir, {"status": "running", "pid": os.getpid()})

    try:
        with open(config_path) as f:
            config = json.load(f)

        module_name = f"crocodash_driver_{case_dir_path.name}"
        spec = importlib.util.spec_from_file_location(module_name, driver_path)
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)

        ran: list[str] = []

        process_ic = not args.no_ic
        process_vt = not args.no_vt

        if process_ic or process_vt:
            driver.run_workflow(
                ic=process_ic,
                bc=process_vt,
            )
            ran.append("conditions")

        if "bgcironforcing" in config and not args.no_bgc:
            driver.process_bgcironforcing()
            ran.append("bgc_iron_forcing")

        if "bgcic" in config and not args.no_bgc:
            driver.process_bgcic()
            ran.append("bgc_ic")

        if "tides" in config and not args.no_tides:
            driver.process_tides()
            ran.append("tides")

        if "chl" in config and not args.no_chl:
            driver.process_chl()
            ran.append("chl")

        if "runoff" in config and not args.no_runoff:
            driver.process_runoff()
            ran.append("runoff")

        if "bgcrivernutrients" in config and not args.no_bgc_river:
            driver.process_bgcrivernutrients()
            ran.append("bgc_river_nutrients")

        _write_status(extract_dir, {"status": "done", "processed": ran})

    except Exception:
        _write_status(extract_dir, {"status": "failed", "error": traceback.format_exc()})
        sys.exit(1)


if __name__ == "__main__":
    main()
