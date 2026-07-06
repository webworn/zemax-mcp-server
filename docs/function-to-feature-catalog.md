# ZOS-API Function-to-Feature Catalog

A master map coupling **Ansys Zemax OpticStudio 2025 R2** GUI features to their
**ZOS-API (.NET automation)** object-model members. Everything below is rooted at
`ZOSAPI.IOpticalSystem`, which the connection layer exposes as **`TheSystem`**
(`z.system` in this project), obtained from **`TheApplication`**
(`ZOSAPI.IZOSAPI_Application`, `z.application`).

> Canonical reference: `ZOS-API.chm` at
> `C:\Program Files\Ansys Zemax OpticStudio 2025 R2.02\ZOS-API\Help\ZOS-API.chm`.
> When a member below is flagged **[confirm]**, the exact name/signature should be
> verified there before relying on it in code.

---

## How Claude should use this catalog

When a user asks for an optical-design action, find the matching **GUI feature** row,
read the **ZOS-API path** to know which member to call, and follow the **use-case**
column for the intended calling pattern. Enums live under the `ZOSAPI.*` namespace
(e.g. `ZOSAPI.SystemData.ZemaxApertureType`, `ZOSAPI.Analysis.AnalysisIDM`) and are
resolved by name via `getattr(ZOSAPI, ...)`. Treat rows marked **[confirm]** as
"probably correct but verify in the .chm before shipping code that depends on the
exact spelling," and never invent an enum value — enumerate the enum at runtime
(`dir()` / reflection) if unsure.

---

## 1. Connection & Application

Entry points before `TheSystem` exists. `ZOSAPI_Connection` is the bootstrap object
returned by the `ZOSAPI.dll` assembly.

| GUI feature / concept | ZOS-API path | What it does | Typical Claude use-case |
|---|---|---|---|
| (bootstrap object) | `ZOSAPI.ZOSAPI_Connection()` | Concrete connection factory. | First call after loading the assemblies. |
| Programming ▸ Interactive Extension | `connection.ConnectAsExtension(0)` → `IZOSAPI_Application` | Attaches to an **already-running** OpticStudio GUI so edits appear live. User must arm the extension in the GUI first. | Interactive "watch it change" sessions. |
| (headless launch) | `connection.CreateNewApplication()` → `IZOSAPI_Application` | Spawns a **fresh headless** OpticStudio process owned by the script. Needs Professional/Premium/Enterprise license. | Batch/automation, CI, no GUI. |
| License valid for API | `TheApplication.IsValidLicenseForAPI` (bool) | True if the license tier permits API use. | Guard before doing real work. |
| License edition | `TheApplication.LicenseStatus` → `ZOSAPI.LicenseStatusType` | Enum of edition (Premium/Enterprise/Professional/Standard/HPC). | Report capabilities to user. |
| Serial number | `TheApplication.SerialCode` | License serial string. | Diagnostics. |
| Samples folder | `TheApplication.SamplesDir` | Path to the shipped `Samples` directory. | Locate example `.zmx`/`.zos` files to open. |
| Primary system | `TheApplication.PrimarySystem` → `IOpticalSystem` | The active optical system = **`TheSystem`**. | Every design operation starts here. |
| Close application | `TheApplication.CloseApplication()` | Terminates the process (standalone only). | Clean shutdown of headless sessions. |
| File ▸ New | `TheSystem.New(saveIfNeeded: bool)` | Starts a new empty (sequential) system. | Begin a design from scratch. |
| File ▸ Open | `TheSystem.LoadFile(path, saveIfNeeded: bool)` | Loads a `.zmx`/`.zos`. | Open an existing design. |
| File ▸ Save | `TheSystem.Save()` | Saves in place. | Persist edits. |
| File ▸ Save As | `TheSystem.SaveAs(path)` | Saves to a new path. | Fork a design / save results. |
| (close a system) | `TheSystem.Close(saveIfNeeded: bool)` **[confirm]** | Closes a secondary system. | Multi-system workflows. |
| Current file | `TheSystem.SystemFile` | Full path of the loaded file. | Report state. |
| Sequential vs NSC | `TheSystem.Mode` → `ZOSAPI.SystemType` (`Sequential` / `NonSequential`) | Reports current mode. | Branch logic by mode. |
| Make non-sequential | `TheSystem.MakeNonSequential()` | Converts/creates a pure NSC system. | Illumination / stray-light work. |
| Update behavior | `TheSystem.UpdateMode` → `ZOSAPI.LensUpdateMode` (`EditorsOnly`, `AllWindows`, `None`) | Controls how much redraws on each edit. `EditorsOnly` keeps live GUI cheap. | Set once at session start. |
| Force recompute | `TheSystem.UpdateStatus()` **[confirm]** | Forces a system update / recompute. | After a batch of edits before reading results. |

