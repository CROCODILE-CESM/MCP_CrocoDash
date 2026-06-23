# CrocoDash MCP Server — Scope

## Background

CrocoDash is a Python toolkit for configuring and deploying regional MOM6 ocean models within CESM. A typical user workflow is:

1. Define a grid (horizontal, topography, vertical)
2. Create a CESM case
3. Configure forcings (date range, data product, optional tides/BGC/etc.)
4. Process forcings (download, regrid, generate boundary/IC files)

The MCP server exposes this workflow as a sequence of tools, making it drivable by an LLM agent.

---

## Philosophy

- **One tool per workflow step** — tools map directly to `Case` methods and setup objects.
- **Stateless server, file-backed state** — the server holds no in-memory state. Each tool takes a `case_dir` path and reads/writes the config JSON that CrocoDash already produces there. This makes the server restartable and crash-safe.
- **Discoverable** — resources expose available products and forcing configurations so an agent can reason about valid inputs before calling setup tools.

---

## Tools

### Discovery / Introspection

| Tool | Purpose |
|------|---------|
| `list_products` | Returns available data products (GLORYS, GEBCO, GLOFAS, Seawifs, etc.) and their required inputs |
| `list_forcing_configs` | Returns available optional forcing configurators (tides, BGC, chlorophyll, runoff, etc.) and which compsets they apply to |
| `get_case_status` | Given a `case_dir`, returns step completion state: which files exist, what config JSON contains, which steps are done |

### Case Setup Workflow (sequential)

| Tool | Maps to | Key inputs |
|------|---------|------------|
| `create_grid` | Instantiate `Grid`, `Topo`, `VGrid` | lon/lat bounds, resolution, topo product, vertical grid params |
| `create_case` | Instantiate `Case` + CIME case creation | CESM paths, grid objects, compset, machine, project, case dir |
| `configure_forcings` | `Case.configure_forcings()` | date range, open boundaries, product name, optional config flags (tides, BGC, etc.) |
| `process_forcings` | `Case.process_forcings()` | `case_dir` only — reads config JSON written by prior step |

### Utilities

| Tool | Purpose |
|------|---------|
| `validate_domain` | Check that a lon/lat bounding box + resolution is valid before committing to grid creation |
| `preview_config` | Return the configuration JSON that *would be* written, without executing anything |

---

## Resources

| URI | Content |
|-----|---------|
| `crocodash://products` | Live list of registered products and their metadata |
| `crocodash://case/{case_dir}/config` | The case's current `config.json` |
| `crocodash://case/{case_dir}/status` | Step completion status (grid files exist?, case built?, forcings processed?) |

---

## Repo Structure

```
crocodash-mcp/
  server.py          # FastMCP app — tool + resource registration
  tools/
    discovery.py     # list_products, list_forcing_configs
    grid.py          # create_grid, validate_domain
    case.py          # create_case, configure_forcings, process_forcings
    status.py        # get_case_status, preview_config
  resources.py       # URI handlers for crocodash:// scheme
  pyproject.toml
  README.md
```

---

## Out of Scope (v1)

- Compilation / job submission (CIME domain, machine-specific)
- Visualization of grids or outputs
- Multi-case orchestration
- Anything in `visualCaseGen` beyond what `Case.__init__` already calls
