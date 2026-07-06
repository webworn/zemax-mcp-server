"""zemax_mcp — non-invasive Claude Code <-> Ansys Zemax OpticStudio bridge (ZOS-API)."""
from .connection import ZemaxSession, connect, ZemaxConnectionError, ZemaxLicenseError

__all__ = ["ZemaxSession", "connect", "ZemaxConnectionError", "ZemaxLicenseError"]
__version__ = "0.1.0"
