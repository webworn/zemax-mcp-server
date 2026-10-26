"""
zemax_mcp.server
================

An MCP (Model Context Protocol) server that exposes Ansys Zemax OpticStudio to
Claude Code as a set of named, self-describing tools. Each tool couples one or a
few ZOS-API operations with a clear feature description, so Claude can discover
and call them directly ("interact with Zemax from input to output").

Run it under WINDOWS Python (pythonnet requirement):

    python.exe -m zemax_mcp.server

Register it with Claude Code (from WSL) e.g.:

    claude mcp add zemax -- python.exe -m zemax_mcp.server

Design notes
------------
* One long-lived ZemaxSession is held in the server process. `zemax_connect`
  (re)establishes it; every other tool operates on it.
* Tools return JSON-serializable dicts/strings so Claude gets structured output.
* `zemax_eval` is an advanced escape hatch: it runs a Python expression/statement
  against the live session (`z`, `TheSystem`, `ZOSAPI` in scope) for anything not
  yet wrapped. Disable it by setting ZEMAX_MCP_ALLOW_EVAL=0 if you want a locked-down
  surface.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Optional

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "The 'mcp' package is required: `pip install \"mcp[cli]\"` under Windows Python."
    ) from exc

from .connection import ZemaxSession, connect, ZemaxConnectionError

mcp = FastMCP("zemax")

# --------------------------------------------------------------------------- state
_session: Optional[ZemaxSession] = None


def _require() -> ZemaxSession:
    if _session is None or _session.system is None:
        raise RuntimeError("Not connected. Call zemax_connect first.")
    return _session


def _sanitize(v):
    """Make values valid-JSON-safe. Non-finite floats (inf/nan) aren't legal JSON;
    OpticStudio returns them routinely (a planar surface has radius = +inf), so map
    them to readable string tokens the LLM still understands."""
    if isinstance(v, float):
        if math.isnan(v):
            return "NaN"
        if math.isinf(v):
            return "Infinity" if v > 0 else "-Infinity"
        return v
    if isinstance(v, dict):
        return {k: _sanitize(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_sanitize(x) for x in v]
    return v


def _ok(**kw) -> dict:
    return _sanitize({"ok": True, **kw})


def _new_analysis(z: ZemaxSession, analysis_type: str):
    """Open an analysis by AnalysisIDM name, guarding the None return that means the
    OpticStudio session is wedged (typically after many rapid reconnect cycles)."""
    idm = getattr(z.zosapi.Analysis.AnalysisIDM, analysis_type)
    a = z.system.Analyses.New_Analysis(idm)
    if a is None:
        raise RuntimeError(
            f"New_Analysis('{analysis_type}') returned None. The OpticStudio session "
            "looks unhealthy (often caused by many rapid connect/New/disconnect cycles). "
            "Restart OpticStudio, re-arm Programming > Interactive Extension, then reconnect."
        )
    return a


def _close_tool(t) -> bool:
    """Cancel (if running) and close a ZOS-API tool. Returns False if there was none."""
    if t is None:
        return False
    if t.IsRunning:
        t.Cancel()
        t.WaitForCompletion()
    t.Close()
    return True


@contextmanager
def _tool(z: ZemaxSession, opener: str, owner=None):
    """Open a ZOS-API tool (Tools.<opener>()) and always close it afterwards.

    ZOS-API allows ONE open tool at a time: a handle leaked by an earlier error (or
    a zemax_eval snippet) makes every later Tools.Open* return None — the 'wedged
    session' symptom. So any stale CurrentTool is closed before opening."""
    tools = z.system.Tools
    _close_tool(tools.CurrentTool)
    t = getattr(owner or tools, opener)()
    if t is None:
        raise RuntimeError(
            f"{opener}() returned None even after closing stale tools. Restart the "
            "OpticStudio GUI, re-arm Interactive Extension, then zemax_connect again."
        )
    try:
        yield t
    finally:
        _close_tool(t)


def _apply_settings(a, analysis_type: str, settings: dict) -> None:
    """Change analysis settings via MODIFYSETTINGS keywords (ZPL help > Keywords >
    MODIFYSETTINGS, e.g. MTF_MAXF). Works for every analysis type."""
    s = a.GetSettings()
    cfg = os.path.join(tempfile.gettempdir(), f"zemax_mcp_{analysis_type}.cfg")
    s.SaveTo(cfg)
    bad = [k for k, v in settings.items() if not s.ModifySettings(cfg, k, str(v))]
    if bad:
        raise ValueError(f"ModifySettings rejected {bad} for {analysis_type}; valid "
                         "keywords are listed under MODIFYSETTINGS in the ZPL help.")
    s.LoadFrom(cfg)


@contextmanager
def _analysis(z: ZemaxSession, analysis_type: str, settings: Optional[dict] = None):
    """Open an analysis, apply settings, run it, yield its results, always close it
    (unclosed analyses pile up as windows in the GUI)."""
    a = _new_analysis(z, analysis_type)
    try:
        if settings:
            _apply_settings(a, analysis_type, settings)
        a.ApplyAndWaitForCompletion()
        yield a.GetResults()
    finally:
        a.Close()


def _names(o) -> list[str]:
    """Public non-method member names of a .NET object or enum (for error hints)."""
    out = []
    for n in dir(o):
        try:
            if n[:1].isupper() and not callable(getattr(o, n)):
                out.append(n)
        except Exception:
            pass
    return out


def _set_props(obj, props: Optional[dict]) -> None:
    """Set .NET properties by name. A string given for a non-string property is taken
    as a member name of that property's enum (e.g. Criterion="Spot")."""
    for k, v in (props or {}).items():
        if not hasattr(obj, k):
            raise ValueError(f"Unknown property {k!r}; available: {_names(obj)}")
        cur = getattr(obj, k)
        if isinstance(v, str) and not isinstance(cur, str):
            if not hasattr(type(cur), v):
                raise ValueError(f"{k}={v!r}: choose one of {_names(type(cur))}")
            v = getattr(type(cur), v)
        elif isinstance(cur, int) and not isinstance(cur, bool) and not isinstance(v, bool):
            v = int(v)
        setattr(obj, k, v)


def _get_cell(cell):
    """Read an editor cell with the accessor its data type needs."""
    dt = str(cell.DataType)
    if dt.endswith("Integer"):
        return int(cell.IntegerValue)
    if dt.endswith("Double"):
        return float(cell.DoubleValue)
    return str(cell.Value)


