import json
from pathlib import Path


def get_case_status(case_dir: str) -> dict:
    """
    Return step completion state for a case directory.

    Checks which files exist and what config.json contains to report
    which workflow steps have been completed.

    Parameters
    ----------
    case_dir : str
        Working directory (inputdir) for this case.
    """
    case_dir_path = Path(case_dir)

    status: dict = {
        "case_dir": str(case_dir_path),
        "exists": case_dir_path.exists(),
        "steps": {
            "grid_created": False,
            "case_created": False,
            "forcings_configured": False,
            "forcings_processed": False,
        },
    }

    if not case_dir_path.exists():
        return status

    # Step 1: Grid created?
    grid_params_path = case_dir_path / "mcp_grid_params.json"
    if grid_params_path.exists():
        status["steps"]["grid_created"] = True
        params = json.loads(grid_params_path.read_text())
        # Plain lat/lon grids (create_grid) and polar-projected grids
        # (create_polar_grid) save different key sets -- only report keys
        # actually present rather than assuming lat/lon ones exist.
        grid_keys = [
            "grid_name", "nx", "ny", "nk",
            "lon_min", "lon_max", "lat_min", "lat_max", "resolution",
            "projection", "x_min", "x_max", "y_min", "y_max", "resolution_m",
        ]
        status["grid"] = {k: params[k] for k in grid_keys if k in params}

    # Step 2: Case created?
    case_params_path = case_dir_path / "mcp_case_params.json"
    ocnice_dir = case_dir_path / "ocnice"
    if case_params_path.exists() and ocnice_dir.exists():
        status["steps"]["case_created"] = True
        params = json.loads(case_params_path.read_text())
        status["case"] = {k: params[k] for k in ["caseroot", "compset", "machine"]}

    # Step 3: Forcings configured?
    config_path = case_dir_path / "extract_forcings" / "config.json"
    if config_path.exists():
        status["steps"]["forcings_configured"] = True
        with open(config_path) as f:
            config = json.load(f)
        active = [k for k in config if k not in ("basic",)]
        status["forcing"] = {
            "product": config.get("basic", {}).get("forcing", {}).get("product_name", ""),
            "date_range": [
                config.get("basic", {}).get("dates", {}).get("start", ""),
                config.get("basic", {}).get("dates", {}).get("end", ""),
            ],
            "boundaries": list(
                config.get("basic", {}).get("general", {}).get("boundary_number_conversion", {}).keys()
            ),
            "active_configurators": active,
        }

    # Step 4: Forcings processed? (output NetCDF files in ocnice/)
    if ocnice_dir.exists():
        nc_files = list(ocnice_dir.glob("*.nc"))
        ic_files = [f for f in nc_files if "init_" in f.name]
        obc_files = [f for f in nc_files if "forcing_obc_" in f.name]
        if ic_files or obc_files:
            status["steps"]["forcings_processed"] = True
            status["output_files"] = sorted(f.name for f in ic_files + obc_files)

    # Background process_forcings job status (if process_forcings was run with background=True)
    bg_status_file = case_dir_path / "extract_forcings" / "process_forcings_status.json"
    if bg_status_file.exists():
        bg = json.loads(bg_status_file.read_text())
        status["forcings_processing"] = bg
        if bg.get("status") == "done" and not status["steps"]["forcings_processed"]:
            status["steps"]["forcings_processed"] = True

    return status


def preview_config(
    case_dir: str,
    date_range: list[str],
    boundaries: list[str] = ["south", "north", "west", "east"],
    product_name: str = "GLORYS",
    function_name: str = "get_glorys_data_script_for_cli",
) -> dict:
    """
    Return the configuration JSON that configure_forcings would write, without executing anything.

    Parameters
    ----------
    case_dir : str
        Working directory (inputdir) for this case.
    date_range : list of str
        Two-element list of ISO dates: ["YYYY-MM-DD", "YYYY-MM-DD"].
    boundaries : list of str
        Open boundaries to process.
    product_name : str
        Forcing data product name.
    function_name : str
        Access method on the product.
    """
    import pandas as pd

    case_dir_path = Path(case_dir)
    grid_params_path = case_dir_path / "mcp_grid_params.json"
    case_params_path = case_dir_path / "mcp_case_params.json"

    if not grid_params_path.exists():
        raise FileNotFoundError(f"No grid params found at {grid_params_path}. Run create_grid first.")
    if not case_params_path.exists():
        raise FileNotFoundError(f"No case params found at {case_params_path}. Run create_case first.")

    grid_params = json.loads(grid_params_path.read_text())
    case_params = json.loads(case_params_path.read_text())

    date_range_parsed = pd.to_datetime(date_range)
    date_format = "%Y%m%d"
    step = (date_range_parsed[1] - date_range_parsed[0]).days + 1

    grid_name = grid_params["grid_name"]
    session_placeholder = "<session_id>"

    preview = {
        "basic": {
            "paths": {
                "hgrid_path": f"{case_dir}/ocnice/ocean_hgrid_{grid_name}_{session_placeholder}.nc",
                "vgrid_path": f"{case_dir}/ocnice/ocean_vgrid_{grid_name}_{session_placeholder}.nc",
                "bathymetry_path": f"{case_dir}/ocnice/ocean_topog_{grid_name}_{session_placeholder}.nc",
                "raw_dataset_path": f"{case_dir}/extract_forcings/raw_data",
                "regridded_dataset_path": f"{case_dir}/extract_forcings/regridded_data",
                "output_path": f"{case_dir}/ocnice",
            },
            "dates": {
                "start": date_range_parsed[0].strftime(date_format),
                "end": date_range_parsed[1].strftime(date_format),
                "format": date_format,
            },
            "forcing": {
                "product_name": product_name.upper(),
                "function_name": function_name,
            },
            "general": {
                "boundary_number_conversion": {b: i + 1 for i, b in enumerate(boundaries)},
                "step": step,
            },
        },
        "_meta": {
            "note": "Paths contain placeholder session IDs; actual IDs are assigned at create_case time.",
            "grid": grid_params,
            "case": case_params,
        },
    }

    return preview


def register(mcp):
    mcp.tool()(get_case_status)
    mcp.tool()(preview_config)
