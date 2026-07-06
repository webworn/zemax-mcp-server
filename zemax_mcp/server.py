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
import os
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


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


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
    """Return a compact table of every surface: index, comment, radius, thickness,
    material, semi-diameter. This is the sequential 'prescription' at a glance."""
    z = _require()
    lde = z.system.LDE
    rows = []
    for i in range(1, lde.NumberOfSurfaces + 1):
        s = lde.GetSurfaceAt(i - 1)
        rows.append({
            "surf": i - 1,
            "comment": str(s.Comment),
            "radius": float(s.Radius),
            "thickness": float(s.Thickness),
            "material": str(s.Material),
            "semi_dia": float(s.SemiDiameter),
        })
    return _ok(surfaces=rows, count=lde.NumberOfSurfaces)


@mcp.tool()
def zemax_lde_insert_surface(position: int) -> dict:
    """Insert a new surface at the given 0-based position in the Lens Data Editor."""
    z = _require()
    z.system.LDE.InsertNewSurfaceAt(position)
    return _ok(inserted_at=position, count=z.system.LDE.NumberOfSurfaces)


@mcp.tool()
def zemax_lde_set_surface(surface: int, radius: Optional[float] = None,
                          thickness: Optional[float] = None,
                          material: Optional[str] = None,
                          comment: Optional[str] = None,
                          semi_diameter: Optional[float] = None) -> dict:
    """Edit a surface (0-based index). Any omitted parameter is left unchanged.
    Set material='' for air. Use this to build a prescription surface by surface."""
    z = _require()
    s = z.system.LDE.GetSurfaceAt(surface)
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
def zemax_set_variable(surface: int, cell: str = "thickness") -> dict:
    """Make a Lens Data cell a VARIABLE for optimization. cell is 'radius',
    'thickness', or 'conic'. (Variables are what the optimizer is allowed to change.)"""
    z = _require()
    s = z.system.LDE.GetSurfaceAt(surface)
    col = {"radius": s.RadiusCell, "thickness": s.ThicknessCell, "conic": s.ConicCell}[cell]
    col.MakeSolveVariable()
    return _ok(surface=surface, cell=cell, state="variable")


# ================================================================ OPTIMIZATION / MFE
@mcp.tool()
def zemax_merit_wizard(optimization_goal: str = "RMS_Spot") -> dict:
    """Run the Optimization (Merit Function) Wizard to build a default merit function.
    optimization_goal is informational; the wizard defaults target RMS wavefront/spot.
    Returns the resulting merit function value."""
    z = _require()
    mfe = z.system.MFE
    wizard = mfe.SEQOptimizationWizard
    wizard.Apply()
    return _ok(goal=optimization_goal, merit=float(mfe.CalculateMeritFunction()))


@mcp.tool()
def zemax_merit_value() -> dict:
    """Return the current merit function value (lower is better)."""
    z = _require()
    return _ok(merit=float(z.system.MFE.CalculateMeritFunction()))


@mcp.tool()
def zemax_optimize(method: str = "local", cores: int = 8, cycles: int = 0) -> dict:
    """Run optimization on the current variables + merit function.

    method='local'  -> Damped Least Squares local optimization (fast, local minimum).
    method='hammer' -> Hammer optimization (escapes local minima via perturbation).
    method='global' -> Global Search (broad exploration; long-running).
    cycles=0 means 'automatic' for local. Returns before/after merit values.
    """
    z = _require()
    ZOSAPI = z.zosapi
    mfe = z.system.MFE
    before = float(mfe.CalculateMeritFunction())
    tools = z.system.Tools
    if method == "local":
        opt = tools.OpenLocalOptimization()
        opt.Algorithm = ZOSAPI.Tools.Optimization.OptimizationAlgorithm.DampedLeastSquares
        opt.Cycles = (ZOSAPI.Tools.Optimization.OptimizationCycles.Automatic
                      if cycles == 0 else ZOSAPI.Tools.Optimization.OptimizationCycles.Fixed_1_Cycle)
        opt.NumberOfCores = int(cores)
        opt.RunAndWaitForCompletion()
        opt.Close()
    elif method == "hammer":
        opt = tools.OpenHammerOptimization()
        opt.NumberOfCores = int(cores)
        opt.RunAndWaitWithTimeout(30.0)
        opt.Close()
    elif method == "global":
        opt = tools.OpenGlobalOptimization()
        opt.NumberOfCores = int(cores)
        opt.RunAndWaitWithTimeout(60.0)
        opt.Close()
    else:
        raise ValueError(f"Unknown method: {method}")
    after = float(mfe.CalculateMeritFunction())
    return _ok(method=method, merit_before=before, merit_after=after,
               improved=after < before)


@mcp.tool()
def zemax_quick_focus() -> dict:
    """Run Quick Focus (adjusts back focal distance to minimize the spot/RMS)."""
    z = _require()
    qf = z.system.Tools.OpenQuickFocus()
    qf.RunAndWaitForCompletion()
    qf.Close()
    return _ok(merit=float(z.system.MFE.CalculateMeritFunction()))


# ======================================================================= ANALYSES
@mcp.tool()
def zemax_run_analysis(analysis_type: str) -> dict:
    """Open an analysis by its ZOSAPI AnalysisIDM name, run it, and return the numeric
    results grid if available. Examples: 'RayFan', 'Spot', 'FftPsf', 'FftMtf',
    'Wavefront', 'SeidelDiagram'. Returns headers + data rows when the analysis
    exposes a results data grid; otherwise returns a text summary path.
    """
    z = _require()
    ZOSAPI = z.zosapi
    idm = getattr(ZOSAPI.Analysis.AnalysisIDM, analysis_type)
    a = z.system.Analyses.New_Analysis(idm)
    a.ApplyAndWaitForCompletion()
    res = a.GetResults()
    out: dict[str, Any] = {"analysis": analysis_type}
    try:
        grid = res.GetDataGrid(0)
        nx, ny = grid.Nx, grid.Ny
        out["grid"] = {"nx": nx, "ny": ny,
                       "sample": [[float(grid.Z(ix, iy)) for ix in range(min(nx, 5))]
                                  for iy in range(min(ny, 5))]}
    except Exception:
        out["note"] = "No numeric data grid; use zemax_eval on the analysis for series data."
    a.Close()
    return _ok(**out)


@mcp.tool()
def zemax_spot_rms() -> dict:
    """Compute the RMS spot radius per field via a Spot Diagram analysis (quick metric)."""
    z = _require()
    ZOSAPI = z.zosapi
    a = z.system.Analyses.New_Analysis(ZOSAPI.Analysis.AnalysisIDM.StandardSpot)
    a.ApplyAndWaitForCompletion()
    res = a.GetResults()
    vals = []
    try:
        for i in range(res.SpotData.NumberOfFields):
            vals.append(float(res.SpotData.GetRMSSpotSizeForField(i + 1)))
    except Exception:
        pass
    a.Close()
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
                        rays: int = 100000) -> dict:
    """Run a Non-Sequential ray trace (for illumination / stray-light).
    split=True enables ray splitting (transmit/reflect/absorb child rays);
    scatter=True enables scattering. Returns completion status."""
    z = _require()
    rt = z.system.Tools.OpenNSCRayTrace()
    rt.SplitNSCRays = bool(split)
    rt.ScatterNSCRays = bool(scatter)
    rt.UsePolarization = False
    rt.IgnoreErrors = True
    rt.RunAndWaitForCompletion()
    rt.Close()
    return _ok(split=split, scatter=scatter, rays=rays)


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