def _set_cell(cell, v) -> None:
    """Write an editor cell with the accessor its data type needs."""
    dt = str(cell.DataType)
    if dt.endswith("Integer"):
        cell.IntegerValue = int(v)
    elif dt.endswith("Double"):
        cell.DoubleValue = float(v)
    else:
        cell.Value = str(v)


_CELLS = {"radius": "RadiusCell", "thickness": "ThicknessCell", "conic": "ConicCell",
          "semi_diameter": "SemiDiameterCell", "material": "MaterialCell"}


def _cell(z: ZemaxSession, s, name: str):
    """A surface cell by name: radius|thickness|conic|semi_diameter|material|par<N>."""
    if name.startswith("par"):
        return s.GetSurfaceCell(getattr(z.zosapi.Editors.LDE.SurfaceColumn, "Par" + name[3:]))
    if name not in _CELLS:
        raise ValueError(f"cell must be one of {list(_CELLS)} or par<N>")
    return getattr(s, _CELLS[name])


def _surface_rows(z: ZemaxSession, system, max_par: int = 12) -> list[dict]:
    """The sequential prescription as flat per-surface dicts: geometry, type, stop,
    non-fixed solves (e.g. radius_solve='Variable') and non-zero Par1..Par<max_par>."""
    lde = system.LDE
    rows = []
    for i in range(lde.NumberOfSurfaces):
        s = lde.GetSurfaceAt(i)
        row = {"surf": i, "type": str(s.TypeName), "comment": str(s.Comment),
               "radius": float(s.Radius), "thickness": float(s.Thickness),
               "material": str(s.Material), "semi_dia": float(s.SemiDiameter),
               "conic": float(s.Conic), "stop": bool(s.IsStop)}
        for name in ("radius", "thickness", "conic", "material"):
            solve = str(getattr(s, _CELLS[name]).Solve).split(".")[-1]
            if solve not in ("Fixed", "None"):
                row[name + "_solve"] = solve
        for n in range(1, max_par + 1):
            try:
                v = _get_cell(_cell(z, s, f"par{n}"))
            except Exception:
                continue
            if v not in (0, ""):
                row[f"par{n}"] = v
        rows.append(row)
    return rows


def _system_state(system) -> dict:
    """System-level data compared by zemax_diff."""
    sd = system.SystemData
    wl, f = sd.Wavelengths, sd.Fields
    return {
        "aperture": [str(sd.Aperture.ApertureType), float(sd.Aperture.ApertureValue)],
        "wavelengths": [float(wl.GetWavelength(i).Wavelength)
                        for i in range(1, wl.NumberOfWavelengths + 1)],
        "fields": [[float(f.GetField(i).X), float(f.GetField(i).Y)]
                   for i in range(1, f.NumberOfFields + 1)],
        "merit": float(system.MFE.CalculateMeritFunction()),
    }


def _same(x, y) -> bool:
    """Equality that ignores file round-trip float noise."""
    if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        return len(x) == len(y) and all(_same(a, b) for a, b in zip(x, y))
    if isinstance(x, float) and isinstance(y, float):
        return math.isclose(x, y, rel_tol=1e-9, abs_tol=1e-12)
    return x == y


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]


def _out_dir(z: ZemaxSession, sub: str) -> str:
    d = os.path.join(str(z.application.ZemaxDataDir), "MCP", sub)
    os.makedirs(d, exist_ok=True)
    return d


def _wsl(path: str) -> str:
    """C:\\x\\y -> /mnt/c/x/y, so Claude (running in WSL) can Read the file."""
    if path[1:2] != ":":
        return path
    return "/mnt/" + path[0].lower() + path[2:].replace("\\", "/")


def _snapshot(z: ZemaxSession, label: str = "") -> str:
    """Save a copy of the live system to <ZemaxDataDir>\\MCP\\snapshots. Uses
    CopySystem so the user's own file (and SystemFile) is never touched."""
    stem = os.path.splitext(os.path.basename(str(z.system.SystemFile)))[0] or "untitled"
    stem = re.sub(r"_\d{8}-\d{6}-\d{3}.*$", "", stem)  # restored snapshot: drop old stamp
    name = "_".join(p for p in (stem, _stamp(), label) if p)
    path = os.path.join(_out_dir(z, "snapshots"), name + ".zmx")
    copy = z.system.CopySystem()
    try:
        copy.SaveAs(path)
    finally:
        copy.Close(False)
    return path


# ====================================================================== CONNECTION
@mcp.tool()
def zemax_connect(mode: str = "extension") -> dict:
    """Connect to OpticStudio.

    mode="extension": attach to the ALREADY-RUNNING OpticStudio GUI (edits appear
    live). First enable Programming > Interactive Extension in OpticStudio.
    mode="standalone": launch a fresh HEADLESS OpticStudio (needs Pro/Premium license).
    Returns license edition, serial, and current system info.
    """
    global _session
    if _session is not None:
        _session.close()
        _session = None
    _session = connect(mode)  # type: ignore[arg-type]
    return _ok(**_session.info())


@mcp.tool()
def zemax_disconnect(save: bool = False) -> dict:
    """Drop the ZOS-API connection. In standalone mode this closes the headless process."""
    global _session
    if _session is not None:
        _session.close(save=save)
        _session = None
    return _ok(disconnected=True)


@mcp.tool()
def zemax_info() -> dict:
    """Report the current connection: mode, license edition, serial, open file, seq/nonseq."""
    return _ok(**_require().info())


# ==================================================================== FILE / SYSTEM
@mcp.tool()
def zemax_new_system(sequential: bool = True) -> dict:
    """Start a new, empty optical system. sequential=False starts in non-sequential mode."""
    z = _require()
    z.system.New(False)
    if not sequential:
        z.system.MakeNonSequential()
    return _ok(mode="sequential" if sequential else "non-sequential")


@mcp.tool()
def zemax_open_file(path: str) -> dict:
    """Open a .zmx / .zos lens file (Windows path)."""
    z = _require()
    z.system.LoadFile(path, False)
    return _ok(file=str(z.system.SystemFile))


@mcp.tool()
def zemax_save_file(path: str = "") -> dict:
    """Save the current system. If path is given, Save-As to that path."""
    z = _require()
    if path:
        z.system.SaveAs(path)
    else:
        z.system.Save()
    return _ok(file=str(z.system.SystemFile))