---

## 2. System Explorer / SystemData

`TheSystem.SystemData` (`ISystemData`) mirrors the **System Explorer** tree.

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Aperture ▸ Aperture Type | `SystemData.Aperture.ApertureType` → `ZOSAPI.SystemData.ZemaxApertureType` | Sets how the entrance pupil is defined. Values: `EntrancePupilDiameter`, `ImageSpaceFNum`, `ObjectSpaceNA`, `FloatByStopSize`, `ParaxialWorkingFNum`, `ObjectConeAngle`. | Define the pupil for a lens. |
| Aperture ▸ Aperture Value | `SystemData.Aperture.ApertureValue` (double) | The numeric value paired with the type. | Set EPD = 10 mm, etc. |
| Aperture ▸ Apodization | `SystemData.Aperture.ApodizationType` / `ApodizationFactor` **[confirm]** | Gaussian/uniform pupil apodization. | Model Gaussian-beam illumination. |
| Wavelengths ▸ count | `SystemData.Wavelengths.NumberOfWavelengths` | How many wavelengths defined. | Loop over wavelengths. |
| Wavelengths ▸ get one | `SystemData.Wavelengths.GetWavelength(i)` → `IWavelength` (1-based) | Access a row: `.Wavelength` (µm), `.Weight`. | Read/set a wavelength. |
| Wavelengths ▸ add | `SystemData.Wavelengths.AddWavelength(wl_um, weight)` | Appends a wavelength row. | Build a spectral set. |
| Wavelengths ▸ remove | `SystemData.Wavelengths.RemoveWavelength(i)` **[confirm]** | Deletes a row. | Prune wavelengths. |
| Wavelengths ▸ primary | `IWavelength.MakePrimary()` or `Wavelengths.MakePrimary(i)` **[confirm]** | Sets the primary wavelength. | Choose reference λ. |
| Wavelengths ▸ preset | `SystemData.Wavelengths.SelectWavelengthPreset(ZOSAPI.SystemData.WavelengthPreset.<x>)` | Loads F,d,C etc. presets. | Quick standard spectra. |
| Fields ▸ field type | `SystemData.Fields.SetFieldType(ZOSAPI.SystemData.FieldType.<x>)` | Values: `Angle`, `ObjectHeight`, `ParaxialImageHeight`, `RealImageHeight`, `TheodoliteAngle`. | Choose how fields are specified. |
| Fields ▸ get one | `SystemData.Fields.GetField(i)` → `IField` (1-based) | Access `.X`, `.Y`, `.Weight`, vignetting factors `VDX/VDY/VCX/VCY/VAN`. | Read/set a field point. |
| Fields ▸ add | `SystemData.Fields.AddField(x, y, weight)` | Appends a field. | Build field set. |
| Fields ▸ remove | `SystemData.Fields.RemoveField(i)` **[confirm]** | Deletes a field. | Prune fields. |
| Fields ▸ normalization | `SystemData.Fields.Normalization` → `ZOSAPI.SystemData.FieldNormalizationType` (`Radial`/`Rectangular`) | Field normalization convention. | Match GUI plots. |
| Units | `SystemData.Units.LensUnits` → `ZOSAPI.SystemData.ZemaxSystemUnits` (`Millimeters`/`Centimeters`/`Inches`/`Meters`) | Lens/source/analysis units. | Set mm before entering values. |
| Environment | `SystemData.Environment` (`.Temperature`, `.Pressure`, `.AdjustIndexToEnvironment`) | Thermal/pressure environment. | Thermal analysis. |
| Polarization | `SystemData.Polarization` (`.Method`, `.JonesX/JonesY`, `.Unpolarized`) **[confirm]** | Default polarization state. | Polarization ray tracing. |
| Ray Aiming | `SystemData.RayAiming.RayAiming` → `ZOSAPI.SystemData.RayAimingMethod` (`Off`/`Paraxial`/`Real`) | Enables ray aiming for accurate pupils. | High-NA / wide-field systems. |
| Title / Notes | `SystemData.TitleNotes.Title` **[confirm]** | System title metadata. | Label the design. |

---

## 3. Lens Data Editor (LDE)

