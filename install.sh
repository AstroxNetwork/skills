#!/bin/sh
set -eu

REPOSITORY=${HOLYCRAB_INSTALL_REPOSITORY:-AstroxNetwork/skills}
VERSION=${HOLYCRAB_INSTALL_VERSION:-v0.2.0}
SOURCE_DIR=${HOLYCRAB_INSTALL_SOURCE_DIR:-}
INSTALL_MCP=${HOLYCRAB_INSTALL_MCP:-1}
INSTALL_AGENTS=${HOLYCRAB_INSTALL_AGENTS:-codex,claude}
PREFIX=${HOLYCRAB_INSTALL_PREFIX:-"$HOME/.local"}
BIN_DIR="$PREFIX/bin"
LIB_DIR="$PREFIX/lib/holycrab"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/holycrab-install.XXXXXX")

command -v python3 >/dev/null 2>&1 || {
  echo "Python 3 is required for this HolyCrab CLI build." >&2
  exit 1
}

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT HUP INT TERM

fetch() {
  fetch_relative=$1
  fetch_destination=$2
  if [ -n "$SOURCE_DIR" ]; then
    cp "$SOURCE_DIR/$fetch_relative" "$fetch_destination"
  else
    command -v curl >/dev/null 2>&1 || {
      echo "curl is required to install HolyCrab." >&2
      exit 1
    }
    curl -fsSL "https://raw.githubusercontent.com/$REPOSITORY/$VERSION/$fetch_relative" -o "$fetch_destination"
  fi
}

mkdir -p "$BIN_DIR" "$LIB_DIR/references"
fetch "holycrab/scripts/holycrab_cli.py" "$TEMP_DIR/holycrab_cli.py"
fetch "holycrab/references/capabilities.json" "$TEMP_DIR/capabilities.json"
fetch "bin/holycrab" "$TEMP_DIR/holycrab"
install -m 755 "$TEMP_DIR/holycrab_cli.py" "$LIB_DIR/holycrab_cli.py"
install -m 644 "$TEMP_DIR/capabilities.json" "$LIB_DIR/references/capabilities.json"
install -m 755 "$TEMP_DIR/holycrab" "$BIN_DIR/holycrab"

install_skill() {
  skill_destination=$1
  mkdir -p "$skill_destination/references"
  fetch "holycrab/SKILL.md" "$TEMP_DIR/SKILL.md"
  fetch "holycrab/references/capabilities.json" "$TEMP_DIR/skill-capabilities.json"
  install -m 644 "$TEMP_DIR/SKILL.md" "$skill_destination/SKILL.md"
  install -m 644 "$TEMP_DIR/skill-capabilities.json" "$skill_destination/references/capabilities.json"
}

case ",$INSTALL_AGENTS," in
  *,codex,*) install_skill "$HOME/.agents/skills/holycrab" ;;
esac
case ",$INSTALL_AGENTS," in
  *,claude,*) install_skill "$HOME/.claude/skills/holycrab" ;;
esac

if [ "$INSTALL_MCP" = "1" ]; then
  case ",$INSTALL_AGENTS," in
    *,codex,*)
      if command -v codex >/dev/null 2>&1; then
        if ! codex mcp get holycrab >/dev/null 2>&1; then
          if ! codex mcp add holycrab -- "$BIN_DIR/holycrab" mcp serve >/dev/null; then
            echo "Warning: Codex MCP registration failed; run: codex mcp add holycrab -- $BIN_DIR/holycrab mcp serve" >&2
          fi
        fi
      fi
      ;;
  esac
  case ",$INSTALL_AGENTS," in
    *,claude,*)
      if command -v claude >/dev/null 2>&1; then
        if ! claude mcp get holycrab >/dev/null 2>&1; then
          if ! claude mcp add --scope user holycrab -- "$BIN_DIR/holycrab" mcp serve >/dev/null; then
            echo "Warning: Claude MCP registration failed; run: claude mcp add --scope user holycrab -- $BIN_DIR/holycrab mcp serve" >&2
          fi
        fi
      fi
      ;;
  esac
fi

echo "HolyCrab CLI, local MCP, and Skill are installed."
echo "Command: $BIN_DIR/holycrab"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "Add this directory to PATH: $BIN_DIR" ;;
esac
echo "Next: holycrab setup"
