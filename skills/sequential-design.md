# Sequential Imaging Design in Zemax OpticStudio (ZOS-API Playbook)

A practical, executable methodology for designing **sequential** imaging systems
(camera lenses, telescopes, microscopes, relays) in Ansys Zemax OpticStudio, with
embedded ZOS-API (Python) snippets so an agent can *run* each step rather than only
describe it.

> **Companion docs:** `non-sequential-design.md` (illumination, stray light, sources)
> and `function-to-feature-catalog.md` (authoritative list of ZOS-API members).
> Where an exact API member name is uncertain below, a `# VERIFY` comment flags it —
> confirm against the catalog before relying on it.

**Boilerplate assumed in every snippet.** A live connection exposes three handles:

```python
# Standard ZOS-API handles (established by the connection layer):
#   TheApplication  -> ZOSAPI.IZOSAPI_Application
#   TheSystem       -> TheApplication.PrimarySystem  (the optical system)
#   ZOSAPI          -> the ZOSAPI namespace (enums, constants)
# All snippets below assume these are in scope.
```

---

## 1. When to use sequential mode

Use **sequential mode** when light propagates through a *known, ordered* list of
surfaces — object → surface 1 → surface 2 → … → image — and hits each surface exactly
once. This is the natural model for **imaging** optics:

- Camera and photographic lenses (singlets, triplets, double Gauss, zooms).
- Telescopes and objectives (refractive and reflective).
- Microscope objectives and tube lenses.
- Relays, eyepieces, collimators, spectrometer optics.

Sequential mode gives you the fast, aberration-oriented toolset: paraxial ray tracing,
Seidel coefficients, ray fans, OPD, MTF, and gradient-based optimization (DLS). The
raytrace is cheap because the surface order is fixed.

**Contrast with non-sequential (NSC):** use NSC when rays can hit objects in *any*
order, split, scatter, or reflect unpredictably — illumination, light pipes, stray
light, prisms with TIR, LED optics. NSC is physically general but slower and lacks the
paraxial/aberration machinery. Many designs are hybrid: image-forming core in
sequential, with an NSC region for a prism or a light guide. If your task is "form a
good image of a scene," stay sequential (this doc). If it is "get photons from A to B,"
see `non-sequential-design.md`.

---

## 2. The design workflow (overview)

The whole loop, top to bottom:

1. **Define specs** — focal length / magnification, F/#, field of view, spectral band,
   image format, MTF/spot target, packaging constraints.
