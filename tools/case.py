from __future__ import annotations

import contextlib
import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional


# CrocoDash/visualCaseGen print plain (ANSI-colored) status/error text straight to
# stdout when run outside a Jupyter notebook (see visualCaseGen's DummyOutput). For
# this MCP server, stdout is the JSON-RPC transport to the client — any stray print
# corrupts the protocol stream and the client hangs forever waiting for a response
# that can never be parsed. Redirect stdout to stderr around every call into those
# libraries so nothing but FastMCP's own output ever reaches the real stdout.
_redirect_library_stdout = lambda: contextlib.redirect_stdout(sys.stderr)


def _case_targets_derecho(case_dir: Path) -> bool:
    """Return True if the case was created for the derecho machine, or if RDA GLORYS is accessible."""
    params_path = case_dir / "mcp_case_params.json"
    if params_path.exists():
        params = json.loads(params_path.read_text())
        if params.get("machine", "").lower() == "derecho":
            return True
    # Fallback: RDA path accessible (both Casper and Derecho mount /glade)
    return Path("/glade/collections/rda/data").exists()


# In-memory cache: case_dir (str) → Case object.
# This cache is valid for the lifetime of the server process.
# If the server restarts between create_case and configure_forcings,
# re-run create_case with override=True to rebuild.
_case_registry: dict[str, Case] = {}


def _load_grid_params(case_dir: Path) -> dict:
    params_path = case_dir / "mcp_grid_params.json"
    if not params_path.exists():
        raise FileNotFoundError(
            f"Grid params not found at {params_path}. Run create_grid first."
        )
    return json.loads(params_path.read_text())


def _reconstruct_objects(params: dict, case_dir: Path) -> tuple[Grid, Topo, VGrid]:
    from CrocoDash.grid import Grid
    from CrocoDash.topo import Topo
    from CrocoDash.vgrid import VGrid
    from mom6_forge.git_utils import get_domain_dir

    grid_type = params.get("grid_type", "latlon")

    if grid_type == "polar":
        grid = Grid.from_projection(
            crs=params["projection"],
            x_min=params["x_min"],
            x_max=params["x_max"],
            y_min=params["y_min"],
            y_max=params["y_max"],
            resolution_m=params["resolution_m"],
            name=params["grid_name"],
        )
    else:
        grid = Grid(
            lenx=params["lenx"],
            leny=params["leny"],
            resolution=params["resolution"],
            xstart=params["lon_min"],
            ystart=params["lat_min"],
            name=params["grid_name"],
        )

    topo_library_dir = params.get("topo_library_dir", str(case_dir / "TopoLibrary"))
    domain_dir = get_domain_dir(grid, base_dir=topo_library_dir)
    topo = Topo.from_version_control(domain_dir)

    vgrid_type = params["vgrid_type"]
    if vgrid_type == "uniform":
        vgrid = VGrid.uniform(
            nk=params["nk"], depth=params["depth"], name=params["grid_name"]
        )
    else:
        vgrid = VGrid.hyperbolic(
            nk=params["nk"],
            depth=params["depth"],
            ratio=params["ratio"],
            name=params["grid_name"],
        )

    return grid, topo, vgrid


