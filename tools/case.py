from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Literal, Optional


# CrocoDash/visualCaseGen print plain (ANSI-colored) status/error text straight to
# stdout when run outside a Jupyter notebook (see visualCaseGen's DummyOutput). For
# this MCP server, stdout is the JSON-RPC transport to the client — any stray print
# corrupts the protocol stream and the client hangs forever waiting for a response
# that can never be parsed. Redirect stdout to stderr around every call into those
# libraries so nothing but FastMCP's own output ever reaches the real stdout.
_redirect_library_stdout = lambda: contextlib.redirect_stdout(sys.stderr)

REUSE_MARKER_FILENAME = "crocodash_mcp_reuse.json"


def _targets_derecho(machine: str) -> bool:
    """Return True if the case was created for the derecho machine, or if RDA GLORYS is accessible."""
    if machine.lower() == "derecho":
        return True
    # Fallback: RDA path accessible (both Casper and Derecho mount /glade)
    return Path("/glade/collections/rda/data").exists()


def _caseroot_from_inputdir(inputdir: Path) -> str:
    """Look up a case's caseroot from its inputdir.

    crocodash_state.json (case_state.read/write) lives inside *caseroot*, not
    inputdir, so it can't be found starting from inputdir alone. But
    Case.configure_forcings() writes {"caseroot": str(self.caseroot), ...} as
    the first thing into inputdir/extract_forcings/config.json -- that's the
    one file, keyed by inputdir, that always points back at the caseroot.
    """
    config_path = inputdir / "extract_forcings" / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"No config.json found at {config_path} -- can't resolve caseroot "
            "for this inputdir. Run create_case/create_direct_case first."
        )
    with open(config_path) as f:
        config = json.load(f)
    caseroot = config.get("caseroot")
    if caseroot is None:
        raise RuntimeError(f"{config_path} has no 'caseroot' key.")
    return caseroot


def _collapse_pes(caseroot: str, ntasks: int) -> None:
    """Set NTASKS/ROOTPE uniformly across every component and regenerate batch scripts.

    Case.__init__'s ntasks_ocn only sets NTASKS_OCN -- every other component
    (ATM, CPL, ICE, LND, ROF, GLC, WAV, ESP) is left at CIME's default of 128,
    which blows past small queues' PE caps (e.g. Derecho's develop queue tops
    out at jobmax=63) for a case that's meant to run on just ntasks_ocn cores.
    Collapsing every component onto the same PE range is the right default for
    a single-instance regional case, which never benefits from a mismatched
    layout across components. recipe.py/Case.__init__ don't do this themselves.
    """
    case = Path(caseroot)
    subprocess.run(["./xmlchange", f"NTASKS={ntasks}"], cwd=case, check=True)
    subprocess.run(["./xmlchange", "ROOTPE=0"], cwd=case, check=True)
    subprocess.run(["./case.setup", "--clean"], cwd=case, check=True)
    subprocess.run(["./case.setup"], cwd=case, check=True)


def _reconstruct_objects_from_master(master_dir: Path, grid_name: str):
    """Reconstruct Grid/Topo/VGrid from an already-processed master case's own
    on-disk netCDF files, for a case that reuses that master's forcings instead
    of downloading/regridding its own (see reuse_forcings_from below).
    """
    from CrocoDash.grid import Grid
    from CrocoDash.topo import Topo
    from CrocoDash.vgrid import VGrid
    import xarray as xr

    master_ocean = master_dir / "ocean"
    if not master_ocean.exists():
        raise FileNotFoundError(
            f"No ocean/ directory found at {master_ocean} -- reuse_forcings_from "
            "must point at a case_dir that has already been through create_case/"
            "create_direct_case."
        )

    def master_file(prefix: str) -> Path:
        matches = sorted(master_ocean.glob(f"{prefix}*.nc"))
        if not matches:
            raise FileNotFoundError(f"No {prefix}*.nc found in {master_ocean}")
        return matches[0]

    hgrid_path = master_file("ocean_hgrid_")
    topo_path = master_file("ocean_topog_")
    vgrid_path = master_file("ocean_vgrid_")

    grid = Grid.from_supergrid(str(hgrid_path), name=grid_name)

    with xr.open_dataset(topo_path) as topo_ds:
        min_depth = float(topo_ds.attrs.get("min_depth", 0.0))
    topo = Topo.from_topo_file(grid, str(topo_path), min_depth=min_depth)

    vgrid = VGrid.from_file(str(vgrid_path), name=grid_name)

    return grid, topo, vgrid


