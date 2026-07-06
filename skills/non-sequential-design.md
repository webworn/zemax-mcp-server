# Non-Sequential Optical Design & Analysis in Zemax OpticStudio

A practical methodology guide for designing and analyzing **non-sequential (NSC)** optical
systems — illumination, stray light, scatter, light pipes, LEDs, and detectors — and for
driving each step through the ZOS-API from Python.

This is the non-sequential companion to `sequential-design.md`. For the exact mapping
between OpticStudio features and API members, cross-check `function-to-feature-catalog.md`.
Where an API member below is marked **[verify]**, confirm the spelling/signature against
that catalog before relying on it in production code.

> **API idioms used here.** Snippets assume a live session exposing `TheSystem`
> (`IOpticalSystem`), `TheApplication`, and the `ZOSAPI` namespace, matching the bridge in
> `zemax_mcp/`. In that bridge the same objects are reachable as `z.system`,
> `z.application`, and `z.zosapi`. Substitute whichever your context provides.

---

## 1. When to use non-sequential mode

In **sequential** mode, rays visit surfaces in a fixed, predetermined order (surface 1, then
2, then 3 …). That is exactly right for imaging systems — cameras, telescopes, microscopes —
where every ray is expected to traverse the same ordered stack of surfaces once.

In **non-sequential** mode, rays travel in true 3D and interact with objects **in the order
they physically strike them**. A ray may hit an object many times, or never; it may split,
scatter, reflect back, or leave the system entirely. There is no predetermined order. This
is the recommended mode whenever geometry — not a fixed surface list — governs where light
goes.

Use non-sequential mode for:

- **Illumination** — LEDs and luminaires, backlights, light pipes / light guides, TIR
  collimator lenses, reflectors, projector engines, automotive lamps.
- **Stray light & ghost analysis** — light reaching the detector by unintended paths:
  ghost reflections between lens surfaces, edge scatter, housing-wall bounces, veiling
  glare.
- **Scattering** — surface and volume scatter (Lambertian, Gaussian, ABg, measured BSDF),
  diffusers, matte housings, particulate media.
- **Detectors / radiometry & photometry** — collecting flux on spatial, angular, or color
  detectors and computing irradiance, intensity, efficiency, and uniformity.
- **Mixed systems** — an imaging lens with a prism, light guide, or scattering element that
  cannot be described as an ordered surface stack (see §10).

**Rule of thumb:** if you care about *where energy lands and how much*, and light can take
more than one path, use non-sequential. If you care about *image quality* along one ordered
path, use sequential.

---

## 2. Core concepts

**Sources, objects, detectors.** A non-sequential system is a collection of three kinds of
objects living in one 3D coordinate space:

- **Sources** emit rays. Each carries a power. In **radiometric** units power is in **watts
  (W)**; in **photometric** units it is in **lumens (lm)**, weighted by the photopic eye
  response. Choose one convention per system and stay consistent.
- **Geometry objects** (lenses, mirrors, pipes, baffles, CAD parts) redirect, transmit,
  reflect, absorb, or scatter rays.
- **Detectors** collect rays and accumulate flux, spatially and/or angularly.

**3D propagation & physical-order interaction.** The tracer launches a ray from a source and
finds the *next* object surface it hits anywhere in space, applies that surface's optical
behavior, and repeats until the ray is absorbed, escapes the system, or exhausts its energy
/ bounce budget. Nothing about the object list order dictates the sequence — geometry does.

**Ray splitting.** With splitting enabled, a parent ray striking an interface divides into
child rays according to Fresnel equations and any coating: a **transmitted** (refracted)
child, a **reflected** child, optionally a **scattered** child, with the balance
**absorbed**. Each child carries a fraction of the parent's power and is traced onward. This
is what makes ghost and stray-light energy quantitative.

> Ray splitting is turned on by the **Split NSC Rays** option in the ray-trace control
> (§6) — it is a geometric/energy-partitioning feature driven by Fresnel and coating data.
> It is *not* something you enable by turning on polarization; polarization ray tracing is a
> separate, independent option that refines the Fresnel/coating coefficients but does not
> "create" splitting.

