import os
from pathlib import Path

# ESMF is bundled inside the CrocoDash conda environment.
# Must be set before any xesmf/esmpy import (which happens in tools/).
_conda_prefix = Path(os.__file__).parent.parent.parent  # lib/pythonX.Y -> prefix
_esmf_mk = _conda_prefix / "lib" / "esmf.mk"
if _esmf_mk.exists():
    os.environ["ESMFMKFILE"] = str(_esmf_mk)

from fastmcp import FastMCP

from tools import discovery, grid, case, status, shareable
from resources import register_resources

mcp = FastMCP(
    "CrocoDash",
    instructions=(
        "CrocoDash MCP server — configure and deploy regional MOM6 ocean models within CESM.\n\n"
        "This server mirrors the `crocodash` CLI (create/dump/process/bundle/fork/duplicate) --\n"
        "each MCP tool below maps onto one CLI verb. Case creation is now a single atomic call\n"
        "(grid + topo + vgrid + CESM case + configure_forcings all happen inside create_case /\n"
        "create_direct_case) -- there is no separate configure_forcings step to call.\n\n"
        "== DEPLOYMENT PATHS ==\n\n"
        "Choose the path that matches your environment:\n\n"
        "PATH A — HPC (Derecho), batch queue (standard):\n"
        "  create_case(config, override=...) [machine='derecho' in config['case']]\n"
        "  → process_forcings(case_dir)\n"
        "  Then in cesm-runner MCP: case_setup → case_build → case_submit\n\n"
        "PATH B — HPC (Derecho), container, no queue (fast iteration):\n"
        "  create_case(config) [machine='derecho'] → process_forcings(case_dir)\n"
        "  → bundle_case(caseroot, output_dir)\n"
        "  Then in cesm-runner MCP: start_container → run_case_in_container(container, bundle_dir)\n"
        "  Output lands in the scratch_dir you passed to start_container.\n\n"
        "PATH C — Laptop, container (Podman), no queue:\n"
        "  create_case(config) [machine='ubuntu-latest'] — creates the whole case and configures\n"
        "    forcings in one call. On non-NCAR machines this generates a GLORYS download script;\n"
        "    run that script manually BEFORE calling process_forcings.\n"
        "  → process_forcings(case_dir)\n"
        "  → bundle_case(caseroot, output_dir)\n"
        "  Then in cesm-runner MCP: start_container(inputdata_dir=...) → run_case_in_container\n\n"
        "PATH D — Laptop or HPC, no container (direct Python/CLI):\n"
        "  create_case(config) — creates the case and configures forcings in one call.\n"
        "    On non-NCAR machines, run the generated GLORYS download script first.\n"
        "  → process_forcings(case_dir)\n"
        "  Then build/submit via cesm-runner case_setup → case_build → case_submit(no_batch=True).\n"
        "  (no_batch=True is required on systems without PBS/Slurm.)\n\n"
        "== NOTES ==\n\n"
        "  - process_forcings can take minutes to tens of minutes (data download + regrid).\n"
        "    Pass background=True to detach, or background=\"pbs\" to submit a real PBS job on a\n"
        "    compute node (recommended for anything but small domains); poll get_case_status.\n"
        "  - process_forcings runs CICE/WW3 forcing generation automatically when the case's\n"
        "    compset requires it (process_cice/process_ww3 default True, auto-skipped if not\n"
        "    configured for this case); set either to False to skip it explicitly.\n"
        "  - create_case auto-selects forcings.function_name='get_glorys_data_from_rda' when\n"
        "    case.machine='derecho' (NCAR local archive) and 'get_glorys_data_script_for_cli'\n"
        "    elsewhere, if function_name isn't given explicitly. With the script variant, run\n"
        "    the output script manually BEFORE process_forcings.\n"
        "  - create_case/create_direct_case with override=True delete and recreate an existing\n"
        "    caseroot/inputdir. Never reuse a case_dir/caseroot from a prior failed attempt\n"
        "    without override=True -- CIME's create_newcase can leave EXEROOT/RUNDIR behind and\n"
        "    then prompt interactively on a stdin that is this server's own JSON-RPC pipe, which\n"
        "    hangs forever with no error.\n"
        "  - create_case (recipe/YAML path) only supports plain lat/lon grids and\n"
        "    flat/dataset/from_file topography. For polar grids, spoon bathymetry, or reusing\n"
        "    another case's already-processed forcings (NTASKS scaling sweeps), use\n"
        "    create_direct_case instead.\n"
        "  - fork_case and duplicate_case_tool call configure_forcings internally (via the\n"
        "    recipe). Only process_forcings needs to be called separately afterward.\n"
        "  - bundle_case reads machine/project/cesmroot from the case automatically — only\n"
        "    supply them explicitly to override (e.g. when targeting a different machine).\n"
        "  - dump_case/bundle_case/fork_case/duplicate_case_tool take a caseroot, not an\n"
        "    inputdir -- every other tool here (create_case, create_direct_case,\n"
        "    process_forcings, get_case_status) takes case_dir/inputdir.\n\n"
        "== POLAR DOMAINS ==\n\n"
        "  For Arctic or Antarctic pole-crossing domains, use create_direct_case(grid_type='polar',\n"
        "  ...) instead of create_case. The standard Grid constructor (and CrocoDash.recipe's YAML\n"
        "  schema) rejects lat_max=90 / lat_min=-90; grid_type='polar' uses Grid.from_projection()\n"
        "  with a polar stereographic CRS.\n\n"
        "  Supported projections:\n"
        "    'EPSG:3995' — Arctic Polar Stereographic (centre: North Pole)\n"
        "    'EPSG:3031' — Antarctic Polar Stereographic (centre: South Pole)\n\n"
        "  Domain extents are in projected metres (not degrees). Example for a 2000×2000 km\n"
        "  Arctic cap at 100 km resolution:\n"
        "    create_direct_case(..., grid_type='polar', projection='EPSG:3995',\n"
        "                       x_min=-1_000_000, x_max=1_000_000,\n"
        "                       y_min=-1_000_000, y_max=1_000_000,\n"
        "                       resolution_m=100_000, ...)\n\n"
        "  GLORYS data covers to the poles and process_forcings handles polar domains correctly.\n"
        "  Once created, a polar case can still be dumped/bundled/forked/duplicated via the\n"
        "  normal YAML/recipe path (its supergrid file round-trips regardless of how the grid\n"
        "  was originally built) -- only the initial creation needs create_direct_case.\n\n"
        "Discovery tools: list_products, list_forcing_configs\n"
        "Introspection: get_case_status, dump_case\n"
        "Validation: validate_domain, validate_case_config\n\n"
        "Resources:\n"
        "  crocodash://products            — registered data products\n"
        "  crocodash://forcing-configs     — available optional configurators\n"
        "  crocodash://case/{case_dir}/config  — config.json for a case\n"
        "  crocodash://case/{case_dir}/status  — step completion for a case"
    ),
)

discovery.register(mcp)
grid.register(mcp)
case.register(mcp)
status.register(mcp)
shareable.register(mcp)
register_resources(mcp)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
