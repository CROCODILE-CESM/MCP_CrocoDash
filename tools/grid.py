import json
from pathlib import Path
from typing import Optional, Literal

from CrocoDash.grid import Grid
from CrocoDash.topo import Topo
from CrocoDash.vgrid import VGrid


def validate_domain(
    lon_min: float,
    lon_max: float,
    lat_min: float,
    lat_max: float,
    resolution: float,
) -> dict:
    """Check that a lon/lat bounding box and resolution are valid before committing to grid creation."""
    issues = []
    if lon_min >= lon_max:
        issues.append("lon_min must be less than lon_max")
    if lat_min >= lat_max:
        issues.append("lat_min must be less than lat_max")
    if resolution <= 0:
        issues.append("resolution must be positive")
    if lat_min < -90 or lat_max > 90:
        issues.append("latitudes must be between -90 and 90")
    lenx = lon_max - lon_min
    leny = lat_max - lat_min
    if lenx / resolution < 2 or leny / resolution < 2:
        issues.append("Domain too small for resolution: need at least 2 grid cells in each direction")
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "nx": int(lenx / resolution) if resolution > 0 else None,
        "ny": int(leny / resolution) if resolution > 0 else None,
        "lenx_deg": lenx,
        "leny_deg": leny,
    }


def create_grid(
    case_dir: str,
    lon_min: float,
    lon_max: float,
    lat_min: float,
    lat_max: float,
    resolution: float,
    grid_name: str,
    min_depth: float,
    vgrid_type: Literal["uniform", "hyperbolic"],
    nk: int,
    depth: float,
    ratio: Optional[float] = None,
    topo_type: Literal["spoon", "flat", "from_file"] = "spoon",
    topo_max_depth: Optional[float] = None,
    topo_dedge: Optional[float] = None,
    topo_flat_depth: Optional[float] = None,
    topo_file_path: Optional[str] = None,
) -> dict:
    """
    Create Grid, Topo, and VGrid objects for a regional MOM6 case.

    Saves grid parameters to {case_dir}/mcp_grid_params.json and stores the
    Topo version-control history in {case_dir}/TopoLibrary/ for later reconstruction.

    Parameters
    ----------
    case_dir : str
        Working directory for this case (will become the CrocoDash inputdir).
    lon_min / lon_max : float
        Longitude bounds in degrees (0–360 range recommended for MOM6).
    lat_min / lat_max : float
        Latitude bounds in degrees (-90 to 90).
    resolution : float
        Horizontal grid resolution in degrees.
    grid_name : str
        Short identifier for this grid (used in output file names).
    min_depth : float
        Minimum ocean depth in metres; shallower cells are masked.
    vgrid_type : "uniform" | "hyperbolic"
        Vertical grid spacing type.
    nk : int
        Number of vertical levels.
    depth : float
        Total ocean depth in metres for the vertical grid.
    ratio : float, optional
        For hyperbolic vgrid: target ratio of top-to-bottom layer thickness.
    topo_type : "spoon" | "flat" | "from_file"
        Bathymetry initialisation method.
    topo_max_depth : float, optional
        Maximum depth for spoon bathymetry (metres).
    topo_dedge : float, optional
        Depth at basin edge for spoon bathymetry (metres).
    topo_flat_depth : float, optional
        Uniform depth for flat bathymetry (metres).
    topo_file_path : str, optional
        Path to an existing MOM6 topog NetCDF file (for from_file).
    """
    case_dir_path = Path(case_dir)
    case_dir_path.mkdir(parents=True, exist_ok=True)

    lenx = lon_max - lon_min
    leny = lat_max - lat_min

    grid = Grid(
        lenx=lenx,
        leny=leny,
        resolution=resolution,
        xstart=lon_min,
        ystart=lat_min,
        name=grid_name,
    )

    topo_library_dir = str(case_dir_path / "TopoLibrary")
    topo = Topo(grid=grid, min_depth=min_depth, version_control_dir=topo_library_dir)

    if topo_type == "spoon":
        if topo_max_depth is None or topo_dedge is None:
            raise ValueError("topo_max_depth and topo_dedge are required for topo_type='spoon'")
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

    if vgrid_type == "uniform":
        vgrid = VGrid.uniform(nk=nk, depth=depth, name=grid_name)
    elif vgrid_type == "hyperbolic":
        if ratio is None:
            raise ValueError("ratio is required for vgrid_type='hyperbolic'")
        vgrid = VGrid.hyperbolic(nk=nk, depth=depth, ratio=ratio, name=grid_name)
    else:
        raise ValueError(f"Unknown vgrid_type: {vgrid_type!r}")

    params = {
        "lon_min": lon_min,
        "lon_max": lon_max,
        "lat_min": lat_min,
        "lat_max": lat_max,
        "lenx": lenx,
        "leny": leny,
        "resolution": resolution,
        "grid_name": grid_name,
        "nx": grid.nx,
        "ny": grid.ny,
        "min_depth": min_depth,
        "topo_type": topo_type,
        "topo_max_depth": topo_max_depth,
        "topo_dedge": topo_dedge,
        "topo_flat_depth": topo_flat_depth,
        "topo_file_path": topo_file_path,
        "topo_library_dir": topo_library_dir,
        "topo_actual_max_depth": float(topo.max_depth),
        "vgrid_type": vgrid_type,
        "nk": nk,
        "depth": depth,
        "ratio": ratio,
    }

    params_path = case_dir_path / "mcp_grid_params.json"
    params_path.write_text(json.dumps(params, indent=2))

    return {
        "status": "ok",
        "grid_name": grid_name,
        "nx": grid.nx,
        "ny": grid.ny,
        "nk": nk,
        "topo_max_depth": float(topo.max_depth),
        "params_saved_to": str(params_path),
    }


def register(mcp):
    mcp.tool()(validate_domain)
    mcp.tool()(create_grid)
