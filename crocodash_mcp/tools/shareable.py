"""
Tools for bundling, forking, and duplicating CrocoDash cases.

Public API (PR #238, cli-workflow, merged):
  - CrocoDash.shareable.CaseBundle
  - CrocoDash.shareable.ForkBundle
  - CrocoDash.shareable.duplicate_case
  - CrocoDash.recipe.create_case_from_yaml / case_to_yaml

Typical forcing-variation workflow:
  1. bundle_case(original_case_dir, bundle_output_dir)
       → portable snapshot: crocodash_case.yaml + ocn/ (+ wav/) + non_standard_case_info.json
  2. fork_case(bundle_dir, new_case_dir, new_inputdir, cesmroot, machine, project)
       → new case using recipe.py; run process_forcings to regenerate forcing files
  3. OR fork to container: bundle_case → run_case_in_container (cesm-runner MCP)
"""

import contextlib
import json
import sys
from pathlib import Path

from crocodash_mcp.tools.case import CASE_BUILD_LOCK


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
        Path to the existing CESM case root (caseroot).
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
    import yaml
    from CrocoDash.shareable import CaseBundle

    # identify_non_standard_case_info builds a temporary reference case.
    with CASE_BUILD_LOCK:
        cb = CaseBundle(case_dir)  # CaseBundle takes a caseroot, not an inputdir

        # If caller didn't supply cesmroot/machine/project, read from saved state
        _cesmroot = cesmroot or str(cb.cesmroot)
        _machine = machine or cb.case_machine
        _project = project or cb.case_project or ""

        cb.identify_non_standard_case_info(_cesmroot, _machine, _project)
        bundle_path = cb.bundle(output_dir)

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

    Equivalent to `crocodash fork` with the copy plan given up front. Calls
    CrocoDash.shareable.ForkBundle.fork() with its interactive YAML review
    skipped (an MCP server has no terminal to prompt on): the bundle's recipe is
    patched with the new paths/machine/project, the case is rebuilt via the
    recipe (configure_forcings included), the bundle's ocn/ and wav/ files are
    copied in, and the non-standard CESM state captured at bundle time is applied
    per the copy_* flags.

    To change forcings on the forked case (add tides, swap product, etc.), dump it
    with dump_case, edit, and recreate with create_case. Run process_forcings on
    the new inputdir afterward.

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
        inputdir  — path to the new inputdir
        message   — next steps
    """
    from CrocoDash.shareable import ForkBundle

    forker = ForkBundle(Path(bundle_dir).expanduser())
    # fork() walks a human through the YAML before creating anything; the paths
    # and machine are already given explicitly here, so accept the config as-is.
    forker._guide_yaml_review = lambda config: config

    with CASE_BUILD_LOCK, contextlib.redirect_stdout(sys.stderr):
        case = forker.fork(
            cesmroot=str(Path(cesmroot).expanduser()),
            machine=machine,
            project_number=project,
            new_caseroot=str(Path(new_case_dir).expanduser()),
            new_inputdir=str(Path(new_inputdir).expanduser()),
            plan={
                "xml_files": copy_xml_files,
                "user_nl": copy_user_nl,
                "source_mods": copy_source_mods,
                "xmlchanges": copy_xmlchanges,
            },
        )

    return {
        "case_dir": str(case.caseroot),
        "inputdir": str(case.inputdir),
        "message": (
            "Case forked successfully. "
            "Run process_forcings on the new inputdir to regenerate forcing files."
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
        Path to the existing case to copy (caseroot).
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

    with CASE_BUILD_LOCK:
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
