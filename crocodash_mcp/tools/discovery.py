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


_SET_BY_CONFIGURE_FORCINGS = {"date_range", "start_date", "end_date", "function_args"}


def list_forcing_configs() -> dict:
    """List every forcing type (conditions, tides, BGC, chlorophyll, runoff, CICE, WW3,
    StreamYears, ...): which compsets require/allow it, the configure_forcings kwargs
    it requires (user_args; an optional type activates only when all are given) and
    also accepts (optional_args), and the process_forcings component flags it answers
    to (process_flags; empty for types that do all their work at configure time)."""
    import CrocoDash.forcing  # registers every forcing type
    from CrocoDash.forcing.base import ForcingConfigRegistry

    result = {}
    for cls in ForcingConfigRegistry.registered_types:
        all_args, required_args = ForcingConfigRegistry.get_ctor_signature(cls)
        result[cls.name] = {
            "required_for_compsets": list(getattr(cls, "required_for_compsets", [])),
            "allowed_compsets": list(getattr(cls, "allowed_compsets", [])),
            "forbidden_compsets": list(getattr(cls, "forbidden_compsets", [])),
            "user_args": ForcingConfigRegistry.get_user_args(cls),
            # case_* args are filled from the Case, and the dates/function_args
            # from configure_forcings' own date_range/function_overrides.
            "optional_args": [
                arg
                for arg in all_args
                if arg not in required_args
                and not arg.startswith("case_")
                and arg not in _SET_BY_CONFIGURE_FORCINGS
            ],
            "process_flags": sorted(getattr(cls, "process_components", {})),
        }
    return result


def register(mcp):
    mcp.tool()(list_products)
    mcp.tool()(list_forcing_configs)