**Radiometry vs photometry.** Radiometric analysis reports watts and derived quantities
(W/m² irradiance, W/sr intensity). Photometric analysis reports lumens and derived
quantities (lux, candela). The unit choice is a system/analysis setting; the ray mechanics
are identical.

**Coordinate systems & placement.** Every NSC object has explicit position and rotation
columns — **X, Y, Z** position and **Tilt About X / Y / Z** — defined relative to the global
coordinate frame or to a preceding object's frame (via a *reference object*). A dedicated
**Null Object** or **Coordinate Break**-like reference can group and move sub-assemblies.
Getting placement right is most of the work in NSC modeling.

**NCE vs LDE.** Sequential systems live in the **Lens Data Editor (LDE)**, one row per
surface, in order. Non-sequential systems live in the **Non-Sequential Component Editor
(NCE)**, one row per object, order-independent. `TheSystem.NCE` is the NSC analogue of
`TheSystem.LDE`.

---

## 3. Building a non-sequential system

### Switch the system into non-sequential mode

```python
# Convert the whole system to pure non-sequential mode.
TheSystem.MakeNonSequential()

nce = TheSystem.NCE          # the Non-Sequential Component Editor
print("objects:", nce.NumberOfObjects)
```

### Adding and addressing objects

Objects are addressed by 1-based index. New rows are inserted, then their **type** is set by
applying a type-settings object obtained from the object's `GetObjectTypeSettings` factory
**[verify: exact type-settings enum names in the catalog]**.

```python
def add_object(nce, obj_type_enum):
    """Insert a new NCE row at the end and set its object type. Returns the object."""
    idx = nce.NumberOfObjects + 1
    nce.InsertNewObjectAt(idx)               # [verify signature]
    obj = nce.GetObjectAt(idx)
    settings = obj.GetObjectTypeSettings(obj_type_enum)   # [verify]
    obj.ChangeType(settings)                 # [verify]
    return obj

# Object type enums live under ZOSAPI.Editors.NCE.ObjectType.* [verify names]
OT = ZOSAPI.Editors.NCE.ObjectType
```

Each object exposes:

- `TypeName`, `Comment`, `Material` — informational / material assignment.
- Position: `XPosition`, `YPosition`, `ZPosition`.
- Rotation: `TiltAboutX`, `TiltAboutY`, `TiltAboutZ`.
- `RefObject` — index of the object whose frame this one is defined in (0 = global).
- `ObjectData` — a type-specific view exposing that object's parameter columns (radii,
  dimensions, ray counts, power, file names, etc.).

```python
src = nce.GetObjectAt(1)
src.XPosition = 0.0
src.YPosition = 0.0
src.ZPosition = 0.0
src.TiltAboutX = 0.0
```

### Key SOURCE objects

| Object | Typical use | Notes |
|---|---|---|
| **Source Point** | idealized point emitter, quick tests | isotropic or cone-limited |
| **Source Ellipse** | round emitting area (LED die, fiber face) | area source, set half-widths |
| **Source Rectangle** | rectangular emitting area (chip, panel) | area source |
| **Source File** | measured ray set (`.DAT`/`.SDF`) | vendor LED ray files |
| **Source IESNA / Radial** | photometric distribution (`.IES`/`.LDT`) | luminaire far-field data |
| **Source DLL / Diode** | parametric LED / laser models | angular apodization |

Every source has two ray-count columns that matter:

- **Layout Rays** — a small number (dozens) drawn in 3D layout/shaded views for visual
  sanity checking.
- **Analysis Rays** — the large number actually launched during a ray trace (thousands to
  millions), plus a **Power** value in W or lm.

```python
src.ObjectData.NumberOfLayoutRays   = 50          # [verify member names]
src.ObjectData.NumberOfAnalysisRays = 2_000_000
src.ObjectData.SourcePower          = 1.0         # watts (or lumens)
```

### Key DETECTOR objects