def _point_case_at_master(caseroot: str, master_dir: Path) -> None:
    """Rewrite user_nl_mom to reference a master case's already-processed
    grid/forcing files directly, instead of this case's own (freshly written
    but never-processed) copies. Called by process_forcings when the case was
    created with reuse_forcings_from, skipping the real download/regrid.
    """
    import re

    master_ocean = master_dir / "ocean"

    def master_file(prefix: str) -> str:
        matches = sorted(master_ocean.glob(f"{prefix}*.nc"))
        if not matches:
            raise FileNotFoundError(f"No {prefix}*.nc found in {master_ocean}")
        return matches[0].name

    replacements = {
        "INPUTDIR": str(master_ocean),
        "GRID_FILE": master_file("ocean_hgrid_"),
        "TOPO_FILE": master_file("ocean_topog_"),
        "ALE_COORDINATE_CONFIG": f"FILE:{master_file('ocean_vgrid_')}",
    }

    user_nl_mom = Path(caseroot) / "user_nl_mom"
    lines = user_nl_mom.read_text().splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(\w+)\s*=", line)
        if m and m.group(1) in replacements:
            lines[i] = f"{m.group(1)} = {replacements[m.group(1)]}"
    user_nl_mom.write_text("\n".join(lines) + "\n")


def create_case(config: dict, override: bool = False) -> dict:
    """
    Create a complete CrocoDash case from a config dict, in one atomic call.

    Equivalent to `crocodash create --config x.yaml`, but takes the config inline
    instead of requiring a YAML file on disk. Builds Grid, Topo, and VGrid from the
    `grid`/`topo`/`vgrid` sections, creates the CESM case, and calls
    configure_forcings() from the `forcings` section -- all in this one call.
    process_forcings must still be run separately afterward.

    Only supports what CrocoDash.recipe.build_grid/build_topo/build_vgrid support:
    grid via `supergrid_path` or plain lon/lat params (no polar/projection grids),
    topo via flat/dataset/from_file (no spoon). For polar grids, spoon bathymetry,
    or reusing another case's already-processed forcings, use create_direct_case
    instead.

    Parameters
    ----------
    config : dict
        Same schema as the YAML file `crocodash create --config` takes. Required
        top-level sections: grid, topo, vgrid, case (cesmroot, caseroot, inputdir,
        compset, machine), forcings (date_range). See
        docs/source/for_users/cli.md in the CrocoDash repo for the full schema.
    override : bool
        If True, delete and recreate existing caseroot and inputdir.

    Returns
    -------
    dict with keys: status, caseroot, inputdir, machine, compset.
    """
    from CrocoDash.recipe import validate_config_structure, create_case_from_yaml

    validate_config_structure(config)

    if not config["forcings"].get("function_name"):
        # Mirror configure_forcings' old auto-selection behavior: prefer the NCAR
        # local archive on Derecho, fall back to the CLI-script variant elsewhere.
        config = {**config, "forcings": dict(config["forcings"])}
        config["forcings"]["function_name"] = (
            "get_glorys_data_from_rda"
            if _targets_derecho(config["case"]["machine"])
            else "get_glorys_data_script_for_cli"
        )

    with _redirect_library_stdout():
        # configure_only=True: this tool only configures forcings, it doesn't
        # process them -- that's the separate process_forcings tool, which allows
        # background=True on slow machines.
        case = create_case_from_yaml(config, override=override, configure_only=True)

    ntasks_ocn = config["case"].get("ntasks_ocn")
    if ntasks_ocn is not None:
        _collapse_pes(str(case.caseroot), ntasks_ocn)

    return {
        "status": "ok",
        "caseroot": str(case.caseroot),
        "inputdir": str(case.inputdir),
        "machine": config["case"]["machine"],
        "compset": config["case"]["compset"],
    }


