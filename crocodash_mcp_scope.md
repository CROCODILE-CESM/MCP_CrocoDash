# CrocoDash MCP Server — Scope

## Background

CrocoDash is a Python toolkit for configuring and deploying regional MOM6 ocean models within CESM. It ships a `crocodash` CLI built on a YAML-driven `recipe.py` layer:

```
crocodash create   --config x.yaml       # grid + topo + vgrid + CESM case + configure_forcings, atomically
crocodash process  --caseroot ...        # download/regrid boundary + initial condition files
crocodash dump      --caseroot ...       # reconstruct the YAML for an existing case
crocodash bundle/fork/duplicate          # share a case across machines/users (CrocoDash.shareable)
```

The MCP server exposes this workflow as a sequence of tools, making it drivable by an LLM agent, mapping directly onto the CLI verbs above.

**Version tracking**: this server is not pip-pinned to CrocoDash (consumed via conda-env/sys.path, not a `pyproject.toml` dependency), so there's no lockfile to keep it in sync automatically. Last verified against CrocoDash `b8b87f04` (main, CESM `crocodash` branch) and fastmcp 4.0.10 as of 2026-10-16 — in particular, the `CrocoDash.forcing` package (formerly `forcing_configurations` + `extract_forcings`), the case inputdir layout (`ocn/`, `ice/`, `wav/`; older `ocean/`, `sea_ice/`, `wave/` still read), the bundle layout (`ocn/`, `wav/`), and the registry-driven process flags (`ForcingConfigRegistry.available_process_flags`). If CrocoDash changes its inputdir layout, `run_workflow`/`configure_forcings`/`process_forcings` signatures, or bundle format again, grep this repo for the affected symbol/path before merging that change upstream.

---

## Philosophy

- **One tool per CLI verb** — tools map directly onto `crocodash create/process/dump/bundle/fork/duplicate`.
- **Stateless server, file-backed state** — the server holds no in-memory state. `create_case`/`create_direct_case` do grid+topo+vgrid+CESM-case+configure_forcings in one atomic call, so nothing needs to survive between tool calls (or a server restart) in memory. Each tool takes a `case_dir` (inputdir) or `caseroot` path and reads/writes the state CrocoDash itself already produces there (`crocodash_state.json` in caseroot, `extract_forcings/config.json` in inputdir).
- **Discoverable** — resources expose available products and forcing configurations so an agent can reason about valid inputs before calling setup tools.

---

## Tools

### Discovery / Introspection

| Tool | Purpose |
|------|---------|
| `list_products` | Returns available data products (GLORYS, GEBCO, GLOFAS, Seawifs, etc.) and their required inputs |
| `list_forcing_configs` | Returns every forcing type (conditions, tides, BGC, chlorophyll, runoff, CICE, WW3, StreamYears, ...), which compsets they apply to, their `configure_forcings` kwargs, and their `process_forcings` flags |
| `list_templates` | `crocodash template --list-notebooks` — CrocoGallery notebook IDs to start a case from |
| `get_case_status` | Given a `case_dir` (inputdir), returns step completion state: which files exist, what config JSON contains, which steps are done |
| `dump_case` | Given a `caseroot`, reconstructs a YAML-equivalent config dict from the case's own state files (`crocodash_state.json`, `extract_forcings/config.json`) |

### Case Setup Workflow

| Tool | Maps to | Key inputs |
|------|---------|------------|
| `create_case` | `crocodash create --config x.yaml` (inline dict instead of a file) | grid/topo/vgrid/case/forcings config dict |
| `create_case_from_yaml_file` | `crocodash create --config x.yaml` | path to a YAML file (e.g. from `dump_case` or a bundle) |
| `create_direct_case` | Direct `Grid`/`Topo`/`VGrid`/`Case` construction | polar, rotated (`Grid.from_center`) or cut-from-global-supergrid grids, spoon/bowl bathymetry, or `reuse_forcings_from` — everything `recipe.py`'s YAML schema doesn't support; `forcing_kwargs` passes any `configure_forcings` kwarg through |
| `process_forcings` | `crocodash process --all/--<flag>/--skip` / `CrocoDash.forcing.driver.run_workflow` | `case_dir` (inputdir), optional `components`/`skip`/`preview` — reads `extract_forcings/config.json` |
| `write_template` | `crocodash template` | output path (.yaml/.ipynb/.py, or pbs), gallery notebook ID, machine |

### Sharing (`crocodash bundle`/`fork`/`duplicate`)

| Tool | Maps to | Key inputs |
|------|---------|------------|
| `bundle_case` | `crocodash bundle` / `CrocoDash.shareable.CaseBundle` | `caseroot`, output dir |
| `fork_case` | `crocodash fork` / `CrocoDash.shareable.ForkBundle.fork` with its interactive YAML review skipped | bundle dir, new caseroot/inputdir, cesmroot/machine/project |
| `duplicate_case_tool` | `crocodash duplicate` / `CrocoDash.shareable.duplicate_case` | source caseroot, new caseroot/inputdir |

### Utilities

| Tool | Purpose |
|------|---------|
| `validate_domain` | Check that a lon/lat bounding box + resolution is valid before committing to grid creation |
| `validate_case_config` | Wraps `CrocoDash.recipe.validate_config_structure` — fast structural check of a `create_case` config dict, without doing any real work |

---

## Resources

| URI | Content |
|-----|---------|
| `crocodash://products` | Live list of registered products and their metadata |
| `crocodash://forcing-configs` | Live list of forcing types and their process flags |
| `crocodash://case/{case_dir}/config` | The case's current `extract_forcings/config.json` |
| `crocodash://case/{case_dir}/status` | Step completion status (case created?, forcings configured?, forcings processed?) |

---

## Repo Structure

```
MCP_CrocoDash/
  server.py            # launch shim for `python server.py` from a checkout
  crocodash_mcp/       # uniquely named so it can share an env with the other CROCODILE MCP servers
    server.py          # FastMCP app — tool + resource registration (console script: crocodash-mcp)
    tools/
      discovery.py     # list_products, list_forcing_configs
      grid.py          # validate_domain
      case.py          # create_case, create_case_from_yaml_file, create_direct_case, process_forcings, dump_case
      status.py        # get_case_status, validate_case_config
      shareable.py     # bundle_case, fork_case, duplicate_case_tool
      template.py      # list_templates, write_template
      _bg_worker.py    # background/PBS worker spawned by process_forcings(background=...)
    resources.py       # URI handlers for crocodash:// scheme
  pyproject.toml
```

---

## Out of Scope (v1)

- Compilation / job submission (CIME domain, machine-specific)
- Visualization of grids or outputs
- Multi-case orchestration
- Anything in `visualCaseGen` beyond what `Case.__init__` already calls
