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


def _ocn_dir(inputdir: Path) -> Path:
    """The MOM6 input subdirectory of a CrocoDash inputdir.

    CrocoDash writes MOM6 files to inputdir/ocn (CICE to ice/, WW3 to wav/);
    cases created before that split used inputdir/ocean instead.
    """
    for name in ("ocn", "ocean"):
        if (inputdir / name).exists():
            return inputdir / name
    return inputdir / "ocn"


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

    master_ocean = _ocn_dir(master_dir)
    if not master_ocean.exists():
        raise FileNotFoundError(
            f"No ocn/ directory found at {master_ocean} -- reuse_forcings_from "
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

    master_ocean = _ocn_dir(master_dir)

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


def _build_grid(
    grid_type: str,
    grid_name: str,
    *,
    lon_min=None,
    lon_max=None,
    lat_min=None,
    lat_max=None,
    resolution=None,
    cyclic_x=False,
    projection=None,
    x_min=None,
    x_max=None,
    y_min=None,
    y_max=None,
    resolution_m=None,
    center_lat=None,
    center_lon=None,
    width_m=None,
    height_m=None,
    angle_deg=0.0,
    supergrid_path=None,
):
    from CrocoDash.grid import Grid

    def need(**kwargs):
        missing = [k for k, v in kwargs.items() if v is None]
        if missing:
            raise ValueError(f"grid_type={grid_type!r} requires {', '.join(missing)}.")

    if grid_type == "latlon":
        need(lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max, resolution=resolution)
        return Grid(
            lenx=lon_max - lon_min,
            leny=lat_max - lat_min,
            resolution=resolution,
            xstart=lon_min,
            ystart=lat_min,
            cyclic_x=cyclic_x,
            name=grid_name,
        )
    if grid_type == "polar":
        need(projection=projection, x_min=x_min, x_max=x_max, y_min=y_min, y_max=y_max, resolution_m=resolution_m)
        return Grid.from_projection(
            crs=projection,
            x_min=x_min,
            x_max=x_max,
            y_min=y_min,
            y_max=y_max,
            resolution_m=resolution_m,
            name=grid_name,
        )
    if grid_type == "rotated":
        need(center_lat=center_lat, center_lon=center_lon, width_m=width_m, height_m=height_m, resolution_m=resolution_m)
        return Grid.from_center(
            center_lat=center_lat,
            center_lon=center_lon,
            width_m=width_m,
            height_m=height_m,
            resolution_m=resolution_m,
            angle_deg=angle_deg,
            name=grid_name,
        )
    if grid_type == "subgrid":
        need(supergrid_path=supergrid_path, lon_min=lon_min, lon_max=lon_max, lat_min=lat_min, lat_max=lat_max)
        return Grid.subgrid_from_supergrid(
            supergrid_path, llc=(lat_min, lon_min), urc=(lat_max, lon_max), name=grid_name
        )
    raise ValueError(f"Unknown grid_type: {grid_type!r}")


def _build_topo(
    grid,
    topo_type: str,
    min_depth: float,
    *,
    topo_max_depth=None,
    topo_dedge=None,
    topo_flat_depth=None,
    topo_file_path=None,
    topo_dataset=None,
):
    from CrocoDash.topo import Topo

    if topo_type == "from_file":
        if topo_file_path is None:
            raise ValueError("topo_file_path is required for topo_type='from_file'")
        return Topo.from_topo_file(grid, topo_file_path, min_depth=min_depth)

    topo = Topo(grid=grid, min_depth=min_depth)
    if topo_type in ("spoon", "bowl"):
        if topo_max_depth is None or topo_dedge is None:
            raise ValueError(
                f"topo_max_depth and topo_dedge are required for topo_type={topo_type!r}"
            )
        getattr(topo, f"set_{topo_type}")(topo_max_depth, topo_dedge)
    elif topo_type == "flat":
        if topo_flat_depth is None:
            raise ValueError("topo_flat_depth is required for topo_type='flat'")
        topo.set_flat(topo_flat_depth)
    elif topo_type == "dataset":
        if not topo_dataset or "bathymetry_path" not in topo_dataset:
            raise ValueError(
                "topo_dataset with at least bathymetry_path, longitude_coordinate_name, "
                "latitude_coordinate_name and vertical_coordinate_name is required for "
                "topo_type='dataset'"
            )
        topo.set_from_dataset(**topo_dataset)
    else:
        raise ValueError(f"Unknown topo_type: {topo_type!r}")
    return topo


def _build_vgrid(topo, vgrid_type: str, nk: int, depth, ratio, grid_name: str):
    from CrocoDash.vgrid import VGrid

    vgrid_depth = depth if depth is not None else topo.max_depth
    if vgrid_type == "uniform":
        return VGrid.uniform(nk=nk, depth=vgrid_depth, name=grid_name)
    if vgrid_type == "hyperbolic":
        if ratio is None:
            raise ValueError("ratio is required for vgrid_type='hyperbolic'")
        return VGrid.hyperbolic(nk=nk, depth=vgrid_depth, ratio=ratio, name=grid_name)
    raise ValueError(f"Unknown vgrid_type: {vgrid_type!r}")


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
    grid_type: Literal["latlon", "polar", "rotated", "subgrid"] = "latlon",
    lon_min: Optional[float] = None,
    lon_max: Optional[float] = None,
    lat_min: Optional[float] = None,
    lat_max: Optional[float] = None,
    resolution: Optional[float] = None,
    cyclic_x: bool = False,
    projection: Optional[Literal["EPSG:3995", "EPSG:3031"]] = None,
    x_min: Optional[float] = None,
    x_max: Optional[float] = None,
    y_min: Optional[float] = None,
    y_max: Optional[float] = None,
    resolution_m: Optional[float] = None,
    center_lat: Optional[float] = None,
    center_lon: Optional[float] = None,
    width_m: Optional[float] = None,
    height_m: Optional[float] = None,
    angle_deg: float = 0.0,
    supergrid_path: Optional[str] = None,
    grid_name: str = "grid",
    min_depth: float = 0.0,
    topo_type: Literal["flat", "spoon", "bowl", "dataset", "from_file"] = "flat",
    topo_max_depth: Optional[float] = None,
    topo_dedge: Optional[float] = None,
    topo_flat_depth: Optional[float] = None,
    topo_file_path: Optional[str] = None,
    topo_dataset: Optional[dict] = None,
    vgrid_type: Literal["uniform", "hyperbolic"] = "uniform",
    nk: int = 10,
    depth: Optional[float] = None,
    ratio: Optional[float] = None,
    boundaries: list[str] = ["south", "north", "west", "east"],
    product_name: str = "GLORYS",
    function_name: Optional[str] = None,
    function_overrides: Optional[dict] = None,
    forcing_kwargs: Optional[dict] = None,
    reuse_forcings_from: Optional[str] = None,
) -> dict:
    """
    Create a CrocoDash case by direct Grid/Topo/VGrid/Case construction, in one
    atomic call. This is the escape hatch for cases create_case (the YAML/recipe
    path) can't express:

      - grid_type="polar": Grid.from_projection() with a polar stereographic CRS --
        'EPSG:3995' (Arctic) or 'EPSG:3031' (Antarctic). Extents in projected metres
        (x_min/x_max/y_min/y_max/resolution_m).
      - grid_type="rotated": Grid.from_center() -- a rectangle of width_m x height_m
        centred on (center_lat, center_lon), rotated angle_deg, e.g. a coast-aligned
        domain.
      - grid_type="subgrid": Grid.subgrid_from_supergrid() -- cut lon/lat bounds out
        of an existing (e.g. global tx2_3 / tx1_12) supergrid file at supergrid_path,
        so the regional grid matches the global one point-for-point.
      - grid_type="latlon" with cyclic_x=True: a zonally periodic channel (e.g. a
        Southern Ocean ring); use boundaries without east/west.
      - topo_type="spoon"/"bowl": idealised bathymetry.
      - reuse_forcings_from: reconstruct Grid/Topo/VGrid from another case_dir
        that has already been through create_case/create_direct_case (ideally
        also process_forcings), instead of building a fresh grid here. Follow up
        with process_forcings as normal -- it will detect reuse_forcings_from and
        skip the real download/regrid, instead repointing user_nl_mom at the
        master's files. This is the standard pattern for an NTASKS scaling sweep.

    Builds Grid, Topo, VGrid, constructs Case(...), and calls
    case.configure_forcings(...) all within this one call. process_forcings must
    still be run separately afterward.

    Parameters
    ----------
    case_dir : str
        Working directory for this case (the CrocoDash inputdir).
    cesmroot, caseroot, compset, machine, project, atm_grid_name, rof_grid_name,
    ninst, ntasks_ocn, job_queue, job_wallclock_time, override :
        Same as the `case` section of create_case's config schema. ntasks_ocn is
        optional: CrocoDash picks a PE layout from the number of active ocean
        points when it is omitted. When given, every component is collapsed onto
        that many tasks.
    date_range, boundaries, product_name, function_name, function_overrides :
        Same as the `forcings` section of create_case's config schema.
    forcing_kwargs : dict, optional
        Any other configure_forcings keyword, passed through as-is -- e.g.
        tpxo_elevation_filepath/tpxo_velocity_filepath/tidal_constituents (tides),
        chl_processed_filepath, marbl_ic_filepath, global_river_nutrients_filepath,
        cice_product_name/cice_function_name/cice_function_args/n_halo_cells,
        ww3_obc_product_name/ww3_obc_function_name/get_step_days/regrid_step_days,
        stream_year_padding/narrow_stream_files (StreamYears). See
        list_forcing_configs for every forcing type's user_args.
    grid_type : "latlon" | "polar" | "rotated" | "subgrid"
        See above. Ignored when reuse_forcings_from is set.
    lon_min, lon_max, lat_min, lat_max, resolution, cyclic_x :
        For grid_type="latlon" (degrees). lon/lat bounds are also the cut-out box
        for grid_type="subgrid".
    projection, x_min, x_max, y_min, y_max, resolution_m :
        For grid_type="polar". resolution_m is also used by "rotated".
    center_lat, center_lon, width_m, height_m, angle_deg :
        For grid_type="rotated".
    supergrid_path : str, optional
        For grid_type="subgrid": the parent supergrid NetCDF file.
    grid_name : str
        Short identifier for this grid (used in output file names).
    min_depth : float
        Minimum ocean depth in metres; shallower cells are masked.
    topo_type : "flat" | "spoon" | "bowl" | "dataset" | "from_file"
        Bathymetry initialisation method.
    topo_max_depth, topo_dedge : float, optional
        For topo_type="spoon"/"bowl": max depth and basin-edge depth (metres).
    topo_flat_depth : float, optional
        For topo_type="flat": uniform depth (metres).
    topo_file_path : str, optional
        For topo_type="from_file": path to an existing MOM6 topog NetCDF file.
    topo_dataset : dict, optional
        For topo_type="dataset": kwargs for Topo.set_from_dataset, e.g.
        {"bathymetry_path": ".../GEBCO_2024.nc", "longitude_coordinate_name": "lon",
         "latitude_coordinate_name": "lat", "vertical_coordinate_name": "elevation",
         "fill_channels": False, "write_to_file": False}.
    vgrid_type : "uniform" | "hyperbolic"
        Vertical grid spacing type.
    nk : int
        Number of vertical levels.
    depth : float, optional
        Total depth for the vertical grid; defaults to the topo's max depth.
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

    case_dir_path = Path(case_dir)

    with _redirect_library_stdout():
        if reuse_forcings_from is not None:
            grid, topo, vgrid = _reconstruct_objects_from_master(
                Path(reuse_forcings_from), grid_name
            )
        else:
            grid = _build_grid(
                grid_type,
                grid_name,
                lon_min=lon_min,
                lon_max=lon_max,
                lat_min=lat_min,
                lat_max=lat_max,
                resolution=resolution,
                cyclic_x=cyclic_x,
                projection=projection,
                x_min=x_min,
                x_max=x_max,
                y_min=y_min,
                y_max=y_max,
                resolution_m=resolution_m,
                center_lat=center_lat,
                center_lon=center_lon,
                width_m=width_m,
                height_m=height_m,
                angle_deg=angle_deg,
                supergrid_path=supergrid_path,
            )
            topo = _build_topo(
                grid,
                topo_type,
                min_depth,
                topo_max_depth=topo_max_depth,
                topo_dedge=topo_dedge,
                topo_flat_depth=topo_flat_depth,
                topo_file_path=topo_file_path,
                topo_dataset=topo_dataset,
            )
            vgrid = _build_vgrid(topo, vgrid_type, nk, depth, ratio, grid_name)

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

        case.configure_forcings(
            date_range=date_range,
            boundaries=boundaries,
            product_name=product_name,
            function_name=function_name,
            function_overrides=function_overrides,
            **(forcing_kwargs or {}),
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


def _resolve_process_flags(
    config: dict, components: Optional[list[str]], skip: Optional[list[str]]
) -> tuple[dict, list[str]]:
    """Turn components/skip into run_workflow's {flag_name: bool} kwargs.

    Mirrors `crocodash process --all --skip ...` / `--<flag>`: the valid flag
    names come from the process_components each forcing type in config.json
    declares, so new forcing types show up here without touching this server.
    Returns (flags, ignored) where ignored lists requested names that this
    case's config.json has no forcing type for.
    """
    from CrocoDash.forcing.base import ForcingConfigRegistry

    available = ForcingConfigRegistry.available_process_flags(config)
    skip_set = {name.lower() for name in (skip or [])}
    if components is None:
        requested = set(available)
    else:
        requested = {name.lower() for name in components}
    ignored = sorted(requested - available)
    flags = {
        name: name in requested and name not in skip_set
        for name in sorted(available)
    }
    return flags, ignored


def process_forcings(
    case_dir: str,
    components: Optional[list[str]] = None,
    skip: Optional[list[str]] = None,
    preview: bool = False,
    background: bool | str = False,
    pbs_queue: str = "main",
    pbs_walltime: str = "03:00:00",
    pbs_project: Optional[str] = None,
) -> dict:
    """
    Process boundary conditions, initial conditions, and every other configured forcing.

    Equivalent to `crocodash process --caseroot <caseroot> --all` (or `--<flag>`
    for a subset). Fully stateless -- reads the config.json written by
    create_case/create_direct_case and drives CrocoDash.forcing.driver.run_workflow
    directly. Components declared as dependencies of a requested one (e.g. a
    filled IC before BGC IC) are enabled automatically by the driver, and
    components run in dependency order.

    Component flags come from the forcing types active in this case's config.json
    -- call list_forcing_configs to see every type's flags. Typical ones:
      ic, bc            MOM6 initial condition / open-boundary conditions
      tides             TPXO tidal OBCs
      chl               chlorophyll
      runoff            runoff->ocean mapping file
      bgcic, bgcironforcing, bgcrivernutrients   MARBL BGC inputs
      cice              CICE restoring/IC file (CICE compsets)
      ww3               WW3 boundary spectra (WW3 compsets)
    Forcing types with no process step (e.g. StreamYears, which aligns DATM/DROF
    stream years at configure time) need nothing here.

    This step can be slow (minutes to tens of minutes) because it downloads
    and regrids data. Use background=True to run it as a detached subprocess,
    or background="pbs" to submit it as a real PBS job on a compute node
    (see below); poll get_case_status to check progress.

    If this case was created with create_direct_case(reuse_forcings_from=...), this
    call skips the real download/regrid entirely and just repoints user_nl_mom at
    the master case's already-processed files.

    Parameters
    ----------
    case_dir : str
        Working directory (inputdir) for this case.
    components : list[str], optional
        Process-component flags to run. Default (None) runs every component
        this case has configured -- the `--all` behaviour.
    skip : list[str], optional
        Flags to leave out, e.g. ["runoff", "tides"] to reuse those from a
        previous run. Applied after `components`.
    preview : bool
        Preview the task graph without downloading or regridding anything.
    background : bool or "pbs"
        If True, spawn processing as a detached background subprocess on this MCP
        server's own host and return immediately. This host is typically a login
        node with a low memory ulimit -- large domains (e.g. a pole-centered polar
        grid, whose GLORYS query can pull a near-global longitude slice) can get
        silently OOM-killed there. If "pbs", submit the same worker as a PBS job
        on a compute node instead (recommended for anything but small domains) and
        return the PBS job id. Status is written to
        extract_forcings/process_forcings_status.json either way; poll
        get_case_status to track completion (default False).
    pbs_queue : str
        Queue to submit to when background="pbs" (default "main").
    pbs_walltime : str
        Walltime for the PBS job when background="pbs", hh:mm:ss (default "03:00:00").
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
        master_ocn = _ocn_dir(Path(reuse_forcings_from))
        status_file = case_dir_path / "extract_forcings" / "process_forcings_status.json"
        status_file.parent.mkdir(parents=True, exist_ok=True)
        status_file.write_text(
            json.dumps(
                {
                    "status": "done",
                    "processed": ["ic", "bc"],
                    "note": f"user_nl_mom repointed at {master_ocn}, not reprocessed here",
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

    with open(config_path) as f:
        config = json.load(f)

    flags, ignored = _resolve_process_flags(config, components, skip)
    selected = [name for name, enabled in flags.items() if enabled]
    if not selected:
        return {
            "status": "nothing_to_do",
            "available": sorted(flags),
            "ignored_not_in_config": ignored,
        }

    if background:
        worker = Path(__file__).parent / "_bg_worker.py"
        extract_dir = case_dir_path / "extract_forcings"
        log_file = extract_dir / "process_forcings.log"
        status_file = extract_dir / "process_forcings_status.json"

        cmd = [sys.executable, str(worker), str(case_dir_path), "--flags", json.dumps(flags)]
        if preview:
            cmd.append("--preview")

        if background == "pbs":
            # The PBS script joins cmd with spaces, so the JSON needs shell quoting.
            import shlex

            result = _submit_process_forcings_pbs(
                case_dir_path,
                [shlex.quote(str(c)) for c in cmd],
                pbs_queue,
                pbs_walltime,
                pbs_project,
            )
            result["selected"] = selected
            result["ignored_not_in_config"] = ignored
            return result

        status_file.write_text(json.dumps({"status": "starting"}))
        with open(log_file, "w") as log_f:
            subprocess.Popen(cmd, stdout=log_f, stderr=log_f, start_new_session=True)

        return {
            "status": "running",
            "selected": selected,
            "ignored_not_in_config": ignored,
            "log_file": str(log_file),
            "status_file": str(status_file),
            "hint": "Call get_case_status to track progress.",
        }

    from CrocoDash.forcing.driver import run_workflow

    with _redirect_library_stdout():
        timings = run_workflow(config_path=config_path, preview=preview, **flags)

    return {
        "status": "ok",
        "processed": selected,
        "ignored_not_in_config": ignored,
        "timings_s": timings or {},
    }


def register(mcp):
    mcp.tool()(create_case)
    mcp.tool()(create_case_from_yaml_file)
    mcp.tool()(dump_case)
    mcp.tool()(create_direct_case)
    mcp.tool()(process_forcings)