| Object | Measures | Use |
|---|---|---|
| **Detector Rectangle** | spatial irradiance/illuminance **and** angular intensity | the workhorse |
| **Detector Color** | true-color (X,Y,Z / RGB) spatial data | color mixing, uniformity |
| **Detector Polar / Surface** | angular or curved-surface collection | far-field, domes |
| **Detector Volume** | 3D flux density | volumetric media |

A **Detector Rectangle** is divided into pixels (`NumberXPixels` × `NumberYPixels`). It can
be read as an **irradiance/position** map or, using its angular bins, as an
**intensity/angle** map.

```python
det = add_object(nce, OT.DetectorRectangle)          # [verify]
det.ZPosition = 100.0
det.ObjectData.XHalfWidth   = 25.0                    # [verify member names]
det.ObjectData.YHalfWidth   = 25.0
det.ObjectData.NumberXPixels = 200
det.ObjectData.NumberYPixels = 200
```

### Key GEOMETRY objects

- **Standard Lens** — a lens with front/back radii, conic, thickness, clear/edge apertures
  (the NSC counterpart of a sequential lens).
- **Aspheric / Toroidal Lens** — freeform and toroidal surfaces for collimators, cylinder
  optics.
- **Cylinder / Rectangle / Annulus** — primitive volumes and apertures used as baffles,
  masks, and housing walls.
- **Light Pipe** — a solid TIR guide with entrance/exit faces and taper; the backbone of
  backlight and mixing-rod designs.
- **CAD Part: STEP/IGES/STL** — imported mechanical geometry (housings, reflectors) with
  optical faces assigned properties.

Place each object with its position/rotation columns; use `RefObject` to lock sub-assemblies
together so moving the parent moves the children.

---

## 4. Material & optical properties

An NSC object's `Material` column controls how its faces behave:

- A **glass name** (e.g. `N-BK7`) or `MIRROR`, `ABSORB`, or blank (air/vacuum).
- Material determines refraction; **coatings** and face properties determine the split
  between reflected, transmitted, and absorbed energy.

```python
lens = nce.GetObjectAt(2)
lens.Material = "N-BK7"
```

**Face properties.** Most objects have multiple **faces** (e.g. front, back, edge). Through
the object's face/coating settings you assign:

- **Coating** — a named coating from the coating file (AR, HR, metal) that sets
  wavelength-dependent R/T.
- **Reflect / Transmit / Absorb behavior** — you can force a face to be a perfect mirror,
  perfect absorber, or let Fresnel + coating decide.
- **Scatter model** (see §5) — attached per face.

```python
# Face access is via the object's coating/scatter settings interface.
# Exact members vary by object; confirm in the catalog. [verify]
face = lens.CoatScatterData.GetFaceData(0)     # [verify: face indexing / accessor]
face.Coating = "AR"
```

**When child rays are generated.** Child rays (reflected/transmitted/scattered) are only
created **when Split NSC Rays is enabled** in the ray trace (§6). Without splitting, at each
interface the tracer follows the single most-probable outcome (typically refraction) and the
ray carries full power — fast, but it cannot quantify ghosts or partial reflections. With
splitting on, every interface can spawn children weighted by the face's R/T/A coefficients,
and a **minimum relative ray intensity** threshold prunes children too dim to matter.

---

## 5. Scattering

Scatter turns a specular interaction into a distribution of directions. Assign a scatter
model **per face**:

| Model | Shape | Typical use |
|---|---|---|
| **Lambertian** | cosine, fully diffuse | matte paint, white housings, diffuse reflectors |
| **Gaussian** | narrow lobe about specular | lightly roughened / polished-but-imperfect |
| **ABg** | parametric BSDF (A, B, g) | measured scatter fit; smooth-to-rough surfaces |
| **BSDF / measured** | tabulated distribution | scatterometer data, diffuser films |

Each face also has a **scatter fraction** — the probability that an incident ray scatters
rather than reflects/refracts specularly.

```python
# Attach a Lambertian scatter to a face, 100% of rays scatter. [verify accessors]
face.ScatterModel   = "Lambertian"
face.ScatterFraction = 1.0
```