def create_case_from_yaml_file(yaml_path: str, override: bool = False) -> dict:
    """
    Create a complete CrocoDash case from a YAML recipe file on disk.

    Thin wrapper around create_case for a YAML file instead of an inline dict --
    e.g. a file produced by dump_case, or a bundle's crocodash_case.yaml. Exactly
    equivalent to running `crocodash create --config <yaml_path>` on the CLI.

    Parameters
    ----------
    yaml_path : str
        Path to the YAML case config file.
    override : bool
        If True, delete and recreate existing caseroot and inputdir.
    """
    from CrocoDash.recipe import load_config

    config = load_config(yaml_path)
    return create_case(config, override=override)


def dump_case(caseroot: str) -> dict:
    """
    Reconstruct a YAML-equivalent config dict from an existing case's state files.

    Equivalent to `crocodash dump --caseroot <caseroot>`. Reads crocodash_state.json
    (which lives inside caseroot, not inputdir -- unlike every other tool in this
    module, this one is keyed by caseroot) and, if present,
    extract_forcings/config.json. The returned dict can be edited and passed
    straight back into create_case (e.g. to fork or duplicate a case by hand,
    changing paths/machine/dates first).

    Parameters
    ----------
    caseroot : str
        Path to the existing CESM case root.
    """
    from CrocoDash.recipe import case_to_yaml

    return case_to_yaml(caseroot)