`TheSystem.LDE` (`ILensDataEditor`). **Surfaces are 0-based** via `GetSurfaceAt`.

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Surface count | `LDE.NumberOfSurfaces` | Includes OBJ (0) and IMA (last). | Iterate the stack. |
| Get a surface | `LDE.GetSurfaceAt(index)` → `ILDERow` (0-based) | Access one surface row. | Read/write a surface. |
| Insert surface | `LDE.InsertNewSurfaceAt(index)` | Inserts before `index`. | Add an element. |
| Remove surface | `LDE.RemoveSurfaceAt(index)` | Deletes a surface. | Simplify a design. |
| Add at end | `LDE.AddSurface()` **[confirm]** | Appends a surface. | Grow the stack. |
| Radius | `ILDERow.Radius` (double) | Radius of curvature. | Set/read curvature. |
| Thickness | `ILDERow.Thickness` (double) | Axial thickness / air gap. | Set spacing. |
| Material / Glass | `ILDERow.Material` (string) | Glass name, e.g. `"N-BK7"`, `"AIR"`. | Assign glass. |
| Comment | `ILDERow.Comment` (string) | Row label. | Annotate. |
| Semi-diameter | `ILDERow.SemiDiameter` (double) | Aperture half-height. | Read clear aperture. |
| Conic | `ILDERow.Conic` (double) | Conic constant. | Aspheric design. |
| Stop flag | `ILDERow.IsStop` (bool) | Marks/reports the STOP surface. | Set aperture stop. |
| Surface number | `ILDERow.SurfaceNumber` | Its index in the editor. | Cross-reference. |
| Change surface type | `ILDERow.ChangeType(newSurfaceType)` where `newSurfaceType = ILDERow.GetSurfaceTypeSettings(ZOSAPI.Editors.LDE.SurfaceType.<x>)` | Converts surface type (Standard, EvenAspheric, Toroidal, CoordinateBreak, Paraxial, DiffractionGrating, …). | Make a surface aspheric etc. |
| Extra parameters | `ILDERow.GetSurfaceCell(ZOSAPI.Editors.LDE.SurfaceColumn.Par1…ParN)` **[confirm]** | Type-specific parameter columns (aspheric coeffs, CB tilts). | Set aspheric terms. |

### Cells and solves

Each editable value has a **`IEditorCell`** exposing solves. Cell accessors on `ILDERow`:

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Radius cell | `ILDERow.RadiusCell` → `IEditorCell` | Cell for radius (value + solve). | Attach a solve/variable. |
| Thickness cell | `ILDERow.ThicknessCell` | Cell for thickness. | Variable / pickup on spacing. |
| Conic cell | `ILDERow.ConiccCell` / `ConicCell` **[confirm spelling]** | Cell for conic. | Vary conic. |
| Make variable | `cell.MakeSolveVariable()` | Marks the value as an optimization variable. | Set up before optimizing. |
| Set/get solve | `cell.GetSolveData()` / `cell.SetSolveData(ISolveData)` | Reads/writes the solve object. | Program a solve. |
| Solve type enum | `ZOSAPI.Editors.SolveType` — `Variable`, `Fixed`, `MarginalRayHeight`, `ChiefRayHeight`, `Pickup`, `PositionSolve`, `MarginalRayAngle`, `EdgeThickness`, `Automatic`, `Maximum`, … | Enumerates solve kinds. | Height solve on last thickness (focus), pickups for symmetry. |
| Clear a solve | `cell.MakeSolveFixed()` **[confirm]** | Removes variable/solve. | Freeze a parameter. |

> **Cell name caution:** the exact `*Cell` property spellings (`RadiusCell`,
> `ThicknessCell`, `ConicCell`) and `MakeSolveVariable` are widely used and
> high-confidence, but verify `ConicCell` capitalization in the .chm. The solve-data
> objects live under `ZOSAPI.Editors.<X>.ISolve*` and vary by solve type.

---

## 4. Merit Function Editor (MFE)

`TheSystem.MFE` (`IMeritFunctionEditor`).

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Current merit value | `MFE.CalculateMeritFunction()` (double) | Evaluates and returns the merit. | Report design quality. |
| Optimization Wizard | `MFE.SEQOptimizationWizard` → `ISEQOptimizationWizard` | The sequential merit-function wizard. Set its properties then `.Apply()` (or `.CommonSettings`). | Auto-build an RMS-spot/wavefront merit function. |
| Wizard ▸ apply | `MFE.SEQOptimizationWizard.Apply()` | Generates operands into the MFE. | One-click default MF. |
| Wizard settings | `SEQOptimizationWizard.Type`, `.Data`, `.Reference`, `.PupilIntegrationMethod`, `.Rings`, `.Arms`, `.IsGlassUsed`, `.IsAirUsed`, `.GlassMin/Max`, `.AirMin/Max` **[confirm names]** | Criterion (RMS/PTV), spot/wavefront, ring/arm sampling, boundary constraints. | Configure the auto MF. |
| Get an operand | `MFE.GetOperandAt(row)` → `IMFERow` (1-based) | Access one operand row. | Read operand value/target. |
| Insert operand | `MFE.InsertNewOperandAt(row)` → `IMFERow` | Insert a blank operand. | Add a constraint. |
| Add operand (end) | `MFE.AddOperand()` → `IMFERow` **[confirm]** | Append an operand. | Grow the MF. |
| Remove operand | `MFE.RemoveOperandAt(row)` **[confirm]** | Delete an operand. | Trim the MF. |
| Set operand type | `IMFERow.ChangeType(ZOSAPI.Editors.MFE.MeritOperandType.<X>)` | e.g. `EFFL`, `RMSSpotRadius`/`RMSS`, `DIMX`, `TOTR`, `MNCA`, `MXCA`, `OPGT`, `OPLT`. | Constrain focal length, edge thickness, distortion, etc. |
| Operand columns | `IMFERow.Target`, `.Weight`, `.Value`, `.Contribution`, `.GetOperandCell(col)` | Target/weight/computed value. | Program a constraint. |

