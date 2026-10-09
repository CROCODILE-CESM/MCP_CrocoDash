"""MCP tools mirroring `crocodash template`.

The gallery (crocogallery) owns the template sources and their rendering;
these tools only map arguments onto that call, exactly like CrocoDash's cli.py.
"""
from typing import Literal, Optional


def list_templates() -> dict:
    """List the gallery notebook IDs write_template can start from.

    Equivalent to `crocodash template --list-notebooks`. Each ID is a CrocoGallery
    demo (e.g. the default tutorial, BGC, tides, CICE, WW3, polar, nested cases)
    that write_template can render as a .yaml recipe, .ipynb notebook or .py script.
    """
    from crocogallery import list_notebooks
    from crocogallery.template import DEFAULT_TEMPLATE_NOTEBOOK_ID

    return {
        "default": DEFAULT_TEMPLATE_NOTEBOOK_ID,
        "notebooks": sorted(list_notebooks()),
    }


def write_template(
    output: str,
    notebook_id: Optional[str] = None,
    machine: Optional[str] = None,
    kind: Literal["case", "pbs"] = "case",
) -> dict:
    """
    Write a starter CrocoDash case file, or a PBS script for `crocodash process`.

    Equivalent to `crocodash template --output <output> [--notebook ID] [--machine M]
    [--kind case|pbs]`. A good starting point for a new case: render a gallery demo
    close to what the user wants, edit it, then feed a .yaml straight to
    create_case_from_yaml_file.

    Parameters
    ----------
    output : str
        Output path. For kind="case" the suffix picks the format: .yaml (a recipe
        create_case_from_yaml_file accepts), .ipynb (notebook) or .py (script).
        For kind="pbs" any path (a .pbs suffix also selects the pbs template).
    notebook_id : str, optional
        Gallery notebook ID to use as the source (see list_templates). Defaults to
        the gallery's default tutorial. Ignored for standalone templates (pbs).
    machine : str, optional
        Pre-fill known dataset paths for this machine (e.g. "glade"). Omit to leave
        <KEY> placeholders in the output.
    kind : "case" | "pbs"
        What to write.
    """
    from crocogallery import write_template as _write_template
    from crocogallery.template import DEFAULT_TEMPLATE_NOTEBOOK_ID

    path = _write_template(
        output,
        notebook_id=notebook_id or DEFAULT_TEMPLATE_NOTEBOOK_ID,
        machine=machine,
        kind=kind,
    )
    return {"status": "ok", "path": str(path)}


def register(mcp):
    mcp.tool()(list_templates)
    mcp.tool()(write_template)