def create_case(
    case_dir: str,
    cesmroot: str,
    caseroot: str,
    compset: str,
    machine: str,
    project: Optional[str] = None,
    atm_grid_name: str = "TL319",
    rof_grid_name: Optional[str] = None,
    ninst: int = 1,
    ntasks_ocn: Optional[int] = None,
    job_queue: Optional[str] = None,
    job_wallclock_time: Optional[str] = None,
    override: bool = False,
) -> dict:
    """
    Create a CESM case for a regional MOM6 experiment.

    Reads the grid configuration from {case_dir}/mcp_grid_params.json (written by
    create_grid) and calls Case.__init__(), which writes grid input files to {case_dir}
    and creates the CESM case directory at caseroot.

    WARNING: never call this with a caseroot/case_dir pair that was already used by a
    prior create_case call (including one that failed or errored partway) unless
    override=True. CIME's create_newcase leaves EXEROOT/RUNDIR (on Derecho:
    $SCRATCH/<casename>/{bld,run}) behind even when the attempt failed. If those
    directories already exist, CIME prompts interactively ("(r)eplace, (a)bort, or
    (u)se existing?") instead of erroring. That prompt reads from stdin, which for
    this MCP server is the JSON-RPC transport pipe — not a terminal — so the prompt
    can never be answered and the tool call hangs forever with no error or output.
    Pick a fresh case_dir/caseroot name for each new attempt, or pass override=True
    to reuse one (which deletes and recreates it cleanly first).

    Parameters
    ----------
    case_dir : str
        Working directory for this case (the CrocoDash inputdir).
    cesmroot : str
        Path to the CESM source root directory.
    caseroot : str
        Path where the CESM case directory will be created.
    compset : str
        CESM compset alias (e.g. "G_JRA") or long name.
    machine : str
        CESM machine name (e.g. "derecho").
    project : str, optional
        HPC project/account code (required on most machines).
    atm_grid_name : str
        Atmosphere grid name (default "TL319").
    rof_grid_name : str, optional
        Runoff grid name; required when compset includes a runoff component.
    ninst : int
        Number of ensemble instances (default 1).
    ntasks_ocn : int, optional
        MPI tasks for the ocean component.
    job_queue : str, optional
        Scheduler queue name (e.g. "main", "regular").
    job_wallclock_time : str, optional
        Wall-clock time limit in hh:mm:ss format.
    override : bool
        If True, delete and recreate existing caseroot and input directories.
    """
    from CrocoDash.case import Case

    case_dir_path = Path(case_dir)
    params = _load_grid_params(case_dir_path)

    with _redirect_library_stdout():
        grid, topo, vgrid = _reconstruct_objects(params, case_dir_path)

        case = Case(
            cesmroot=cesmroot,
            caseroot=caseroot,
            inputdir=str(case_dir_path),
            compset=compset,
            ocn_grid=grid,
            ocn_topo=topo,
            ocn_vgrid=vgrid,
            atm_grid_name=atm_grid_name,
            rof_grid_name=rof_grid_name,
            ninst=ninst,
            machine=machine,
            project=project,
            override=override,
            ntasks_ocn=ntasks_ocn,
            job_queue=job_queue,
            job_wallclock_time=job_wallclock_time,
        )

    _case_registry[str(case_dir_path)] = case

    # Re-save grid params — Case.__init__ with override=True wipes the inputdir.
    (case_dir_path / "mcp_grid_params.json").write_text(json.dumps(params, indent=2))

    case_params = {
        "cesmroot": cesmroot,
        "caseroot": caseroot,
        "inputdir": str(case_dir_path),
        "compset": compset,
        "machine": machine,
        "project": project,
        "atm_grid_name": atm_grid_name,
        "rof_grid_name": rof_grid_name,
        "ninst": ninst,
        "ntasks_ocn": ntasks_ocn,
        "job_queue": job_queue,
        "job_wallclock_time": job_wallclock_time,
        "override": override,
    }
    (case_dir_path / "mcp_case_params.json").write_text(json.dumps(case_params, indent=2))

    return {
        "status": "ok",
        "caseroot": caseroot,
        "inputdir": str(case_dir_path),
        "compset": compset,
        "machine": machine,
    }


