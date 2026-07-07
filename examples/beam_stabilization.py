"""
examples/beam_stabilization.py
==============================
Closed-loop 2-FSM beam / aimpoint stabilization on a REAL reflective telescope,
driven through the Claude Code <-> OpticStudio bridge (ZOS-API, non-sequential).

Background (see the "Beam Stabilization" study, Loop 3 = aimpoint / hit-spot):
    Both source and target vibrate, so you hold the beam on the target by closing
    a loop on the RETURN light. Two Fast Steering Mirrors give 2-axis (yaw+pitch)
    control; a position-sensing detector measures where the return lands; the
    controller drives the two FSMs so the hit-spot stays at the detector center.

What this script does, end to end (one extension-mode connection):
    1. Loads a user reflective-telescope design (default: opm_naren) and CONVERTS
       it to a non-sequential group -- the real collector optics.
    2. opm_naren turns out to be a 362mm off-axis AFOCAL beam director (it images
       the 700m target to a plane ~2km away), so its exit beam is collimated. We
       sense the return LINE-OF-SIGHT ANGLE as a position on a far detector
       (autocollimator / boresight sensor) = the hit-spot sensor.
    3. Models the target return as a collimated beam whose angle = (disturbance -
       FSM correction). FSM1 = yaw (tilt about Y), FSM2 = pitch (tilt about X).
    4. Calibrates the 2x2 sense Jacobian (hit-spot mm per FSM deg).
    5. Scenario A: rejects a fixed pointing disturbance (step response).
    6. Scenario B: tracks a *moving* disturbance (slewing target) and reports the
       steady-state residual jitter.

Requires: Windows Python + pythonnet, OpticStudio open with Programming >
Interactive Extension armed. Point TELESCOPE_ZMX at your own reflective design to
try a different collector.

    python.exe examples/beam_stabilization.py
"""
import os, sys, math

# allow running from anywhere: put the repo root (parent of examples/) on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from zemax_mcp import connect

TELESCOPE_ZMX = r"D:\Claude_code\Zemax plugin\Projects\opm_naren.zmx"
SAVE_AS       = r"D:\Claude_code\Zemax plugin\Projects\beam_stab_demo.zos"

ZDET   = 6000.0     # boresight sensor plane (long lever arm for angle sensing), mm
DHALF  = 55.0       # detector half-width, mm
DCX, DCY = 15.0, -15.0   # detector center (covers both the on-axis input and the exit beam)
NPIX   = 220
NRAY   = 60000
EXCL_R = 12.0       # exclude the on-axis input beam within this radius of origin, mm


def build(z):
    """Load + convert the telescope, add the hit-spot sensor and the return beam.
    Returns (helpers dict)."""
    s = z.system; ZOSAPI = z.zosapi
    NCE = ZOSAPI.Editors.NCE; OC = NCE.ObjectColumn; OT = NCE.ObjectType
    s.LoadFile(TELESCOPE_ZMX, False)
    if str(s.Mode) != "NonSequential":
        conv = s.Tools.OpenConvertToNSCGroup()
        conv.RunAndWaitForCompletion(); conv.Close()
    nce = s.NCE

    def setp(o, i, v, integer=False):
        c = o.GetObjectCell(getattr(OC, f"Par{i}"))
        if integer: c.IntegerValue = int(v)
        else: c.DoubleValue = float(v)

    det = nce.InsertNewObjectAt(nce.NumberOfObjects + 1)
    det.ChangeType(det.GetObjectTypeSettings(OT.DetectorRectangle))
    det.Comment = "hit-spot sensor"; det.RefObject = 0
    det.XPosition = DCX; det.YPosition = DCY; det.ZPosition = ZDET
    setp(det, 1, DHALF); setp(det, 2, DHALF)
    setp(det, 3, NPIX, integer=True); setp(det, 4, NPIX, integer=True)
    det_num = nce.NumberOfObjects

    src = nce.InsertNewObjectAt(nce.NumberOfObjects + 1)
    src.ChangeType(src.GetObjectTypeSettings(OT.SourceRectangle))
    src.Comment = "target return (LOS = disturbance - FSM)"; src.RefObject = 0
    src.XPosition = 0.0; src.YPosition = 0.0; src.ZPosition = 8000.0
    setp(src, 1, 40, integer=True); setp(src, 2, NRAY, integer=True)
    setp(src, 3, 1.0); setp(src, 4, 1, integer=True)   # power, wavelength #1 (1.08um)
    setp(src, 6, 7.0); setp(src, 7, 7.0)               # 14mm return beam
    setp(src, 8, 0.0); setp(src, 9, 0.0)               # collimated

    def set_los(yaw_deg, pitch_deg):
        # base 180 flip so the source emits -Z into the telescope, + small steering
        src.TiltAboutX = 180.0 + pitch_deg
        src.TiltAboutY = yaw_deg
        src.TiltAboutZ = 0.0

    def measure():
        rt = s.Tools.OpenNSCRayTrace(); rt.ClearDetectors(0)
        rt.SplitNSCRays = False; rt.ScatterNSCRays = False
        rt.UsePolarization = False; rt.IgnoreErrors = True
        rt.RunAndWaitForCompletion(); rt.Close()
        d = nce.GetAllDetectorDataSafe(det_num, 1)
        dims = nce.GetDetectorDimensions(det_num); nx, ny = int(dims[1]), int(dims[2])
        tot = sx = sy = 0.0
        for a in range(nx):
            ax = DCX + (a + 0.5) / nx * 2 * DHALF - DHALF
            for b in range(ny):
                v = float(d[a, b])
                if v <= 0: continue
                ay = DCY + (b + 0.5) / ny * 2 * DHALF - DHALF
                if ax * ax + ay * ay < EXCL_R * EXCL_R:   # skip on-axis input beam
                    continue
                tot += v; sx += ax * v; sy += ay * v
        if tot <= 0: return None
        return (sx / tot, sy / tot, tot)   # absolute mm centroid, flux

    return dict(set_los=set_los, measure=measure)


