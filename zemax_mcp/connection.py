"""
zemax_mcp.connection
=====================

Non-invasive bridge to Ansys Zemax OpticStudio via the ZOS-API .NET assemblies.

This module NEVER touches Zemax's source or binaries. It only *loads* the three
public ZOS-API assemblies that ship with OpticStudio and talks to them through
pythonnet (the `clr` module):

    ZOSAPI_NetHelper.dll   -> locates the OpticStudio install + boots the .NET runtime
    ZOSAPI.dll             -> concrete connection object (ZOSAPI_Connection)
    ZOSAPI_Interfaces.dll  -> the IOpticalSystem / editor / analysis interfaces

Two connection modes are supported (both are official, documented modes):

  * "extension"  -> ConnectAsExtension(0): attach to an ALREADY-RUNNING OpticStudio
                    GUI so edits appear live. Requires the user to click
                    Programming > Interactive Extension in the GUI first (it then
                    waits for a connection). Great for interactive/"watch it change"
                    work. UpdateMode is set to EditorsOnly so editor edits reflect
                    live without forcing a full redraw on every call.

  * "standalone" -> CreateNewApplication(): launch a fresh HEADLESS OpticStudio
                    process owned by this script. No GUI. Great for batch/automation.
                    Requires a license tier valid for the API (Professional/Premium/
                    Enterprise); IsValidLicenseForAPI reports this.

IMPORTANT (WSL note): pythonnet loads Windows .NET DLLs, so this module must run
under *Windows* Python (invoked from WSL as `python.exe`), NOT the WSL `python3`.
See setup/INSTALL.md.

References (verified against official Ansys/Zemax sources):
  - ZOS-API.NET Overview:        optics.ansys.com/.../ZOS-API-NET-An-Overview
  - Interactive Extension (Py):  optics.ansys.com/.../ZOS-API-Interactive-Extension
  - Canonical templates:         github.com/ansys/lib-zemax-programming
"""

from __future__ import annotations

import os
import sys
from typing import Literal, Optional

Mode = Literal["extension", "standalone"]


class ZemaxConnectionError(RuntimeError):
    """Raised when a connection to OpticStudio cannot be established."""


class ZemaxLicenseError(RuntimeError):
    """Raised when the license is not valid for ZOS-API use."""


def _find_nethelper() -> str:
    """Locate ZOSAPI_NetHelper.dll via the HKCU\\Software\\Zemax\\ZemaxRoot registry key.

    Falls back to the ZEMAX_ROOT env var if the registry is unavailable. This mirrors
    the exact logic in the Zemax-generated PythonZOSConnection.py template.
    """
    zemax_root: Optional[str] = os.environ.get("ZEMAX_ROOT")
    if not zemax_root:
        try:
            import winreg  # Windows-only; present under Windows Python

            key = winreg.OpenKey(
                winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER),
                r"Software\Zemax",
                0,
                winreg.KEY_READ,
            )
            zemax_root = winreg.QueryValueEx(key, "ZemaxRoot")[0]
            winreg.CloseKey(key)
        except Exception as exc:  # pragma: no cover - environment dependent
            raise ZemaxConnectionError(
                "Could not read HKCU\\Software\\Zemax\\ZemaxRoot. "
                "Set the ZEMAX_ROOT env var to your Zemax data folder "
                r"(e.g. C:\Users\<you>\Documents\Zemax)."
            ) from exc

    nethelper = os.path.join(zemax_root, "ZOS-API", "Libraries", "ZOSAPI_NetHelper.dll")
    if not os.path.isfile(nethelper):
        raise ZemaxConnectionError(f"ZOSAPI_NetHelper.dll not found at: {nethelper}")
    return nethelper


