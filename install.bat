@echo off
setlocal

:: Manon MCP -- Windows Installer
:: Usage: Double-click or run from CMD/PowerShell

:: ── Try bash first ────────────────────────────────────
where bash >nul 2>&1
if %errorlevel% == 0 (
    bash "%~dp0install.sh"
    exit /b %errorlevel%
)

:: ── No bash: run embedded PowerShell ─────────────────
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
powershell -ExecutionPolicy Bypass -Command ^
  "& { $sd = '%SCRIPT_DIR%'; $f = '%~f0'; $lines = [IO.File]::ReadAllLines($f, [Text.Encoding]::UTF8); $ps = ($lines | Where-Object { $_ -match '^::PS ' } | ForEach-Object { $_ -replace '^::PS ', '' }) -join \"`n\"; $ps = ('$SCRIPT_DIR = ''' + $sd + '''') + \"`n\" + $ps; Invoke-Expression $ps }"
exit /b %errorlevel%

::PS $SERVER_PY  = "$SCRIPT_DIR\run_mcp.py"
::PS $VENV_DIR   = "$SCRIPT_DIR\.venv"
::PS $DEFAULT_API_URL = "http://117.131.45.179:3700"
::PS $ErrorActionPreference = "Stop"
::PS function info($m)  { Write-Host "[+] $m" -ForegroundColor Green }
::PS function warn($m)  { Write-Host "[!] $m" -ForegroundColor Yellow }
::PS function err($m)   { Write-Host "[x] $m" -ForegroundColor Red; exit 1 }
::PS function head1($m) { Write-Host "`n-- $m --" -ForegroundColor Cyan }
::PS Write-Host ""; Write-Host "  Manon MCP -- 代码智能工具"; Write-Host "  ------------------------"; Write-Host ""
::PS # ── Python check ──────────────────────────────────────
::PS head1 "Python"
::PS $pythonCmd = $null
::PS foreach ($cmd in @("python", "python3", "py")) {
::PS     try {
::PS         $ver = & $cmd --version 2>&1
::PS         if ($ver -match "Python (\d+)\.(\d+)") {
::PS             $major = [int]$Matches[1]; $minor = [int]$Matches[2]
::PS             if ($major -ge 3 -and $minor -ge 10) { $pythonCmd = $cmd; break }
::PS         }
::PS     } catch {}
::PS }
::PS if (-not $pythonCmd) {
::PS     warn "Python 3.10+ not found, attempting auto-install via winget..."
::PS     $winget = Get-Command winget -ErrorAction SilentlyContinue
::PS     if ($winget) {
::PS         winget install --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
::PS         $env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path","User")
::PS         foreach ($cmd in @("python", "python3", "py")) {
::PS             try { $ver = & $cmd --version 2>&1; if ($ver -match "Python (\d+)\.(\d+)") { $major=[int]$Matches[1]; $minor=[int]$Matches[2]; if ($major -ge 3 -and $minor -ge 10) { $pythonCmd = $cmd; break } } } catch {}
::PS         }
::PS     }
::PS     if (-not $pythonCmd) {
::PS         warn "Auto-install failed. Please install Python 3.10+ from https://www.python.org/downloads/"
::PS         warn "Make sure to check 'Add Python to PATH' during installation."
::PS         Start-Process "https://www.python.org/downloads/"
::PS         err "Re-run this script after installing Python."
::PS     }
::PS     info "Python installed successfully"
::PS }
::PS info "Python OK ($pythonCmd)"
::PS # ── Detect platforms ──────────────────────────────────
::PS head1 "Platform Detection"
::PS $PLATFORMS = @(); $HOME_DIR = $env:USERPROFILE
::PS if ((Test-Path "$HOME_DIR\.claude") -or (Get-Command claude -ErrorAction SilentlyContinue)) { $PLATFORMS += "claude-code" }
::PS if (Test-Path "$HOME_DIR\.cursor") { $PLATFORMS += "cursor" }
::PS if ((Test-Path "$HOME_DIR\.codeium\windsurf") -or (Test-Path "$HOME_DIR\.windsurf")) { $PLATFORMS += "windsurf" }
::PS if ((Test-Path "$HOME_DIR\.config\zed") -or (Get-Command zed -ErrorAction SilentlyContinue)) { $PLATFORMS += "zed" }
::PS if (Test-Path "$HOME_DIR\.continue") { $PLATFORMS += "continue" }
::PS if ((Test-Path "$HOME_DIR\.codebuddy") -or (Test-Path "$HOME_DIR\.tencent\codebuddy")) { $PLATFORMS += "codebuddy" }
::PS if ($PLATFORMS.Count -eq 0) { err "No supported platform detected (Claude Code / Cursor / Windsurf / Zed / Continue / CodeBuddy)" }
::PS info "Detected: $($PLATFORMS -join ', ')"
::PS # ── Venv + deps ───────────────────────────────────────
::PS head1 "Dependencies"
::PS if (-not (Test-Path $VENV_DIR)) { & $pythonCmd -m venv $VENV_DIR }
::PS if (Test-Path "$VENV_DIR\Scripts\python.exe") { $VENV_PYTHON = "$VENV_DIR\Scripts\python.exe" } else { err "Failed to locate venv python" }
::PS & $VENV_PYTHON -m pip install -q -r "$SCRIPT_DIR\mcp\requirements.txt"
::PS info "Dependencies installed"
::PS # ── Check for existing API key ────────────────────────
::PS $API_KEY = ""; $API_URL = "auto"
::PS foreach ($cfg in @("$HOME_DIR\.claude.json","$HOME_DIR\.claude\settings.json","$HOME_DIR\.cursor\mcp.json","$HOME_DIR\.codeium\windsurf\mcp_config.json","$HOME_DIR\.windsurf\mcp_config.json")) {
::PS     if (Test-Path $cfg) {
::PS         $key = & $VENV_PYTHON -c "import json`ntry:`n    d=json.load(open(r'$cfg',encoding='utf-8'))`n    k=d.get('mcpServers',{}).get('manon',{}).get('env',{}).get('MANON_API_KEY','')`n    if k.startswith('msk_'): print(k)`nexcept: pass" 2>`$null
::PS         if ($key -and $key.Trim().StartsWith("msk_")) { $API_KEY = $key.Trim(); info "Existing API key found, skipping registration"; break }
::PS     }
::PS }
::PS # ── Auto-register ─────────────────────────────────────
::PS if (-not $API_KEY) {
::PS     head1 "Auto-register"
::PS     $REG_URL = if ($API_URL -eq "auto") { $DEFAULT_API_URL } else { $API_URL }
::PS     $REG_RESULT = & $VENV_PYTHON -c "import httpx,sys`ntry:`n    r=httpx.post('$REG_URL/api/v1/register',json={'name':'$env:USERNAME'},timeout=10)`n    r.raise_for_status()`n    print(r.json()['api_key'])`nexcept Exception as e:`n    print(f'FAIL:{e}',file=sys.stderr);sys.exit(1)" 2>&1
::PS     if ($REG_RESULT -and $REG_RESULT.Trim().StartsWith("msk_")) { $API_KEY = $REG_RESULT.Trim(); info "Auto-registered, API key: $($API_KEY.Substring(0,12))..." }
::PS     else { warn "Auto-register failed ($REG_RESULT) -- set key manually later"; $API_KEY = "" }
::PS }
::PS # ── Normalize paths + MCP writer ──────────────────────
::PS $VENV_PYTHON_NORM = $VENV_PYTHON -replace '\\', '/'; $SERVER_PY_NORM = $SERVER_PY -replace '\\', '/'
::PS function Write-McpJson($t) {
::PS     $d = Split-Path -Parent $t; if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
::PS     & $VENV_PYTHON -c "import json,os`nt,vp,sv,url,key=r'$t','$VENV_PYTHON_NORM','$SERVER_PY_NORM','$API_URL','$API_KEY'`ncfg={}`nif os.path.exists(t):`n    with open(t,'r',encoding='utf-8') as f: cfg=json.load(f)`ncfg.setdefault('mcpServers',{})`nenv={'MANON_API_KEY':key}`nif url!='auto': env['MANON_API_URL']=url`ncfg['mcpServers']['manon']={'command':vp,'args':[sv],'env':env}`nwith open(t,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2,ensure_ascii=False)"
::PS }
::PS $MANON_RULES = "# Manon -- 代码智能工具规则`n`n当用户提问涉及代码理解、架构分析时，必须使用 Manon MCP 工具。`n`n| 场景 | 工具 |`n|------|------|`n| 代码理解/搜索 | ``manon_deep_query`` |`n| 调用关系/依赖 | ``manon_graph`` |`n| 改动影响 | ``manon_impact`` |"
::PS # ── Configure platforms ───────────────────────────────
::PS head1 "Configuration"; $CONFIGURED = @()
::PS foreach ($platform in $PLATFORMS) {
::PS     switch ($platform) {
::PS         "claude-code" {
::PS             Write-McpJson "$HOME_DIR\.claude.json"; info "Claude Code MCP registered"
::PS             $sd = "$HOME_DIR\.claude\skills\manon"; if (-not (Test-Path $sd)) { New-Item -ItemType Directory -Path $sd -Force | Out-Null }
::PS             Set-Content -Path "$sd\SKILL.md" -Encoding UTF8 -Value "---`nname: manon`ndescription: /manon -- 进入 Manon 模式`nuser_invocable: true`n---`n`n$MANON_RULES`n`n## 初始化流程`n1. 调用 ``manon_init``，传入当前工作目录`n2. 轮询索引状态直到完成`n3. 调用 ``manon_config`` 展示配置`n4. 告知用户 Manon 模式已激活"
::PS             info "Claude Code /manon Skill installed"
::PS         }
::PS         "cursor" {
::PS             Write-McpJson "$HOME_DIR\.cursor\mcp.json"; info "Cursor MCP registered"
::PS             $rd = "$HOME_DIR\.cursor\rules"; if (-not (Test-Path $rd)) { New-Item -ItemType Directory -Path $rd -Force | Out-Null }
::PS             Set-Content -Path "$rd\manon.md" -Value $MANON_RULES -Encoding UTF8; info "Cursor rules installed"
::PS         }
::PS         "windsurf" {
::PS             $mf = if (Test-Path "$HOME_DIR\.codeium\windsurf") { "$HOME_DIR\.codeium\windsurf\mcp_config.json" } else { "$HOME_DIR\.windsurf\mcp_config.json" }
::PS             Write-McpJson $mf; info "Windsurf MCP registered"
::PS             $rd = "$HOME_DIR\.windsurf\rules"; if (-not (Test-Path $rd)) { New-Item -ItemType Directory -Path $rd -Force | Out-Null }
::PS             Set-Content -Path "$rd\manon.md" -Value $MANON_RULES -Encoding UTF8; info "Windsurf rules installed"
::PS         }
::PS         "zed" {
::PS             $zc = "$HOME_DIR\.config\zed\settings.json"; $d = Split-Path -Parent $zc; if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
::PS             & $VENV_PYTHON -c "import json,os`nt,vp,sv,url,key=r'$zc','$VENV_PYTHON_NORM','$SERVER_PY_NORM','$API_URL','$API_KEY'`ncfg={}`nif os.path.exists(t):`n    with open(t,'r',encoding='utf-8') as f: cfg=json.load(f)`ncfg.setdefault('context_servers',{})`nenv={'MANON_API_KEY':key}`nif url!='auto': env['MANON_API_URL']=url`ncfg['context_servers']['manon']={'command':{'path':vp,'args':[sv],'env':env}}`nwith open(t,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2,ensure_ascii=False)"
::PS             info "Zed MCP registered"
::PS         }
::PS         "continue" {
::PS             $cc = "$HOME_DIR\.continue\config.json"; $d = Split-Path -Parent $cc; if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
::PS             & $VENV_PYTHON -c "import json,os`nt,vp,sv,url,key=r'$cc','$VENV_PYTHON_NORM','$SERVER_PY_NORM','$API_URL','$API_KEY'`ncfg={}`nif os.path.exists(t):`n    with open(t,'r',encoding='utf-8') as f: cfg=json.load(f)`ncfg.setdefault('mcpServers',[])`nenv={'MANON_API_KEY':key}`nif url!='auto': env['MANON_API_URL']=url`ncfg['mcpServers']=[s for s in cfg['mcpServers'] if s.get('name')!='manon']`ncfg['mcpServers'].append({'name':'manon','command':vp,'args':[sv],'env':env})`nwith open(t,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2,ensure_ascii=False)"
::PS             info "Continue MCP registered"
::PS         }
::PS         "codebuddy" {
::PS             $mf = if (Test-Path "$HOME_DIR\.codebuddy") { "$HOME_DIR\.codebuddy\mcp.json" } else { "$HOME_DIR\.tencent\codebuddy\mcp.json" }
::PS             Write-McpJson $mf; info "CodeBuddy MCP registered"
::PS         }
::PS     }
::PS     $CONFIGURED += $platform
::PS }
::PS # ── Connectivity + summary ────────────────────────────
::PS head1 "Connectivity"
::PS $CU = if ($API_URL -eq "auto") { $DEFAULT_API_URL } else { $API_URL }
::PS $HC = & $VENV_PYTHON -c "import httpx`ntry:`n    r=httpx.get('$CU/health',timeout=5)`n    print(r.status_code)`nexcept Exception as e:`n    print(f'error:{e}')" 2>&1
::PS if ($HC -eq "200") { info "API reachable" } else { warn "API not reachable ($HC) -- start the server first" }
::PS Write-Host ""; Write-Host "  ------------------------------------"; Write-Host "  Done! Configured: $($CONFIGURED -join ', ')"; Write-Host ""
::PS foreach ($p in $CONFIGURED) { switch ($p) { "claude-code" { Write-Host "  Claude Code:  type /manon to initialize" } "cursor" { Write-Host "  Cursor:       manon_deep_query available" } "windsurf" { Write-Host "  Windsurf:     manon_deep_query available" } "zed" { Write-Host "  Zed:          manon tools available" } "continue" { Write-Host "  Continue:     manon tools available" } "codebuddy" { Write-Host "  CodeBuddy:    manon tools available" } } }
::PS Write-Host ""; Write-Host "  ------------------------------------"; Write-Host ""