@mcp.tool()
def zemax_set_aperture(aperture_type: str = "EntrancePupilDiameter",
                       value: float = 10.0) -> dict:
    """Set the System Aperture.

    aperture_type is a ZOSAPI SystemData.ZemaxApertureType name, e.g.
    'EntrancePupilDiameter', 'ImageSpaceFNum', 'ObjectSpaceNA', 'FloatByStopSize'.
    value is the aperture value in the type's units.
    """
    z = _require()
    ZOSAPI = z.zosapi
    ap = z.system.SystemData.Aperture
    ap.ApertureType = getattr(ZOSAPI.SystemData.ZemaxApertureType, aperture_type)
    ap.ApertureValue = float(value)
    return _ok(aperture_type=aperture_type, value=value)


@mcp.tool()
def zemax_set_wavelengths(wavelengths_um: list[float], primary: int = 1) -> dict:
    """Replace the wavelength set with the given values (in micrometers).

    'primary' is the 1-based index of the primary wavelength.
    """
    z = _require()
    wl = z.system.SystemData.Wavelengths
    while wl.NumberOfWavelengths > 1:
        wl.RemoveWavelength(wl.NumberOfWavelengths)
    wl.GetWavelength(1).Wavelength = float(wavelengths_um[0])
    for w in wavelengths_um[1:]:
        wl.AddWavelength(float(w), 1.0)
    wl.GetWavelength(primary).MakePrimary()
    return _ok(count=len(wavelengths_um), primary=primary)


@mcp.tool()
def zemax_set_fields(field_type: str, points: list[list[float]]) -> dict:
    """Set field points. field_type is 'Angle', 'ObjectHeight', 'ParaxialImageHeight',
    or 'RealImageHeight'. points is a list of [x, y] field coordinates.
    """
    z = _require()
    ZOSAPI = z.zosapi
    f = z.system.SystemData.Fields
    f.SetFieldType(getattr(ZOSAPI.SystemData.FieldType, field_type))
    while f.NumberOfFields > 1:
        f.RemoveField(f.NumberOfFields)
    f.GetField(1).X, f.GetField(1).Y = float(points[0][0]), float(points[0][1])
    for (x, y) in points[1:]:
        f.AddField(float(x), float(y), 1.0)
    return _ok(field_type=field_type, count=len(points))


# ============================================================ LENS DATA EDITOR (LDE)
@mcp.tool()
def zemax_lde_summary() -> dict:
    """Return the sequential 'prescription' at a glance: per surface its type,
    comment, radius, thickness, material, semi-diameter, conic, stop flag, any
    non-fixed solves (e.g. radius_solve='Variable') and non-zero parameters
    (par1..par12, e.g. asphere terms or coordinate-break decenters/tilts).
    Also returns the lens units."""
    z = _require()
    rows = _surface_rows(z, z.system)
    return _ok(units=str(z.system.SystemData.Units.LensUnits), surfaces=rows,
               count=len(rows))


@mcp.tool()
def zemax_lde_insert_surface(position: int) -> dict:
    """Insert a new surface at the given 0-based position in the Lens Data Editor."""
    z = _require()
    z.system.LDE.InsertNewSurfaceAt(position)
    return _ok(inserted_at=position, count=z.system.LDE.NumberOfSurfaces)


@mcp.tool()
def zemax_lde_remove_surface(position: int) -> dict:
    """Delete the surface at the given 0-based position (not the object or image)."""
    z = _require()
    z.system.LDE.RemoveSurfaceAt(position)
    return _ok(removed=position, count=z.system.LDE.NumberOfSurfaces)


@mcp.tool()
def zemax_lde_set_surface(surface: int, radius: Optional[float] = None,
                          thickness: Optional[float] = None,
                          material: Optional[str] = None,
                          comment: Optional[str] = None,
                          semi_diameter: Optional[float] = None,
                          surface_type: Optional[str] = None,
                          conic: Optional[float] = None,
                          is_stop: Optional[bool] = None,
                          params: Optional[dict[str, float]] = None) -> dict:
    """Edit a surface (0-based index). Any omitted parameter is left unchanged.
    Set material='' for air. Use this to build a prescription surface by surface.

    surface_type is a ZOSAPI SurfaceType name, applied first, e.g. 'Standard',
    'EvenAspheric', 'CoordinateBreak', 'Paraxial', 'Toroidal'. is_stop=True makes
    this the aperture stop. params sets parameter columns by number, e.g.
    {"2": 1e-6} = Even Asphere 4th-order term; for a CoordinateBreak 1/2 =
    decenter X/Y, 3/4 = tilt about X/Y (deg); for Paraxial 1 = focal length."""
    z = _require()
    s = z.system.LDE.GetSurfaceAt(surface)
    if surface_type is not None:
        s.ChangeType(s.GetSurfaceTypeSettings(
            getattr(z.zosapi.Editors.LDE.SurfaceType, surface_type)))
    if conic is not None:
        s.Conic = float(conic)
    if is_stop is not None:
        s.IsStop = bool(is_stop)
    for k, v in (params or {}).items():
        _set_cell(_cell(z, s, "par" + str(k).removeprefix("par")), v)
    if radius is not None:
        s.Radius = float(radius)
    if thickness is not None:
        s.Thickness = float(thickness)
    if material is not None:
        s.Material = material
    if comment is not None:
        s.Comment = comment
    if semi_diameter is not None:
        s.SemiDiameter = float(semi_diameter)
    return _ok(surface=surface)


@mcp.tool()
def zemax_set_variable(surface: int, cell: str = "thickness", state: str = "variable",
                       catalog: str = "") -> dict:
    """Make a Lens Data cell a VARIABLE for optimization (what the optimizer may
    change), or state='fixed' to freeze it again.

    cell: 'radius' | 'thickness' | 'conic' | 'semi_diameter' | 'material' | 'par<N>'
    (e.g. 'par2' = Even Asphere 4th-order term). 'material' + variable puts a glass
    substitution solve on it (optionally limited to `catalog`, e.g. 'SCHOTT') so
    Hammer/Global optimization can swap real glasses."""
    if state not in ("variable", "fixed"):
        raise ValueError("state must be 'variable' or 'fixed'")
    z = _require()
    c = _cell(z, z.system.LDE.GetSurfaceAt(surface), cell)
    if state == "fixed":
        c.MakeSolveFixed()
    elif cell == "material":
        solve = c.CreateSolveType(z.zosapi.Editors.SolveType.MaterialSubstitute)
        if catalog:
            solve._S_MaterialSubstitute.Catalog = catalog
        c.SetSolveData(solve)
    else:
        c.MakeSolveVariable()
    return _ok(surface=surface, cell=cell, state=state)