**Importance sampling.** A Lambertian surface throws rays into a full hemisphere; only a tiny
fraction happen to reach a small detector, so naive scatter traces are extremely noisy.
**Importance sampling** aims a controlled share of scattered rays toward one or more target
objects (usually the detector), then reweights them to keep the result unbiased. This can
cut the ray count needed for a given noise level by orders of magnitude.

```python
# Point this face's scatter toward the detector object for variance reduction. [verify]
face.ImportanceSampling.TargetObject = det_index   # object to aim scattered rays at
face.ImportanceSampling.Size         = 30.0        # angular size of the importance cone
```

**Enabling scatter in the trace.** Scatter only happens when **Scatter NSC Rays** is enabled
in the ray-trace control (§6). Scatter and splitting are independent toggles; you commonly
run **both** on for stray-light work (a ray can partially reflect *and* scatter).

---

## 6. Running the ray trace

The non-sequential ray trace is a tool, opened, configured, run, then closed.

```python
rt = TheSystem.Tools.OpenNSCRayTrace()

rt.SplitNSCRays   = True     # generate transmit/reflect/absorb child rays (Fresnel/coating)
rt.ScatterNSCRays = True     # allow faces with a scatter model to scatter
rt.UsePolarization = False   # track polarization; refines Fresnel/coating, costs time
rt.IgnoreErrors    = True    # skip rays that error out instead of aborting the trace

# Optional: save the traced rays to a .ZRD ray database for path analysis / replay.
# rt.SaveRays = True
# rt.SaveRaysFile = "trace.ZRD"     # [verify member names]

rt.ClearDetectors(0)          # zero all detectors before accumulating [verify signature]
rt.RunAndWaitForCompletion()
rt.Close()
```

**Layout rays vs analysis rays.** The layout ray count only affects 3D views; the trace
launches each source's **analysis ray** count. Set analysis rays high enough for the metric
you care about (§12).

**Monte-Carlo convergence.** A non-sequential trace is a Monte-Carlo experiment: detector
values carry statistical noise that falls as **1/√N** in the ray count `N`. To halve the
noise you must roughly **quadruple** the rays. Splitting and scatter increase the *effective*
work per launched ray (more children), improving energy accounting but adding cost.

**Multiple runs / accumulation.** Detectors accumulate. You can run several traces (e.g.
different sources) without clearing to sum contributions, or clear between runs to isolate
them.

---

## 7. Reading results

### Detector Viewer

The **Detector Viewer** displays a chosen detector as:

- **Incoherent Irradiance / Illuminance** — flux per area vs position (the spatial map).
- **Radiant / Luminous Intensity** — flux per solid angle vs angle (the angular map).
- **True Color** — for Detector Color, an RGB-rendered spatial map.
- **Cross-sections and false-color** plots for uniformity inspection.

### Extracting detector data via the API

Detector pixel data and summary flux are read with `NSDD`-style detector queries. Through the
API, the batch accessor returns all pixels at once:

```python
# Total flux on a detector = grid-value type 0 (or the detector's summary member). [verify]
det_index = 3
success, data = TheSystem.NCE.GetDetectorData(det_index, -1, 0)   # [verify signature]
# Some API versions expose GetAllDetectorData / GetDetectorGrid returning a 2D array.

# Per-pixel map (Pythonic loop; prefer the batch accessor when available):
nx = det.ObjectData.NumberXPixels
ny = det.ObjectData.NumberYPixels
total = 0.0
for pix in range(1, nx * ny + 1):
    ok, val = TheSystem.NCE.GetDetectorData(det_index, pix, 0)     # [verify]
    total += val
print("total flux on detector:", total)     # W or lm depending on source units
```

The **NSDD** merit-function operand is the optimization-time analogue: `NSDD(surf, det,
pix, data)` returns a single detector value (total flux, a pixel value, centroid, RMS, etc.)
for use in a merit function (§9).

### Flux, efficiency, and distribution

