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

## Connecting to OpticStudio (step by step)

Extension mode attaches the MCP server to your **already-running** OpticStudio
GUI, so every edit Claude makes appears live in the Lens Data Editor. This is the
mode to use (standalone/headless needs a license tier this install doesn't grant —
see caveats).

**In OpticStudio (the GUI):**

1. **Launch OpticStudio** and open the system you want to work on (or start a new
   one). Any lens file is fine — the bridge attaches to whatever is open.
2. Click the **`Programming`** tab in the ribbon at the top.
3. In that tab, click **`Interactive Extension`**.
4. A small dialog appears — *"Zemax is listening for the ZOS-API client to
   connect…"* (it shows a status like **Not Connected / Waiting**). **Leave this
   dialog open.** OpticStudio is now listening for exactly one connection.

**In Claude Code (this session):**

5. Ask Claude to connect — e.g. *"connect to zemax"* — which calls
   **`zemax_connect`** (`mode="extension"`, the default). On success it returns
   the license **edition, serial, samples dir, and the currently open file**, and
   the OpticStudio dialog flips to **Connected**.
6. That's it — now ask Claude to open a file, edit surfaces, trace rays, optimize,
   etc. Changes show up live in the GUI. Use **`zemax_save_file`** to write the
   result back to disk.

**Quick self-test instead of step 5** (from WSL, optional):

```bash
python.exe -m zemax_mcp.connection extension   # prints edition/serial if the arm worked
```

### Things that trip people up

- **One arm per click.** *Interactive Extension* accepts a **single**
  `ConnectAsExtension` and then stops listening. The long-lived MCP server grabs
  it once and holds it for the whole session, so you normally click **once**. If
  the connection drops (or you ran a one-shot script), **re-click
  `Interactive Extension`** before reconnecting.
- **"License is not valid for ZOS-API use" on connect** almost always means the
  dialog isn't armed — go back to *Programming ▸ Interactive Extension* and click
  it, then retry `zemax_connect`.
- **Don't close the dialog** while you want the live link; closing it drops the
  connection.
- **Edits are live and unsaved.** Nothing is written to `.zmx`/`.zos` until you
  call `zemax_save_file` (which can Save-As to a path). Save before closing.
- **Standalone/headless mode** (`zemax_connect` with `mode="standalone"`) is the
  no-GUI alternative, but it needs a Professional/Premium/Enterprise API license;
  on this install `CreateNewApplication()` reports an invalid API license, so use
  extension mode.

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
| `examples/beam_stabilization.py` | Closed-loop **2-FSM aimpoint stabilization** on a real reflective telescope (converts it to NSC, senses the return, drives two FSMs). See [`docs/beam-stabilization-demo.md`](docs/beam-stabilization-demo.md). |
| `examples/atmosphere_1080nm_2km.py` | **Atmospheric channel** for the 1.08 µm beam over 2 km — absorption (link budget) + scintillation (aperture averaging, fade) + turbulence tilt feeding the FSM loop, plus `delivered_power()` with first-order thermal blooming. See [`docs/atmosphere-1080nm-2km.md`](docs/atmosphere-1080nm-2km.md). |
| `examples/blooming_split_step.py` | **Wave-optics split-step (BPM)** thermal-blooming model — propagates the complex field to the 2 km target with a nonlinear thermal phase; the trustworthy blooming Strehl / beam-bending numbers. Validated vs diffraction theory + the link budget. |
| `requirements.txt` | Python deps to install under **Windows** Python: `pythonnet`, `mcp[cli]` (optional `zospy`). |
| `setup/INSTALL.md` | Full setup + troubleshooting. |

---

## Quick start

```powershell
# 1. Install deps under Windows Python (3.11–3.13 recommended)
python -m pip install -r requirements.txt      # or: pip install pythonnet "mcp[cli]"

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
`zemax_spot_rms` · `zemax_analysis_series` (x/y curves for ray fans / MTF) ·
`zemax_batch_ray_trace` (fast normalized ray trace)
**Multi-config:** `zemax_mce_summary` · `zemax_mce_add_config` · `zemax_mce_add_operand`
**Tolerancing:** `zemax_tde_summary` · `zemax_tolerance_wizard` · `zemax_run_tolerancing`
**Non-sequential:** `zemax_nsc_summary` · `zemax_nsc_ray_trace` · `zemax_nsc_detector_data`
**Advanced:** `zemax_eval` (gated escape hatch for un-wrapped calls)

The tool set is intentionally extensible — the
[function-to-feature catalog](docs/function-to-feature-catalog.md) is the roadmap
for wrapping the rest of the API.

---

## Status & caveats

- **v0.1 — verified live.** All 31 MCP tools were run end-to-end against
  **OpticStudio 2025 R2.02 (Enterprise)** in extension mode — including a full
  build→optimize loop (merit 1.03→0.08, RMS spot 1453→56 µm), batch ray tracing,
  ray-fan/MTF curve extraction, multi-config edits, a 21-operand tolerance wizard
  + Monte-Carlo run, and NSC detector readout on the `Diode sample` (50×50, total
  flux 15625). A member-name probe confirmed 33/35 members on the first pass; the
  two misses were fixed and re-verified: RMS spot is
  `SpotData.GetRMSSpotSizeFor(field, 0)` (polychromatic), the wavefront analysis
  IDM is `WavefrontMap` (not `Wavefront`). Tool output is JSON-sanitized so
  non-finite values (e.g. a planar surface's `radius = Infinity`) serialize safely.
- **Session health.** Many rapid `connect → New() → disconnect` cycles can wedge
  the OpticStudio session (`New_Analysis` returns `None`; on-axis rays report
  spurious errors). The long-lived MCP server does one connection and holds it, so
  it won't hit this; the analysis tools now raise a clear "restart & reconnect"
  message if they detect the wedged state.
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
