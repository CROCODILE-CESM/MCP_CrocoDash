import json
from pathlib import Path

REUSE_MARKER_FILENAME = "crocodash_mcp_reuse.json"


def get_case_status(case_dir: str) -> dict:
    """
    Return step completion state for a case directory.

    Checks which files exist and what config JSON contains to report which
    workflow steps have been completed. `case_dir` is the CrocoDash inputdir
    (same convention as create_case/create_direct_case/process_forcings) --
    crocodash_state.json itself lives inside the case's *caseroot*, which this
    resolves via extract_forcings/config.json's "caseroot" key once forcings
    have been configured.

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
            "case_created": False,
            "forcings_configured": False,
            "forcings_processed": False,
        },
    }

    if not case_dir_path.exists():
        return status

    # Step 1: Forcings configured? (also the only file-based way to learn caseroot
    # from an inputdir -- see tools/case.py::_caseroot_from_inputdir)
    config_path = case_dir_path / "extract_forcings" / "config.json"
    caseroot = None
    if config_path.exists():
        status["steps"]["forcings_configured"] = True
        with open(config_path) as f:
            config = json.load(f)
        caseroot = config.get("caseroot")
        active = [k for k in config if k not in ("caseroot", "conditions")]
        conditions = config.get("conditions", {})
        # start/end dates are conditions *inputs* (formatted per outputs.date_format)
        dates = {**conditions.get("outputs", {}), **conditions.get("inputs", {})}
        status["forcing"] = {
            "date_range": [dates.get("start_date", ""), dates.get("end_date", "")],
            "boundaries": list(
                config.get("conditions", {})
                .get("outputs", {})
                .get("boundary_number_conversion", {})
                .keys()
            ),
            "active_configurators": active,
        }

    # Step 2: Case created? Read crocodash_state.json from caseroot, if known.
    if caseroot is not None:
        from CrocoDash import case_state

        try:
            state = case_state.read(caseroot)
        except FileNotFoundError:
            state = None
        if state is not None:
            status["steps"]["case_created"] = True
            # The state file never records caseroot itself (it lives inside it).
            status["case"] = {
                "caseroot": caseroot,
                **{
                    k: state[k]
                    for k in ("compset_lname", "machine", "grid_name")
                    if k in state
                },
            }

    # Step 3: Forcings processed? (output NetCDF files in ocn/, or ocean/ for
    # cases created before CrocoDash split the inputdir into ocn/ice/wav)
    ocean_dir = next(
        (case_dir_path / d for d in ("ocn", "ocean") if (case_dir_path / d).exists()),
        None,
    )
    if ocean_dir is not None:
        nc_files = list(ocean_dir.glob("*.nc"))
        ic_files = [f for f in nc_files if "init_" in f.name]
        obc_files = [f for f in nc_files if "forcing_obc_" in f.name]
        if ic_files or obc_files:
            status["steps"]["forcings_processed"] = True
            status["output_files"] = sorted(f.name for f in ic_files + obc_files)

    # CICE/WW3 write to their own sibling directories (ice/, wav/; sea_ice/,
    # wave/ on older cases) rather than ocn/ -- check those too when the case's
    # config activated them, so a CICE/WW3-active case isn't reported as
    # unprocessed just because MOM6's ocn/ output alone was checked.
    active_configurators = status.get("forcing", {}).get("active_configurators", [])
    for subdir_names, config_key in ((("ice", "sea_ice"), "cice"), (("wav", "wave"), "ww3")):
        if config_key not in active_configurators:
            continue
        for subdir_name in subdir_names:
            subdir = case_dir_path / subdir_name
            if not subdir.exists():
                continue
            component_files = sorted(f.name for f in subdir.glob("*.nc"))
            if component_files:
                status["steps"]["forcings_processed"] = True
                status.setdefault("output_files_by_component", {})[
                    config_key
                ] = component_files
            break

    # reuse_forcings_from marker, if this case was created with create_direct_case(reuse_forcings_from=...)
    reuse_marker_path = case_dir_path / REUSE_MARKER_FILENAME
    if reuse_marker_path.exists():
        status["reused_from"] = json.loads(reuse_marker_path.read_text()).get(
            "reuse_forcings_from"
        )

    # Background process_forcings job status (if process_forcings was run with background=True)
    bg_status_file = case_dir_path / "extract_forcings" / "process_forcings_status.json"
    if bg_status_file.exists():
        bg = json.loads(bg_status_file.read_text())
        status["forcings_processing"] = bg
        if bg.get("status") == "done" and not status["steps"]["forcings_processed"]:
            status["steps"]["forcings_processed"] = True

    return status


def validate_case_config(config: dict) -> dict:
    """
    Fast, side-effect-free structural check of a create_case config dict.

    Wraps CrocoDash.recipe.validate_config_structure -- the same pre-flight check
    create_case itself runs before doing any real work (grid/topo/vgrid
    construction, CIME case creation). Call this first to catch schema mistakes
    without paying for a real (slow, disk-touching) case creation attempt.

    Parameters
    ----------
    config : dict
        Same schema as create_case's config argument.
    """
    from CrocoDash.recipe import validate_config_structure

    try:
        validate_config_structure(config)
    except ValueError as e:
        return {"valid": False, "issues": [str(e)]}
    return {"valid": True, "issues": []}


def register(mcp):
    mcp.tool()(get_case_status)
    mcp.tool()(validate_case_config)
