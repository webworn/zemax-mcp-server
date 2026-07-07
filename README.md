# zemax-mcp-server

**Drive Ansys Zemax OpticStudio from Claude Code — live, via the ZOS-API — without touching Zemax's source or binaries.**

This project turns a locally-installed **Ansys Zemax OpticStudio** into a set of
named, self-describing tools that [Claude Code](https://claude.com/claude-code)
(or any MCP client) can call. Each tool couples one ZOS-API operation with a plain
description, so an AI agent can go from *intent* → *optical-design action* →
*numeric result*.

It is **non-invasive**: it only loads the three public ZOS-API .NET assemblies
that ship with OpticStudio (`ZOSAPI.dll`, `ZOSAPI_Interfaces.dll`,
`ZOSAPI_NetHelper.dll`) through [`pythonnet`](https://github.com/pythonnet/pythonnet).
Nothing in the Zemax install is modified.

> Verified against **Ansys Zemax OpticStudio 2025 R2.02**.

---

## Why this exists

Optical design in OpticStudio is a GUI-heavy, expertise-heavy loop. The ZOS-API
exposes almost the entire object model — editors, analyses, optimizers, ray
tracing — to external code. This repo wraps that surface for an AI agent so you
can *converse* your way through a design:

> "Connect in extension mode, set the aperture to F/4, add visible wavelengths,
> build a singlet, make the radii variables, run the wizard, then hammer-optimize
> and tell me the RMS spot."

---

## Architecture

```
┌─────────────────┐   MCP (stdio)   ┌──────────────────────┐   pythonnet/.NET   ┌───────────────────────┐
│  Claude Code     │◄──────────────►│  zemax_mcp.server    │◄─────────────────►│  OpticStudio (ZOS-API) │
│  (WSL / Linux)   │  python.exe     │  (Windows Python)    │   ZOSAPI.dll etc.  │  GUI or headless       │
└─────────────────┘                 └──────────────────────┘                    └───────────────────────┘
        tools:  zemax_connect · zemax_set_aperture · zemax_lde_* · zemax_optimize · zemax_run_analysis · zemax_nsc_* · zemax_eval
```

Two official connection modes, both supported:

| Mode | Call | Use it for |
|---|---|---|
| **Interactive Extension** | `ConnectAsExtension(0)` | Attach to a **running GUI** so edits appear **live**. Enable *Programming ▸ Interactive Extension* first. |
| **Standalone / Headless** | `CreateNewApplication()` | Launch a **headless** OpticStudio for batch/automation (needs Pro/Premium tier). |

> ⚠️ **WSL note:** `pythonnet` loads Windows .NET, so the server runs under
> **Windows Python** (`python.exe`), invoked from Claude Code in WSL. See
> [`setup/INSTALL.md`](setup/INSTALL.md).

---

## Repository layout

| Path | What |
|---|---|
| `zemax_mcp/connection.py` | Non-invasive dual-mode connector (extension + standalone) via `ZOSAPI_NetHelper`. |
| `zemax_mcp/server.py` | The **MCP server** — every ZOS-API feature exposed as a described tool. |
| `docs/function-to-feature-catalog.md` | Master map: **GUI feature ↔ ZOS-API member ↔ what it does ↔ AI use-case**. |
| `skills/sequential-design.md` | Playbook for **sequential** imaging design (aperture/fields/wavelengths → variables/solves → merit function → local/hammer/global optimization → aberration control), with runnable ZOS-API snippets. |
| `skills/non-sequential-design.md` | Playbook for **non-sequential** work (illumination, stray light, scatter, sources/detectors, ray splitting, importance sampling). |
| `examples/smoke_test.py` | End-to-end: build a singlet, optimize, print RMS spot. |
| `setup/INSTALL.md` | Full setup + troubleshooting. |

---

## Quick start

```powershell
# 1. Install deps under Windows Python (3.11–3.13 recommended)
python -m pip install pythonnet "mcp[cli]"

# 2. Smoke-test the connection (open OpticStudio + Programming ▸ Interactive Extension first)
python -m zemax_mcp.connection extension
```

```bash
# 3. Register with Claude Code (from WSL)
claude mcp add zemax -- python.exe -m zemax_mcp.server
```

Then just talk to Claude about your optical system.

---

## Tool surface (v0.1)

**Connection:** `zemax_connect` · `zemax_disconnect` · `zemax_info`
**System:** `zemax_new_system` · `zemax_open_file` · `zemax_save_file` ·
`zemax_set_aperture` · `zemax_set_wavelengths` · `zemax_set_fields`
**Lens Data Editor:** `zemax_lde_summary` · `zemax_lde_insert_surface` ·
`zemax_lde_set_surface` · `zemax_set_variable`
**Optimize:** `zemax_merit_wizard` · `zemax_merit_value` · `zemax_optimize`
(local/hammer/global) · `zemax_quick_focus`
**Analyze:** `zemax_run_analysis` (RayFan, StandardSpot, FftPsf, FftMtf, WavefrontMap, …) ·
`zemax_spot_rms`
**Non-sequential:** `zemax_nsc_summary` · `zemax_nsc_ray_trace`
**Advanced:** `zemax_eval` (gated escape hatch for un-wrapped calls)

The tool set is intentionally extensible — the
[function-to-feature catalog](docs/function-to-feature-catalog.md) is the roadmap
for wrapping the rest of the API.

---

## Status & caveats

- **v0.1 — verified live.** The connection module and every MCP tool were run
  end-to-end against **OpticStudio 2025 R2.02 (Enterprise)** in extension mode.
  A member-name probe confirmed 33/35 API members on the first pass; the two
  misses were fixed and re-verified: RMS spot is `SpotData.GetRMSSpotSizeFor(field, 0)`
  (poly­chromatic), and the wavefront analysis IDM is `WavefrontMap` (not `Wavefront`).
  Tool output is JSON-sanitized so non-finite values (e.g. a planar surface's
  `radius = Infinity`) serialize safely.
- **Extension mode arms one connection per click.** *Programming ▸ Interactive
  Extension* accepts a single `ConnectAsExtension` then stops listening; the
  long-lived MCP server grabs it once and holds it, so this only matters if you
  run multiple one-shot scripts (re-click between each).
- **Standalone/headless** was **not** available on this license despite the
  Enterprise edition — `CreateNewApplication()` succeeds but `IsValidLicenseForAPI`
  is false. Use extension mode.
- Third-party wrappers exist and are worth knowing:
  [**ZOSPy**](https://github.com/MREYE-LUMC/ZOSPy) (MIT, pythonnet, actively
  maintained — recommended for heavier use) and the older COM-based
  [PyZOS](https://github.com/xzos/pyzos) (largely unmaintained since ~2016).

## License

MIT (see project owner).

---

*Built with a cited deep-research pass over the official Ansys/Zemax ZOS-API
documentation and Knowledgebase. Non-invasive by construction.*
