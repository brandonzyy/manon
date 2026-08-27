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
::PS $API_URL_CN = "http://saas.matrixone.online:3700"
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
::PS if ((Test-Path "$HOME_DIR\.codex") -or (Get-Command codex -ErrorAction SilentlyContinue)) { $PLATFORMS += "codex" }
::PS if ((Test-Path "$HOME_DIR\.zcode") -or (Get-Command zcode -ErrorAction SilentlyContinue)) { $PLATFORMS += "zcode" }
::PS if ((Test-Path "$HOME_DIR\.kimi-code") -or (Get-Command kimi -ErrorAction SilentlyContinue)) { $PLATFORMS += "kimi-code" }
::PS if ($PLATFORMS.Count -eq 0) { err "No supported platform detected (Claude Code / Codex / ZCode / Kimi Code)" }
::PS info "Detected: $($PLATFORMS -join ', ')"
::PS # ── Git remote ────────────────────────────────────────
::PS $GIT_REMOTE = "https://github.com/brandonzyy/manon.git"
::PS $GIT_BRANCH = "master"
::PS try { git -C $SCRIPT_DIR remote set-url origin $GIT_REMOTE 2>$null } catch {}
::PS info "Git remote -> $GIT_REMOTE"
::PS # ── Venv + deps ───────────────────────────────────────
::PS head1 "Dependencies"
::PS if (-not (Test-Path $VENV_DIR)) { & $pythonCmd -m venv $VENV_DIR }
::PS if (Test-Path "$VENV_DIR\Scripts\python.exe") { $VENV_PYTHON = "$VENV_DIR\Scripts\python.exe" } else { err "Failed to locate venv python" }
::PS & $VENV_PYTHON -m pip install -q -r "$SCRIPT_DIR\manon_mcp\requirements.txt"
::PS info "Dependencies installed"
::PS # ── Check for existing API key ────────────────────────
::PS $API_KEY = ""; $API_URL = $API_URL_CN
::PS foreach ($cfg in @("$HOME_DIR\.claude.json","$HOME_DIR\.claude\settings.json","$HOME_DIR\.codex\config.toml","$HOME_DIR\.zcode\cli\config.json","$HOME_DIR\.kimi-code\mcp.json")) {
::PS     if (Test-Path $cfg) {
::PS         $key = & $VENV_PYTHON -c "import json,re,sys`nf=sys.argv[1]`ntry:`n    if f.endswith('.toml'):`n        text=open(f,encoding='utf-8').read()`n        m=re.search(r'MANON_API_KEY\s*=\s*\x22(msk_[^\x22]+)\x22',text)`n        if m: print(m.group(1))`n    else:`n        d=json.load(open(f,encoding='utf-8'))`n        k=d.get('mcpServers',{}).get('manon',{}).get('env',{}).get('MANON_API_KEY','')`n        if not k: k=d.get('mcp',{}).get('manon',{}).get('environment',{}).get('MANON_API_KEY','')`n        if not k: k=d.get('mcp',{}).get('servers',{}).get('manon',{}).get('env',{}).get('MANON_API_KEY','')`n        if k.startswith('msk_'): print(k)`nexcept: pass" "$cfg" 2>`$null
::PS         if ($key -and $key.Trim().StartsWith("msk_")) { $API_KEY = $key.Trim(); info "Existing API key found, skipping registration"; break }
::PS     }
::PS }
::PS # ── Auto-register ─────────────────────────────────────
::PS if (-not $API_KEY) {
::PS     head1 "Auto-register"
::PS     $REG_URL = $API_URL
::PS     $REG_RESULT = & $VENV_PYTHON -c "import httpx,sys`ntry:`n    r=httpx.post('$REG_URL/api/v1/register',json={'name':'$env:USERNAME'},timeout=10)`n    r.raise_for_status()`n    print(r.json()['api_key'])`nexcept Exception as e:`n    print(f'FAIL:{e}',file=sys.stderr);sys.exit(1)" 2>&1
::PS     if ($REG_RESULT -and $REG_RESULT.Trim().StartsWith("msk_")) { $API_KEY = $REG_RESULT.Trim(); info "Auto-registered, API key: $($API_KEY.Substring(0,12))..." }
::PS     else { warn "Auto-register failed ($REG_RESULT) -- set key manually later"; $API_KEY = "" }
::PS }
::PS # ── Normalize paths + MCP writer ──────────────────────
::PS $VENV_PYTHON_NORM = $VENV_PYTHON -replace '\\', '/'; $SERVER_PY_NORM = $SERVER_PY -replace '\\', '/'
::PS function Write-McpJson($t) {
::PS     $d = Split-Path -Parent $t; if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
::PS     & $VENV_PYTHON -c "import json,os`nt,vp,sv,url,key=r'$t','$VENV_PYTHON_NORM','$SERVER_PY_NORM','$API_URL','$API_KEY'`ncfg={}`nif os.path.exists(t):`n    with open(t,'r',encoding='utf-8') as f: cfg=json.load(f)`ncfg.setdefault('mcpServers',{})`nenv={'MANON_API_KEY':key}`nif url!='auto': env['MANON_API_URL']=url`ncfg['mcpServers']['manon']={'command':vp,'args':[sv],'env':env}`nif 'playwright' not in cfg['mcpServers']: cfg['mcpServers']['playwright']={'command':'npx','args':['@playwright/mcp@latest']}`nwith open(t,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2,ensure_ascii=False)"
::PS }
::PS function Write-ZcodeMcpJson($t) {
::PS     $d = Split-Path -Parent $t; if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
::PS     & $VENV_PYTHON -c "import json,os`nt,vp,sv,url,key=r'$t','$VENV_PYTHON_NORM','$SERVER_PY_NORM','$API_URL','$API_KEY'`ncfg={}`nif os.path.exists(t):`n    with open(t,'r',encoding='utf-8') as f: cfg=json.load(f)`ncfg.setdefault('mcp',{}).setdefault('servers',{})`nenv={'MANON_API_KEY':key}`nif url!='auto': env['MANON_API_URL']=url`ncfg['mcp']['servers']['manon']={'type':'stdio','command':vp,'args':[sv],'env':env}`nwith open(t,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2,ensure_ascii=False)"
::PS }
::PS function Install-AgentsSkills {
::PS     # ~/.agents/skills —— ZCode 与 Kimi Code 用户级都读的共享位，装一份覆盖两个平台
::PS     $base = "$HOME_DIR\.agents\skills"
::PS     $sd = "$base\manon"; New-Item -ItemType Directory -Path "$sd\scripts" -Force | Out-Null
::PS     Copy-Item "$SCRIPT_DIR\skills\manon\SKILL.md" "$sd\SKILL.md" -Force
::PS     Copy-Item "$SCRIPT_DIR\skills\manon\scripts\*.py" "$sd\scripts\" -Force
::PS     $as = "$base\assurance"; New-Item -ItemType Directory -Path "$as\scripts","$as\references" -Force | Out-Null
::PS     Copy-Item "$SCRIPT_DIR\skills\assurance\SKILL.md" "$as\SKILL.md" -Force
::PS     Copy-Item "$SCRIPT_DIR\skills\assurance\scripts\*.py" "$as\scripts\" -Force
::PS     Copy-Item "$SCRIPT_DIR\skills\assurance\references\*.md" "$as\references\" -Force
::PS     foreach ($old in @("tc","dao","audit","retire-checks","experience","idea")) { $od = "$base\$old"; if (Test-Path $od) { Remove-Item -Recurse -Force $od } }
::PS }
::PS # ── Configure platforms ───────────────────────────────
::PS head1 "Configuration"; $CONFIGURED = @()
::PS foreach ($platform in $PLATFORMS) {
::PS     switch ($platform) {
::PS         "claude-code" {
::PS             Write-McpJson "$HOME_DIR\.claude.json"; info "Claude Code MCP registered"
::PS             $sd = "$HOME_DIR\.claude\skills\manon"; New-Item -ItemType Directory -Path "$sd\scripts" -Force | Out-Null; Copy-Item "$SCRIPT_DIR\skills\manon\SKILL.md" "$sd\SKILL.md"; Copy-Item "$SCRIPT_DIR\skills\manon\scripts\*.py" "$sd\scripts\"; info "Claude Code /manon Skill installed"
::PS             & $VENV_PYTHON -c "import sys; sys.path.insert(0, r'$SCRIPT_DIR'); from manon_mcp._hooks import _install_claude_hooks; _install_claude_hooks()"
::PS             info "Claude Code hooks installed (search/edit/agent/commit->impact)"
::PS             $as_sd = "$HOME_DIR\.claude\skills\assurance"; New-Item -ItemType Directory -Path "$as_sd\scripts","$as_sd\references" -Force | Out-Null; Copy-Item "$SCRIPT_DIR\skills\assurance\SKILL.md" "$as_sd\SKILL.md"; Copy-Item "$SCRIPT_DIR\skills\assurance\scripts\*.py" "$as_sd\scripts\"; Copy-Item "$SCRIPT_DIR\skills\assurance\references\*.md" "$as_sd\references\"; info "Claude Code /assurance Skill installed (assurance stack: gap-fill, coverage loop, behaviour audit, simplification, retirement)"
::PS             foreach ($old in @("tc","dao","audit","retire-checks","experience","idea")) { $od = "$HOME_DIR\.claude\skills\$old"; if (Test-Path $od) { Remove-Item -Recurse -Force $od } }
::PS         }
::PS         "codex" {
::PS             $cf = "$HOME_DIR\.codex\config.toml"; $cd = Split-Path -Parent $cf; if (-not (Test-Path $cd)) { New-Item -ItemType Directory -Path $cd -Force | Out-Null }
::PS             if ((Test-Path $cf) -and (Select-String -Path $cf -Pattern '\[mcp_servers\.manon\]' -Quiet)) { info "Codex MCP already configured" }
::PS             else { Add-Content -Path $cf -Encoding UTF8 -Value "`n[mcp_servers.manon]`ncommand = `"$VENV_PYTHON_NORM`"`nargs = [`"$SERVER_PY_NORM`"]`nenv = { MANON_API_KEY = `"$API_KEY`" }`nstartup_timeout_sec = 30.0`ntool_timeout_sec = 120.0"; info "Codex MCP registered" }
::PS             $af = "$HOME_DIR\AGENTS.md"
::PS             if ((Test-Path $af) -and (Select-String -Path $af -Pattern 'manon_search' -Quiet)) { info "Codex AGENTS.md already has Manon rules" }
::PS             else { Add-Content -Path $af -Encoding UTF8 -Value "`n# Codex AGENTS.md -- Manon 知识图谱规则`n`n## 核心规则（MUST）`n`n代码理解、架构分析、代码搜索时，必须优先使用 Manon MCP 工具，禁止跳过图谱直接搜索文件。`n`n## 强制规则`n`n### 规则 1：搜索前必查图谱`n在使用 grep、find、文件搜索等操作前，必须先用 manon_search / manon_deep_query / manon_graph 查询图谱。`n图谱不足时才用文件搜索补充，并声明'图谱未覆盖，补充搜索'。`n`n### 规则 2：编辑代码前必查上下文`n修改代码文件前，必须先用 manon_search/manon_graph 了解上下文，同时用 git log 查看近期改动。`n`n### 规则 3：探索代码库前必查图谱`n在进行大范围代码探索或规划前，必须先用 manon_search / manon_deep_query 查询图谱。"; info "Codex AGENTS.md rules installed" }
::PS         }
::PS         "zcode" {
::PS             Write-ZcodeMcpJson "$HOME_DIR\.zcode\cli\config.json"; info "ZCode MCP registered"
::PS             Install-AgentsSkills; info "ZCode /manon + /assurance Skills installed (via ~/.agents/skills/)"
::PS         }
::PS         "kimi-code" {
::PS             Write-McpJson "$HOME_DIR\.kimi-code\mcp.json"; info "Kimi Code MCP registered"
::PS             Install-AgentsSkills; info "Kimi Code /manon + /assurance Skills installed (via ~/.agents/skills/)"
::PS         }
::PS     }
::PS     $CONFIGURED += $platform
::PS }
::PS # ── Connectivity + summary ────────────────────────────
::PS head1 "Connectivity"
::PS $CU = $API_URL
::PS $HC = & $VENV_PYTHON -c "import httpx`ntry:`n    r=httpx.get('$CU/health',timeout=5)`n    print(r.status_code)`nexcept Exception as e:`n    print(f'error:{e}')" 2>&1
::PS if ($HC -eq "200") { info "API reachable" } else { warn "API not reachable ($HC) -- start the server first" }
::PS $MV = & $VENV_PYTHON -c "from pathlib import Path`nimport subprocess`nvf=Path(r'$SCRIPT_DIR') / 'VERSION'`ntry:`n    v=vf.read_text(encoding='utf-8').strip()`n    print(v if v else '1.0.0')`nexcept Exception:`n    r=subprocess.run(['git','rev-list','--count','HEAD'],cwd=r'$SCRIPT_DIR',capture_output=True,text=True)`n    print(f'1.0.{r.stdout.strip()}' if r.returncode==0 else '1.0.0')" 2>&1; if (-not $MV) { $MV = "1.0.0" }
::PS Write-Host ""; Write-Host "  ------------------------------------"; Write-Host "  Manon v$MV installed"; Write-Host "  Configured: $($CONFIGURED -join ', ')"; Write-Host ""
::PS foreach ($p in $CONFIGURED) { switch ($p) { "claude-code" { Write-Host "  Claude Code:  type /manon to initialize" } "codex" { Write-Host "  Codex:        manon tools available via MCP" } "zcode" { Write-Host "  ZCode:        type /manon to initialize" } "kimi-code" { Write-Host "  Kimi Code:    type /manon to initialize" } } }
::PS Write-Host ""; Write-Host "  ------------------------------------"; Write-Host ""
