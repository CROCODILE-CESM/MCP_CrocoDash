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
        "== DEPLOYMENT PATHS ==\n\n"
        "Choose the path that matches your environment:\n\n"
        "PATH A — HPC (Derecho), batch queue (standard):\n"
        "  create_grid → create_case(machine='derecho') → configure_forcings → process_forcings\n"
        "  Then in cesm-runner MCP: case_setup → case_build → case_submit\n\n"
        "PATH B — HPC (Derecho), container, no queue (fast iteration):\n"
        "  create_grid → create_case(machine='derecho') → configure_forcings → process_forcings\n"
        "  → bundle_case(case_dir, output_dir)\n"
        "  Then in cesm-runner MCP: start_container → run_case_in_container(container, bundle_dir)\n"
        "  Output lands in the scratch_dir you passed to start_container.\n\n"
        "PATH C — Laptop, container (Podman), no queue:\n"
        "  create_case_from_yaml(yaml_path, machine='ubuntu-latest') — creates grid+case and calls\n"
        "    configure_forcings internally (no separate configure_forcings call needed).\n"
        "    On non-NCAR machines this generates a GLORYS download script; run that script\n"
        "    manually BEFORE calling process_forcings.\n"
        "  → process_forcings(case_dir)\n"
        "  → bundle_case(case_dir, output_dir)\n"
        "  Then in cesm-runner MCP: start_container(inputdata_dir=...) → run_case_in_container\n\n"
        "PATH D — Laptop or HPC, no container (direct Python/CLI):\n"
        "  create_case_from_yaml(yaml_path) — creates grid+case and calls configure_forcings\n"
        "    internally (no separate configure_forcings call needed).\n"
        "    On non-NCAR machines, run the generated GLORYS download script first.\n"
        "  → process_forcings(case_dir)\n"
        "  Then build/submit via cesm-runner case_setup → case_build → case_submit(no_batch=True).\n"
        "  (no_batch=True is required on systems without PBS/Slurm.)\n\n"
        "== NOTES ==\n\n"
        "  - process_forcings can take minutes to tens of minutes (data download + regrid).\n"
        "    Pass background=True to detach; poll get_case_status to track progress.\n"
        "  - configure_forcings auto-selects function_name='get_glorys_data_from_rda'\n"
        "    on Derecho (NCAR local archive) and 'get_glorys_data_script_for_cli' elsewhere.\n"
        "    With the script variant, run the output script manually BEFORE process_forcings.\n"
        "  - create_case with override=True wipes the inputdir. Always run create_grid first.\n"
        "  - create_case_from_yaml and fork_case call configure_forcings internally (via the recipe).\n"
        "    Only process_forcings needs to be called separately as a distinct MCP step.\n"
        "    On non-NCAR machines, run the generated GLORYS download script before process_forcings.\n"
        "  - bundle_case reads machine/project/cesmroot from the case automatically — only\n"
        "    supply them explicitly to override (e.g. when targeting a different machine).\n\n"
        "== POLAR DOMAINS ==\n\n"
        "  For Arctic or Antarctic pole-crossing domains use create_polar_grid instead of\n"
        "  create_grid. The standard Grid constructor rejects lat_max=90 / lat_min=-90;\n"
        "  create_polar_grid uses Grid.from_projection() with a polar stereographic CRS.\n\n"
        "  Supported projections:\n"
        "    'EPSG:3995' — Arctic Polar Stereographic (centre: North Pole)\n"
        "    'EPSG:3031' — Antarctic Polar Stereographic (centre: South Pole)\n\n"
        "  Domain extents are in projected metres (not degrees). Example for a 2000×2000 km\n"
        "  Arctic cap at 100 km resolution:\n"
        "    create_polar_grid(projection='EPSG:3995',\n"
        "                      x_min=-1_000_000, x_max=1_000_000,\n"
        "                      y_min=-1_000_000, y_max=1_000_000,\n"
        "                      resolution_m=100_000, ...)\n\n"
        "  After create_polar_grid, call create_case and configure_forcings as usual.\n"
        "  GLORYS data covers to the poles and process_forcings handles polar domains correctly.\n\n"
        "Discovery tools: list_products, list_forcing_configs\n"
        "Introspection: get_case_status, preview_config\n"
        "Validation: validate_domain\n\n"
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
