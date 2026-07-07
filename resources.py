import json
from pathlib import Path


def register_resources(mcp):

    @mcp.resource("crocodash://products")
    def get_products() -> str:
        """Live list of registered products and their metadata."""
        import CrocoDash.forcing_configurations.configurations  # trigger class registration
        from CrocoDash.raw_data_access.registry import ProductRegistry
        from CrocoDash.raw_data_access.datasets import load_all_datasets

        ProductRegistry.load()
        result = {}
        for name in ProductRegistry.list_products():
            product = ProductRegistry.get_product(name)
            result[name] = {
                "description": getattr(product, "description", ""),
                "link": getattr(product, "link", ""),
                "access_methods": ProductRegistry.list_access_methods(name),
            }
        return json.dumps(result, indent=2)

    @mcp.resource("crocodash://forcing-configs")
    def get_forcing_configs() -> str:
        """Live list of available forcing configurators and their compset compatibility."""
        import CrocoDash.forcing_configurations.configurations  # trigger class registration
        from CrocoDash.forcing_configurations.base import ForcingConfigRegistry

        result = {}
        for cls in ForcingConfigRegistry.registered_types:
            user_args = ForcingConfigRegistry.get_user_args(cls)
            result[cls.name] = {
                "required_for_compsets": list(getattr(cls, "required_for_compsets", [])),
                "allowed_compsets": list(getattr(cls, "allowed_compsets", [])),
                "forbidden_compsets": list(getattr(cls, "forbidden_compsets", [])),
                "user_args": user_args,
            }
        return json.dumps(result, indent=2)

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
