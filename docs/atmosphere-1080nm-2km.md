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

## Delivered power + thermal blooming (6 kW, M² = 1.5)

`delivered_power(P_in, vis_km, M2, w0, v_wind, cn2, ...)` returns power-on-target and
beam quality. **Only absorption removes watts**; M², turbulence, and thermal blooming
spread the spot (lower peak irradiance, same total power). For 6 kW, M²=1.5, a 0.32 m
transmit beam, 5 m/s crosswind, Cn²=10⁻¹⁴:

| Visibility | Power on target | Spot radius | Blooming | Strehl | Peak irradiance |
|---|---|---|---|---|---|
| 50 km | 5.61 kW | 20.7 mm | 0.085 λ | 0.75 | 624 W/cm² |
| 23 km | 5.20 kW | 20.7 mm | 0.097 λ | 0.69 | 531 W/cm² |
| 10 km | 4.33 kW | 20.7 mm | 0.117 λ | 0.58 | 372 W/cm² |
| 5 km | 2.90 kW | 20.7 mm | 0.132 λ | 0.50 | 217 W/cm² |

The spot at 2 km is **turbulence-dominated** (~21 mm) — far larger than the 3 mm
diffraction/M² spot — so higher-order turbulence, not M², sets the delivered
irradiance (the FSM removes only tilt; higher orders need AO).

**Thermal blooming is wind-critical** (absorbed power heats the air into a defocusing
lens; crosswind clears it). At 6 kW, clear air:

| Crosswind | Blooming | Strehl | Peak irradiance |
|---|---|---|---|
| 10 m/s | 0.049 λ | 0.91 | 701 W/cm² |
| 5 m/s | 0.097 λ | 0.69 | 531 W/cm² |
| 2 m/s | 0.243 λ | 0.10 | 75 W/cm² |
| 1 m/s | 0.486 λ | ~0 | ~0 |

Below ~2 m/s the beam blooms catastrophically (stagnation) — the classic high-energy-
laser limit. **These blooming numbers are first-order** (Maréchal / distortion-number);
accurate blooming needs wave-optics split-step propagation.

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
