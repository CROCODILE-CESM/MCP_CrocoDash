def list_products() -> dict:
    """List all available data products (GLORYS, GEBCO, GloFAS, Seawifs, etc.) and their access methods."""
    import CrocoDash.forcing  # registers every forcing type (and the products they use)
    from CrocoDash.raw_data_access.registry import ProductRegistry

    ProductRegistry.load()
    result = {}
    for name in ProductRegistry.list_products():
        product = ProductRegistry.get_product(name)
        result[name] = {
            "description": getattr(product, "description", ""),
            "link": getattr(product, "link", ""),
            "access_methods": ProductRegistry.list_access_methods(name),
        }
    return result


def list_forcing_configs() -> dict:
    """List every forcing type (conditions, tides, BGC, chlorophyll, runoff, CICE, WW3,
    StreamYears, ...): which compsets require/allow it, the configure_forcings kwargs
    it takes (user_args), and the process_forcings component flags it answers to
    (process_flags; empty for types that do all their work at configure time)."""
    import CrocoDash.forcing  # registers every forcing type
    from CrocoDash.forcing.base import ForcingConfigRegistry

    result = {}
    for cls in ForcingConfigRegistry.registered_types:
        result[cls.name] = {
            "required_for_compsets": list(getattr(cls, "required_for_compsets", [])),
            "allowed_compsets": list(getattr(cls, "allowed_compsets", [])),
            "forbidden_compsets": list(getattr(cls, "forbidden_compsets", [])),
            "user_args": ForcingConfigRegistry.get_user_args(cls),
            "process_flags": sorted(getattr(cls, "process_components", {})),
        }
    return result


def register(mcp):
    mcp.tool()(list_products)
    mcp.tool()(list_forcing_configs)
