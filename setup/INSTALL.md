# Setup — Claude Code ↔ Zemax OpticStudio bridge

This bridge talks to OpticStudio through the **ZOS-API .NET assemblies** using
[`pythonnet`](https://github.com/pythonnet/pythonnet). Nothing here modifies Zemax —
it only *loads* the three public assemblies that already ship with your install.

## 0. The critical constraint (read this first)

`pythonnet` loads **Windows .NET** DLLs, so the bridge **must run under Windows
Python**, not the WSL/Linux `python3`. Claude Code (running in WSL) launches it by
calling `python.exe`, which crosses the WSL→Windows boundary automatically.

Your machine currently has **Windows Python 3.14.2**, but `pythonnet`'s stable
wheels typically trail the newest CPython. If `pip install pythonnet` fails on
3.14, install a **64-bit Python 3.11–3.13** from python.org and use that
interpreter for the steps below.

## 1. Verify the Zemax install (already confirmed)

- Product: **Ansys Zemax OpticStudio 2025 R2.02**
- Install: `C:\Program Files\Ansys Zemax OpticStudio 2025 R2.02`
- API assemblies present: `ZOSAPI.dll`, `ZOSAPI_Interfaces.dll`, `ZOSAPI_NetHelper.dll`
- Data root (registry `HKCU\Software\Zemax\ZemaxRoot`): `C:\Users\PC\Documents\Zemax`

## 2. Install Python dependencies (Windows Python)

From a **Windows** terminal (PowerShell/cmd), or from WSL prefixing with `python.exe`:

```powershell
# use a 3.11–3.13 interpreter if 3.14 rejects pythonnet
python -m pip install --upgrade pip
python -m pip install pythonnet "mcp[cli]"
```

Verify pythonnet can load .NET:

```powershell
python -c "import clr; print('pythonnet OK')"
```

## 3. Smoke-test the connection

### Extension mode (live GUI)
1. Open OpticStudio.
2. Menu: **Programming ▸ Interactive Extension** — it now waits for a connection.
3. From this project folder, run:
   ```powershell
   python -m zemax_mcp.connection extension
   ```
   You should see JSON with your license edition, serial, and current file.

### Standalone mode (headless)
```powershell
python -m zemax_mcp.connection standalone
```
Launches a headless OpticStudio owned by the script. Needs a Professional/
Premium/Enterprise-tier license for the API.

## 4. Register the MCP server with Claude Code

From WSL (Claude Code):

```bash
claude mcp add zemax -- python.exe -m zemax_mcp.server
```

Then in Claude Code you can say things like *"connect to Zemax in extension mode
and show me the lens data"* and Claude will call the `zemax_connect` /
`zemax_lde_summary` tools.

If `python.exe` isn't on PATH from WSL, use the full path, e.g.
`/mnt/c/Users/PC/AppData/Local/Programs/Python/Python312/python.exe`.

### Lock down the escape hatch (optional)
`zemax_eval` runs arbitrary Python against the live session. To disable it:
```bash
claude mcp add zemax -e ZEMAX_MCP_ALLOW_EVAL=0 -- python.exe -m zemax_mcp.server
```

## 5. Troubleshooting

| Symptom | Fix |
|---|---|
| `pythonnet` install fails | Use Python 3.11–3.13 (not 3.14). |
| `ConnectAsExtension returned None` | Click Programming ▸ Interactive Extension in the GUI first. |
| `License is not valid for ZOS-API` (standalone) | Your tier may not include headless API; use extension mode. |
| `ZOSAPI_NetHelper.dll not found` | Set `ZEMAX_ROOT` to your Zemax data folder. |
| Claude can't launch server from WSL | Use the absolute `python.exe` path in `claude mcp add`. |