def create_direct_case(
    case_dir: str,
    cesmroot: str,
    caseroot: str,
    compset: str,
    machine: str,
    date_range: list[str],
    project: Optional[str] = None,
    atm_grid_name: str = "TL319",
    rof_grid_name: Optional[str] = None,
    ninst: int = 1,
    ntasks_ocn: Optional[int] = None,
    job_queue: Optional[str] = None,
    job_wallclock_time: Optional[str] = None,
    override: bool = False,
    grid_type: Literal["latlon", "polar"] = "latlon",
    lon_min: Optional[float] = None,
    lon_max: Optional[float] = None,
    lat_min: Optional[float] = None,
    lat_max: Optional[float] = None,
    resolution: Optional[float] = None,
    projection: Optional[Literal["EPSG:3995", "EPSG:3031"]] = None,
    x_min: Optional[float] = None,
    x_max: Optional[float] = None,
    y_min: Optional[float] = None,
    y_max: Optional[float] = None,
    resolution_m: Optional[float] = None,
    grid_name: str = "grid",
    min_depth: float = 0.0,
    topo_type: Literal["spoon", "flat", "from_file"] = "flat",
    topo_max_depth: Optional[float] = None,
    topo_dedge: Optional[float] = None,
    topo_flat_depth: Optional[float] = None,
    topo_file_path: Optional[str] = None,
    vgrid_type: Literal["uniform", "hyperbolic"] = "uniform",
    nk: int = 10,
    depth: Optional[float] = None,
    ratio: Optional[float] = None,
    boundaries: list[str] = ["south", "north", "west", "east"],
    product_name: str = "GLORYS",
    function_name: Optional[str] = None,
    tpxo_elevation_filepath: Optional[str] = None,
    tpxo_velocity_filepath: Optional[str] = None,
    tidal_constituents: Optional[list[str]] = None,
    chl_processed_filepath: Optional[str] = None,
    marbl_ic_filepath: Optional[str] = None,
    global_river_nutrients_filepath: Optional[str] = None,
    cice_product_name: Optional[str] = None,
    cice_function_name: Optional[str] = None,
    cice_function_args: Optional[dict] = None,
    n_halo_cells: Optional[int] = None,
    ww3_obc_product_name: Optional[str] = None,
    ww3_obc_function_name: Optional[str] = None,
    get_step_days: Optional[int] = None,
    regrid_step_days: Optional[int] = None,
    function_overrides: Optional[dict] = None,
    reuse_forcings_from: Optional[str] = None,
) -> dict:
    """
    Create a CrocoDash case by direct Grid/Topo/VGrid/Case construction, in one
    atomic call. This is the escape hatch for cases create_case (the YAML/recipe
    path) can't express:

      - polar grids (grid_type="polar"): CrocoDash.recipe doesn't support
        projection-based grids. Uses Grid.from_projection() with a polar
        stereographic CRS -- 'EPSG:3995' (Arctic) or 'EPSG:3031' (Antarctic).
        Domain extents are in projected metres, not degrees.
      - spoon bathymetry (topo_type="spoon"): CrocoDash.recipe's YAML schema only
        supports flat/dataset/from_file; spoon is still on the Topo class itself.
      - reuse_forcings_from: reconstruct Grid/Topo/VGrid from another case_dir
        that has already been through create_case/create_direct_case (ideally
        also process_forcings), instead of building a fresh grid here. Follow up
        with process_forcings as normal -- it will detect reuse_forcings_from and
        skip the real download/regrid, instead repointing user_nl_mom at the
        master's files. This is the standard pattern for an NTASKS scaling sweep,
        where every point after the first should reuse one point's
        already-processed forcings rather than re-downloading GLORYS.

    Builds Grid, Topo, VGrid, constructs Case(...), and calls
    case.configure_forcings(...) all within this one call -- no state is kept in
    the server process between calls. process_forcings must still be run
    separately afterward.

    Parameters
    ----------
    case_dir : str
        Working directory for this case (the CrocoDash inputdir).
    cesmroot, caseroot, compset, machine, project, atm_grid_name, rof_grid_name,
    ninst, ntasks_ocn, job_queue, job_wallclock_time, override :
        Same as the `case` section of create_case's config schema.
    date_range, boundaries, product_name, function_name, tpxo_elevation_filepath,
    tpxo_velocity_filepath, tidal_constituents, chl_processed_filepath,
    marbl_ic_filepath, global_river_nutrients_filepath, cice_product_name,
    cice_function_name, cice_function_args, n_halo_cells, ww3_obc_product_name,
    ww3_obc_function_name, get_step_days, regrid_step_days, function_overrides :
        Same as the `forcings` section of create_case's config schema. The
        cice_*/n_halo_cells and ww3_obc_*/get_step_days/regrid_step_days
        parameters are only used when the case's compset requires CICE/WW3
        respectively -- CICEConfigurator/WW3Configurator activate automatically
        with sensible defaults otherwise, so these are safe to leave unset for
        non-CICE/WW3 compsets. function_overrides lets a raw-data-access
        function's own non-required default kwargs be overridden (e.g.
        {"member": 3} to pick an ensemble member).
    grid_type : "latlon" | "polar"
        "latlon" uses lon_min/lon_max/lat_min/lat_max/resolution. "polar" uses
        projection/x_min/x_max/y_min/y_max/resolution_m. Ignored (grid is instead
        read from reuse_forcings_from's own files) when reuse_forcings_from is set.
    grid_name : str
        Short identifier for this grid (used in output file names).
    min_depth : float
        Minimum ocean depth in metres; shallower cells are masked.
    topo_type : "spoon" | "flat" | "from_file"
        Bathymetry initialisation method.
    topo_max_depth, topo_dedge : float, optional
        For topo_type="spoon": max depth and basin-edge depth (metres).
    topo_flat_depth : float, optional
        For topo_type="flat": uniform depth (metres).
    topo_file_path : str, optional
        For topo_type="from_file": path to an existing MOM6 topog NetCDF file.
    vgrid_type : "uniform" | "hyperbolic"
        Vertical grid spacing type.
    nk : int
        Number of vertical levels.
    depth : float, optional
        Total ocean depth in metres for the vertical grid. Defaults to the topo's
        own max depth if omitted.
    ratio : float, optional
        For vgrid_type="hyperbolic": target ratio of top-to-bottom layer thickness.
    reuse_forcings_from : str, optional
        Path to another case_dir whose Grid/Topo/VGrid and processed forcing files
        should be reused instead of building/downloading fresh ones.

    Returns
    -------
    dict with keys: status, caseroot, inputdir, machine, compset.
    """
    from CrocoDash.case import Case
    from CrocoDash.grid import Grid
    from CrocoDash.topo import Topo
    from CrocoDash.vgrid import VGrid

    case_dir_path = Path(case_dir)
    case_dir_path.mkdir(parents=True, exist_ok=True)

    with _redirect_library_stdout():
        if reuse_forcings_from is not None:
            master_dir = Path(reuse_forcings_from)
            grid, topo, vgrid = _reconstruct_objects_from_master(
                master_dir, grid_name
            )
        elif grid_type == "polar":
            if None in (projection, x_min, x_max, y_min, y_max, resolution_m):
                raise ValueError(
                    "grid_type='polar' requires projection, x_min, x_max, y_min, "
                    "y_max, and resolution_m."
                )
            grid = Grid.from_projection(
                crs=projection,
                x_min=x_min,
                x_max=x_max,
                y_min=y_min,
                y_max=y_max,
                resolution_m=resolution_m,
                name=grid_name,
            )
            topo = Topo(grid=grid, min_depth=min_depth)
            if topo_type == "spoon":
                if topo_max_depth is None or topo_dedge is None:
                    raise ValueError(
                        "topo_max_depth and topo_dedge are required for topo_type='spoon'"
                    )
                topo.set_spoon(topo_max_depth, topo_dedge)
            elif topo_type == "flat":
                if topo_flat_depth is None:
                    raise ValueError("topo_flat_depth is required for topo_type='flat'")
                topo.set_flat(topo_flat_depth)
            elif topo_type == "from_file":
                if topo_file_path is None:
                    raise ValueError("topo_file_path is required for topo_type='from_file'")
                topo.set_depth_via_topog_file(topo_file_path)
            else:
                raise ValueError(f"Unknown topo_type: {topo_type!r}")

            vgrid_depth = depth if depth is not None else topo.max_depth
            if vgrid_type == "uniform":
                vgrid = VGrid.uniform(nk=nk, depth=vgrid_depth, name=grid_name)
            elif vgrid_type == "hyperbolic":
                if ratio is None:
                    raise ValueError("ratio is required for vgrid_type='hyperbolic'")
                vgrid = VGrid.hyperbolic(
                    nk=nk, depth=vgrid_depth, ratio=ratio, name=grid_name
                )
            else:
                raise ValueError(f"Unknown vgrid_type: {vgrid_type!r}")
        else:
            if None in (lon_min, lon_max, lat_min, lat_max, resolution):
                raise ValueError(
                    "grid_type='latlon' requires lon_min, lon_max, lat_min, "
                    "lat_max, and resolution."
                )
            grid = Grid(
                lenx=lon_max - lon_min,
                leny=lat_max - lat_min,
                resolution=resolution,
                xstart=lon_min,
                ystart=lat_min,
                name=grid_name,
            )
            topo = Topo(grid=grid, min_depth=min_depth)
            if topo_type == "spoon":
                if topo_max_depth is None or topo_dedge is None:
                    raise ValueError(
                        "topo_max_depth and topo_dedge are required for topo_type='spoon'"
                    )
                topo.set_spoon(topo_max_depth, topo_dedge)
            elif topo_type == "flat":
                if topo_flat_depth is None:
                    raise ValueError("topo_flat_depth is required for topo_type='flat'")
                topo.set_flat(topo_flat_depth)
            elif topo_type == "from_file":
                if topo_file_path is None:
                    raise ValueError("topo_file_path is required for topo_type='from_file'")
                topo.set_depth_via_topog_file(topo_file_path)
            else:
                raise ValueError(f"Unknown topo_type: {topo_type!r}")

            vgrid_depth = depth if depth is not None else topo.max_depth
            if vgrid_type == "uniform":
                vgrid = VGrid.uniform(nk=nk, depth=vgrid_depth, name=grid_name)
            elif vgrid_type == "hyperbolic":
                if ratio is None:
                    raise ValueError("ratio is required for vgrid_type='hyperbolic'")
                vgrid = VGrid.hyperbolic(
                    nk=nk, depth=vgrid_depth, ratio=ratio, name=grid_name
                )
            else:
                raise ValueError(f"Unknown vgrid_type: {vgrid_type!r}")

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

        if function_name is None:
            function_name = (
                "get_glorys_data_from_rda"
                if _targets_derecho(machine)
                else "get_glorys_data_script_for_cli"
            )

        forcing_kwargs: dict = {}
        if tpxo_elevation_filepath is not None:
            forcing_kwargs["tpxo_elevation_filepath"] = tpxo_elevation_filepath
        if tpxo_velocity_filepath is not None:
            forcing_kwargs["tpxo_velocity_filepath"] = tpxo_velocity_filepath
        if tidal_constituents is not None:
            forcing_kwargs["tidal_constituents"] = tidal_constituents
        if chl_processed_filepath is not None:
            forcing_kwargs["chl_processed_filepath"] = chl_processed_filepath
        if marbl_ic_filepath is not None:
            forcing_kwargs["marbl_ic_filepath"] = marbl_ic_filepath
        if global_river_nutrients_filepath is not None:
            forcing_kwargs["global_river_nutrients_filepath"] = global_river_nutrients_filepath
        if cice_product_name is not None:
            forcing_kwargs["cice_product_name"] = cice_product_name
        if cice_function_name is not None:
            forcing_kwargs["cice_function_name"] = cice_function_name
        if cice_function_args is not None:
            forcing_kwargs["cice_function_args"] = cice_function_args
        if n_halo_cells is not None:
            forcing_kwargs["n_halo_cells"] = n_halo_cells
        if ww3_obc_product_name is not None:
            forcing_kwargs["ww3_obc_product_name"] = ww3_obc_product_name
        if ww3_obc_function_name is not None:
            forcing_kwargs["ww3_obc_function_name"] = ww3_obc_function_name
        if get_step_days is not None:
            forcing_kwargs["get_step_days"] = get_step_days
        if regrid_step_days is not None:
            forcing_kwargs["regrid_step_days"] = regrid_step_days
        if function_overrides is not None:
            forcing_kwargs["function_overrides"] = function_overrides

        case.configure_forcings(
            date_range=date_range,
            boundaries=boundaries,
            product_name=product_name,
            function_name=function_name,
            **forcing_kwargs,
        )

    if ntasks_ocn is not None:
        _collapse_pes(caseroot, ntasks_ocn)

    if reuse_forcings_from is not None:
        (case_dir_path / REUSE_MARKER_FILENAME).write_text(
            json.dumps({"reuse_forcings_from": str(reuse_forcings_from)}, indent=2)
        )

    return {
        "status": "ok",
        "caseroot": caseroot,
        "inputdir": str(case_dir_path),
        "machine": machine,
        "compset": compset,
    }


