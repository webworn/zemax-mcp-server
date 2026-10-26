"""
examples/tools_check.py
=======================
Live end-to-end check of the MCP tools, calling the tool functions directly
(FastMCP's @mcp.tool() returns the plain function). Run under Windows Python with
Interactive Extension armed and the MCP server disconnected (it holds the one
extension slot):

    python.exe examples/tools_check.py

Each step reports PASS/FAIL and the run continues, so one armed connection shows
every failure. WARNING: replaces the design open in the GUI (test singlet, sample and
project designs); it is snapshotted first and restored at the end.
"""

import os
import traceback

from zemax_mcp import connect
from zemax_mcp import server as S

failures = []


def step(name, fn):
    try:
        out = fn()
        print(f"PASS {name}" + (f": {out}" if out is not None else ""))
        return out
    except Exception as e:
        failures.append(name)
        print(f"FAIL {name}: {type(e).__name__}: {e}")
        traceback.print_exc(limit=-2)
        return None


def check(cond, msg):
    if not cond:
        raise AssertionError(msg)


def build():
    # OBJ | 1 front (STOP, even asphere) | 2 back | IMA
    S.zemax_new_system()
    S.zemax_set_aperture("ImageSpaceFNum", 4.0)
    S.zemax_set_wavelengths([0.4861, 0.5876, 0.6563], primary=2)
    S.zemax_set_fields("Angle", [[0, 0], [0, 3.5], [0, 5]])
    S.zemax_lde_insert_surface(2)
    S.zemax_lde_set_surface(1, surface_type="EvenAspheric", radius=100, thickness=4,
                            material="N-BK7", conic=0, is_stop=True)
    S.zemax_lde_set_surface(2, radius=-100, thickness=95)


def stale_tool():
    # The 'wedged session' hypothesis: a leaked tool blocks every later Open*.
    tools = S._session.system.Tools
    check(tools.OpenQuickFocus() is not None, "first OpenQuickFocus returned None")
    blocked = tools.OpenQuickFocus() is None
    S.zemax_quick_focus()  # must succeed: _tool closes the stale handle first
    check(tools.CurrentTool is None, "tool left open after zemax_quick_focus")
    return f"second Open* blocked by leaked tool: {blocked}"


def variables():
    S.zemax_set_variable(1, "radius")
    S.zemax_set_variable(2, "radius")
    S.zemax_set_variable(1, "par2")
    S.zemax_set_solve(2, "thickness", "MarginalRayHeight", {"Height": 0})
    rows = S.zemax_lde_summary()["surfaces"]
    check(rows[1].get("radius_solve") == "Variable", rows[1])
    check("MarginalRayHeight" in rows[2].get("thickness_solve", ""), rows[2])
    return rows[1:3]


def wizard_enums():
    # pythonnet enum by-name resolution in _set_props (applied by the next step)
    wiz = S._session.system.MFE.SEQOptimizationWizard2
    S._set_props(wiz, {"Criterion": "Spot", "Reference": "Centroid"})
    return str(wiz.Criterion), str(wiz.Reference)


def merit():
    w = S.zemax_merit_wizard({"UseGlassBoundaryValues": True, "GlassMin": 2,
                              "GlassMax": 10, "GlassEdgeThickness": 1})
    S.zemax_mfe_set_operand("EFFL", row=1, insert=True, params=[0, 2], target=100, weight=1)
    top = S.zemax_mfe_summary(sort_by_contribution=True, max_rows=5)
    check(top["rows"], top)
    return w["merit"], [(r["type"], r["params"], r["contribution"]) for r in top["rows"]]


def optimize():
    opt = S.zemax_optimize("local", cycles=5)
    check(opt["merit_after"] < opt["merit_before"], opt)
    effl = S.zemax_operand("EFFL", [0, 2])["value"]
    check(abs(effl - 100) < 1, f"EFFL={effl}")
    return f"merit {opt['merit_before']:.4g} -> {opt['merit_after']:.4g}, EFFL {effl:.3f}"


def diff(snap):
    d = S.zemax_diff(snap)
    check(any(c["field"] == "radius" for c in d["surface_changes"]), d)
    return d["surface_changes"][:4], d["system_changes"]


def text():
    t = S.zemax_analysis_text("FftMtf", {"MTF_MAXF": 50})
    check(t["length"] > 0, t)
    return ascii(t["text"][:300])


