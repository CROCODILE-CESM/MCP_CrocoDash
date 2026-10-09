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


def register(mcp):
    mcp.tool()(validate_domain)
