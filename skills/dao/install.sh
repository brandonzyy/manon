#!/bin/bash
# Dao Skill Installer

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILLS_DEST="$HOME/.claude/skills/dao"

echo "Installing dao skill..."

# Copy skill files
mkdir -p "$SKILLS_DEST/scripts"
cp "$SKILL_DIR/SKILL.md" "$SKILLS_DEST/SKILL.md"
cp "$SKILL_DIR/scripts/"*.py "$SKILLS_DEST/scripts/"
chmod +x "$SKILLS_DEST/scripts/"*.py

echo "Installed to $SKILLS_DEST"
echo "Restart Claude Code to activate /dao"
