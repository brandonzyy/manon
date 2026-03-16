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
::PS $DEFAULT_API_URL = "http://saas.matrixone.online:3700"
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
::PS if ((Test-Path "$HOME_DIR\.config\opencode") -or (Get-Command opencode -ErrorAction SilentlyContinue)) { $PLATFORMS += "opencode" }
::PS if ((Test-Path "$HOME_DIR\.codex") -or (Get-Command codex -ErrorAction SilentlyContinue)) { $PLATFORMS += "codex" }
::PS if ($PLATFORMS.Count -eq 0) { err "No supported platform detected (Claude Code / Cursor / Windsurf / Zed / Continue / CodeBuddy / OpenCode / Codex)" }
::PS info "Detected: $($PLATFORMS -join ', ')"
::PS # ── Region-aware git remote ──────────────────────────
::PS $GIT_REMOTE_CN = "https://gitee.com/ymxy_1_0/manon.git"
::PS $GIT_REMOTE_INTL = "https://github.com/brandonzyy/manon.git"
::PS $REGION = "CN"; $REGION_FILE = "$env:USERPROFILE\.manon\region.json"
::PS if (Test-Path $REGION_FILE) { try { $REGION = (Get-Content $REGION_FILE -Raw | ConvertFrom-Json).region } catch { $REGION = "CN" } }
::PS if ($REGION -eq "CN") { $GIT_REMOTE = $GIT_REMOTE_CN; $GIT_BRANCH = "master" } else { $GIT_REMOTE = $GIT_REMOTE_INTL; $GIT_BRANCH = "main" }
::PS try { git -C $SCRIPT_DIR remote set-url origin $GIT_REMOTE 2>$null } catch {}
::PS info "Git remote -> $GIT_REMOTE ($REGION)"
::PS # ── Venv + deps ───────────────────────────────────────
::PS head1 "Dependencies"
::PS if (-not (Test-Path $VENV_DIR)) { & $pythonCmd -m venv $VENV_DIR }
::PS if (Test-Path "$VENV_DIR\Scripts\python.exe") { $VENV_PYTHON = "$VENV_DIR\Scripts\python.exe" } else { err "Failed to locate venv python" }
::PS & $VENV_PYTHON -m pip install -q -r "$SCRIPT_DIR\mcp\requirements.txt"
::PS info "Dependencies installed"
::PS # ── Check for existing API key ────────────────────────
::PS $API_KEY = ""; $API_URL = "auto"
::PS foreach ($cfg in @("$HOME_DIR\.claude.json","$HOME_DIR\.claude\settings.json","$HOME_DIR\.cursor\mcp.json","$HOME_DIR\.codeium\windsurf\mcp_config.json","$HOME_DIR\.windsurf\mcp_config.json","$HOME_DIR\.config\opencode\opencode.json","$HOME_DIR\.codex\config.toml")) {
::PS     if (Test-Path $cfg) {
::PS         $key = & $VENV_PYTHON -c "import json,re,sys`nf=sys.argv[1]`ntry:`n    if f.endswith('.toml'):`n        text=open(f,encoding='utf-8').read()`n        m=re.search(r'MANON_API_KEY\s*=\s*\x22(msk_[^\x22]+)\x22',text)`n        if m: print(m.group(1))`n    else:`n        d=json.load(open(f,encoding='utf-8'))`n        k=d.get('mcpServers',{}).get('manon',{}).get('env',{}).get('MANON_API_KEY','')`n        if not k: k=d.get('mcp',{}).get('manon',{}).get('environment',{}).get('MANON_API_KEY','')`n        if k.startswith('msk_'): print(k)`nexcept: pass" "$cfg" 2>`$null
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
::PS function Write-OpenCodeMcpJson($t) {
::PS     $d = Split-Path -Parent $t; if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
::PS     & $VENV_PYTHON -c "import json,os`nt,vp,sv,url,key=r'$t','$VENV_PYTHON_NORM','$SERVER_PY_NORM','$API_URL','$API_KEY'`ncfg={}`nif os.path.exists(t):`n    with open(t,'r',encoding='utf-8') as f: cfg=json.load(f)`ncfg.setdefault('mcp',{})`nenv={'MANON_API_KEY':key}`nif url!='auto': env['MANON_API_URL']=url`ncfg['mcp']['manon']={'type':'local','command':[vp,sv],'environment':env}`nwith open(t,'w',encoding='utf-8') as f: json.dump(cfg,f,indent=2,ensure_ascii=False)"
::PS }
::PS $MANON_RULES = "# Manon -- 代码智能工具规则`n`n当用户提问涉及代码理解、架构分析时，必须使用 Manon MCP 工具。`n`n| 场景 | 工具 |`n|------|------|`n| 代码理解/搜索 | ``manon_deep_query`` |`n| 调用关系/依赖 | ``manon_graph`` |`n| 改动影响 | ``manon_impact`` |"
::PS # ── Configure platforms ───────────────────────────────
::PS head1 "Configuration"; $CONFIGURED = @()
::PS foreach ($platform in $PLATFORMS) {
::PS     switch ($platform) {
::PS         "claude-code" {
::PS             Write-McpJson "$HOME_DIR\.claude.json"; info "Claude Code MCP registered"
::PS             $sd = "$HOME_DIR\.claude\skills\manon"; if (-not (Test-Path $sd)) { New-Item -ItemType Directory -Path $sd -Force | Out-Null }
::PS             Set-Content -Path "$sd\SKILL.md" -Encoding UTF8 -Value "---`nname: manon`ndescription: /manon -- 进入 Manon 模式`nuser_invocable: true`n---`n`n$MANON_RULES`n`n## 初始化流程（/manon 触发时执行）`n`n执行以下步骤，按顺序完成：`n`n### Step 1: manon_init`n调用 ``manon_init``，传入当前工作目录。从输出中提取 ``repo_id``。`n`n### Step 2: Smart Analysis（首次执行）`n检查 ``manon_init`` 输出中的标记：`n- 含 ``<!-- SMART_ANALYSIS_DONE -->`` → 跳到 Step 3`n- 含 ``<!-- SMART_ANALYSIS_NEEDED -->`` → 执行以下子步骤：`n  2a. 调用 ``manon_directory_signals(project_path)`` 获取目录信号 JSON`n  2b. 根据目录判断规则，对每个顶级目录判断 index 或 skip`n  2c. 调用 ``manon_configure_excludes(project_path, exclude_patterns)`` 应用排除`n  2d. 向用户展示分析结果`n`n### Step 3: 文件同步（AST 提取 + 上传）`n**3a.** 从 ``manon_init`` 输出中提取 ``MANON_DIR``（``<!-- MANON_DIR=xxx -->`` 标记）和 ``MANON_PYTHON``（``<!-- MANON_PYTHON=xxx -->`` 标记）。`n`n**3b.** 用 Bash 运行扫描脚本：`n<MANON_PYTHON> <MANON_DIR>/scripts/manon-scan.py <repo_id>`n- 脚本输出 JSON: {total_files, deleted_files, total_batches}`n- 展示: 扫描完成: {total_files} 个文件待同步, {deleted_files} 个已删除`n- 如果 total_files == 0 且 deleted_files == 0 → 无变更 → 跳到 Step 4`n`n**3c.** 调用 ``manon_scan_files(repo_id)`` 加载扫描缓存到 MCP 内存。`n`n**3d.** 循环调用 ``manon_upload_batch(repo_id)`` 直到 status == done:`n- 每次展示: 上传中: {uploaded}/{total} ({batch}/{total_batches})`n- 完成时展示: 同步完成: {uploaded} 文件同步, {deleted} 文件删除`n`n### Step 4: 索引覆盖率`n调用 ``manon_index_status(repo_id)`` 展示索引状态和覆盖率。`n`n### Step 5: 代码健康度`n调用 ``manon_code_health(repo_id)`` 展示代码健康评分。`n`n### Step 6: 激活`n告知用户 Manon 模式已激活。`n`n## 目录判断规则`n1. 源码占比：编程语言扩展名文件占比 > 30% → index`n2. 构建配置：有 package.json/tsconfig.json/Cargo.toml 等 → index`n3. 业务关联：核心代码/共享库 → index；静态资源/文档/数据/日志 → skip"
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
::PS             $cbDir = if (Test-Path "$HOME_DIR\.codebuddy") { "$HOME_DIR\.codebuddy" } else { "$HOME_DIR\.tencent\codebuddy" }
::PS             $sd = "$cbDir\skills\manon"; if (-not (Test-Path $sd)) { New-Item -ItemType Directory -Path $sd -Force | Out-Null }
::PS             Set-Content -Path "$sd\SKILL.md" -Encoding UTF8 -Value "---`nname: manon`ndescription: /manon -- 进入 Manon 模式`nuser_invocable: true`n---`n`n$MANON_RULES`n`n## 初始化流程（/manon 触发时执行）`n`n执行以下步骤，按顺序完成：`n`n### Step 1: manon_init`n调用 ``manon_init``，传入当前工作目录。从输出中提取 ``repo_id``。`n`n### Step 2: Smart Analysis（首次执行）`n检查 ``manon_init`` 输出中的标记：`n- 含 ``<!-- SMART_ANALYSIS_DONE -->`` → 跳到 Step 3`n- 含 ``<!-- SMART_ANALYSIS_NEEDED -->`` → 执行以下子步骤：`n  2a. 调用 ``manon_directory_signals(project_path)`` 获取目录信号 JSON`n  2b. 根据目录判断规则，对每个顶级目录判断 index 或 skip`n  2c. 调用 ``manon_configure_excludes(project_path, exclude_patterns)`` 应用排除`n  2d. 向用户展示分析结果`n`n### Step 3: 文件同步（AST 提取 + 上传）`n**3a.** 从 ``manon_init`` 输出中提取 ``MANON_DIR``（``<!-- MANON_DIR=xxx -->`` 标记）和 ``MANON_PYTHON``（``<!-- MANON_PYTHON=xxx -->`` 标记）。`n`n**3b.** 用 Bash 运行扫描脚本：`n<MANON_PYTHON> <MANON_DIR>/scripts/manon-scan.py <repo_id>`n- 脚本输出 JSON: {total_files, deleted_files, total_batches}`n- 展示: 扫描完成: {total_files} 个文件待同步, {deleted_files} 个已删除`n- 如果 total_files == 0 且 deleted_files == 0 → 无变更 → 跳到 Step 4`n`n**3c.** 调用 ``manon_scan_files(repo_id)`` 加载扫描缓存到 MCP 内存。`n`n**3d.** 循环调用 ``manon_upload_batch(repo_id)`` 直到 status == done:`n- 每次展示: 上传中: {uploaded}/{total} ({batch}/{total_batches})`n- 完成时展示: 同步完成: {uploaded} 文件同步, {deleted} 文件删除`n`n### Step 4: 索引覆盖率`n调用 ``manon_index_status(repo_id)`` 展示索引状态和覆盖率。`n`n### Step 5: 代码健康度`n调用 ``manon_code_health(repo_id)`` 展示代码健康评分。`n`n### Step 6: 激活`n告知用户 Manon 模式已激活。`n`n## 目录判断规则`n1. 源码占比：编程语言扩展名文件占比 > 30% → index`n2. 构建配置：有 package.json/tsconfig.json/Cargo.toml 等 → index`n3. 业务关联：核心代码/共享库 → index；静态资源/文档/数据/日志 → skip"
::PS             info "CodeBuddy /manon Skill installed"
::PS         }
::PS         "opencode" {
::PS             Write-OpenCodeMcpJson "$HOME_DIR\.config\opencode\opencode.json"; info "OpenCode MCP registered"
::PS             $sd = "$HOME_DIR\.claude\skills\manon"
::PS             if (-not (Test-Path "$sd\SKILL.md")) {
::PS                 if (-not (Test-Path $sd)) { New-Item -ItemType Directory -Path $sd -Force | Out-Null }
::PS                 Set-Content -Path "$sd\SKILL.md" -Encoding UTF8 -Value "---`nname: manon`ndescription: /manon -- 进入 Manon 模式`nuser_invocable: true`n---`n`n$MANON_RULES`n`n## 初始化流程（/manon 触发时执行）`n`n执行以下步骤，按顺序完成：`n`n### Step 1: manon_init`n调用 ``manon_init``，传入当前工作目录。从输出中提取 ``repo_id``。`n`n### Step 2: Smart Analysis（首次执行）`n检查 ``manon_init`` 输出中的标记：`n- 含 ``<!-- SMART_ANALYSIS_DONE -->`` → 跳到 Step 3`n- 含 ``<!-- SMART_ANALYSIS_NEEDED -->`` → 执行以下子步骤：`n  2a. 调用 ``manon_directory_signals(project_path)`` 获取目录信号 JSON`n  2b. 根据目录判断规则，对每个顶级目录判断 index 或 skip`n  2c. 调用 ``manon_configure_excludes(project_path, exclude_patterns)`` 应用排除`n  2d. 向用户展示分析结果`n`n### Step 3: 文件同步（AST 提取 + 上传）`n**3a.** 从 ``manon_init`` 输出中提取 ``MANON_DIR``（``<!-- MANON_DIR=xxx -->`` 标记）和 ``MANON_PYTHON``（``<!-- MANON_PYTHON=xxx -->`` 标记）。`n`n**3b.** 用 Bash 运行扫描脚本：`n<MANON_PYTHON> <MANON_DIR>/scripts/manon-scan.py <repo_id>`n- 脚本输出 JSON: {total_files, deleted_files, total_batches}`n- 展示: 扫描完成: {total_files} 个文件待同步, {deleted_files} 个已删除`n- 如果 total_files == 0 且 deleted_files == 0 → 无变更 → 跳到 Step 4`n`n**3c.** 调用 ``manon_scan_files(repo_id)`` 加载扫描缓存到 MCP 内存。`n`n**3d.** 循环调用 ``manon_upload_batch(repo_id)`` 直到 status == done:`n- 每次展示: 上传中: {uploaded}/{total} ({batch}/{total_batches})`n- 完成时展示: 同步完成: {uploaded} 文件同步, {deleted} 文件删除`n`n### Step 4: 索引覆盖率`n调用 ``manon_index_status(repo_id)`` 展示索引状态和覆盖率。`n`n### Step 5: 代码健康度`n调用 ``manon_code_health(repo_id)`` 展示代码健康评分。`n`n### Step 6: 激活`n告知用户 Manon 模式已激活。`n`n## 目录判断规则`n1. 源码占比：编程语言扩展名文件占比 > 30% → index`n2. 构建配置：有 package.json/tsconfig.json/Cargo.toml 等 → index`n3. 业务关联：核心代码/共享库 → index；静态资源/文档/数据/日志 → skip"
::PS                 info "OpenCode /manon Skill installed (via ~/.claude/skills/)"
::PS             } else { info "OpenCode Skill already present (shared with Claude Code)" }
::PS         }
::PS         "codex" {
::PS             $cf = "$HOME_DIR\.codex\config.toml"
::PS             if ((Test-Path $cf) -and (Select-String -Path $cf -Pattern '\[mcp_servers\.manon\]' -Quiet)) { info "Codex MCP already configured" }
::PS             else { Add-Content -Path $cf -Encoding UTF8 -Value "`n[mcp_servers.manon]`ncommand = `"$VENV_PYTHON_NORM`"`nargs = [`"$SERVER_PY_NORM`"]`nenv = { MANON_API_KEY = `"$API_KEY`" }`nstartup_timeout_sec = 30.0`ntool_timeout_sec = 120.0"; info "Codex MCP registered" }
::PS             $af = "$HOME_DIR\AGENTS.md"
::PS             if ((Test-Path $af) -and (Select-String -Path $af -Pattern 'manon_search' -Quiet)) { info "Codex AGENTS.md already has Manon rules" }
::PS             else { Add-Content -Path $af -Encoding UTF8 -Value "`n# Codex AGENTS.md -- Manon 知识图谱规则`n`n## 核心规则（MUST）`n`n代码理解、架构分析、代码搜索时，必须优先使用 Manon MCP 工具，禁止跳过图谱直接搜索文件。`n`n## 强制规则`n`n### 规则 1：搜索前必查图谱`n在使用 grep、find、文件搜索等操作前，必须先用 manon_search / manon_deep_query / manon_graph 查询图谱。`n图谱不足时才用文件搜索补充，并声明'图谱未覆盖，补充搜索'。`n`n### 规则 2：编辑代码前必查上下文`n修改代码文件前，必须先用 manon_search/manon_graph 了解上下文，同时用 git log 查看近期改动。`n`n### 规则 3：探索代码库前必查图谱`n在进行大范围代码探索或规划前，必须先用 manon_search / manon_deep_query 查询图谱。"; info "Codex AGENTS.md rules installed" }
::PS         }
::PS     }
::PS     $CONFIGURED += $platform
::PS }
::PS # ── Connectivity + summary ────────────────────────────
::PS head1 "Connectivity"
::PS $CU = if ($API_URL -eq "auto") { $DEFAULT_API_URL } else { $API_URL }
::PS $HC = & $VENV_PYTHON -c "import httpx`ntry:`n    r=httpx.get('$CU/health',timeout=5)`n    print(r.status_code)`nexcept Exception as e:`n    print(f'error:{e}')" 2>&1
::PS if ($HC -eq "200") { info "API reachable" } else { warn "API not reachable ($HC) -- start the server first" }
::PS $MV = & $VENV_PYTHON -c "import subprocess`nr=subprocess.run(['git','rev-list','--count','HEAD'],cwd=r'$SCRIPT_DIR',capture_output=True,text=True)`nprint(f'0.1.{r.stdout.strip()}' if r.returncode==0 else '0.1.0')" 2>&1; if (-not $MV) { $MV = "0.1.0" }
::PS Write-Host ""; Write-Host "  ------------------------------------"; Write-Host "  Manon v$MV installed"; Write-Host "  Configured: $($CONFIGURED -join ', ')"; Write-Host ""
::PS foreach ($p in $CONFIGURED) { switch ($p) { "claude-code" { Write-Host "  Claude Code:  type /manon to initialize" } "cursor" { Write-Host "  Cursor:       manon_deep_query available" } "windsurf" { Write-Host "  Windsurf:     manon_deep_query available" } "zed" { Write-Host "  Zed:          manon tools available" } "continue" { Write-Host "  Continue:     manon tools available" } "codebuddy" { Write-Host "  CodeBuddy:    type /manon to initialize" } "opencode" { Write-Host "  OpenCode:     type /manon to initialize" } "codex" { Write-Host "  Codex:        manon tools available via MCP" } } }
::PS Write-Host ""; Write-Host "  ------------------------------------"; Write-Host ""