def urad(deg): return deg * math.pi / 180 * 1e6


def main():
    with connect("extension") as z:
        h = build(z)
        set_los, measure = h["set_los"], h["measure"]

        # ---- boresight ----
        set_los(0.0, 0.0)
        m0 = measure()
        if not m0:
            print("No return flux on the hit-spot sensor; check the telescope path."); return
        c0x, c0y, f0 = m0
        print(f"Boresight hit-spot = ({c0x:+.3f}, {c0y:+.3f}) mm  flux={f0:.4g}")

        # ---- calibrate 2x2 sense Jacobian (hit-spot mm per FSM deg) ----
        DEG = 0.01
        set_los(DEG, 0.0); ay = measure()
        set_los(0.0, DEG); ap = measure()
        Jxx = (ay[0] - c0x) / DEG; Jyx = (ay[1] - c0y) / DEG
        Jxy = (ap[0] - c0x) / DEG; Jyy = (ap[1] - c0y) / DEG
        detJ = Jxx * Jyy - Jxy * Jyx
        iJ = (Jyy / detJ, -Jxy / detJ, -Jyx / detJ, Jxx / detJ)
        print(f"Sense Jacobian J = [[{Jxx:+.2f},{Jxy:+.2f}],[{Jyx:+.2f},{Jyy:+.2f}]] mm/deg "
              f"(plate scale {math.hypot(Jxx,Jyx):.1f} mm/deg)")

        def control_step(u_yaw, u_pitch, d_yaw, d_pitch, g):
            """Apply net LOS, measure, return (residual_urad, err_mm, new u)."""
            net_y, net_p = d_yaw - u_yaw, d_pitch - u_pitch
            set_los(net_y, net_p)
            m = measure()
            if not m: return None
            ex, ey = m[0] - c0x, m[1] - c0y
            u_yaw   += g * (iJ[0] * ex + iJ[1] * ey)
            u_pitch += g * (iJ[2] * ex + iJ[3] * ey)
            return (math.hypot(urad(net_y), urad(net_p)), math.hypot(ex, ey), u_yaw, u_pitch)

        # ---- Scenario A: reject a fixed disturbance (step response) ----
        print("\n--- Scenario A: reject a fixed pointing disturbance ---")
        d_yaw, d_pitch, g = 0.030, -0.020, 0.6
        print(f"disturbance = ({urad(d_yaw):+.0f}, {urad(d_pitch):+.0f}) urad ; loop gain {g}")
        uy = up = 0.0
        first = last = None
        for step in range(10):
            r = control_step(uy, up, d_yaw, d_pitch, g)
            if not r: print(f"  step {step}: return off sensor"); break
            res, err, uy, up = r
            if first is None: first = (res, err)
            last = (res, err)
            print(f"  step {step:>2}: residual {res:8.1f} urad   hit-spot err {err:7.4f} mm")
        print(f"  => {first[0]:.0f} urad -> {last[0]:.2f} urad  ({first[0]/max(last[0],1e-9):.0f}x); "
              f"FSM held ({uy:+.4f},{up:+.4f}) deg vs disturbance ({d_yaw:+.4f},{d_pitch:+.4f})")

        # ---- Scenario B: track a moving disturbance (slewing target) ----
        # Nested architecture (per the report): a fast FSM inner loop settles between
        # slow target updates. Here: 3 FSM iterations per target position.
        print("\n--- Scenario B: track a slewing target (nested fast FSM loop) ---")
        uy = up = 0.0; g = 0.6; inner = 3
        print(f"  target motion per step ~52 urad; {inner} FSM iterations per step; gain {g}")
        print(f"  {'t':>3} {'disturb(urad)':>18} {'residual(urad)':>15} {'err(mm)':>9}")
        peak = 0.0
        for t in range(14):
            d_yaw   = 0.003 * t                      # slow yaw slew
            d_pitch = 0.010 * math.sin(t / 2.5)      # pitch oscillation
            res = err = None
            for _ in range(inner):                   # fast inner FSM loop
                r = control_step(uy, up, d_yaw, d_pitch, g)
                if not r: break
                res, err, uy, up = r
            if res is None: print(f"  t={t}: return off sensor"); break
            if t >= 2: peak = max(peak, res)         # steady-state after initial lock
            print(f"  {t:>3} ({urad(d_yaw):>7.0f},{urad(d_pitch):>7.0f})   {res:>15.1f} {err:>9.4f}")
        print(f"  => steady-state residual jitter <= {peak:.1f} urad while the target slews")

        z.system.SaveAs(SAVE_AS)
        print(f"\nSaved NSC model -> {SAVE_AS}")


if __name__ == "__main__":
    main()