- **Efficiency** = flux collected on the target detector ÷ total source power. Track it as
  the primary illumination figure of merit.
- **Uniformity** = min/mean or (max−min)/(max+min) across the detector's active pixels.
- **Angular distribution** — read the detector's intensity (angle-space) data for beam
  divergence, FWHM, or far-field candela plots.
- **False-color / cross-section** plots expose hot spots, roll-off, and ghost images.

---

## 8. Stray light & ghost analysis workflow

### Convert a sequential design to an NSC group

Stray-light study usually starts from an existing sequential lens. Rather than rebuild it,
convert it:

- **Convert to NSC Group** wraps the sequential surfaces into a single non-sequential group
  inside an otherwise sequential (or fully NSC) system, so real 3D bounces and ghosts can be
  traced through the actual lens geometry.

```python
# Tools → Convert to NSC Group (converts a range of sequential surfaces). [verify tool name]
conv = TheSystem.Tools.OpenConvertToNSCGroup()      # [verify]
conv.RunAndWaitForCompletion()
conv.Close()
```

### Filter strings

**Ray filter strings** select which traced ray paths contribute to a plot or detector, so
you can isolate a specific stray-light mechanism:

- `Hn` — rays that **hit object n** (e.g. `H12` = rays that struck object 12).
- `G0` — **ghost** segments: rays that reflected off surfaces they should have transmitted
  through (double-bounce ghosts). Broadly, `Gn` targets ghost order.
- `Xn` — rays that hit object n but were **not** required to (context-dependent).
- Combine with boolean operators: `H5 & H9` (hit both), `H5 & ~H9` (hit 5 but not 9),
  `G0 & H12` (ghost paths that also reach object 12).

Filter strings are applied in the Detector Viewer settings or via the analysis/ray-database
filter field **[verify: API member for the filter string]**.

### Path Analysis (Premium)

**Path Analysis** (OpticStudio **Premium** tier) mines a saved ray database (`.ZRD`) and
**ranks every distinct object-hit path by the flux it delivers** to the detector. This turns
"there's stray light somewhere" into a sorted list: path #1 might be a specific
lens-surface-to-housing-wall ghost carrying 2% of source flux.

Workflow:

1. Run the trace with `SaveRays = True` to a `.ZRD` file (§6).
2. Open Path Analysis on that database; it enumerates and ranks paths by flux.
3. Read off the worst offenders and their object sequences.
4. **Fix the path**: add a **baffle/vane**, blacken (`ABSORB`) or roughen an offending face,
   improve an **AR coating** to kill a ghost reflection, or reshape a mechanical edge.
5. Re-trace and confirm the path's flux dropped.

This is the standard loop for reducing **veiling glare** (a low-contrast wash of scattered
light across the image) and localized ghost images.

---

## 9. Illumination design workflow

The goal is usually **efficiency** (get as much source flux as possible onto the target) and
**uniformity** (spread it evenly), or a specified **intensity distribution**.

1. **Model** source, optics, and target detector (§3).
2. **Trace** with realistic ray counts; read efficiency and uniformity (§7).
3. **Optimize** geometry against a non-sequential merit function.

### NSC merit-function operands

- **`NSDD`** — Non-Sequential Detector Data: returns total flux, a pixel value, centroid, or
  RMS spot on a detector. The core building block for illumination targets.
- **`NSTR`** — triggers a non-sequential ray trace inside the merit function so that
  downstream `NSDD` operands read fresh detector data. **[verify operand name/args]**
- Compose these into targets: maximize `NSDD` total flux (efficiency), drive pixel-to-pixel
  spread toward zero (uniformity), or match an angular profile.

```python
mf = TheSystem.MFE
# Conceptual: an NSTR row runs the trace, NSDD rows read the detector afterward. [verify]
row = mf.AddOperand()
row.ChangeType(ZOSAPI.Editors.MFE.MeritOperandType.NSDD)   # [verify enum path]
# set surface/detector/pixel/data columns, target, and weight via row.GetOperandCell(...)
```