def _submit_process_forcings_pbs(
    case_dir_path: Path,
    cmd: list[str],
    pbs_queue: str,
    pbs_walltime: str,
    pbs_project: Optional[str],
) -> dict:
    """Submit the background worker as a real PBS job instead of a bare subprocess.

    A bare subprocess.Popen (the background=True path) runs on this MCP
    server's own host, typically a login node with a low memory ulimit --
    large domains can get silently OOM-killed there. Submitting as a PBS job
    runs the same worker on an actual compute node with real memory.
    """
    from CrocoDash import case_state

    extract_dir = case_dir_path / "extract_forcings"
    log_file = extract_dir / "process_forcings_pbs.log"
    status_file = extract_dir / "process_forcings_status.json"
    script_file = extract_dir / "process_forcings_pbs.sh"

    if pbs_project is None:
        try:
            caseroot = _caseroot_from_inputdir(case_dir_path)
            pbs_project = case_state.read(caseroot).get("project")
        except FileNotFoundError:
            pbs_project = None
    if pbs_project is None:
        raise ValueError(
            "pbs_project not given and no project found in crocodash_state.json "
            "-- pass pbs_project explicitly."
        )

    # sys.executable is .../<conda-env>/bin/python; ESMF's own mkfile lives at
    # <conda-env>/lib/esmf.mk. A PBS job starts with a fresh environment, so
    # this must be exported explicitly -- unlike the background=True subprocess
    # path, which inherits the MCP server's already-active conda env.
    esmfmkfile = Path(sys.executable).parent.parent / "lib" / "esmf.mk"

    script = f"""#!/bin/bash
#PBS -N process_forcings
#PBS -A {pbs_project}
#PBS -q {pbs_queue}
#PBS -l select=1:ncpus=4:mem=64GB
#PBS -l walltime={pbs_walltime}
#PBS -j oe
#PBS -o {log_file}

set -euo pipefail
export ESMFMKFILE={esmfmkfile}
{" ".join(str(c) for c in cmd)}
"""
    extract_dir.mkdir(parents=True, exist_ok=True)
    script_file.write_text(script)
    script_file.chmod(0o755)

    status_file.write_text(json.dumps({"status": "starting"}))
    result = subprocess.run(
        ["qsub", str(script_file)], capture_output=True, text=True
    )
    if result.returncode != 0:
        status_file.write_text(
            json.dumps({"status": "failed", "error": result.stderr.strip()})
        )
        raise RuntimeError(f"qsub failed: {result.stderr.strip()}")

    jobid = result.stdout.strip()
    return {
        "status": "queued",
        "pbs_jobid": jobid,
        "script_file": str(script_file),
        "log_file": str(log_file),
        "status_file": str(status_file),
        "hint": "Call get_case_status to track progress once the job starts running.",
    }


