"""
Tools for bundling, forking, and duplicating CrocoDash cases.

After PR #238 (cli-workflow) merges, the public API is:
  - CrocoDash.shareable.CaseBundle    (was BundleCrocoDashCase)
  - CrocoDash.shareable.ForkBundle    (was ForkCrocoDashBundle)
  - CrocoDash.shareable.duplicate_case
  - CrocoDash.recipe.create_case_from_yaml / case_to_yaml

Typical forcing-variation workflow:
  1. bundle_case(original_case_dir, bundle_output_dir)
       → portable snapshot: crocodash_case.yaml + ocnice/ + non_standard_case_info.json
  2. fork_case(bundle_dir, new_case_dir, new_inputdir, cesmroot, machine, project)
       → new case using recipe.py; run process_forcings to regenerate forcing files
  3. OR fork to container: bundle_case → run_case_in_container (cesm-runner MCP)
"""

import json
import shutil
from pathlib import Path


def bundle_case(
    case_dir: str,
    output_dir: str,
    cesmroot: str = "",
    machine: str = "",
    project: str = "",
) -> dict:
    """
    Bundle an existing CrocoDash case into a portable, shareable folder.

    The bundle captures grid files, forcing configuration, user namelists,
    XML changes, and SourceMods. It contains a crocodash_case.yaml recipe so
    the case can be recreated on any machine via fork_case or the
    `crocodash fork` CLI.

    The bundle is also the input for run_case_in_container (cesm-runner MCP),
    which is the fast-iteration path for running in a crocontainer without queue.

    Parameters
    ----------
    case_dir : str
        Path to the existing CESM case root.
    output_dir : str
        Directory where the bundle folder will be written.
    cesmroot : str, optional
        CESM install path. If omitted, read from the case's saved state.
    machine : str, optional
        Machine name (e.g. "derecho"). If omitted, read from saved state.
    project : str, optional
        Project/account. If omitted, read from saved state.

    Returns
    -------
    dict with keys:
        bundle_path   — path to the created bundle folder
        recipe        — the crocodash_case.yaml contents (paths, compset, forcings)
        non_standard_info — what non-default CESM state was captured
    """
    from CrocoDash.shareable import CaseBundle

    cb = CaseBundle(case_dir)

    # If caller didn't supply cesmroot/machine/project, read from saved state
    _cesmroot = cesmroot or str(cb.cesmroot)
    _machine = machine or cb.case_machine
    _project = project or cb.case_project or ""

    cb.identify_non_standard_case_info(_cesmroot, _machine, _project)
    bundle_path = cb.bundle(output_dir)

    import yaml
    with open(bundle_path / "crocodash_case.yaml") as f:
        recipe = yaml.safe_load(f)
    with open(bundle_path / "non_standard_case_info.json") as f:
        diffs = json.load(f)

    return {
        "bundle_path": str(bundle_path),
        "recipe": recipe,
        "non_standard_info": diffs,
    }


