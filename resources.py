import json
from pathlib import Path


def register_resources(mcp):

    @mcp.resource("crocodash://products")
    def get_products() -> str:
        """Live list of registered products and their metadata."""
        from tools.discovery import list_products

        return json.dumps(list_products(), indent=2)

    @mcp.resource("crocodash://forcing-configs")
    def get_forcing_configs() -> str:
        """Live list of forcing types, their compset compatibility and process flags."""
        from tools.discovery import list_forcing_configs

        return json.dumps(list_forcing_configs(), indent=2)

    @mcp.resource("crocodash://case/{case_dir}/config")
    def get_case_config(case_dir: str) -> str:
        """The case's current config.json (written by configure_forcings)."""
        config_path = Path(case_dir) / "extract_forcings" / "config.json"
        if not config_path.exists():
            return json.dumps({"error": f"config.json not found at {config_path}"})
        return config_path.read_text()

    @mcp.resource("crocodash://case/{case_dir}/status")
    def get_case_status_resource(case_dir: str) -> str:
        """Step completion status: which files exist, what config contains, which steps are done."""
        from tools.status import get_case_status
        return json.dumps(get_case_status(case_dir), indent=2)
