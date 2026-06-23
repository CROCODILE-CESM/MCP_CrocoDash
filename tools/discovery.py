import CrocoDash.forcing_configurations.configurations  # trigger class registration
from CrocoDash.raw_data_access.registry import ProductRegistry
from CrocoDash.raw_data_access.datasets import load_all_datasets
from CrocoDash.forcing_configurations.base import ForcingConfigRegistry


def list_products() -> dict:
    """List all available data products (GLORYS, GEBCO, GloFAS, Seawifs, etc.) and their access methods."""
    ProductRegistry.load()
    result = {}
    for name in ProductRegistry.list_products():
        product = ProductRegistry.get_product(name)
        methods = ProductRegistry.list_access_methods(name)
        result[name] = {
            "description": getattr(product, "description", ""),
            "link": getattr(product, "link", ""),
            "access_methods": methods,
        }
    return result


def list_forcing_configs() -> dict:
    """List available optional forcing configurators (tides, BGC, chlorophyll, runoff, etc.) and which compsets they apply to."""
    result = {}
    for cls in ForcingConfigRegistry.registered_types:
        user_args = ForcingConfigRegistry.get_user_args(cls)
        result[cls.name] = {
            "required_for_compsets": list(getattr(cls, "required_for_compsets", [])),
            "allowed_compsets": list(getattr(cls, "allowed_compsets", [])),
            "forbidden_compsets": list(getattr(cls, "forbidden_compsets", [])),
            "user_args": user_args,
        }
    return result


def register(mcp):
    mcp.tool()(list_products)
    mcp.tool()(list_forcing_configs)
