"""
examples/smoke_test.py
======================
Minimal end-to-end check that the bridge can drive OpticStudio. Run under Windows
Python. Builds a trivial singlet in a headless (or extension) session, optimizes,
and prints the RMS spot — proving input -> output round-trips through the API.

Usage:
    python.exe examples/smoke_test.py [extension|standalone]
"""

import sys

from zemax_mcp import connect


def main(mode="standalone"):
    with connect(mode) as z:
        s = z.system
        ZOSAPI = z.zosapi
        print("Connected:", z.info())

        # --- new sequential system ---
        s.New(False)

        # aperture: F/4  (image-space F-number)
        ap = s.SystemData.Aperture
        ap.ApertureType = ZOSAPI.SystemData.ZemaxApertureType.ImageSpaceFNum
        ap.ApertureValue = 4.0

        # single on-axis field, single d-line wavelength
        s.SystemData.Fields.SetFieldType(ZOSAPI.SystemData.FieldType.Angle)
        s.SystemData.Wavelengths.GetWavelength(1).Wavelength = 0.587_6

        # LDE: object(0) - stop/lens front(1) - lens back(2) - image(3)
        lde = s.LDE
        lde.InsertNewSurfaceAt(1)  # front
        lde.InsertNewSurfaceAt(2)  # back
        front = lde.GetSurfaceAt(1)
        back = lde.GetSurfaceAt(2)
        front.Comment, front.Material, front.Thickness = "lens front", "N-BK7", 4.0
        back.Comment, back.Thickness = "lens back", 95.0
        front.Radius, back.Radius = 100.0, -100.0

        # variables: both radii + last airspace (back focal)
        front.RadiusCell.MakeSolveVariable()
        back.RadiusCell.MakeSolveVariable()
        back.ThicknessCell.MakeSolveVariable()

        # merit function via wizard, then local optimize
        s.MFE.SEQOptimizationWizard.Apply()
        before = s.MFE.CalculateMeritFunction()

        opt = s.Tools.OpenLocalOptimization()
        opt.Algorithm = ZOSAPI.Tools.Optimization.OptimizationAlgorithm.DampedLeastSquares
        opt.Cycles = ZOSAPI.Tools.Optimization.OptimizationCycles.Automatic
        opt.NumberOfCores = 8
        opt.RunAndWaitForCompletion()
        opt.Close()

        after = s.MFE.CalculateMeritFunction()
        print(f"Merit: {before:.6f} -> {after:.6f}  (improved={after < before})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "standalone")