def fork_case(
    bundle_dir: str,
    new_case_dir: str,
    new_inputdir: str,
    cesmroot: str,
    machine: str,
    project: str,
    copy_xml_files: bool = True,
    copy_user_nl: bool = True,
    copy_source_mods: bool = True,
    copy_xmlchanges: bool = True,
) -> dict:
    """
    Recreate a CrocoDash case from a bundle for a new machine or user.

    Reads the recipe YAML from the bundle, patches it with the new location and
    machine, then calls create_case_from_yaml to rebuild the case — bypassing
    the interactive CLI prompts that ForkBundle.fork() uses for human users.
    After creation, copies grid/forcing files from the bundle and applies any
    non-standard CESM state (SourceMods, XML overrides, xmlchanges) per the plan.

    To change forcings on the forked case (add tides, swap product, etc.), call
    configure_forcings then process_forcings on the new case_dir afterward.

    Parameters
    ----------
    bundle_dir : str
        Path to a bundle folder created by bundle_case.
    new_case_dir : str
        Path for the new CESM case root.
    new_inputdir : str
        Path for the new case's input data directory.
    cesmroot : str
        Path to the CESM install on the target machine.
    machine : str
        Machine name for the new case (e.g. "derecho", "ubuntu-latest").
    project : str
        Project/account number for job submission.
    copy_xml_files : bool
        Copy non-default XML files from the bundle (default True).
    copy_user_nl : bool
        Copy non-default user_nl parameters from the bundle (default True).
    copy_source_mods : bool
        Copy SourceMods from the bundle (default True).
    copy_xmlchanges : bool
        Apply non-default xmlchange settings from the bundle (default True).

    Returns
    -------
    dict with keys:
        case_dir  — path to the new case
        message   — next steps
    """
    import yaml
    from CrocoDash.recipe import create_case_from_yaml
    from CrocoDash.shareable import (
        copy_xml_files_from_case,
        copy_source_mods_from_case,
        apply_xmlchanges_to_case,
        BundleDifferences,
    )

    bundle_path = Path(bundle_dir).expanduser()

    # Read and patch the recipe YAML for the new environment
    with open(bundle_path / "crocodash_case.yaml") as f:
        config = yaml.safe_load(f)

    config["case"]["cesmroot"] = str(Path(cesmroot).expanduser())
    config["case"]["machine"] = machine
    config["case"]["project"] = project
    config["case"]["caseroot"] = str(Path(new_case_dir).expanduser())
    config["case"]["inputdir"] = str(Path(new_inputdir).expanduser())

    # Redirect grid file paths from original host to bundle's ocnice folder
    if "supergrid_path" in config.get("grid", {}):
        config["grid"]["supergrid_path"] = str(
            bundle_path / "ocnice" / Path(config["grid"]["supergrid_path"]).name
        )
    topo_src = config.get("topo", {}).get("source", {})
    if topo_src.get("type") == "from_file":
        topo_src["topo_file_path"] = str(
            bundle_path / "ocnice" / Path(topo_src["topo_file_path"]).name
        )
    if config.get("vgrid", {}).get("type") == "from_file":
        config["vgrid"]["filename"] = str(
            bundle_path / "ocnice" / Path(config["vgrid"]["filename"]).name
        )

    # Create the case from the patched recipe (configure_only=True: the recipe runs
    # configure_forcings but not process_forcings, which the caller handles explicitly).
    case = create_case_from_yaml(config, override=True, configure_only=True)

    # Copy all forcing and grid files from bundle/ocnice into the new inputdir
    bundle_ocnice = bundle_path / "ocnice"
    case_ocnice = Path(case.inputdir) / "ocnice"
    case_ocnice.mkdir(parents=True, exist_ok=True)
    for src in bundle_ocnice.iterdir():
        dst = case_ocnice / src.name
        if not dst.exists():
            shutil.copy(src, dst)

    # Apply non-standard CESM state captured at bundle time
    diffs = BundleDifferences(
        **json.loads((bundle_path / "non_standard_case_info.json").read_text())
    )

    if copy_xml_files and diffs.xml_files_missing_in_new:
        copy_xml_files_from_case(
            bundle_path / "xml_files",
            case.caseroot,
            diffs.xml_files_missing_in_new,
        )
    if copy_source_mods and diffs.source_mods_missing_files:
        copy_source_mods_from_case(
            bundle_path,
            case.caseroot,
            diffs.source_mods_missing_files,
        )
    if copy_xmlchanges and diffs.xmlchanges_missing:
        apply_xmlchanges_to_case(bundle_path, diffs.xmlchanges_missing)
    if copy_user_nl and diffs.user_nl_missing_params:
        # user_nl params are stored in non_standard_case_info; if the bundle
        # includes user_nl_* files, copy them; otherwise skip silently.
        from CrocoDash.shareable import copy_user_nl_params_from_case
        user_nl_dir = bundle_path
        if any((bundle_path / f"user_nl_{k}").exists() for k in diffs.user_nl_missing_params):
            copy_user_nl_params_from_case(user_nl_dir, diffs.user_nl_missing_params)

    return {
        "case_dir": str(case.caseroot),
        "message": (
            "Case forked successfully. "
            "Run process_forcings to regenerate any forcing files for the new domain."
        ),
    }


def duplicate_case_tool(
    case_dir: str,
    new_case_dir: str,
    new_inputdir: str,
    bundle_dir: str = "",
) -> dict:
    """
    One-step copy of a CrocoDash case to a new location on the same machine.

    Machine, project, and CESM root are read automatically from the original
    case's saved state. Simpler than bundle + fork when you just want an
    identical copy at a different path (e.g. to vary run length or xmlchange
    settings without re-processing forcings).

    Parameters
    ----------
    case_dir : str
        Path to the existing case to copy.
    new_case_dir : str
        Destination path for the new case.
    new_inputdir : str
        Destination path for the new input data.
    bundle_dir : str, optional
        If provided, also saves a bundle to this directory for later forking.

    Returns
    -------
    dict with keys:
        case_dir  — path to the new case
    """
    from CrocoDash.shareable import duplicate_case

    result = duplicate_case(
        caseroot=Path(case_dir).expanduser(),
        new_caseroot=Path(new_case_dir).expanduser(),
        new_inputdir=Path(new_inputdir).expanduser(),
        bundle_dir=Path(bundle_dir).expanduser() if bundle_dir else None,
    )
    return {"case_dir": str(result.caseroot)}


def register(mcp):
    mcp.tool()(bundle_case)
    mcp.tool()(fork_case)
    mcp.tool()(duplicate_case_tool)