2. **Set system aperture** (EPD or F/#).
3. **Set fields** (angles or object heights; include 0, ~0.7, and 1.0 zones).
4. **Set wavelengths** (band + primary; weights).
5. **Choose a starting point** (singlet, known form, or a stock/catalog lens).
6. **Enter surfaces** in the Lens Data Editor (LDE) — the prescription.
7. **Set variables & solves** (radii first, then thicknesses/airspaces; focus solve).
8. **Build the merit function** via the Optimization Wizard, then add constraints.
9. **Optimize** — Local (DLS) → Hammer → Global Search, escalating as needed.
10. **Analyze aberrations** — ray fan, spot, OPD, Seidel, MTF, field curvature.
11. **Iterate** — add degrees of freedom or elements; repeat 7–10.
12. **Tolerance** — hand off to the tolerancing tool once nominal performance is met.

Steps 2–4 are "System Explorer" data; 6–11 are the design core; 12 is verification.

---

## 3. System setup

### Aperture

The system aperture sets the cone of light (the "speed"). Pick the definition that
matches how the spec is stated:

- **Entrance Pupil Diameter (EPD)** — use when the physical clear aperture / pupil size
  is fixed (e.g., a telescope with a 100 mm aperture). EPD is independent of focal
  length.
- **Image-Space F/#** — use for photographic lenses where speed is quoted as F/#
  (F/2.8, F/4). Note F/# = EFL / EPD, so it floats if EFL changes during optimization.
- **Object-Space NA** — use for finite-conjugate / microscope work quoted in NA.
- **Float By Stop Size** — the aperture is defined by the stop surface's semi-diameter;
  handy once the stop is a real element.

```python
ap = TheSystem.SystemData.Aperture
# Photographic F/4 lens:
ap.ApertureType  = ZOSAPI.SystemData.ZemaxApertureType.ImageSpaceFNum
ap.ApertureValue = 4.0
# --- or a fixed 100 mm telescope aperture: ---
# ap.ApertureType  = ZOSAPI.SystemData.ZemaxApertureType.EntrancePupilDiameter
# ap.ApertureValue = 100.0
```

### Fields

Fields sample the field of view. Choose the type that matches conjugates:

- **Angle** (degrees) — for objects at infinity (cameras, telescopes).
- **Object Height** — for finite conjugates (a real object plane; microscopes, copiers).
- **Paraxial/Real Image Height** — when the image format is the fixed quantity.

Sample at least three normalized zones: **0.0** (on-axis), **~0.7**, and **1.0** (edge).
The **0.7 zone matters** because RMS-over-the-field is area-weighted: the 0.7 radius
splits the circular image area roughly in half, so it represents the "average"
performance far better than the axis or the extreme corner. Aberrations also grow
monotonically but non-linearly toward the edge, and the 0.7 point is where field
curvature / astigmatism first become obvious. A three-field {0, 0.7, 1.0} set gives the
optimizer a balanced picture; add 0.5 and 0.85 for wide-angle or demanding designs.

```python
f = TheSystem.SystemData.Fields
f.SetFieldType(ZOSAPI.SystemData.FieldType.Angle)   # infinite conjugate
# For a 20-degree half-FOV lens, place fields at 0, 0.7*20, 1.0*20 degrees:
# GetField(1) already exists; set it, then add the rest.
f.GetField(1).X, f.GetField(1).Y = 0.0, 0.0
f.AddField(0.0, 14.0, 1.0)   # 0.7 zone, weight 1.0   # VERIFY AddField(x,y,weight)
f.AddField(0.0, 20.0, 1.0)   # full field
```

### Wavelengths

Cover the operating band and pick a **primary** (the wavelength for paraxial layout and
default focus). Common presets:

- **Visible (photopic):** F (0.4861 µm), d (0.5876 µm), C (0.6563 µm) — primary = **d**.
  These are the standard achromatization pair endpoints (F & C) plus the central d line.
- Broadband/NIR/UV: sample the band edges plus center, weight by detector/source
  response.

```python
wl = TheSystem.SystemData.Wavelengths
# Fast path: apply the F,d,C preset if available in this build:
# wl.SelectWavelengthPreset(ZOSAPI.SystemData.WavelengthPreset.FdC_Visible)  # VERIFY name
# Manual, explicit version (robust):
wl.GetWavelength(1).Wavelength = 0.4861          # F line
wl.AddWavelength(0.5876, 1.0)                     # d line
wl.AddWavelength(0.6563, 1.0)                     # C line
wl.GetWavelength(2).MakePrimary()                # d is primary
```

Weight wavelengths by importance (source spectrum × detector QE). Equal weights are a
fine default for a broadband visible lens.

---

## 4. Starting points

Optimization is local — it polishes a starting shape, it does not invent one. Choose a
starting form appropriate to the spec:

- **Singlet** — one positive element. The canonical teaching example; only good for slow
  speed / narrow field / one aberration to control. Great first exercise.
- **Achromat (cemented doublet)** — the workhorse for chromatic correction; two glasses
  of differing dispersion.
- **Cooke triplet** — three elements (─ + ─); enough degrees of freedom to control all
  primary Seidels; classic starting point for a modest photographic lens.
- **Double Gauss** — six-ish elements, near-symmetric about the stop; the go-to for fast
  (F/2) normal-FOV lenses; symmetry kills coma, distortion, lateral color.
- **Petzval** — two positive groups; long, flat-ish field; projectors, telecine.
- **Reflective forms** — Newtonian, Cassegrain, RC for telescopes (mirrors → no
  chromatic aberration).

**Get a real starting prescription** in one of three ways:

1. **Build it by hand** in the LDE from a known patent/textbook prescription.
2. **Lens Catalogs / stock lenses** — insert a real vendor part (Thorlabs, Edmund,
   etc.) as a fully-populated surface group, then optimize around it.
3. **Import** — a `.zmx`/`.zar` file or a ZEMAX prescription from a paper.

### Canonical first example — the F/4 N-BK7 singlet

The official Zemax Knowledgebase **"How to design a singlet lens"** tutorial series
(article **KA-01510**, *"OpticStudio tutorials, Part 1 — sequential"*) walks through an
**F/4, N-BK7** singlet: set an Entrance Pupil Diameter (or F/4), one on-axis field,
a single wavelength to start, an N-BK7 element with a variable front/back radius, a
marginal-ray-height focus solve on the last airspace, and a default RMS-spot merit
function optimized with local DLS. It is the reference "hello world" of sequential
design and the template for the worked example in Section 11.

---

## 5. Variables & solves

The optimizer only moves cells you mark as **variable**. Solves compute a cell
automatically from a paraxial/geometric condition.

**What to vary, in priority order:**

1. **Radii of curvature** — the primary power/aberration levers. Vary these first;
   curvature bending is where most aberration correction lives.
2. **Airspaces / thicknesses** — secondary; vary once curvatures are working. Free
   thicknesses relieve field curvature and Petzval balance and set focus.
3. **Glasses** — via a **Substitute** solve (see Section 7) so material choice becomes a
   discrete optimization variable resolving to real catalog glasses.
4. **Conics / aspheric coefficients** — only after spherical surfaces are optimized and
   only where an asphere pays off (usually near the stop or a strong surface). Adding
   aspheres too early wastes effort and hides the real design balance.

```python
lde = TheSystem.LDE                       # surfaces are 0-based via GetSurfaceAt
# Vary both radii of the singlet (surfaces 1 and 2 = the two glass faces):
lde.GetSurfaceAt(1).RadiusCell.MakeSolveVariable()
lde.GetSurfaceAt(2).RadiusCell.MakeSolveVariable()
# Vary the element thickness and the back airspace:
lde.GetSurfaceAt(1).ThicknessCell.MakeSolveVariable()
lde.GetSurfaceAt(2).ThicknessCell.MakeSolveVariable()
# A conic, if/when you go aspheric:
# lde.GetSurfaceAt(1).ConicCell.MakeSolveVariable()
```

**Focus solve on the last airspace** — instead of varying the back distance and letting
the merit function chase focus, put a **Marginal Ray Height = 0** solve on the last
thickness so the image surface always sits at paraxial focus automatically:

```python
last = TheSystem.LDE.NumberOfSurfaces - 2       # thickness surface before image
cell = TheSystem.LDE.GetSurfaceAt(last).ThicknessCell
solve = cell.CreateSolveType(ZOSAPI.Editors.SolveType.MarginalRayHeight)   # VERIFY enum
solve._S_MarginalRayHeight.Height = 0.0
cell.SetSolveData(solve)                          # VERIFY CreateSolveType/SetSolveData
```

**Pickup solves for symmetry** — in symmetric forms (double Gauss), a *pickup* solve
copies a radius/thickness from a mirror-image surface (optionally with a sign/scale) so
the two halves stay linked and the design keeps its symmetry through optimization.
Symmetry about the stop automatically cancels coma, distortion, and lateral color.

**Stop placement** — the stop surface controls where the aperture sits. Its axial
position is a powerful lever for coma and distortion (aberrations that depend on the
chief-ray height at each surface). Make the stop an interior surface (not surface 1)
when you need that freedom; you can even vary the airspaces around it.

```python
TheSystem.LDE.GetSurfaceAt(3).IsStop = True       # designate surface 3 as the stop
```

---

## 6. Merit function

### Build the default with the Optimization Wizard

The **Sequential Optimization Wizard** generates a balanced RMS merit function over your
fields and wavelengths in one call. This is almost always the right starting merit
function:

```python
mfe   = TheSystem.MFE
wiz   = mfe.SEQOptimizationWizard
# Configure the wizard (members verified against catalog):
# wiz.Type      = 0   # 0 = RMS
# wiz.Data      = 1   # 1 = Spot Radius, 2 = Wavefront   # VERIFY code mapping
# wiz.Reference = 0   # 0 = Centroid (best for spot), 1 = Chief ray
# wiz.Ring      = 3   # pupil sampling rings (Gaussian quadrature)
wiz.Apply()                                       # writes operands into the MFE
merit = mfe.CalculateMeritFunction()
print("merit after wizard:", merit)
```

**RMS spot vs RMS wavefront:**

- **RMS Spot Radius** — geometric; the right criterion when the system is far from
  diffraction-limited (early design, fast/large-aberration systems). Robust and fast.
- **RMS Wavefront (OPD)** — physical; use when you are *near* diffraction-limited
  (well-corrected systems, tight MTF specs). It correlates directly with Strehl/MTF.

Rule of thumb: start with **spot**, switch to **wavefront** for the final polish once
spots are near the Airy disk.

### Add constraints (operands) on top of the wizard defaults

The wizard optimizes image quality but knows nothing about your first-order spec or
mechanical limits. Add operands to pin those down. The wizard writes its image-quality
operands *below* a blank line; put your constraints **above** it so they aren't
overwritten on re-run.

Common constraint operands:

| Operand | Controls | Typical use |
|---------|----------|-------------|
| `EFFL`  | Effective focal length | Hold the design to target EFL (target=value, weight high) |
| `MNCT`  | **M**i**n** **C**enter **T**hickness | Keep glass from going too thin |
| `MXCT`  | Max center thickness | Cap element mass / length |
| `MNET`  | Min **E**dge **T**hickness | Keep edges manufacturable (>~1 mm) |
| `MNEA`/`MXEA` | Min/max edge thickness for airspaces | Prevent element collisions |
| `DIMX`  | **Di**stortion **max** | Bound distortion (e.g., < 2%) |
| `TOTR`  | Total track | Constrain overall length / packaging |
| `RAID`/`CTGT` etc. | Ray angles / telecentricity | Force image-space telecentricity |
| `POWR`/`CVGT` | Surface power/curvature bounds | Manufacturability |

Telecentricity: constrain the **chief-ray angle at the image** to ~0 (e.g., drive the
real ray angle operand to 0) for sensor/scanner work.

```python
# Add EFFL = 100 mm as a hard-ish target near the top of the MFE.
op = mfe.InsertNewOperandAt(1)                    # 1-based row
op.ChangeType(ZOSAPI.Editors.MFE.MeritOperandType.EFFL)
op.Target = 100.0
op.Weight = 10.0
# Bound minimum edge thickness on the singlet's second face:
op2 = mfe.InsertNewOperandAt(2)
op2.ChangeType(ZOSAPI.Editors.MFE.MeritOperandType.MNET)
# op2.GetCellAt(2).IntegerValue = 1   # Surf1 field; set Surf1/Surf2 range   # VERIFY cells
op2.Target = 1.0                                  # >= 1 mm edge
op2.Weight = 1.0
merit = mfe.CalculateMeritFunction()
```

### Reading `CalculateMeritFunction`

`mfe.CalculateMeritFunction()` returns a single scalar — the RSS of
`weight * (value - target)` over all operands. **Lower is better; it is not an absolute
scale.** Compare it only against itself across iterations. Its *value* is dominated by
whatever you weighted most, so a "good" number depends on your operand set. To interpret
performance in physical terms, read the actual image-quality metrics (RMS spot, MTF)
rather than the merit scalar alone.

---

## 7. Optimization strategy

Escalate through three tools, cheapest first:

### Local — Damped Least Squares (DLS)

Gradient-based descent to the **nearest** local minimum. Fast, deterministic, the
everyday workhorse. Run it after every change to variables or the merit function.

```python
tools = TheSystem.Tools
lo = tools.OpenLocalOptimization()
lo.Algorithm      = ZOSAPI.Tools.Optimization.OptimizationAlgorithm.DampedLeastSquares
lo.Cycles         = ZOSAPI.Tools.Optimization.OptimizationCycles.Automatic
lo.NumberOfCores  = 8
lo.RunAndWaitForCompletion()
lo.Close()
print("merit:", TheSystem.MFE.CalculateMeritFunction())
```

### Hammer — escape local minima

Hammer repeatedly perturbs the design and re-runs local optimization, keeping the best
result. Use it when local DLS has stalled but you believe a better minimum is *near* the
current form. It runs until stopped, so time-box it.

```python
hm = tools.OpenHammerOptimization()
hm.NumberOfCores = 8
hm.RunAndWaitWithTimeout(60.0)     # seconds; Hammer never "finishes" on its own
hm.Close()
```

### Global Search — broad exploration

Global Search samples many *different* configurations across the whole variable space,
optimizing each. Use it when you lack a good starting point or suspect a fundamentally
better form exists. Long-running; leave it for minutes to hours and harvest the top
candidates it saves.

```python
gs = tools.OpenGlobalOptimization()
gs.NumberOfCores = 8
gs.RunAndWaitWithTimeout(120.0)
gs.Close()
```

**Decision guide:**

| Situation | Tool |
|-----------|------|
| Just changed variables/MF, want the nearest good answer | Local DLS |
| DLS converged but performance still short; form looks right | Hammer |
| No good starting point, or want to survey the design space | Global Search |
| Only focus is off (spots blur uniformly) | Quick Focus |

### Glass substitution — let material be a variable

Real glasses are discrete, so you can't smoothly "vary" refractive index. Instead put a
**Substitute** solve on the glass cell; **Hammer and Global** then swap in real catalog
glasses (from the specified catalogs) and keep whichever improves the merit function.
This is the mechanism behind the KB glass-substitution guidance — see the Zemax
Knowledgebase articles **"How to use glass substitution to optimize designs"** and
**"Using the Glass Substitution Template"**. Local DLS ignores Substitute solves (it is
continuous); you must run Hammer/Global to exercise them.

```python
g = TheSystem.LDE.GetSurfaceAt(1)     # a glass surface
sub = g.MaterialCell.CreateSolveType(ZOSAPI.Editors.SolveType.MaterialSubstitute)  # VERIFY
# sub._S_MaterialSubstitute.Catalog = "SCHOTT"    # restrict catalog if desired  # VERIFY
g.MaterialCell.SetSolveData(sub)
# Now run Hammer or Global to let the optimizer try real glasses:
# ... OpenHammerOptimization / OpenGlobalOptimization as above ...
```

Constrain the substitution with boundary operands (min/max index, Abbe, relative
partial dispersion) if you need a manufacturable, athermal, or low-cost glass map.

### Quick Focus

A one-shot best-focus adjustment of the back distance — useful before analysis to make
sure a blurry spot isn't just defocus:

```python
qf = tools.OpenQuickFocus()
qf.RunAndWaitForCompletion()
qf.Close()
```

---

## 8. Aberration control

Diagnose with analyses, then pull the matching design lever. Open any analysis by its
`AnalysisIDM` name:

```python
a = TheSystem.Analyses.New_Analysis(ZOSAPI.Analysis.AnalysisIDM.RayFan)
a.ApplyAndWaitForCompletion()
res = a.GetResults()      # data series / grid; see function-to-feature-catalog
a.Close()
```

Key analyses and what they reveal:

| Aberration | Diagnostic (`AnalysisIDM`) | Signature | Design lever |
|-----------|----------------------------|-----------|--------------|
| **Spherical** | `RayFan` (transverse), `SeidelDiagram` | S-shaped (cubic) ray-fan curve, symmetric about axis; on-axis blur | Split the element; bend surfaces; add an **asphere**; move the stop; use higher-index glass |
| **Coma** | `RayFan`, `Spot` | Asymmetric ray fan; comet-shaped off-axis spot | **Symmetry about the stop**; stop position; bending; aplanatic surfaces |
| **Astigmatism** | `RayFan` (tangential vs sagittal differ), `FieldCurv` | Tangential and sagittal foci separate off-axis | Stop position; **field flattener**; add elements; balance powers |
| **Field curvature** | `FieldCurv`, `SeidelDiagram` (Petzval) | Curved image surface; edge defocuses | Reduce **Petzval sum** via glass choice (positive low-index + negative high-index); **field flattener** (negative element near image); thick meniscus |
| **Distortion** | `FieldCurv` / distortion plot, `DIMX` operand | Grid pincushion/barrel; image quality fine | Stop position; **symmetry**; add operand `DIMX` to bound |
| **Chromatic (axial)** | `RayFan` at multiple λ, `ChromaticFocalShift` | Colors focus at different depths | **Achromatize**: crown+flint doublet; glass pairing (high V + low V) |
| **Lateral color** | `RayFan`, `SeidelDiagram` | Color-dependent image height off-axis | Symmetry about stop; glass pairing |
| **Overall** | `FftMtf`/`HuygensMtf`, `FftPsf`, `Wavefront`/`WavefrontMap`, `StandardSpot` | System-level quality | Balance all of the above |

Useful `AnalysisIDM` names (confirm exact spelling in the catalog): `RayFan`,
`StandardSpot`, `Wavefront` / `WavefrontMap`, `SeidelDiagram` / `SeidelCoefficients`,
`FftMtf`, `HuygensMtf`, `FftPsf`, `FieldCurvatureAndDistortion` (a.k.a. `FieldCurv`),
`ChromaticFocalShift`. `# VERIFY names against function-to-feature-catalog.md`.

**Reading the Seidel diagram** is the fastest way to see *which* primary aberration
dominates and *which surface* generates it — it lists SPHA, COMA, ASTI, FCUR (Petzval),
DIST, plus axial (CLA) and lateral (CTR) color, per surface. Attack the biggest bar
first, at the surface that contributes it.

**Petzval sum** deserves special mention: it is set almost entirely by *element powers
and indices*, not by bending, so once astigmatism is balanced you flatten the field by
(a) choosing a high-index positive / low-index-ish arrangement, or (b) adding a negative
**field-flattener** element just ahead of the image. This is why a good design "adds a
field flattener when Petzval dominates."

---

## 9. Iteration & convergence

- **Diagnosing a local minimum:** DLS has converged (merit stops dropping) but the
  performance target isn't met. Test it by (a) running **Hammer** — if the merit
  suddenly improves, you were stuck; (b) nudging a variable by hand and re-optimizing to
  a different value; (c) running **Global Search** to see if a better basin exists.
- **Degrees of freedom vs constraints:** count your variables against your operands. If
  the design is over-constrained (more hard targets than free variables), the optimizer
  can't satisfy everything — relax weights or free more variables. If under-constrained,
  it may wander into unphysical shapes — add boundary operands (MNCT/MNET/MXCT).
- **When to add an element:** if, after Hammer/Global, the best form still can't meet
  spec, the current *number of elements* lacks the degrees of freedom to correct the
  residual aberrations. Add an element (or split a strong element into two) where the
  dominant Seidel is generated, then re-optimize. Symmetric additions (about the stop)
  buy off-axis correction cheaply.
- **Order of operations each cycle:** vary curvatures → local → add thicknesses → local
  → Substitute glasses → Hammer → analyze Seidel/MTF → decide (add DOF or tolerance).
- **Watch for degeneracy:** rays failing to trace, negative/rapidly-oscillating
  thicknesses, or a wildly bent surface signal the optimizer exploiting a loophole in
  the constraints. Add the missing boundary operand and re-run.

---

## 10. Tolerancing (brief)

Once the **nominal** design meets spec, verify it survives manufacturing and assembly.
Hand off to the tolerancing tool (`TheSystem.Tools.OpenTolerancing()` — see the
tolerance skill/catalog). What to check:

- **Sensitivities:** radius, thickness, index/Abbe, surface & element decenter/tilt,
  wedge, and irregularity. Identify the few tolerances that dominate the performance
  drop.
- **Criterion:** tolerance against a real metric — RMS wavefront, MTF at the spec
  frequency, or boresight — not the raw merit function.
- **Compensators:** allow back-focus (and maybe one airspace) to adjust, mimicking
  factory focus.
- **Monte Carlo:** run a few hundred trials to get the *statistical* yield (e.g., 90th
  percentile MTF). If yield is poor, loosen the design (slower, more symmetric, fewer
  strong surfaces) — a tolerant design often beats a sharper but fragile one.

---

## 11. Worked micro-example — F/4 singlet, end to end

A compact, runnable-looking script following the KB singlet tutorial: new system, F/4
aperture, three visible wavelengths, three fields, an N-BK7 singlet, variables, wizard,
local optimization, and a performance read-out.

```python
# --- assumes TheApplication / TheSystem / ZOSAPI in scope (boilerplate) ---

# 0) Fresh sequential system
TheSystem.New(False)                       # new system, don't prompt to save  # VERIFY sig
TheSystem.MakeSequential()                 # ensure sequential mode            # VERIFY

# 1) Aperture: F/4
ap = TheSystem.SystemData.Aperture
ap.ApertureType  = ZOSAPI.SystemData.ZemaxApertureType.ImageSpaceFNum
ap.ApertureValue = 4.0

# 2) Wavelengths: F, d, C  (d primary)
wl = TheSystem.SystemData.Wavelengths
wl.GetWavelength(1).Wavelength = 0.4861
wl.AddWavelength(0.5876, 1.0)
wl.AddWavelength(0.6563, 1.0)
wl.GetWavelength(2).MakePrimary()

# 3) Fields: 0, 0.7*5, 5 degrees  (a modest FOV singlet)
f = TheSystem.SystemData.Fields
f.SetFieldType(ZOSAPI.SystemData.FieldType.Angle)
f.GetField(1).X, f.GetField(1).Y = 0.0, 0.0
f.AddField(0.0, 3.5, 1.0)
f.AddField(0.0, 5.0, 1.0)

# 4) Build the singlet in the LDE.
#    A new system has surfaces: 0 = OBJ, 1 = STOP, 2 = IMA.
#    Insert two surfaces so we get: OBJ | front | back(STOP) | IMA.
lde = TheSystem.LDE
lde.InsertNewSurfaceAt(1)                  # front face becomes surface 1
# Object at infinity:
lde.GetSurfaceAt(0).Thickness = float('inf')   # infinite object distance   # VERIFY inf
# Front glass face (surface 1):
s1 = lde.GetSurfaceAt(1)
s1.Comment   = "N-BK7 front"
s1.Radius    = 50.0
s1.Thickness = 5.0
s1.Material  = "N-BK7"
# Back face / stop (surface 2):
s2 = lde.GetSurfaceAt(2)
s2.Comment   = "N-BK7 back (stop)"
s2.Radius    = -200.0
s2.Thickness = 95.0        # back working distance toward image
s2.IsStop    = True

# 5) Variables: both radii + the back airspace (focus).
s1.RadiusCell.MakeSolveVariable()
s2.RadiusCell.MakeSolveVariable()
s2.ThicknessCell.MakeSolveVariable()

# 6) Merit function via the Optimization Wizard (default RMS spot).
mfe = TheSystem.MFE
wiz = mfe.SEQOptimizationWizard
wiz.Apply()
# (Optional) pin the focal length with an EFFL operand:
op = mfe.InsertNewOperandAt(1)
op.ChangeType(ZOSAPI.Editors.MFE.MeritOperandType.EFFL)
op.Target, op.Weight = 100.0, 1.0
print("merit (start):", mfe.CalculateMeritFunction())

# 7) Local optimization (DLS).
tools = TheSystem.Tools
lo = tools.OpenLocalOptimization()
lo.Algorithm     = ZOSAPI.Tools.Optimization.OptimizationAlgorithm.DampedLeastSquares
lo.Cycles        = ZOSAPI.Tools.Optimization.OptimizationCycles.Automatic
lo.NumberOfCores = 8
lo.RunAndWaitForCompletion()
lo.Close()

# 8) Read out performance.
print("merit (optimized):", mfe.CalculateMeritFunction())
spot = TheSystem.Analyses.New_Analysis(ZOSAPI.Analysis.AnalysisIDM.StandardSpot)
spot.ApplyAndWaitForCompletion()
sres = spot.GetResults()
for i in range(sres.SpotData.NumberOfFields):
    print(f"  field {i+1} RMS spot (um):",
          sres.SpotData.GetRMSSpotSizeForField(i + 1))   # VERIFY accessor names
spot.Close()
```

Expected shape of the result: the two radii bend to balance spherical and field
aberrations, the back airspace snaps to best focus, and on-axis RMS spot lands in the
tens-of-microns range (a singlet cannot reach diffraction limit at F/4 — that's the
lesson). Next moves: split into a doublet for chromatic correction, or asphere the front
surface for spherical.

---

## 12. Design checklists & rules of thumb

**Setup**
- State the spec fully before touching the LDE: EFL/mag, F/#, FOV, band, format, MTF/spot
  target, packaging.
- Use **EPD** when the aperture is physical (telescope), **F/#** when speed is quoted
  (camera), **NA** for finite conjugates (microscope).
- Fields at **0 / 0.7 / 1.0** minimum; the 0.7 zone represents the area-average field.
- Visible = **F, d, C** with **d** primary; weight by source × detector.

**Variables & optimization**
- **Vary curvatures before thicknesses**; vary glasses (Substitute) before aspheres.
- Put a **marginal-ray-height focus solve** on the last airspace instead of chasing
  focus with the merit function.
- **Local DLS after every change**; **Hammer** when stalled; **Global** when lost.
- **Glass substitution needs Hammer/Global** — DLS ignores Substitute solves.
- Bound the design (**MNCT / MNET / MXCT**, `TOTR`) *before* running Hammer/Global, or it
  will exploit unphysical shapes.
- Start with **RMS spot**, finish with **RMS wavefront** near diffraction limit.

**Aberration heuristics**
- **Symmetry about the stop** cancels coma, distortion, and lateral color — reach for a
  symmetric form (double Gauss) for fast, wide lenses.
- **Stop position** is a free lever for coma and distortion — move it, don't just bend.
- **Spherical:** split the strong element, or asphere it, before adding glass.
- **Chromatic:** the first cure is an **achromat** (crown high-V + flint low-V).
- **Field curvature dominated → add a field flattener**; flatten Petzval via glass index
  choice, not bending.
- Read the **Seidel diagram** to find the worst aberration *and the surface* making it;
  fix at the source.

**Convergence & handoff**
- If DLS converges but spec isn't met, you're likely in a **local minimum** (try Hammer)
  or **short on degrees of freedom** (add/split an element).
- Match **DOF to constraints**: over-constrained → relax weights; under-constrained → add
  boundaries.
- A **tolerant** design (slower, symmetric, fewer strong surfaces) beats a sharper
  fragile one — confirm with Monte Carlo tolerancing before declaring done.

**Typical F/# strategy**
- Slow (≥F/8): a singlet or doublet often suffices.
- Moderate (F/4–F/5.6): Cooke triplet class.
- Fast (F/2–F/2.8): double Gauss / near-symmetric multi-element.
- Very fast (≤F/1.4): add elements, aspheres, and high-index glasses; expect a long
  Hammer/Global campaign.

---

### References

- Zemax Knowledgebase — **"How to design a singlet lens" / "OpticStudio tutorials,
  Part 1: sequential"** (article **KA-01510** series): the canonical F/4 N-BK7 singlet
  walkthrough this doc's Section 11 mirrors.
- Zemax Knowledgebase — **"How to use glass substitution to optimize designs"** and
  **"Using the Glass Substitution Template"**: the basis for Section 7's Substitute-solve
  + Hammer/Global workflow.
- Companion: `function-to-feature-catalog.md` (authoritative ZOS-API member names —
  resolve every `# VERIFY` against it) and `non-sequential-design.md` (NSC / illumination).
