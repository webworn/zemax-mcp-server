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
    results if available. Examples: 'RayFan', 'StandardSpot', 'FftPsf', 'FftMtf',
    'WavefrontMap', 'SeidelDiagram'. Grid-based analyses (PSF, wavefront) return a
    sampled data grid; series-based analyses (ray fan, MTF) report their series count
    (extract curves via zemax_eval on GetDataSeries).
    """
    z = _require()
    a = _new_analysis(z, analysis_type)
    a.ApplyAndWaitForCompletion()
    res = a.GetResults()
    out: dict[str, Any] = {"analysis": analysis_type}
    try:
        if res.NumberOfDataGrids > 0:
            grid = res.GetDataGrid(0)
            nx, ny = grid.Nx, grid.Ny
            out["grid"] = {"nx": nx, "ny": ny,
                           "sample": [[float(grid.Z(ix, iy)) for ix in range(min(nx, 5))]
                                      for iy in range(min(ny, 5))]}
        elif res.NumberOfDataSeries > 0:
            out["data_series_count"] = res.NumberOfDataSeries
            out["note"] = ("Series (x/y) analysis; call zemax_analysis_series "
                           "to get the actual curve values.")
        else:
            out["note"] = "No numeric grid/series exposed; use zemax_eval for extraction."
    except Exception as e:
        out["note"] = f"Result extraction issue: {type(e).__name__}: {e}"
    a.Close()
    return _ok(**out)


@mcp.tool()
def zemax_spot_rms() -> dict:
    """Compute the RMS spot radius per field via a Spot Diagram analysis (quick metric)."""
    z = _require()
    a = _new_analysis(z, "StandardSpot")
    a.ApplyAndWaitForCompletion()
    sd = a.GetResults().SpotData
    vals = []
    try:
        # GetRMSSpotSizeFor(field, wave); wave=0 -> polychromatic (all wavelengths).
        for i in range(1, sd.NumberOfFields + 1):
            vals.append(float(sd.GetRMSSpotSizeFor(i, 0)))
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
                        clear_detectors: bool = True, rays: int = 100000) -> dict:
    """Run a Non-Sequential ray trace (for illumination / stray-light).
    split=True enables ray splitting (transmit/reflect/absorb child rays);
    scatter=True enables scattering. clear_detectors=True zeroes all detectors
    first so a following zemax_nsc_detector_data read reflects only this trace.
    Returns completion status."""
    z = _require()
    rt = z.system.Tools.OpenNSCRayTrace()
    if clear_detectors:
        rt.ClearDetectors(0)  # 0 = all detectors
    rt.SplitNSCRays = bool(split)
    rt.ScatterNSCRays = bool(scatter)
    rt.UsePolarization = False
    rt.IgnoreErrors = True
    rt.RunAndWaitForCompletion()
    rt.Close()
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
    brt = z.system.Tools.OpenBatchRayTrace()
    reader = brt.CreateNormUnpol(len(rays), rtype, nsur)
    reader.ClearData()
    for r in rays:
        hx, hy, px, py = (list(r) + [0.0, 0.0, 0.0, 0.0])[:4]
        reader.AddRay(int(wavelength), float(hx), float(hy),
                      float(px), float(py), opd_none)
    brt.RunAndWaitForCompletion()
    reader.StartReadingResults()
    out = []
    while True:
        res = reader.ReadNextResult()
        if not res[0]:
            break
        (_s, num, err, vig, x, y, zc, l, m, n, _l2, _m2, _n2, opd, inten) = res
        out.append({"ray": num, "err": err, "vignette": vig,
                    "x": x, "y": y, "z": zc, "L": l, "M": m, "N": n,
                    "opd": opd, "intensity": inten})
    brt.Close()
    return _ok(surface=nsur, ray_type=ray_type, count=len(out), rays=out)


# ================================================================ ANALYSIS SERIES
@mcp.tool()
def zemax_analysis_series(analysis_type: str, max_points: int = 40) -> dict:
    """Run an analysis and return its x/y CURVE data (ray fans, MTF-vs-frequency, ...).

    Use this for series analyses where zemax_run_analysis only reports a count.
    Examples: 'RayFan', 'FftMtf', 'FftMtfvsField', 'OpticalPathFan'. For each data
    series it returns the x-axis label, the per-curve series labels (e.g. wavelength
    or field), and decimated x plus y values (down-sampled to ~max_points).
    """
    z = _require()
    a = _new_analysis(z, analysis_type)
    a.ApplyAndWaitForCompletion()
    res = a.GetResults()
    series_out = []
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
    a.Close()
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
    tol = z.system.Tools.OpenTolerancing()
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
    tol.Close()
    return _ok(**out)


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