> **Optimization is slow in NSC.** Every merit-function evaluation launches a full
> Monte-Carlo ray trace, and the noise floor of that trace becomes noise in the merit
> function. Use enough rays that the metric is stable between identical evaluations, favor
> **global/hammer** optimizers sparingly, and lean on importance sampling (§5) to keep each
> evaluation affordable. Parameterize as few, well-chosen variables as possible.

**TIR collimators & freeform reflectors (high level).** A TIR ("total internal reflection")
collimator surrounds an LED with a solid dielectric whose outer wall collimates by TIR while
a central lens collimates the forward cone — modeled as a rotationally symmetric or freeform
NSC solid. Freeform reflectors are built as CAD or parametric freeform surfaces and tuned
with `NSDD` uniformity/efficiency targets. Both are classic NSC-only design problems.

---

## 10. Mixed-mode systems

Sometimes only *part* of a system is non-sequential — an imaging camera that contains a
prism, a light guide, or a scattering baffle. OpticStudio supports a **Non-Sequential
Component group embedded in a sequential system**: a single surface in the LDE of type
**Non-Sequential Component** contains its own NCE, and rays enter the group, propagate
non-sequentially inside it, then exit back into the ordered sequential stack.

Guidance:

- **Convert a part, keep imaging sequential** when the bulk of the system is a well-behaved
  imaging lens and only a localized region (prism, TIR fold, light pipe, a scattering
  surface whose ghost you want) needs 3D freedom. Keeps optimization fast and image-quality
  operands available. Use **Convert to NSC Group** on just that surface range.
- **Convert the whole system to non-sequential** (`MakeNonSequential()`) when light genuinely
  has many paths everywhere — pure illumination, or a stray-light audit of the entire
  housing — and per-surface image metrics are no longer the point.

The embedded-group approach is the usual way to get quantitative **ghost and scatter**
numbers for an imaging lens without abandoning sequential aberration control.

---

## 11. A worked micro-example

A source rectangle illuminates a diffuser/lens, and a detector rectangle downstream collects
the flux. This is intentionally compact but structured like real ZOS-API code; treat member
names flagged **[verify]** as items to confirm against the catalog.

```python
import clr  # pythonnet, if driving a standalone connection

# --- assume TheSystem (IOpticalSystem) and ZOSAPI namespace are already in scope ---

OT = ZOSAPI.Editors.NCE.ObjectType     # [verify enum path]

def add_object(nce, obj_type):
    idx = nce.NumberOfObjects + 1
    nce.InsertNewObjectAt(idx)                     # [verify]
    obj = nce.GetObjectAt(idx)
    obj.ChangeType(obj.GetObjectTypeSettings(obj_type))   # [verify]
    return idx, obj

# 1) Go non-sequential and grab the NCE.
TheSystem.MakeNonSequential()
nce = TheSystem.NCE
# Object 1 already exists after MakeNonSequential; make it our source.
src = nce.GetObjectAt(1)
src.ChangeType(src.GetObjectTypeSettings(OT.SourceRectangle))   # [verify]

# 2) Source rectangle at the origin, emitting +Z, 1 W, 2M analysis rays.
src.XPosition = src.YPosition = src.ZPosition = 0.0
src.ObjectData.XHalfWidth = 5.0            # [verify member names]
src.ObjectData.YHalfWidth = 5.0
src.ObjectData.NumberOfLayoutRays   = 50
src.ObjectData.NumberOfAnalysisRays = 2_000_000
src.ObjectData.SourcePower = 1.0           # watts

# 3) A diffusing lens 20 mm downstream (N-BK7, front face Lambertian scatter).
lens_i, lens = add_object(nce, OT.StandardLens)     # [verify]
lens.ZPosition = 20.0
lens.Material  = "N-BK7"
lens.ObjectData.Radius1 =  50.0            # [verify]
lens.ObjectData.Radius2 = -50.0
lens.ObjectData.Thickness = 4.0
lens.ObjectData.ClearAperture1 = 12.0
# front face scatter + importance sampling toward the detector (set after det exists)

# 4) A detector rectangle 100 mm downstream.
det_i, det = add_object(nce, OT.DetectorRectangle)  # [verify]
det.ZPosition = 100.0
det.ObjectData.XHalfWidth = 25.0
det.ObjectData.YHalfWidth = 25.0
det.ObjectData.NumberXPixels = 200
det.ObjectData.NumberYPixels = 200

# 4b) Aim the lens's scattered rays at the detector to cut Monte-Carlo noise.
face = lens.CoatScatterData.GetFaceData(0)          # [verify accessor]
face.ScatterModel = "Lambertian"
face.ScatterFraction = 0.2
face.ImportanceSampling.TargetObject = det_i        # [verify]

# 5) Trace with splitting + scatter enabled.
rt = TheSystem.Tools.OpenNSCRayTrace()
rt.SplitNSCRays   = True
rt.ScatterNSCRays = True
rt.UsePolarization = False
rt.IgnoreErrors    = True
rt.ClearDetectors(0)                                # [verify]
rt.RunAndWaitForCompletion()
rt.Close()

# 6) Read total flux on the detector and report efficiency.
total = 0.0
npix = det.ObjectData.NumberXPixels * det.ObjectData.NumberYPixels
for pix in range(1, npix + 1):
    ok, val = TheSystem.NCE.GetDetectorData(det_i, pix, 0)   # [verify signature]
    total += val

source_power = 1.0
print(f"Collected flux : {total:.4f} W")
print(f"Efficiency     : {100.0 * total / source_power:.1f} %")
```