@mcp.tool()
def zemax_set_solve(surface: int, cell: str, solve_type: str,
                    props: Optional[dict] = None) -> dict:
    """Put a SOLVE on a Lens Data cell: a value OpticStudio computes from a rule.

    cell as in zemax_set_variable. solve_type is a ZOSAPI SolveType name; props set
    that solve's fields. Common uses:
      thickness, 'MarginalRayHeight', {"Height": 0}  -> keep the image at paraxial focus
      radius,    'FNumber', {"FNumber": 4}          -> hold the system at F/4
      par1,      'SurfacePickup', {"Surface": 3, "ScaleFactor": -1}
                 -> mirror coordinate break 3 (undo a decenter after an element)
    """
    z = _require()
    c = _cell(z, z.system.LDE.GetSurfaceAt(surface), cell)
    solve = c.CreateSolveType(getattr(z.zosapi.Editors.SolveType, solve_type))
    data = getattr(solve, "_S_" + solve_type)
    props = dict(props or {})
    # Proven pickup recipe: Surface, then MakePickupFromCurrentColumn, then the rest.
    if "Surface" in props:
        data.Surface = int(props.pop("Surface"))
    if hasattr(data, "MakePickupFromCurrentColumn"):
        data.MakePickupFromCurrentColumn()
    _set_props(data, props)
    c.SetSolveData(solve)
    return _ok(surface=surface, cell=cell, solve=solve_type)


# ================================================================ OPTIMIZATION / MFE
@mcp.tool()
def zemax_merit_wizard(settings: Optional[dict] = None) -> dict:
    """Build the default merit function with the Optimization Wizard. It replaces the
    MFE rows from StartAt on, so a snapshot is saved first (see zemax_restore).

    settings are wizard properties; enum values are given by name, e.g.
      {"Criterion": "Spot", "Reference": "Centroid",
       "UseGlassBoundaryValues": True, "GlassMin": 2, "GlassMax": 20,
       "GlassEdgeThickness": 1, "UseAirBoundaryValues": True, "AirMin": 0.5,
       "AirMax": 1000, "AirEdgeThickness": 0.5}
    The glass/air boundaries are what keep the optimizer from producing lenses that
    cannot be built. "OptimizeForManufacturingYield": True (with
    "ManufacturingYieldWeight") adds high-yield (as-built) optimization.
    A bad key or value returns the valid choices.
    Returns the resulting merit function value."""
    z = _require()
    mfe = z.system.MFE
    wiz = getattr(mfe, "SEQOptimizationWizard2", None) or mfe.SEQOptimizationWizard
    _set_props(wiz, settings)
    snap = _snapshot(z, "pre-wizard")
    wiz.Apply()
    return _ok(settings=settings or {}, merit=float(mfe.CalculateMeritFunction()),
               snapshot=snap)


@mcp.tool()
def zemax_operand(operand: str, params: Optional[list[float]] = None) -> dict:
    """Evaluate ANY merit-function operand without adding it to the MFE, e.g.
    EFFL (focal length), TOTR (total track), WFNO (working F/#), RWRE (RMS wavefront),
    SPHA/COMA/ASTI/FCUR/DIST (Seidel terms per surface), FICL (fiber coupling),
    MTFA (MTF), REAY (real ray Y height).

    params fill the operand's columns in order (Int1, Int2, Data1..Data6; missing
    ones are 0), e.g. EFFL: [0, wave]; SPHA: [surface, wave]; REAY: [surface, wave,
    Hx, Hy, Px, Py]. Column meanings per operand are in the OpticStudio help."""
    z = _require()
    p = (list(params or []) + [0] * 8)[:8]
    v = z.system.MFE.GetOperandValue(
        getattr(z.zosapi.Editors.MFE.MeritOperandType, operand),
        int(p[0]), int(p[1]), *(float(x) for x in p[2:]))
    return _ok(operand=operand, params=p, value=float(v))


@mcp.tool()
def zemax_mfe_summary(max_rows: int = 100, sort_by_contribution: bool = False) -> dict:
    """List Merit Function Editor rows: type, params (Int1, Int2, Data1...), target,
    weight, value and % contribution. sort_by_contribution=True shows which operands
    dominate the merit function, i.e. what the optimizer is fighting."""
    z = _require()
    mfe = z.system.MFE
    merit = float(mfe.CalculateMeritFunction())
    rows = []
    for r in range(1, mfe.NumberOfOperands + 1):
        op = mfe.GetOperandAt(r)
        cells = [op.GetCellAt(c) for c in range(2, 10)]
        # unused columns display blank but their DoubleValue reads as inf
        params = [_get_cell(c) if str(c.Value) else "" for c in cells]
        while params and params[-1] in ("", 0):
            params.pop()
        rows.append({"row": r, "type": str(op.TypeName), "params": params,
                     "target": float(op.Target), "weight": float(op.Weight),
                     "value": float(op.Value), "contribution": float(op.Contribution)})
    if sort_by_contribution:
        # non-optimizing rows (DMFS, BLNK) report NaN contribution: sort them last
        rows.sort(key=lambda x: -x["contribution"]
                  if math.isfinite(x["contribution"]) else 1)
    return _ok(merit=merit, operands=len(rows), truncated=len(rows) > max_rows,
               rows=rows[:max_rows])


@mcp.tool()
def zemax_mfe_set_operand(operand: Optional[str] = None, row: Optional[int] = None,
                          insert: bool = False, params: Optional[list] = None,
                          target: Optional[float] = None,
                          weight: Optional[float] = None) -> dict:
    """Add or edit a Merit Function Editor row. This is how design intent (focal
    length, total track, edge thickness, distortion, ...) is given to the optimizer.

    row=None appends a new row; insert=True inserts a new row AT `row` (put
    constraints above the wizard's DMFS row); otherwise `row` is edited in place.
    operand sets the row type (e.g. 'EFFL', 'TOTR', 'MNET', 'DIMX'); params fill its
    columns in order (Int1, Int2, Data1...). Returns the row's current value."""
    z = _require()
    mfe = z.system.MFE
    if row is None:
        op = mfe.AddOperand()
    elif insert:
        op = mfe.InsertNewOperandAt(int(row))
    else:
        op = mfe.GetOperandAt(int(row))
    if operand:
        op.ChangeType(getattr(z.zosapi.Editors.MFE.MeritOperandType, operand))
    for i, v in enumerate(params or []):
        _set_cell(op.GetCellAt(2 + i), v)
    if target is not None:
        op.Target = float(target)
    if weight is not None:
        op.Weight = float(weight)
    mfe.CalculateMeritFunction()
    return _ok(row=op.OperandNumber, type=str(op.TypeName), target=float(op.Target),
               weight=float(op.Weight), value=float(op.Value))


