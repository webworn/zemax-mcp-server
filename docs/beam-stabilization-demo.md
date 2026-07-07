# Beam-stabilization demo — 2-FSM aimpoint control on a real telescope

`examples/beam_stabilization.py` drives a **closed-loop, two–fast-steering-mirror
(FSM) beam / aimpoint stabilization loop** on a real reflective telescope, entirely
through the Claude Code ↔ OpticStudio bridge (ZOS-API, non-sequential). It is the
optical-simulation counterpart of the **"Stabilizing a Laser on a Distant Target"**
study — specifically its **Loop 3 (aimpoint / hit-spot)**:

> *The only sensor that sees the target end is the return light. Two FSMs give
> 2-axis control; a position sensor measures where the return lands; the controller
> drives the FSMs so the hit-spot stays at the detector center.*

## What the telescope turned out to be

The demo's collector is the user's `opm_naren` design. Converting it to a
non-sequential group and characterizing it live revealed it is a **362 mm off-axis
two-mirror afocal beam director**, not a bench imager:

| Property | Value (measured live) |
|---|---|
| Wavelengths | 1.08 µm (primary) + 0.55 µm |
| Target (object) | on-axis at **+700 m** |
| Paraxial image | **+2.09 km** (≈3× relay ⇒ effectively afocal) |
| Exit beam | **collimated**, chief ray at (X≈+20.25, Y≈−22.5) mm, +Z |

Because the exit is collimated, the return **line-of-sight angle** is sensed as a
**position** on a detector placed far downstream (Z=6000 mm) — an autocollimator /
boresight sensor. That detector *is* the hit-spot sensor; its center is the aimpoint.

## Architecture

```
 return (LOS angle = disturbance − FSM correction)
        │  collimated beam into the real converted telescope
        ▼
  opm_naren telescope (NSC group)  ──►  collimated exit (Y=−22.5)
                                              │  long lever arm
                                              ▼
                                     hit-spot sensor (detector)
                                              │  flux-weighted centroid
                                              ▼
        controller:  u += g · J⁻¹ · (centroid − boresight)     [integral]
                                              │
                                              ▼
              FSM1 = yaw (tilt Y) , FSM2 = pitch (tilt X)
```

* **FSMs** are modeled as the two beam-steering axes (each FSM = one tilt axis) —
  the standard control-plant representation.
* **Sense Jacobian** `J` (hit-spot mm per FSM deg) is calibrated live by tilting each
  axis; it comes out anti-diagonal (`[[0,132],[132,0]]`) because the telescope's fold
  rotates the sense axes 90° from the FSM axes. The 2×2 inverse absorbs this.

## Results (live, OpticStudio 2025 R2.02)

**Scenario A — reject a fixed disturbance (step response), gain 0.6:**

```
residual 629 µrad → 0.16 µrad   (3815× reduction) in 9 iterations
FSM command converges to exactly cancel the (0.030°, −0.020°) disturbance
```

**Scenario B — track a slewing target (nested fast FSM loop, 3 iters/step):**

```
target slews ~700 µrad (yaw) + ±175 µrad (pitch, oscillating)
steady-state residual jitter ≤ 14.7 µrad  (~50× rejection)
```

Both are in the regime the study targets (hold residual to single-digit / tens of
µrad — "≈1/10 of the beam divergence"). Scenario B is the report's **nested
architecture**: a fast inner FSM loop settles between slow outer target updates.

## Running it

1. Windows Python + `pythonnet`, OpticStudio open, **Programming ▸ Interactive
   Extension** armed (see [`../setup/INSTALL.md`](../setup/INSTALL.md)).
2. Point `TELESCOPE_ZMX` at a reflective telescope design (default: `opm_naren`).
3. `python.exe examples/beam_stabilization.py`

The script converts the telescope to NSC, builds the sensor + return beam,
calibrates the Jacobian, runs both scenarios, and saves the NSC model.

> **Scope / fidelity note.** The return is modeled as a beam whose *angle* is the
> LOS error (disturbance − FSM), which fully exercises the real converted telescope +
> boresight sensor + 2-FSM closed loop. A full diffuse-target round-trip (illuminate
> the 700 m target → Lambertian scatter → importance-sample back into the aperture)
> is the higher-fidelity variant and a natural next step; the control loop is
> identical.