Expected shape of the result: a roughly Gaussian/diffuse irradiance blob on the detector,
total flux below the 1 W launched (some absorbed/back-reflected/escaped), and an efficiency
number you then try to improve by reshaping the lens or repositioning the detector.

---

## 12. Performance & accuracy rules of thumb

**Ray counts.**

- Layout/prototyping: 10³–10⁴ analysis rays — fast, coarse, good for geometry debugging.
- Working analysis: 10⁵–10⁶ rays — smooth spatial maps for most illumination work.
- Publication / uniformity spec / far-field tails: 10⁷+ rays, especially with scatter.
- Noise falls as **1/√N**: 4× rays ≈ half the noise. Decide the noise you can tolerate on
  your *metric*, not on the whole map, then size N accordingly.

**Importance sampling is the biggest lever.** For any scatter problem with a small detector,
importance sampling (§5) toward the detector can replace tens of millions of blind rays with
a fraction of that at the same noise. Always use it for diffuse-surface-to-detector paths.

**Splitting vs scatter cost.**

- **Splitting** multiplies rays at every interface; set a sensible **minimum relative ray
  intensity** so negligibly dim children are pruned. Turn splitting off when you only need
  first-order illumination geometry (no ghosts), on when you need energy accounting.
- **Scatter** is expensive because scattered rays fan out; pair it with importance sampling
  and only enable it where a face actually scatters.
- For stray-light audits you typically need **both** on — accept the cost and lean on
  importance sampling and filtering.

**Reduce Monte-Carlo noise by:** more rays, importance sampling, larger detector pixels
(coarser bins average more rays each), restricting the source's emission cone to the useful
solid angle, and pruning dim child rays with the intensity threshold.

**GPU ray tracing.** OpticStudio can offload non-sequential ray tracing to a **GPU**, which
can accelerate large scatter/splitting traces by 1–2 orders of magnitude over CPU. It is
subject to feature/precision limitations (not every object, scatter model, or polarization
option is supported), so validate a GPU result against a smaller CPU trace before trusting
it for a spec. **[verify: current GPU support matrix and API toggle in the catalog.]**

**Sanity checks before trusting numbers.**

- Run the same trace twice; if the metric moves more than your spec tolerance, you need more
  rays.
- Confirm **energy conservation**: collected + absorbed + escaped ≈ launched power.
- Inspect the 3D layout with layout rays — most "wrong flux" bugs are placement/orientation
  errors, not physics.
- Verify units (W vs lm) match between source power and the numbers you report.
```