> Operand **type enum** is `ZOSAPI.Editors.MFE.MeritOperandType`; the members map to
> the familiar 4-letter operand mnemonics (EFFL, RSCE/RSCH, DIMX, TOTR…). Spellings
> like `RMSSpotRadius` vs the raw mnemonic differ by build — **[confirm]** the exact
> member names in the .chm.

---

## 5. Multi-Configuration Editor (MCE)

`TheSystem.MCE` (`IMultiConfigEditor`). Rows are **operands** (one per varying
parameter); columns are **configurations**.

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Config count | `MCE.NumberOfConfigurations` | Number of configurations (columns). | Zoom/thermal setups. |
| Operand count | `MCE.NumberOfOperands` | Number of MC operands (rows). | Iterate MC rows. |
| Add configuration | `MCE.AddConfiguration(withPickups: bool)` **[confirm]** | Adds a column. | Add a zoom position. |
| Delete configuration | `MCE.DeleteConfiguration(i)` **[confirm]** | Removes a column. | Remove a config. |
| Get an operand | `MCE.GetOperandAt(row)` → `IMCERow` | Access an MC operand. | Read/write per-config value. |
| Insert operand | `MCE.InsertNewOperandAt(row)` / `AddOperand()` **[confirm]** | Add an MC operand row. | Vary a parameter across configs. |
| Set operand type | `IMCERow.ChangeType(ZOSAPI.Editors.MCE.MultiConfigOperandType.<X>)` | e.g. `THIC` (thickness), `CRVT`, `GLSS` (glass), `TEMP`, `PRES`, `APER`. | Zoom lens, athermalization. |
| Per-config value | `IMCERow.GetOperandCell(config)` → cell (value + solve) | The value in a specific configuration. | Set a different thickness per zoom. |
| Current config | `MCE.SetCurrentConfiguration(i)` / `MCE.CurrentConfiguration` **[confirm]** | Switch the active configuration. | Analyze one zoom position. |

---

## 6. Tolerance Data Editor (TDE) & Tolerancing

`TheSystem.TDE` (`IToleranceDataEditor`).

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Operand count | `TDE.NumberOfOperands` | Tolerance rows. | Inspect tolerance set. |
| Get an operand | `TDE.GetOperandAt(row)` → `ITDERow` | Access a tolerance operand. | Read/write min/max. |
| Insert operand | `TDE.InsertNewOperandAt(row)` / `AddOperand()` **[confirm]** | Add a tolerance row. | Build a tolerance budget. |
| Set operand type | `ITDERow.ChangeType(ZOSAPI.Editors.TDE.ToleranceOperandType.<X>)` | e.g. `TRAD`, `TTHI`, `TCON`, `TSDX/TSDY` (decenter), `TSTX/TSTY` (tilt), `TIRR`, `COMP`. | Radius/thickness/tilt tolerances, compensators. |
| Min / Max / nominal | `ITDERow.MinTolerance`, `.MaxTolerance`, `.Nominal` **[confirm]** | Tolerance bounds. | Set ± limits. |
| Default tolerances | `TheSystem.Tools.OpenDefaultTolerances()` **[confirm]** | Auto-populate a starting tolerance set. | Kick off a budget. |
| Tolerancing tool | `TheSystem.Tools.OpenTolerancing()` → tolerancing tool **[confirm]** | Runs sensitivity/Monte-Carlo tolerancing; set criterion, cycles, then `RunAndWaitForCompletion()`. | Yield/performance-vs-tolerance study. |

> The tolerancing tool's exact opener name and settings interface are **[confirm]** —
> the operator mnemonics (TRAD/TTHI/TSDX…) are canonical, the tool opener may be
> `OpenTolerancing`.

---

## 7. Optimization tools