def process_forcings(
    case_dir: str,
    background: bool | str = False,
    process_initial_condition: bool = True,
    process_velocity_tracers: bool = True,
    process_bgc: bool = True,
    process_tides: bool = True,
    process_chl: bool = True,
    process_runoff: bool = True,
    process_bgc_river_nutrients: bool = True,
    process_cice: bool = True,
    process_ww3: bool = True,
    pbs_queue: str = "main",
    pbs_walltime: str = "03:00:00",
    pbs_project: Optional[str] = None,
) -> dict:
    """
    Process boundary conditions, initial conditions, and other forcings.

    Fully stateless -- reads config.json written by create_case/create_direct_case's
    configure_forcings() call and drives CrocoDash.extract_forcings.driver.run_workflow()
    directly. Does not require any object to be held in memory by the server (unlike
    Case.process_forcings(), which is a thin wrapper around the same call).

    This step can be slow (minutes to tens of minutes) because it downloads
    and regrids ocean data. Use background=True to run it as a detached
    subprocess, or background="pbs" to submit it as a real PBS job on a
    compute node instead (see below); poll get_case_status to check progress.

    If this case was created with create_direct_case(reuse_forcings_from=...), this
    call skips the real download/regrid entirely and just repoints
    user_nl_mom at the master case's already-processed files -- the
    background/PBS options above don't apply in that case since there's no
    real work to run.

    Parameters
    ----------
    case_dir : str
        Working directory (inputdir) for this case.
    background : bool or "pbs"
        If True, spawn processing as a detached background subprocess on
        this MCP server's own host and return immediately. This host is
        typically a login node with a low memory ulimit — large domains
        (e.g. a pole-centered polar grid, whose GLORYS query can pull a
        near-global longitude slice) can get silently OOM-killed there.
        If "pbs", submit the same worker as a PBS job on a compute node
        instead (recommended for anything but small domains) and return the
        PBS job id. Status is written to
        extract_forcings/process_forcings_status.json either way; poll
        get_case_status to track completion (default False).
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
    process_cice : bool
        Whether to run CICE forcing generation if configured (default True).
    process_ww3 : bool
        Whether to run WW3 OBC spectra generation if configured (default True).
    pbs_queue : str
        Queue to submit to when background="pbs" (default "main" — this is
        background data prep, not latency-sensitive, so it doesn't need a
        fast/short dev queue).
    pbs_walltime : str
        Walltime for the PBS job when background="pbs", hh:mm:ss (default
        "03:00:00").
    pbs_project : str, optional
        HPC project/account code for the PBS job when background="pbs".
        Defaults to the project recorded in crocodash_state.json, if any.
    """
    case_dir_path = Path(case_dir)

    reuse_marker_path = case_dir_path / REUSE_MARKER_FILENAME
    if reuse_marker_path.exists():
        reuse_forcings_from = json.loads(reuse_marker_path.read_text())[
            "reuse_forcings_from"
        ]
        caseroot = _caseroot_from_inputdir(case_dir_path)
        _point_case_at_master(caseroot, Path(reuse_forcings_from))
        status_file = case_dir_path / "extract_forcings" / "process_forcings_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(
            json.dumps(
                {
                    "status": "done",
                    "processed": ["conditions"],
                    "note": f"user_nl_mom repointed at {reuse_forcings_from}/ocean, not reprocessed here",
                },
                indent=2,
            )
        )
        return {
            "status": "done",
            "reused_from": reuse_forcings_from,
            "note": "Forcings reused from master case; user_nl_mom repointed, nothing downloaded/regridded.",
        }

    config_path = case_dir_path / "extract_forcings" / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found at {config_path}. Run create_case or "
            "create_direct_case first."
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
        if not process_cice:
            cmd.append("--no-cice")
        if not process_ww3:
            cmd.append("--no-ww3")

        if background == "pbs":
            return _submit_process_forcings_pbs(
                case_dir_path, cmd, pbs_queue, pbs_walltime, pbs_project
            )

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

    from CrocoDash.extract_forcings.driver import run_workflow

    with _redirect_library_stdout():
        run_workflow(
            config_path=config_path,
            ic=process_initial_condition,
            bc=process_velocity_tracers,
            bgcic="bgcic" in config and process_bgc,
            bgcironforcing="bgcironforcing" in config and process_bgc,
            tides="tides" in config and process_tides,
            chl_="chl" in config and process_chl,
            runoff="runoff" in config and process_runoff,
            bgcrivernutrients="bgcrivernutrients" in config and process_bgc_river_nutrients,
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
        ("bgcrivernutrients", process_bgc_river_nutrients),
        ("cice", process_cice),
        ("ww3", process_ww3),
    ):
        if key in config and flag:
            ran.append(key)

    return {"status": "ok", "processed": ran}


def register(mcp):
    mcp.tool()(create_case)
    mcp.tool()(create_case_from_yaml_file)
    mcp.tool()(dump_case)
    mcp.tool()(create_direct_case)
    mcp.tool()(process_forcings)
