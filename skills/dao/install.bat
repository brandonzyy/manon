@echo off
REM Dao Skill Installer for Windows

set SKILL_DIR=%~dp0
set SKILLS_DEST=%USERPROFILE%\.claude\skills\dao

echo Installing dao skill...

if not exist "%SKILLS_DEST%\scripts" mkdir "%SKILLS_DEST%\scripts"
copy /Y "%SKILL_DIR%SKILL.md" "%SKILLS_DEST%\SKILL.md" >nul
copy /Y "%SKILL_DIR%scripts\*.py" "%SKILLS_DEST%\scripts\" >nul

echo Installed to %SKILLS_DEST%
echo Restart Claude Code to activate /dao