`TheSystem.Tools` (`ISystemTools`). Optimization openers return tool objects under
`ZOSAPI.Tools.Optimization`. All long-running tools follow the pattern:
`tool = Open…(); set settings; tool.RunAndWaitForCompletion(); tool.Close()`.

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Optimize (Local) | `Tools.OpenLocalOptimization()` → `ILocalOptimization` | Local DLS/orthogonal-descent optimization. | Standard "optimize" run. |
| Algorithm | `opt.Algorithm` → `ZOSAPI.Tools.Optimization.OptimizationAlgorithm` (`DampedLeastSquares`, `OrthogonalDescent`) | Chooses the local method. | DLS is the default. |
| Cycles | `opt.Cycles` → `ZOSAPI.Tools.Optimization.OptimizationCycles` (`Automatic`, `Fixed_1_Cycle`, `Fixed_5_Cycles`, `Fixed_10_Cycles`, `Fixed_50_Cycles`, `Infinite`) | Number of cycles to run. | `Automatic` = run to convergence. |
| Cores | `opt.NumberOfCores` (int) | CPU threads used. | Speed up on many-core boxes. |
| Run | `opt.RunAndWaitForCompletion()` | Blocks until done. | Synchronous optimize. |
| Current merit | `opt.CurrentMeritFunction` / `opt.InitialMeritFunction` **[confirm]** | Live/initial merit during run. | Report improvement. |
| Hammer | `Tools.OpenHammerOptimization()` → `IHammerOptimization` | Global-ish "hammer" (needs starting design + variables + glass substitution). Run with a time budget then `Cancel()`/`WaitForCompletion`. | Escape local minima. |
| Global Search | `Tools.OpenGlobalOptimization()` → `IGlobalOptimization` | Global optimization; keeps top-N systems. | Broad design-space search. |
| Quick Focus | `Tools.OpenQuickFocus()` → `IQuickFocus` | Adjusts back focus to minimize spot/wavefront. Set `Criterion`/`UseCentroid` then `RunAndWaitForCompletion()`. | Refocus after edits. |
| Quick Adjust | `Tools.OpenQuickAdjust()` **[confirm]** → tool | Quickly adjust a single parameter to a target. | Fast single-variable tweak. |
| Glass substitution | Material cell solve status = **Substitute** (`ZOSAPI.Editors.SolveType.MaterialSubstitute` **[confirm]**) | Marks a glass as substitutable so Hammer/Global search real catalogs. | Let the optimizer pick glasses. |

---

## 8. Analyses

`TheSystem.Analyses` (`IAnalyses`). Open by enum ID, run, then read results.

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Open an analysis | `Analyses.New_Analysis(ZOSAPI.Analysis.AnalysisIDM.<Name>)` → `IA_` | Creates an analysis window/object. | Any analysis. |
| Open with settings | `Analyses.New_Analysis_SettingsFirst(idm)` **[confirm]** | Open without auto-running so settings can be set first. | Configure before compute. |
| Number open | `Analyses.NumberOfAnalyses` | Count of open analyses. | Manage windows. |
| Get open one | `Analyses.Get_AnalysisAtIndex(i)` **[confirm]** | Retrieve an existing analysis. | Re-read results. |
| Get settings | `IA_.GetSettings()` → analysis-specific `IAS_*` | Typed settings object (wavelength, field, sampling, ray density). | Set field/λ/sampling. |
| Apply & wait | `IA_.ApplyAndWaitForCompletion()` | Runs the analysis synchronously. | Compute before reading. |
| Get results | `IA_.GetResults()` → `IAR_` | Result container. | Extract numbers. |
| Close | `IA_.Close()` **[confirm]** | Closes the analysis. | Free resources. |

### Common `AnalysisIDM` values

| GUI analysis | `AnalysisIDM` member | Notes |
|---|---|---|
| Ray Aberration / Ray Fan | `RayFan` | Transverse ray fan. |
| Spot Diagram | `StandardSpot` | Also `ConfigurationMatrixSpot`, `ThroughFocusSpot`. |
| FFT PSF | `FftPsf` | Also `HuygensPsf`, `FftPsfCrossSection`. |
| FFT MTF | `FftMtf` | Also `FftMtfMap`, `FftThroughFocusMtf`. |
| Huygens MTF | `HuygensMtf` | Diffraction MTF via Huygens. |
| Wavefront Map | `WavefrontMap` **[confirm]** | OPD map. |
| Seidel Coefficients | `SeidelCoefficients` | Primary aberrations. |
| Field Curvature / Distortion | `FieldCurvatureAndDistortion` **[confirm]** | Field aberration plot. |
| Relative Illumination | `RelativeIllumination` | Vignetting/illumination falloff. |
| Encircled Energy | `DiffractionEncircledEnergy` **[confirm]** | Also geometric variants. |