def configure_forcings(
    case_dir: str,
    date_range: list[str],
    boundaries: list[str] = ["south", "north", "west", "east"],
    product_name: str = "GLORYS",
    function_name: Optional[str] = None,
    tpxo_elevation_filepath: Optional[str] = None,
    tpxo_velocity_filepath: Optional[str] = None,
    tidal_constituents: Optional[list[str]] = None,
    chl_processed_filepath: Optional[str] = None,
    marbl_ic_filepath: Optional[str] = None,
    global_river_nutrients_filepath: Optional[str] = None,
) -> dict:
    """
    Configure forcings for a regional MOM6 case.

    Calls Case.configure_forcings(), which copies the extraction workflow into
    {case_dir}/extract_forcings/ and writes config.json there. Optional forcing
    configurators (tides, BGC, chlorophyll, runoff) are activated automatically
    when their required arguments are supplied.

    Requires create_case to have been called in the current server session.
    If the server restarted since create_case ran, re-run create_case first.

    Parameters
    ----------
    case_dir : str
        Working directory (inputdir) for this case.
    date_range : list of str
        Two-element list of ISO dates: ["YYYY-MM-DD", "YYYY-MM-DD"].
    boundaries : list of str
        Open boundaries to process (default: all four sides).
    product_name : str
        Name of the forcing data product (e.g. "GLORYS").
    function_name : str, optional
        Access method on the product. Defaults to "get_glorys_data_from_rda" on
        Derecho (NCAR local archive) and "get_glorys_data_script_for_cli" elsewhere.
    tpxo_elevation_filepath : str, optional
        Path to TPXO elevation NetCDF — activates tidal forcing.
    tpxo_velocity_filepath : str, optional
        Path to TPXO velocity NetCDF — activates tidal forcing.
    tidal_constituents : list of str, optional
        Tidal constituents to include (e.g. ["M2", "S2"]).
    chl_processed_filepath : str, optional
        Path to processed chlorophyll NetCDF — activates Chl configurator.
    marbl_ic_filepath : str, optional
        Path to MARBL initial conditions NetCDF — activates BGC IC configurator.
    global_river_nutrients_filepath : str, optional
        Path to global river nutrients NetCDF — activates BGC river nutrients.
    """
    case_dir_path = Path(case_dir)

    if function_name is None:
        function_name = (
            "get_glorys_data_from_rda"
            if _case_targets_derecho(case_dir_path)
            else "get_glorys_data_script_for_cli"
        )

    case_dir_key = str(case_dir_path)
    if case_dir_key not in _case_registry:
        raise RuntimeError(
            f"No active Case found for {case_dir!r}. "
            "The MCP server may have restarted. Re-run create_case (with override=True) "
            "to rebuild the in-memory Case object."
        )

    case = _case_registry[case_dir_key]

    kwargs: dict = {}
    if tpxo_elevation_filepath is not None:
        kwargs["tpxo_elevation_filepath"] = tpxo_elevation_filepath
    if tpxo_velocity_filepath is not None:
        kwargs["tpxo_velocity_filepath"] = tpxo_velocity_filepath
    if tidal_constituents is not None:
        kwargs["tidal_constituents"] = tidal_constituents
    if chl_processed_filepath is not None:
        kwargs["chl_processed_filepath"] = chl_processed_filepath
    if marbl_ic_filepath is not None:
        kwargs["marbl_ic_filepath"] = marbl_ic_filepath
    if global_river_nutrients_filepath is not None:
        kwargs["global_river_nutrients_filepath"] = global_river_nutrients_filepath

    with _redirect_library_stdout():
        case.configure_forcings(
            date_range=date_range,
            boundaries=boundaries,
            product_name=product_name,
            function_name=function_name,
            **kwargs,
        )

    return {
        "status": "ok",
        "date_range": date_range,
        "boundaries": boundaries,
        "product_name": product_name,
        "active_configurators": list(case.fcr.get_active_configurators()),
        "config_written_to": str(Path(case_dir) / "extract_forcings" / "config.json"),
    }


