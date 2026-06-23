from fastmcp import FastMCP

from tools import discovery, grid, case, status
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
register_resources(mcp)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
