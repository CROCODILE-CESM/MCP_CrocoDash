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
        "Workflow (sequential):\n"
        "  1. create_grid   — define horizontal grid, bathymetry, and vertical grid\n"
        "  2. create_case   — create the CESM case (writes input files, runs ./create_newcase)\n"
        "  3. configure_forcings — set date range, product, boundaries, optional tides/BGC/chl\n"
        "  4. process_forcings  — download/regrid/merge boundary and initial conditions\n\n"
        "Notes:\n"
        "  - process_forcings can take minutes to tens of minutes (data download + regrid).\n"
        "    By default it runs synchronously so you can monitor output directly — this is\n"
        "    fine for a first run or short date ranges. Pass background=True to detach it\n"
        "    as a subprocess; poll get_case_status to track progress via forcings_processing.\n"
        "  - configure_forcings auto-selects function_name='get_glorys_data_from_rda'\n"
        "    when the case machine is 'derecho' or when /glade/collections/rda/data is\n"
        "    accessible (covers both Derecho and Casper). Override explicitly if needed.\n"
        "  - With function_name='get_glorys_data_script_for_cli' (non-Derecho), configure_forcings\n"
        "    writes a shell script but does NOT download data. You must run that script manually\n"
        "    before calling process_forcings, or process_forcings will fail with a missing-file error.\n"
        "  - create_case with override=True wipes the inputdir; grid params are automatically\n"
        "    re-saved by create_case, but always run create_grid before create_case.\n\n"
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