class ZemaxSession:
    """A live ZOS-API session. Wraps TheApplication / TheSystem.

    Use as a context manager so standalone applications are always closed:

        with ZemaxSession("standalone") as z:
            z.system.New(False)
            ...

    In "extension" mode, closing does NOT terminate the user's GUI; it only drops
    the API connection.
    """

    def __init__(self, mode: Mode = "extension", install_path: str = ""):
        self.mode: Mode = mode
        self._install_path = install_path
        self._zos = None          # ZOSAPI namespace module
        self._connection = None   # ZOSAPI.ZOSAPI_Connection
        self.application = None    # IZOSAPI_Application
        self.system = None         # IOpticalSystem (TheSystem)
        self._connect()

    # ------------------------------------------------------------------ boot
    def _boot_clr(self):
        try:
            import clr  # from pythonnet
        except ImportError as exc:  # pragma: no cover
            raise ZemaxConnectionError(
                "pythonnet is not installed. Run this under Windows Python and "
                "`pip install pythonnet`. See setup/INSTALL.md."
            ) from exc

        nethelper = _find_nethelper()
        clr.AddReference(nethelper)
        import ZOSAPI_NetHelper  # noqa: E402  (available after AddReference)

        ok = ZOSAPI_NetHelper.ZOSAPI_Initializer.Initialize(self._install_path or "")
        if not ok:
            raise ZemaxConnectionError(
                "ZOSAPI_Initializer.Initialize failed - could not find the "
                "OpticStudio install directory."
            )
        zemax_dir = ZOSAPI_NetHelper.ZOSAPI_Initializer.GetZemaxDirectory()

        clr.AddReference(os.path.join(zemax_dir, "ZOSAPI.dll"))
        clr.AddReference(os.path.join(zemax_dir, "ZOSAPI_Interfaces.dll"))
        import ZOSAPI  # noqa: E402

        self._zos = ZOSAPI
        return ZOSAPI

    # ------------------------------------------------------------- connect
    def _connect(self):
        ZOSAPI = self._boot_clr()

        self._connection = ZOSAPI.ZOSAPI_Connection()
        if self._connection is None:
            raise ZemaxConnectionError("Unable to initialize the .NET ZOSAPI connection.")

        if self.mode == "extension":
            self.application = self._connection.ConnectAsExtension(0)
            if self.application is None:
                raise ZemaxConnectionError(
                    "ConnectAsExtension returned None. In OpticStudio, click "
                    "Programming > Interactive Extension and leave it waiting, "
                    "then retry."
                )
        elif self.mode == "standalone":
            self.application = self._connection.CreateNewApplication()
            if self.application is None:
                raise ZemaxConnectionError("CreateNewApplication returned None.")
        else:  # pragma: no cover
            raise ValueError(f"Unknown mode: {self.mode!r}")

        if not self.application.IsValidLicenseForAPI:
            raise ZemaxLicenseError(
                "License is not valid for ZOS-API use. For extension mode, enable "
                "Programming > Interactive Extension in the GUI. For standalone mode, "
                "you need a Professional/Premium/Enterprise-tier license."
            )

        self.system = self.application.PrimarySystem
        if self.system is None:
            raise ZemaxConnectionError("Unable to acquire the Primary optical system.")

        # In extension mode, keep the live GUI cheap-to-update: reflect editor edits
        # without forcing a full analysis redraw on each API call.
        if self.mode == "extension":
            try:
                self.system.UpdateMode = ZOSAPI.LensUpdateMode.EditorsOnly
            except Exception:
                pass  # not fatal; older builds may differ

    # ---------------------------------------------------------- utilities
    @property
    def zosapi(self):
        """The imported ZOSAPI namespace (for enums/constants)."""
        return self._zos

    def edition(self) -> str:
        """Human-readable license edition string."""
        ls = self.application.LicenseStatus
        LST = self._zos.LicenseStatusType
        for name in ("PremiumEdition", "EnterpriseEdition", "ProfessionalEdition",
                     "StandardEdition", "OpticStudioHPCEdition"):
            if hasattr(LST, name) and ls == getattr(LST, name):
                return name.replace("Edition", "")
        return "Unknown"

    def info(self) -> dict:
        return {
            "mode": self.mode,
            "serial": str(self.application.SerialCode),
            "edition": self.edition(),
            "samples_dir": str(self.application.SamplesDir),
            "system_file": str(self.system.SystemFile),
            "mode_is_sequential": bool(getattr(self.system, "Mode", None)
                                       == self._zos.SystemType.Sequential),
        }

    def close(self, save: bool = False):
        """Close the session. Only terminates the process in standalone mode."""
        try:
            if self.mode == "standalone" and self.application is not None:
                self.application.CloseApplication()
        finally:
            self.application = None
            self.system = None
            self._connection = None

    # ---------------------------------------------------- context manager
    def __enter__(self) -> "ZemaxSession":
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def connect(mode: Mode = "extension", install_path: str = "") -> ZemaxSession:
    """Convenience factory mirroring ZOSPy's zos.connect('extension'|'standalone')."""
    return ZemaxSession(mode=mode, install_path=install_path)


if __name__ == "__main__":
    # Quick smoke test:  python.exe -m zemax_mcp.connection [extension|standalone]
    m: Mode = sys.argv[1] if len(sys.argv) > 1 else "extension"  # type: ignore
    with connect(m) as z:  # type: ignore[arg-type]
        import json
        print(json.dumps(z.info(), indent=2))