def process_forcings(
    case_dir: str,
    background: bool = False,
    process_initial_condition: bool = True,
    process_velocity_tracers: bool = True,
    process_bgc: bool = True,
    process_tides: bool = True,
    process_chl: bool = True,
    process_runoff: bool = True,
    process_bgc_river_nutrients: bool = True,
) -> dict:
    """
    Process boundary conditions, initial conditions, and other forcings.

    Reads config.json written by configure_forcings and imports the driver
    copied into {case_dir}/extract_forcings/. Fully stateless — does not
    require the Case object to be in memory.

    This step can be slow (minutes to tens of minutes) because it downloads
    and regrids ocean data. Use background=True to run it as a detached
    subprocess; poll get_case_status to check progress.

    Parameters
    ----------
    case_dir : str
        Working directory (inputdir) for this case.
    background : bool
        If True, spawn processing as a detached background subprocess and
        return immediately. Status is written to
        extract_forcings/process_forcings_status.json; poll get_case_status
        to track completion (default False).
    process_initial_condition : bool
        Whether to process the initial condition file (default True).
    process_velocity_tracers : bool
        Whether to process velocity/tracer boundary conditions (default True).
    process_bgc : bool
        Whether to run BGC iron forcing and BGC IC steps if configured (default True).
    process_tides : bool
        Whether to run tidal forcing if configured (default True).
    process_chl : bool
        Whether to run chlorophyll processing if configured (default True).
    process_runoff : bool
        Whether to run runoff mapping if configured (default True).
    process_bgc_river_nutrients : bool
        Whether to run BGC river nutrients if configured (default True).
    """
    case_dir_path = Path(case_dir)
    driver_path = case_dir_path / "extract_forcings" / "driver.py"
    config_path = case_dir_path / "extract_forcings" / "config.json"

    if not driver_path.exists():
        raise FileNotFoundError(
            f"Driver not found at {driver_path}. Run configure_forcings first."
        )
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found at {config_path}. Run configure_forcings first."
        )

    if background:
        worker = Path(__file__).parent / "_bg_worker.py"
        extract_dir = case_dir_path / "extract_forcings"
        log_file = extract_dir / "process_forcings.log"
        status_file = extract_dir / "process_forcings_status.json"

        cmd = [sys.executable, str(worker), str(case_dir_path)]
        if not process_initial_condition:
            cmd.append("--no-ic")
        if not process_velocity_tracers:
            cmd.append("--no-vt")
        if not process_bgc:
            cmd.append("--no-bgc")
        if not process_tides:
            cmd.append("--no-tides")
        if not process_chl:
            cmd.append("--no-chl")
        if not process_runoff:
            cmd.append("--no-runoff")
        if not process_bgc_river_nutrients:
            cmd.append("--no-bgc-river")

        status_file.write_text(json.dumps({"status": "starting"}))
        with open(log_file, "w") as log_f:
            subprocess.Popen(cmd, stdout=log_f, stderr=log_f, start_new_session=True)

        return {
            "status": "running",
            "log_file": str(log_file),
            "status_file": str(status_file),
            "hint": "Call get_case_status to track progress.",
        }

    with open(config_path) as f:
        config = json.load(f)

    # Import the driver using its own file path so CONFIG_PATH resolves correctly.
    module_name = f"crocodash_driver_{case_dir_path.name}"
    spec = importlib.util.spec_from_file_location(module_name, driver_path)
    driver = importlib.util.module_from_spec(spec)

    ran: list[str] = []

    with _redirect_library_stdout():
        spec.loader.exec_module(driver)

        if process_initial_condition or process_velocity_tracers:
            driver.run_workflow(
                ic=process_initial_condition,
                bc=process_velocity_tracers,
            )
            ran.append("conditions")

        if "bgcironforcing" in config and process_bgc:
            driver.process_bgcironforcing()
            ran.append("bgc_iron_forcing")

        if "bgcic" in config and process_bgc:
            driver.process_bgcic()
            ran.append("bgc_ic")

        if "tides" in config and process_tides:
            driver.process_tides()
            ran.append("tides")

        if "chl" in config and process_chl:
            driver.process_chl()
            ran.append("chl")

        if "runoff" in config and process_runoff:
            driver.process_runoff()
            ran.append("runoff")

        if "bgcrivernutrients" in config and process_bgc_river_nutrients:
            driver.process_bgcrivernutrients()
            ran.append("bgc_river_nutrients")

    return {"status": "ok", "processed": ran}


def create_case_from_yaml(yaml_path: str, override: bool = False) -> dict:
    """
    Create a complete CrocoDash case from a YAML recipe file.

    The YAML file describes the grid, bathymetry, vertical grid, CESM case
    parameters, and forcings configuration in a single portable document.
    This is the recommended entry point when you have a recipe from a
    previous case (via `crocodash dump` or from a bundle's crocodash_case.yaml).

    Equivalent to running `crocodash create --config <yaml_path>` on the CLI.
    After this call, process_forcings must still be run to download and regrid
    the boundary and initial conditions.

    Parameters
    ----------
    yaml_path : str
        Path to the YAML case config file. Required top-level sections:
        grid, topo, vgrid, case (cesmroot, caseroot, inputdir, compset, machine),
        forcings (date_range).
    override : bool
        If True, delete and recreate existing caseroot and inputdir.

    Returns
    -------
    dict with keys:
        status       — "ok"
        caseroot     — path to the created CESM case
        inputdir     — path to the input data directory
        machine      — machine name used
        compset      — compset used
    """
    from CrocoDash.recipe import load_config, create_case_from_yaml as _create

    config = load_config(yaml_path)
    # configure_only=True: recipe handles configure_forcings but not process_forcings.
    # The caller must invoke the MCP process_forcings tool separately — this keeps
    # the download/regrid step explicit and allows background=True on slow machines.
    with _redirect_library_stdout():
        case = _create(config, override=override, configure_only=True)

    # Cache the case object so configure_forcings can find it
    _case_registry[str(Path(case.inputdir))] = case

    return {
        "status": "ok",
        "caseroot": str(case.caseroot),
        "inputdir": str(case.inputdir),
        "machine": config["case"]["machine"],
        "compset": config["case"]["compset"],
    }


def register(mcp):
    mcp.tool()(create_case)
    mcp.tool()(create_case_from_yaml)
    mcp.tool()(configure_forcings)
    mcp.tool()(process_forcings)