> The `AnalysisIDM` enum is large and its exact members vary slightly by build.
> `RayFan`, `StandardSpot`, `FftPsf`, `FftMtf`, `HuygensMtf`, `SeidelCoefficients`,
> `RelativeIllumination` are high-confidence; **[confirm]** wavefront/EE/distortion
> spellings against the .chm.

### Reading results (`IAR_`)

| What you want | ZOS-API path | Notes |
|---|---|---|
| Did it succeed? | `IAR_.NumberOfDataGrids`, `IAR_.NumberOfDataSeries` | Choose grid vs series API. |
| 2-D grid (PSF/MTF map) | `IAR_.GetDataGrid(i)` → `IAR_DataGrid` with `.Values`, `.Nx`, `.Ny`, `.Dx`, `.Dy`, `.MinX/MinY` | Extract a numeric grid. |
| Line series (MTF/ray fan) | `IAR_.GetDataSeries(i)` → `IAR_DataSeries` with `.XData`, `.YData` (`.Data`), `.SeriesLabels` | Extract x/y curves. |
| Spot data | Spot analysis `IAR_` exposes RMS/GEO radius via `IAR_.SpotData` **[confirm]** or via `GetTextFile()` parsing. | RMS spot radius per field. |
| Header/metadata | `IAR_.HeaderData` **[confirm]** | Title, units. |
| Text dump fallback | `IA_.GetResults().GetTextFile(path)` **[confirm]** | When no typed accessor exists, dump text and parse. |

> Result accessor names (`GetDataGrid`, `GetDataSeries`) are canonical. `SpotData`,
> `HeaderData`, and `GetTextFile` are commonly used but **[confirm]** exact spelling;
> the text-file fallback is the robust way to read analyses lacking typed data.

---

## 9. Ray tracing

`TheSystem.Tools` opens batch/NSC ray-trace tools.

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Batch Ray Trace | `Tools.OpenBatchRayTrace()` → `IBatchRayTrace` | High-throughput sequential ray trace. | Trace thousands of rays fast. |
| Create a normalized ray dataset | `brt.CreateNormUnpol(maxRays, ZOSAPI.Tools.RayTrace.RaysType.Real, endSurface)` | Unpolarized normalized-pupil trace buffer. | Custom spot/wavefront sampling. |
| Polarized batch | `brt.CreateNormPol(...)` / `CreatePolar(...)` **[confirm]** | Polarized ray dataset. | Polarization throughput. |
| Add an input ray | `dataset.AddRay(wave, Hx, Hy, Px, Py, calc)` **[confirm]** | Queue an input ray. | Build the batch. |
| Run | `brt.RunAndWaitForCompletion()` | Executes the batch. | Compute all rays. |
| Read a result | `dataset.ReadNextResult(out ...)` → returns per-ray x,y,z,l,m,n,intensity,error,vignette | Iterate results. | Pull ray coordinates at the image. |
| Single ray trace | `TheSystem.Tools` single-ray, or `LDE`-level trace via `IBatchRayTrace` with one ray | Trace one ray. | Chief/marginal ray checks. |
| NSC Ray Trace | `Tools.OpenNSCRayTrace()` → `INSCRayTrace` | Traces sources through the NSC system into detectors. | Illumination/stray light. |
| NSC ▸ split | `nrt.SplitNSCRays` (bool) | Enable Fresnel splitting. | Ghost/stray analysis. |
| NSC ▸ scatter | `nrt.ScatterNSCRays` (bool) | Enable scattering models. | Scatter analysis. |
| NSC ▸ polarization | `nrt.UsePolarization` (bool) | Track polarization. | Coating/polarization effects. |
| NSC ▸ ignore errors | `nrt.IgnoreErrors` (bool) **[confirm]** | Continue past ray errors. | Robust batch traces. |
| NSC ▸ run | `nrt.ClearDetectors(0); nrt.RunAndWaitForCompletion()` | Clears then traces. | Populate detectors. |

---

## 10. Non-Sequential Component Editor (NCE)

