# Setup — Claude Code ↔ Zemax OpticStudio bridge

This bridge talks to OpticStudio through the **ZOS-API .NET assemblies** using
[`pythonnet`](https://github.com/pythonnet/pythonnet). Nothing here modifies Zemax —
it only *loads* the three public assemblies that already ship with your install.

## 0. The critical constraint (read this first)

`pythonnet` loads **Windows .NET** DLLs, so the bridge **must run under Windows
Python**, not the WSL/Linux `python3`. Claude Code (running in WSL) launches it by
calling `python.exe`, which crosses the WSL→Windows boundary automatically.

Your machine currently has **Windows Python 3.14.2**, but `pythonnet`'s stable
wheels typically trail the newest CPython. Use a **64-bit Python 3.11–3.13**.

> **Verified working here:** Python **3.13-64** at
> `C:\Users\PC\AppData\Local\Python\pythoncore-3.13-64\python.exe`
> with `pythonnet 3.0.5` + `mcp 1.28.1`. From WSL that interpreter is
> `/mnt/c/Users/PC/AppData/Local/Python/pythoncore-3.13-64/python.exe`.
> Find your versions with `py -0p`.

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

> ⚠️ **One connection per click.** Interactive Extension arms a *single*
> connection, then stops listening. Each standalone script run consumes it, so
> **re-click Programming ▸ Interactive Extension before every run**. The MCP
> server (below) is a long-lived process that connects once and holds the slot,
> so this only bites one-shot scripts.

### Standalone mode (headless)
```powershell
python -m zemax_mcp.connection standalone
```
Launches a headless OpticStudio owned by the script. Needs a Professional/
Premium/Enterprise-tier license for the API.

## 4. Register the MCP server with Claude Code

From WSL (Claude Code). Because Claude Code launches the server from its own
working directory (not this repo), put the repo on `PYTHONPATH` as a **Windows**
path and give the full `python.exe` path:

```bash
claude mcp add zemax \
  -e PYTHONPATH='D:\Claude_code\Zemax plugin' \
  -- /mnt/c/Users/PC/AppData/Local/Python/pythoncore-3.13-64/python.exe -m zemax_mcp.server
```

Confirm it registered and is reachable:

```bash
claude mcp list      # expect:  zemax: ... - ✔ Connected
```

`✔ Connected` means the server process started and completed the MCP handshake —
it does **not** yet mean it's attached to OpticStudio. That happens when you (or
Claude) call the `zemax_connect` tool. Then in Claude Code you can say things like
*"connect to Zemax in extension mode and show me the lens data"* and Claude will
call `zemax_connect` / `zemax_lde_summary`.

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