def grid():
    r = S.zemax_run_analysis("WavefrontMap")
    check("grid" in r, r)
    g = r["grid"]
    check(g["shape"][0] > 0 and g["std"] > 0, g)
    return {k: g[k] for k in ("shape", "min", "max", "mean", "std", "x_label", "value_label")}


def image():
    img = S.zemax_layout_image()
    check(os.path.isfile(img["path"]), img)
    return img["wsl_path"]


def restore(snap, radius):
    S.zemax_restore(snap)
    check(S._same(S.zemax_lde_summary()["surfaces"][1]["radius"], radius), "radius not restored")


def review(path, check_fn):
    """Load a design with a known answer and check the review tools agree."""
    if not os.path.exists(path):
        return f"skipped: {path} not found"
    S.zemax_open_file(path)
    p = S.zemax_performance_summary()
    a = S.zemax_design_audit()
    check_fn(p["summary"], {f["check"] for f in a["findings"]})
    return a["counts"]


def rc_telescope(s, checks):
    # OGS-1: f/10 RC, EFL 5000 mm, diffraction limited
    check(abs(s["efl"] - 5000) < 1 and abs(s["working_fno"] - 10) < 0.1, s)
    check(s["diffraction_limited"] and s["strehl"] > 0.99, s)
    spec = S.zemax_verify_spec({"efl": {"min": 4950, "max": 5050}, "diffraction_limited": True})
    check(spec["all_pass"], spec)
    check(not S.zemax_verify_spec({"efl": {"max": 4000}})["all_pass"], "impossible spec passed")


def tolerancing():
    S.zemax_open_file(os.path.join(str(S._session.application.SamplesDir),
                                   r"Sequential\Objectives\Double Gauss 28 degree field.zmx"))
    S.zemax_save_file(os.path.join(S._out_dir(S._session, "tolerancing"), "dg28.zmx"))
    S.zemax_tolerance_wizard({"SurfaceRadius": 0.1, "SurfaceThickness": 0.05,
                              "ElementDecenterX": 0.05, "IsIndexUsed": False})
    r = S.zemax_run_tolerancing(monte_carlo_runs=20, threshold=0.02)
    check(r["worst_offenders"] and r["worst_offenders"][0]["surfaces"], r["worst_offenders"][:1])
    check(r["monte_carlo"]["runs"] == 20 and "90%" in r["monte_carlo"]["percentiles"], r)
    check(r["yield_pct"] is not None, r)
    again = S.zemax_tolerance_results(threshold=0.02)
    check(again["monte_carlo"] == r["monte_carlo"], "re-read differs")
    return r["monte_carlo"]["percentiles"], r["yield_pct"]


def main():
    S._session = connect("extension")
    original = step("snapshot original design",
                    lambda: S.zemax_snapshot("before-tools-check")["path"])
    step("build aspheric singlet", build)
    step("stale-tool recovery", stale_tool)
    step("variables + solve + summary", variables)
    snap = step("snapshot", lambda: S.zemax_snapshot("check")["path"])
    radius = S.zemax_lde_summary()["surfaces"][1]["radius"]
    step("wizard enum props", wizard_enums)
    step("wizard + EFFL + mfe summary", merit)
    step("optimize + operand", optimize)
    if snap:
        step("diff", lambda: diff(snap))
    step("analysis text w/ settings", text)
    step("analysis grid stats", grid)
    step("layout image", image)
    if snap:
        step("restore", lambda: restore(snap, radius))

    # --- item 3: review tools against designs with known answers
    step("review OGS-1 RC telescope", lambda: review(
        r"D:\Claude_code\ogs\Projects\OGS-1_RC_telescope.zmx", rc_telescope))
    step("review FSOC collimator (defocus)", lambda: review(
        r"D:\Claude_code\Zemax plugin\Projects\fc-apc connector with bk7 lens.zmx",
        lambda s, checks: check("defocus" in checks, checks)))
    step("review opm_naren (near afocal)", lambda: review(
        r"D:\Claude_code\Zemax plugin\Projects\opm_naren.zmx",
        lambda s, checks: check("near_afocal" in checks, checks)))
    # --- item 4: tolerancing results, sensitivity, yield
    step("tolerancing + results + yield", tolerancing)

    step("cleanup", S.zemax_cleanup)
    if original:
        step("restore original design", lambda: S.zemax_restore(original))
    print("FAILED:", failures if failures else "none - ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