@mcp.tool()
def zemax_mfe_remove(row: Optional[int] = None, count: int = 1) -> dict:
    """Delete `count` MFE rows starting at `row`. row=None clears the whole merit
    function (a snapshot is saved first)."""
    z = _require()
    mfe = z.system.MFE
    snap = None
    if row is None:
        snap = _snapshot(z, "pre-mfe-clear")
        mfe.DeleteAllRows()
    else:
        mfe.RemoveOperandsAt(int(row), int(count))
    return _ok(operands=mfe.NumberOfOperands, snapshot=snap)


@mcp.tool()
def zemax_merit_value() -> dict:
    """Return the current merit function value (lower is better)."""
    z = _require()
    return _ok(merit=float(z.system.MFE.CalculateMeritFunction()))


_CYCLES = {0: "Automatic", 1: "Fixed_1_Cycle", 5: "Fixed_5_Cycles",
           10: "Fixed_10_Cycles", 50: "Fixed_50_Cycles"}
_OPTIMIZERS = {"local": "OpenLocalOptimization", "hammer": "OpenHammerOptimization",
               "global": "OpenGlobalOptimization"}


@mcp.tool()
def zemax_optimize(method: str = "local", cores: int = 8, cycles: int = 0,
                   seconds: float = 60.0) -> dict:
    """Run optimization on the current variables + merit function.

    method='local'  -> Damped Least Squares local optimization (fast, local minimum).
    method='hammer' -> Hammer optimization (escapes local minima via perturbation).
    method='global' -> Global Search (broad exploration; long-running).
    cycles (local): 0 = automatic, or 1 / 5 / 10 / 50 fixed cycles.
    seconds (hammer/global): run time; the search is then stopped and the best
    design found so far is kept.
    The pre-optimization design is snapshotted first (see zemax_restore).
    Returns before/after merit values.
    """
    if method not in _OPTIMIZERS:
        raise ValueError(f"Unknown method: {method}; use one of {list(_OPTIMIZERS)}")
    if cycles not in _CYCLES:
        raise ValueError(f"cycles must be one of {sorted(_CYCLES)}")
    z = _require()
    Opt = z.zosapi.Tools.Optimization
    mfe = z.system.MFE
    snap = _snapshot(z, "pre-optimize")
    before = float(mfe.CalculateMeritFunction())
    with _tool(z, _OPTIMIZERS[method]) as opt:
        opt.NumberOfCores = int(cores)
        if method == "local":
            opt.Algorithm = Opt.OptimizationAlgorithm.DampedLeastSquares
            opt.Cycles = getattr(Opt.OptimizationCycles, _CYCLES[cycles])
            opt.RunAndWaitForCompletion()
        else:
            opt.RunAndWaitWithTimeout(float(seconds))  # _tool cancels it if still running
    after = float(mfe.CalculateMeritFunction())
    out: dict[str, Any] = {"method": method, "merit_before": before,
                           "merit_after": after, "improved": after < before,
                           "snapshot": snap}
    if after == before:
        out["note"] = "Merit unchanged: are any cells variable (zemax_set_variable)?"
    return _ok(**out)


@mcp.tool()
def zemax_quick_focus() -> dict:
    """Run Quick Focus (adjusts back focal distance to minimize the spot/RMS)."""
    z = _require()
    with _tool(z, "OpenQuickFocus") as qf:
        qf.RunAndWaitForCompletion()
    return _ok(merit=float(z.system.MFE.CalculateMeritFunction()))