`TheSystem.NCE` (`INonSeqEditor`). **Objects are 1-based** via `GetObjectAt`.

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Object count | `NCE.NumberOfObjects` | Number of NSC objects. | Iterate objects. |
| Get an object | `NCE.GetObjectAt(i)` → `INSCRow` (1-based) | Access one object. | Read/write an object. |
| Insert object | `NCE.InsertNewObjectAt(i)` **[confirm]** / `NCE.AddObject()` | Add an NSC object. | Build a scene. |
| Remove object | `NCE.RemoveObjectAt(i)` **[confirm]** | Delete an object. | Edit a scene. |
| Object type | `INSCRow.ChangeType(INSCRow.GetObjectTypeSettings(ZOSAPI.Editors.NCE.ObjectType.<X>))` | Set object type (SourceEllipse, SourceRectangle, SourceFile, DetectorRectangle, DetectorColor, StandardLens, …). | Place a source/detector/optic. |
| Comment | `INSCRow.Comment` | Row label. | Annotate. |
| Material | `INSCRow.Material` | Object glass/coating. | Assign material. |
| Position | `INSCRow.XPosition`, `.YPosition`, `.ZPosition`, `.TiltAboutX/Y/Z` | Global/relative placement. | Position geometry. |
| Reference object | `INSCRow.RefObject` (int) | Parent object for relative coords. | Assemble sub-groups. |
| Object data columns | `INSCRow.GetObjectCell(ZOSAPI.Editors.NCE.ObjectColumn.Par1…ParN)` **[confirm]** | Type-specific parameters (source #rays, power, detector pixels). | Set 1e6 analysis rays, detector resolution. |

### Sources & detectors

| GUI object | `ObjectType` member | Key data columns (via `GetObjectCell`) |
|---|---|---|
| Source Ellipse | `SourceEllipse` | # layout rays, # analysis rays, power (W/lm), X/Y half-widths. |
| Source Rectangle | `SourceRectangle` | # rays, power, X/Y half-widths, cosine exponent. |
| Source File | `SourceFile` | # rays, power, source-file name (`.sdf`/`.dat`). |
| Detector Rectangle | `DetectorRectangle` | X/Y half-widths, # X pixels, # Y pixels, color/mode, smoothing. |
| Detector Color | `DetectorColor` | Pixels + color-space settings. |

### Reading detector results

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Single pixel value | `NCE.GetDetectorData(objNumber, pixel, dataType, out value)` **[confirm signature]** | Reads one detector pixel/quantity (flux, irradiance). | Peak/point readout. |
| Whole-detector array | `NCE.GetAllDetectorData(objNumber, dataType, out double[])` **[confirm]** or `GetAllDetectorDataSafe(...)` | Reads the full pixel grid. | Build an irradiance map. |
| Detector totals | `NCE.GetDetectorData(obj, -1/-2/-3, ...)` for total flux / peak irradiance **[confirm indices]** | Aggregate detector stats. | Total power on detector. |
| Detector Viewer | `Analyses.New_Analysis(AnalysisIDM.DetectorViewer)` **[confirm]** | Opens the detector viewer analysis. | Visual + result extraction. |

> Detector-read APIs (`GetDetectorData`, `GetAllDetectorData`/`…Safe`) are the correct
> family but their **exact argument order and the data-type index enum are
> build-specific — [confirm]** in the .chm before coding. Clearing detectors before a
> trace is done via `NCE.<clear>` or the NSC ray-trace tool's `ClearDetectors(0)`.

---

## 11. Materials / Glass catalogs

`TheSystem.SystemData.MaterialCatalogs` plus per-surface material assignment.

| GUI feature | ZOS-API path | What it does | Typical use-case |
|---|---|---|---|
| Catalogs in use | `SystemData.MaterialCatalogs` → `IMaterialCatalogData` | The list of loaded glass catalogs. | See which catalogs are active. |
| Enumerate catalogs | `MaterialCatalogs.GetCatalog(i)` / `.NumberOfCatalogs` **[confirm]** | Iterate available catalogs (SCHOTT, OHARA, …). | List catalogs for the user. |
| Add / remove catalog | `MaterialCatalogs.AddCatalog(name)` / `RemoveCatalog(name)` **[confirm]** | Enable/disable a catalog. | Restrict glass choices. |
| Catalog contents | `IMaterialsCatalog` (per-catalog object exposing glass records) **[confirm]** | Query glasses/indices in a catalog. | Look up a glass. |
| Set a surface glass | `LDE.GetSurfaceAt(i).Material = "N-BK7"` | Assign glass to a sequential surface. | Change an element's glass. |
| Model glass | Set `Material` to a model-glass string / use the material cell solve **[confirm]** | Index/Abbe model glass. | Placeholder before substitution. |
| Glass substitution status | Material cell → Substitute solve (see §7) | Marks glass as optimizer-substitutable. | Global/Hammer glass search. |

---

## Verified vs. needs-confirmation

**High-confidence / canonical** (used in this project's working code and standard Ansys
templates — safe to rely on):

- Connection: `ZOSAPI_Connection`, `ConnectAsExtension(0)`, `CreateNewApplication()`,
  `IsValidLicenseForAPI`, `LicenseStatus`, `SerialCode`, `SamplesDir`,
  `PrimarySystem`, `CloseApplication()`.
- System: `New`, `LoadFile`, `Save`, `SaveAs`, `SystemFile`, `Mode`,
  `MakeNonSequential()`, `UpdateMode` + `LensUpdateMode.EditorsOnly`, `SystemType`.
- SystemData: `Aperture.ApertureType` / `ApertureValue`, `ZemaxApertureType`
  (`EntrancePupilDiameter`, `ImageSpaceFNum`, `ObjectSpaceNA`, `FloatByStopSize`);
  `Wavelengths.NumberOfWavelengths/GetWavelength/AddWavelength/SelectWavelengthPreset`;
  `Fields.SetFieldType`/`GetField`/`AddField`, `FieldType`
  (`Angle`/`ObjectHeight`/`ParaxialImageHeight`/`RealImageHeight`).
- LDE: `NumberOfSurfaces`, `GetSurfaceAt` (0-based), `InsertNewSurfaceAt`,
  `RemoveSurfaceAt`; `ILDERow.Radius/Thickness/Material/Comment/SemiDiameter/Conic/IsStop`;
  `ChangeType`; `MakeSolveVariable`; `ZOSAPI.Editors.SolveType`.
- MFE: `CalculateMeritFunction()`, `SEQOptimizationWizard`(+`.Apply()`),
  `GetOperandAt`, `InsertNewOperandAt`.
- Analyses: `New_Analysis(AnalysisIDM.<x>)`, `ApplyAndWaitForCompletion()`,
  `GetResults()`, `GetDataGrid`, `GetDataSeries`; IDMs `RayFan`, `StandardSpot`,
  `FftPsf`, `FftMtf`, `HuygensMtf`, `SeidelCoefficients`, `RelativeIllumination`.
- Optimization: `Tools.OpenLocalOptimization/OpenHammerOptimization/OpenGlobalOptimization/OpenQuickFocus`,
  `OptimizationAlgorithm.DampedLeastSquares/OrthogonalDescent`,
  `OptimizationCycles.Automatic`, `NumberOfCores`, `RunAndWaitForCompletion()`.
- Ray trace: `Tools.OpenBatchRayTrace`, `Tools.OpenNSCRayTrace` with
  `SplitNSCRays/ScatterNSCRays/UsePolarization/RunAndWaitForCompletion`.
- NCE: `NumberOfObjects`, `GetObjectAt` (1-based), `INSCRow.Comment/Material/position`.
- Materials: `SystemData.MaterialCatalogs`; per-surface `Material` assignment.

**Needs confirmation in ZOS-API.chm** (name/signature/enum spelling not 100% certain —
verify before shipping code that depends on the exact member):

- `TheSystem.Close`, `UpdateStatus`, `LDE.AddSurface`.
- Wavelength/Field `RemoveWavelength`/`RemoveField`, `MakePrimary` placement.
- Aperture apodization members; `SystemData.Polarization` / `TitleNotes` members.
- Cell spellings (`ConicCell`), `GetSolveData/SetSolveData`, `MakeSolveFixed`,
  and the exact `SolveType` member for glass **Substitute** (`MaterialSubstitute`?).
- Extra-parameter / object-column access (`GetSurfaceCell`, `GetObjectCell`,
  `SurfaceColumn.Par*`, `ObjectColumn.Par*`).
- MFE operand type spellings (`MeritOperandType` members: `RMSSpotRadius` vs mnemonic),
  `AddOperand`, `RemoveOperandAt`; SEQ wizard property names.
- All of MCE (`AddConfiguration`, `DeleteConfiguration`, `MultiConfigOperandType`,
  `SetCurrentConfiguration`) and TDE (`ToleranceOperandType`, `MinTolerance/MaxTolerance`,
  `OpenTolerancing`, `OpenDefaultTolerances`) beyond the structural `Get/InsertNewOperandAt`.
- `New_Analysis_SettingsFirst`, `Get_AnalysisAtIndex`, `IAR_.SpotData`/`HeaderData`/
  `GetTextFile`, and IDMs `WavefrontMap`, `FieldCurvatureAndDistortion`,
  `DiffractionEncircledEnergy`, `DetectorViewer`.
- Batch ray-trace dataset methods (`CreateNormUnpol`/`CreateNormPol`, `AddRay`,
  `ReadNextResult` out-parameters) — signature and out-parameter order vary.
- Detector reads (`GetDetectorData`, `GetAllDetectorData`/`GetAllDetectorDataSafe`)
  argument order and the data-type index convention.
- Material catalog enumeration (`GetCatalog`, `NumberOfCatalogs`, `AddCatalog`,
  `IMaterialsCatalog`) — the exact catalog-query interface.

> When in doubt, prefer runtime discovery: enumerate a live object's members
> (`dir(obj)` under pythonnet) and enum values before hard-coding a name, and fall
> back to `GetTextFile`-style text extraction when a typed result accessor is
> uncertain.
