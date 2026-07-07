# Atmospheric channel — 1080 nm over 2 km (opm_naren)

`examples/atmosphere_1080nm_2km.py` models the atmospheric propagation channel for
the **1.08 µm** beam over the **2 km** path, grounded in the opm_naren telescope's
real parameters (entrance pupil **D ≈ 0.641 m** from object-space NA 0.000458;
image conjugate 2.09 km ≈ the 2 km operating range).

> **Why analytical, not ray-traced:** scintillation and turbulence are random
> wave-optics effects (refractive-index phase screens) outside geometric ray
> tracing. Zemax can render *bulk absorption* as an attenuating medium, but the
> channel statistics are computed from theory (Andrews & Phillips; Kruse/Kim),
> parameterized by the design's aperture / wavelength / range.

## Results

**Absorption / extinction** (Beer–Lambert, Kruse/Kim aerosol + Rayleigh):

| Visibility | ext (1/km) | T 1-way | dB 1-way | T round-trip | dB round-trip |
|---|---|---|---|---|---|
| 50 km (very clear) | 0.033 | 0.935 | 0.29 | 0.875 | 0.58 |
| 23 km (clear std) | 0.072 | 0.867 | 0.62 | 0.751 | 1.24 |
| 10 km (light haze) | 0.164 | 0.721 | 1.42 | 0.520 | 2.84 |
| 5 km (haze) | 0.363 | 0.484 | 3.16 | 0.234 | 6.31 |
| 2 km (mist/fog) | 1.254 | 0.081 | 10.9 | 0.007 | 21.8 |

Round-trip = illuminate the target + return to the telescope (2 × 2 km).

**Scintillation** (Fresnel zone √(λL) = 46.5 mm, so D/r_F = 13.8 → strong aperture
averaging, factor **A ≈ 0.0012 ⇒ ~832× variance reduction**):

| Cn² (m⁻²ᐟ³) | regime | σ_R² | σ_I (point) | σ_I (aperture) | AoA jitter |
|---|---|---|---|---|---|
| 1e-16 | weak | 0.011 | 0.104 | 0.004 | 0.8 µrad |
| 1e-15 | weak | 0.108 | 0.327 | 0.011 | 2.6 µrad |
| 1e-14 | moderate | 1.081 | 0.860 | 0.030 | 8.2 µrad |
| 3e-14 | strong | 3.244 | 1.062 | 0.037 | 14.2 µrad |
| 1e-13 | strong | 10.82 | 1.115 | 0.039 | 26.0 µrad |

## How it connects to the beam-stabilization demo

- **Absorption** sets the link budget (≈1.2 dB round-trip in clear air; the link
  closes only in fog).
- **Scintillation** amplitude fade is mitigated by the **large aperture**, not the
  FSM — the 0.64 m pupil averages σ_I down to a few percent (0.8 dB fade margin at
  99.9% availability).
- **Turbulence tilt** — angle-of-arrival jitter (~8 µrad at Cn²=1e-14) and transmit
  beam wander (~17 mm ≈ 8 µrad at 2 km) — is the dominant *correctable* term, and
  it lands squarely in the range the **2-FSM loop** in
  [`beam_stabilization.py`](../examples/beam_stabilization.py) handles (it held
  residual ≤ 14.7 µrad while tracking). This is the report's Layer 4 (turbulence
  tilt) folding into Loops 1–3.

## Running it

```bash
python3 examples/atmosphere_1080nm_2km.py     # pure Python, no OpticStudio needed
```

Edit `LAMBDA`, `L`, `D` at the top to model a different wavelength / range /
aperture. References: Andrews & Phillips, *Laser Beam Propagation through Random
Media* (SPIE, 2005); Kim et al., SPIE 4214 (2001).