# ======================================================================= ANALYSES
@mcp.tool()
def zemax_run_analysis(analysis_type: str, settings: Optional[dict] = None,
                       max_points: int = 16) -> dict:
    """Open an analysis by its ZOSAPI AnalysisIDM name, run it, and return the numeric
    results if available. Examples: 'RayFan', 'StandardSpot', 'FftPsf', 'FftMtf',
    'WavefrontMap', 'SeidelDiagram'.

    settings: MODIFYSETTINGS keywords {keyword: value} (ZPL help > MODIFYSETTINGS),
    e.g. {"MTF_MAXF": 100}. Grid analyses (PSF, wavefront map) return min/max/mean/
    std over all grid points, the grid extent and labels, and the grid down-sampled
    to about max_points x max_points. Series analyses (ray fan, MTF) report their
    series count: use zemax_analysis_series for the curves. For anything else use
    zemax_analysis_text.
    """
    z = _require()
    out: dict[str, Any] = {"analysis": analysis_type}
    with _analysis(z, analysis_type, settings) as res:
        g = res.GetDataGrid(0) if res.NumberOfDataGrids > 0 else None
        vals = g.Values if g is not None else None  # Double[,]
        if vals is not None and vals.GetLength(0) == 0:
            out["note"] = ("The analysis returned an empty grid; zemax_analysis_text "
                           "shows its warnings (e.g. sampling too low).")
        elif vals is not None:
            n0, n1 = vals.GetLength(0), vals.GetLength(1)
            finite = [v for v in (float(x) for x in vals) if math.isfinite(v)]
            mean = sum(finite) / len(finite) if finite else float("nan")
            s0, s1 = max(1, n0 // max_points), max(1, n1 // max_points)
            out["grid"] = {
                "shape": [n0, n1], "nx": int(g.Nx), "ny": int(g.Ny),
                "min_x": float(g.MinX), "min_y": float(g.MinY),
                "dx": float(g.Dx), "dy": float(g.Dy),
                "x_label": str(g.XLabel or ""), "y_label": str(g.YLabel or ""),
                "value_label": str(g.ValueLabel or ""),
                "min": min(finite, default=float("nan")),
                "max": max(finite, default=float("nan")), "mean": mean,
                "std": (math.sqrt(sum((v - mean) ** 2 for v in finite) / len(finite))
                        if finite else float("nan")),
                "step": [s0, s1],
                "values": [[float(vals[i, j]) for j in range(0, n1, s1)]
                           for i in range(0, n0, s0)],
            }
        elif res.NumberOfDataSeries > 0:
            out["data_series_count"] = res.NumberOfDataSeries
            out["note"] = ("Series (x/y) analysis; call zemax_analysis_series "
                           "to get the actual curve values.")
        else:
            out["note"] = "No numeric grid/series exposed; use zemax_analysis_text."
    return _ok(**out)


@mcp.tool()
def zemax_analysis_text(analysis_type: str, settings: Optional[dict] = None,
                        max_chars: int = 20000) -> dict:
    """Run ANY analysis and return its full text output (what the analysis window's
    Text tab shows). This is the universal way to read results, e.g.
    'SeidelCoefficients' (per-surface aberration contributions),
    'ZernikeStandardCoefficients', 'StandardSpot', 'FftMtf'.
    settings: MODIFYSETTINGS keywords as in zemax_run_analysis.
    Long reports are cut at max_chars (see 'truncated' and 'length')."""
    z = _require()
    path = os.path.join(tempfile.gettempdir(), f"zemax_mcp_{analysis_type}.txt")
    if os.path.exists(path):
        os.remove(path)
    with _analysis(z, analysis_type, settings) as res:
        res.GetTextFile(path)
    if not os.path.isfile(path):
        raise RuntimeError(f"{analysis_type} produced no text output.")
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    elif b"\x00" in raw[:200]:
        text = raw.decode("utf-16-le")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:  # ANSI output (e.g. 'µm' as 0xB5)
            text = raw.decode("cp1252", "replace")
    return _ok(analysis=analysis_type, length=len(text),
               truncated=len(text) > max_chars, text=text[:max_chars])


_LAYOUTS = {"cross_section": "OpenCrossSectionExport",
            "shaded": "OpenShadedModelExport",
            "nsc_shaded": "OpenNSCShadedModelExport"}


@mcp.tool()
def zemax_layout_image(kind: str = "cross_section", width: int = 1600,
                       height: int = 1000, settings: Optional[dict] = None) -> dict:
    """Render the system to a PNG so it can be SEEN: open the returned wsl_path with
    the Read tool. Layout pictures catch sign errors, folded-path mistakes and
    vignetting that numbers hide.

    kind: 'cross_section' (2D sequential), 'shaded' (3D sequential),
    'nsc_shaded' (3D non-sequential). settings are extra export properties, e.g.
    {"StartSurface": 1, "EndSurface": 5, "NumberOfRays": 7}. NumberOfRays defaults
    to 7 per field (the export's own default draws an unreadable ray mesh)."""
    if kind not in _LAYOUTS:
        raise ValueError(f"kind must be one of {list(_LAYOUTS)}")
    z = _require()
    path = os.path.join(_out_dir(z, "images"), f"{kind}_{_stamp()}.png")
    with _tool(z, _LAYOUTS[kind], owner=z.system.Tools.Layouts) as ex:
        ex.SaveImageAsFile = True
        ex.OutputFileName = path
        ex.OutputPixelWidth, ex.OutputPixelHeight = int(width), int(height)
        if hasattr(ex, "NumberOfRays"):
            ex.NumberOfRays = 7
        _set_props(ex, settings)
        ex.RunAndWaitForCompletion()
    if not os.path.isfile(path):
        raise RuntimeError(f"Layout export produced no file at {path}")
    return _ok(kind=kind, path=path, wsl_path=_wsl(path))


@mcp.tool()
def zemax_spot_rms() -> dict:
    """Compute the RMS spot radius per field via a Spot Diagram analysis (quick metric)."""
    z = _require()
    vals = []
    with _analysis(z, "StandardSpot") as res:
        sd = res.SpotData
        try:
            # GetRMSSpotSizeFor(field, wave); wave=0 -> polychromatic (all wavelengths).
            for i in range(1, sd.NumberOfFields + 1):
                vals.append(float(sd.GetRMSSpotSizeFor(i, 0)))
        except Exception:
            pass
    return _ok(rms_spot_by_field=vals)


# ============================================================= NON-SEQUENTIAL (NSC)
@mcp.tool()
def zemax_nsc_summary() -> dict:
    """List Non-Sequential Component Editor objects: index, type, comment, material."""
    z = _require()
    nce = z.system.NCE
    rows = []
    for i in range(1, nce.NumberOfObjects + 1):
        o = nce.GetObjectAt(i)
        rows.append({"obj": i, "type": str(o.TypeName), "comment": str(o.Comment),
                     "material": str(o.Material)})
    return _ok(objects=rows, count=nce.NumberOfObjects)


@mcp.tool()
def zemax_nsc_ray_trace(split: bool = True, scatter: bool = False,
                        clear_detectors: bool = True, rays: int = 100000) -> dict:
    """Run a Non-Sequential ray trace (for illumination / stray-light).
    split=True enables ray splitting (transmit/reflect/absorb child rays);
    scatter=True enables scattering. clear_detectors=True zeroes all detectors
    first so a following zemax_nsc_detector_data read reflects only this trace.
    Returns completion status."""
    z = _require()
    with _tool(z, "OpenNSCRayTrace") as rt:
        if clear_detectors:
            rt.ClearDetectors(0)  # 0 = all detectors
        rt.SplitNSCRays = bool(split)
        rt.ScatterNSCRays = bool(scatter)
        rt.UsePolarization = False
        rt.IgnoreErrors = True
        rt.RunAndWaitForCompletion()
    return _ok(split=split, scatter=scatter, cleared=clear_detectors)


@mcp.tool()
def zemax_nsc_detector_data(object_number: int, data_type: int = 1) -> dict:
    """Read a Non-Sequential DETECTOR object's data after a zemax_nsc_ray_trace.

    object_number is the detector's row in the NCE (see zemax_nsc_summary).
    data_type selects the quantity: 1 = incoherent irradiance/flux (typical),
    2 = coherent irradiance, 3 = coherent phase. Returns detector pixel
    dimensions, total flux, peak pixel value, and a 5x5 corner sample grid.
    """
    z = _require()
    nce = z.system.NCE
    dims = nce.GetDetectorDimensions(object_number)  # (success, nx, ny)
    nx, ny = int(dims[1]), int(dims[2])
    data = nce.GetAllDetectorDataSafe(object_number, int(data_type))  # Double[,]
    r0, r1 = data.GetLength(0), data.GetLength(1)
    total = 0.0
    peak = float("-inf")
    for v in data:
        fv = float(v)
        total += fv
        if fv > peak:
            peak = fv
    sample = [[float(data[a, b]) for b in range(min(r1, 5))]
              for a in range(min(r0, 5))]
    return _ok(object=object_number, data_type=data_type, nx=nx, ny=ny,
               total_flux=total, peak=peak, sample_corner=sample)


# =================================================================== BATCH RAY TRACE
@mcp.tool()
def zemax_batch_ray_trace(rays: list[list[float]], wavelength: int = 1,
                          ray_type: str = "Real", to_surface: int = -1) -> dict:
    """Trace a batch of sequential rays fast and return each ray's state at a surface.

    `rays` is a list of normalized [Hx, Hy, Px, Py]: Hx/Hy are field coordinates
    and Px/Py are pupil coordinates, each in -1..+1. wavelength is the 1-based
    wavelength number. ray_type is 'Real' or 'Paraxial'. to_surface=-1 means the
    image surface. Returns per-ray x,y,z position, L,M,N direction cosines, OPD,
    intensity, and error/vignette codes (err/vignette 0 = OK).
    """
    z = _require()
    ZOSAPI = z.zosapi
    RT = ZOSAPI.Tools.RayTrace
    nsur = (z.system.LDE.NumberOfSurfaces - 1) if to_surface < 0 else int(to_surface)
    rtype = getattr(RT.RaysType, ray_type)
    opd_none = getattr(RT.OPDMode, "None")  # 'None' is a Python keyword -> getattr
    out = []
    with _tool(z, "OpenBatchRayTrace") as brt:
        reader = brt.CreateNormUnpol(len(rays), rtype, nsur)
        reader.ClearData()
        for r in rays:
            hx, hy, px, py = (list(r) + [0.0, 0.0, 0.0, 0.0])[:4]
            reader.AddRay(int(wavelength), float(hx), float(hy),
                          float(px), float(py), opd_none)
        brt.RunAndWaitForCompletion()
        reader.StartReadingResults()
        while True:
            res = reader.ReadNextResult()
            if not res[0]:
                break
            (_s, num, err, vig, x, y, zc, l, m, n, _l2, _m2, _n2, opd, inten) = res
            out.append({"ray": num, "err": err, "vignette": vig,
                        "x": x, "y": y, "z": zc, "L": l, "M": m, "N": n,
                        "opd": opd, "intensity": inten})
    return _ok(surface=nsur, ray_type=ray_type, count=len(out), rays=out)


# ================================================================ ANALYSIS SERIES
@mcp.tool()
def zemax_analysis_series(analysis_type: str, max_points: int = 40,
                          settings: Optional[dict] = None) -> dict:
    """Run an analysis and return its x/y CURVE data (ray fans, MTF-vs-frequency, ...).

    Use this for series analyses where zemax_run_analysis only reports a count.
    Examples: 'RayFan', 'FftMtf', 'FftMtfvsField', 'OpticalPathFan'. For each data
    series it returns the x-axis label, the per-curve series labels (e.g. wavelength
    or field), and decimated x plus y values (down-sampled to ~max_points).
    settings: MODIFYSETTINGS keywords as in zemax_run_analysis.
    """
    z = _require()
    series_out = []
    with _analysis(z, analysis_type, settings) as res:
        for i in range(res.NumberOfDataSeries):
            ds = res.GetDataSeries(i)
            xs = list(ds.XData.Data)
            yd = ds.YData.Data
            npts, ncurves = yd.GetLength(0), yd.GetLength(1)
            step = max(1, npts // max(1, max_points))
            idxs = list(range(0, npts, step))
            labels = [str(x) for x in ds.SeriesLabels]
            curves = [{"label": labels[c] if c < len(labels) else str(c),
                       "y": [float(yd[r, c]) for r in idxs]}
                      for c in range(ncurves)]
            series_out.append({"description": str(ds.Description),
                               "x_label": str(ds.XLabel),
                               "x": [float(xs[r]) for r in idxs],
                               "curves": curves})
    return _ok(analysis=analysis_type, num_series=len(series_out), series=series_out)


# =============================================================== MULTI-CONFIG (MCE)
@mcp.tool()
def zemax_mce_summary() -> dict:
    """List the Multi-Configuration Editor: number of configurations, and for each
    operand its type + value in every configuration (values as display strings)."""
    z = _require()
    mce = z.system.MCE
    nc, no = mce.NumberOfConfigurations, mce.NumberOfOperands
    rows = []
    for r in range(1, no + 1):
        op = mce.GetOperandAt(r)
        vals = []
        for c in range(1, nc + 1):
            try:
                vals.append(str(op.GetOperandCell(c).Value))
            except Exception:
                vals.append(None)
        rows.append({"row": r, "type": str(op.TypeName), "values": vals})
    return _ok(configurations=nc, operands=no, rows=rows)


@mcp.tool()
def zemax_mce_add_config(with_pickups: bool = False) -> dict:
    """Add a new configuration (column) to the MCE. with_pickups=True links the new
    column to the current one via pickup solves."""
    z = _require()
    z.system.MCE.AddConfiguration(bool(with_pickups))
    return _ok(configurations=z.system.MCE.NumberOfConfigurations)


@mcp.tool()
def zemax_mce_add_operand(operand_type: str, param1: int = 0,
                          values: Optional[list[float]] = None) -> dict:
    """Add a multi-configuration operand and optionally set its value per config.

    operand_type is a ZOSAPI MultiConfigOperandType name, e.g. 'THIC' (thickness),
    'GLSS' (glass), 'CRVT' (curvature), 'APER' (system aperture), 'SDIA'
    (semi-diameter). param1 is usually the surface/object number the operand acts on.
    values is a per-configuration list of numeric values (1st entry -> config 1).
    """
    z = _require()
    ZOSAPI = z.zosapi
    mce = z.system.MCE
    mce.AddOperand()
    op = mce.GetOperandAt(mce.NumberOfOperands)
    op.ChangeType(getattr(ZOSAPI.Editors.MCE.MultiConfigOperandType, operand_type))
    if param1:
        try:
            op.Param1 = int(param1)
        except Exception:
            pass
    if values:
        for c, v in enumerate(values, start=1):
            if c <= mce.NumberOfConfigurations:
                try:
                    op.GetOperandCell(c).DoubleValue = float(v)
                except Exception:
                    pass
    return _ok(operand_type=operand_type, row=op.OperandNumber,
               configurations=mce.NumberOfConfigurations)


# ================================================================= TOLERANCING (TDE)
@mcp.tool()
def zemax_tde_summary() -> dict:
    """List the Tolerance Data Editor: each operand's type, comment, and
    min / nominal / max limits plus param1."""
    z = _require()
    tde = z.system.TDE
    rows = []
    for r in range(1, tde.NumberOfOperands + 1):
        op = tde.GetOperandAt(r)
        rows.append({"row": r, "type": str(op.TypeName), "comment": str(op.Comment),
                     "min": float(op.Min), "nominal": float(op.Nominal),
                     "max": float(op.Max), "param1": float(op.Param1)})
    return _ok(operands=tde.NumberOfOperands, rows=rows)


@mcp.tool()
def zemax_tolerance_wizard() -> dict:
    """Build a default tolerance set via the Tolerancing Wizard (radius, thickness,
    decenter, tilt, index/abbe, irregularity, ...). Returns the new operand count."""
    z = _require()
    z.system.TDE.SEQToleranceWizard.Apply()
    return _ok(operands=z.system.TDE.NumberOfOperands)


@mcp.tool()
def zemax_run_tolerancing(monte_carlo_runs: int = 20) -> dict:
    """Run a Monte Carlo tolerancing analysis over the current TDE tolerances.
    Returns whether it succeeded, the number of MC runs, how many statistics rows
    were produced, and the result file path (open it for the full report)."""
    z = _require()
    with _tool(z, "OpenTolerancing") as tol:
        try:
            tol.NumberOfRuns = int(monte_carlo_runs)
        except Exception:
            pass
        tol.RunAndWaitForCompletion()
        out: dict[str, Any] = {"succeeded": bool(tol.Succeeded),
                               "monte_carlo_runs": monte_carlo_runs}
        try:
            out["statistics_rows"] = tol.NumberOfMonteCarloStatistics
        except Exception:
            pass
        try:
            out["result_file"] = str(tol.ResultFilename)
        except Exception:
            pass
    return _ok(**out)


# ====================================================== SNAPSHOTS / UNDO / RECOVERY
@mcp.tool()
def zemax_snapshot(label: str = "") -> dict:
    """Save a restorable copy of the current design to <Zemax data>\\MCP\\snapshots
    (your own file is not touched). zemax_optimize, zemax_merit_wizard,
    zemax_mfe_remove(all) and zemax_restore take one automatically.
    Use the returned path with zemax_diff / zemax_restore."""
    z = _require()
    path = _snapshot(z, label)
    return _ok(path=path, wsl_path=_wsl(path))


@mcp.tool()
def zemax_restore(path: str) -> dict:
    """Undo: load a snapshot (Windows path) back into OpticStudio. The current design
    is snapshotted first, so a restore can itself be undone. Afterwards OpticStudio
    is editing the snapshot file; use zemax_save_file(path=<original_file>) to write
    the restored design back to your own file."""
    z = _require()
    previous = _snapshot(z, "pre-restore")
    original = str(z.system.SystemFile)
    z.system.LoadFile(path, False)
    return _ok(restored=path, previous_state=previous, original_file=original,
               system_file=str(z.system.SystemFile))


@mcp.tool()
def zemax_diff(path: str, other: str = "") -> dict:
    """Show what changed between a snapshot (`path`) and the current design, or
    between two snapshots (`other`). Compares every surface field (radius,
    thickness, material, conic, type, solves, parameters, ...) plus aperture,
    wavelengths, fields and merit value; only differences are returned
    (a = `path`, b = current / `other`). An inserted surface shifts the rows below it."""
    z = _require()
    opened = []

    def load(p):
        s = z.application.LoadNewSystem(p)
        if s is None:
            raise RuntimeError(f"Could not load {p}")
        opened.append(s)
        return s

    try:
        a = load(path)
        b = load(other) if other else z.system
        ra, rb = _surface_rows(z, a), _surface_rows(z, b)
        changes = [{"surf": x["surf"], "field": k, "a": x.get(k), "b": y.get(k)}
                   for x, y in zip(ra, rb)
                   for k in sorted(x.keys() | y.keys())
                   if not _same(x.get(k), y.get(k))]
        sa, sb = _system_state(a), _system_state(b)
        system = {k: {"a": sa[k], "b": sb[k]} for k in sa if not _same(sa[k], sb[k])}
    finally:
        for s in opened:
            s.Close(False)
    return _ok(a=path, b=other or "current", surfaces_a=len(ra), surfaces_b=len(rb),
               surface_changes=changes, system_changes=system)


@mcp.tool()
def zemax_cleanup() -> dict:
    """Recover a stuck session without restarting OpticStudio. Closes a leftover
    open tool (only one ZOS-API tool can be open at a time; a leaked one makes every
    Tools.Open* return None) and closes ALL analysis windows, including any you
    opened yourself in the GUI."""
    z = _require()
    tool_closed = _close_tool(z.system.Tools.CurrentTool)
    an = z.system.Analyses
    n = an.NumberOfAnalyses
    for i in range(n, -1, -1):  # index base is undocumented: sweep n..0
        try:
            an.CloseAnalysis(i)
        except Exception:
            pass
    return _ok(stale_tool_closed=tool_closed, analyses_closed=n - an.NumberOfAnalyses)


# ============================================================== ADVANCED ESCAPE HATCH
@mcp.tool()
def zemax_eval(code: str) -> dict:
    """ADVANCED. Execute a Python snippet against the live session for anything not yet
    wrapped. In scope: `z` (ZemaxSession), `TheSystem`, `TheApplication`, `ZOSAPI`.
    Assign to `result` to return a value. Disabled if ZEMAX_MCP_ALLOW_EVAL=0.

    Example code: "result = TheSystem.LDE.NumberOfSurfaces"
    """
    if os.environ.get("ZEMAX_MCP_ALLOW_EVAL", "1") == "0":
        raise RuntimeError("zemax_eval is disabled (ZEMAX_MCP_ALLOW_EVAL=0).")
    z = _require()
    scope: dict[str, Any] = {
        "z": z, "TheSystem": z.system, "TheApplication": z.application,
        "ZOSAPI": z.zosapi, "result": None,
    }
    exec(code, scope)  # noqa: S102 - intentional, gated escape hatch
    val = scope.get("result")
    try:
        json.dumps(val)
    except TypeError:
        val = repr(val)
    return _ok(result=val)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
