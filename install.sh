#!/bin/sh
set -eu

REPOSITORY=AstroxNetwork/skills
VERSION=v0.3.0
SOURCE_DIR=${HOLYCRAB_INSTALL_SOURCE_DIR:-}
INSTALL_MCP=${HOLYCRAB_INSTALL_MCP:-1}
INSTALL_AGENTS=${HOLYCRAB_INSTALL_AGENTS:-codex,claude}
PREFIX=${HOLYCRAB_INSTALL_PREFIX:-"$HOME/.local"}
BIN_DIR="$PREFIX/bin"
LIB_DIR="$PREFIX/lib/holycrab"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/holycrab-install.XXXXXX")
SHA256_HOLYCRAB_CLI=5d059d1f459e96e6aee383c1085608dba2d7f2300a9012ad9e7a90ec0c5bb111
SHA256_CAPABILITIES=79e3f5b63cfbef2ff5518c2280592d303c155f0d262788f50f46a59873773fa5
SHA256_LAUNCHER=e3b4bce3b4b64d32ccefbbe50990c8bb100d9b88bb16cf5cbe821ef3856ef2f1
SHA256_SKILL=aa83167e5fb3418be5361f61baf91f7ae969d21878d1dc51bf90be6170872a41
SHA256_OPENAI_YAML=64bd549cd32e989324d5a17c2550cd54dfecccf70b4637b05b062a2fb709c1a7
SHA256_SEGNO=28c7d081ed0cf935e0411293a465efd4d500704072cdb039778a2ab8736190c7
SHA256_SEGNO_LICENSE=de6c85fccf5d52902aa13dfe2dc6d2a2a106fc3419ed438f3460f0d4b76a6935

command -v python3 >/dev/null 2>&1 || {
  echo "Python 3.10 or newer is required for HolyCrab." >&2
  exit 1
}
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required for HolyCrab." >&2
  exit 1
}

cleanup() {
  rm -rf "$TEMP_DIR"
}
trap cleanup EXIT HUP INT TERM

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    python3 -c 'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())' "$1"
  fi
}

verify_sha256() {
  verify_file=$1
  verify_expected=$2
  verify_relative=$3
  verify_actual=$(sha256_file "$verify_file")
  if [ "$verify_actual" != "$verify_expected" ]; then
    echo "SHA-256 verification failed for $verify_relative; installation stopped." >&2
    exit 1
  fi
}

fetch() {
  fetch_relative=$1
  fetch_destination=$2
  fetch_sha256=$3
  if [ -n "$SOURCE_DIR" ]; then
    cp "$SOURCE_DIR/$fetch_relative" "$fetch_destination"
  else
    command -v curl >/dev/null 2>&1 || {
      echo "curl is required to install HolyCrab." >&2
      exit 1
    }
    curl -fsSL "https://raw.githubusercontent.com/$REPOSITORY/$VERSION/$fetch_relative" -o "$fetch_destination"
    verify_sha256 "$fetch_destination" "$fetch_sha256" "$fetch_relative"
  fi
}

mkdir -p "$BIN_DIR" "$LIB_DIR/references" "$LIB_DIR/vendor"
fetch "holycrab/scripts/holycrab_cli.py" "$TEMP_DIR/holycrab_cli.py" "$SHA256_HOLYCRAB_CLI"
fetch "holycrab/references/capabilities.json" "$TEMP_DIR/capabilities.json" "$SHA256_CAPABILITIES"
fetch "bin/holycrab" "$TEMP_DIR/holycrab" "$SHA256_LAUNCHER"
fetch "holycrab/SKILL.md" "$TEMP_DIR/SKILL.md" "$SHA256_SKILL"
fetch "holycrab/agents/openai.yaml" "$TEMP_DIR/openai.yaml" "$SHA256_OPENAI_YAML"
fetch "holycrab/scripts/vendor/segno-1.6.6-py3-none-any.whl" "$TEMP_DIR/segno.whl" "$SHA256_SEGNO"
fetch "holycrab/scripts/vendor/LICENSE.segno" "$TEMP_DIR/LICENSE.segno" "$SHA256_SEGNO_LICENSE"
install -m 755 "$TEMP_DIR/holycrab_cli.py" "$LIB_DIR/holycrab_cli.py"
install -m 644 "$TEMP_DIR/capabilities.json" "$LIB_DIR/references/capabilities.json"
install -m 755 "$TEMP_DIR/holycrab" "$BIN_DIR/holycrab"
install -m 644 "$TEMP_DIR/segno.whl" "$LIB_DIR/vendor/segno-1.6.6-py3-none-any.whl"
install -m 644 "$TEMP_DIR/LICENSE.segno" "$LIB_DIR/vendor/LICENSE.segno"

install_skill() {
  skill_destination=$1
  mkdir -p "$skill_destination/references" "$skill_destination/agents"
  install -m 644 "$TEMP_DIR/SKILL.md" "$skill_destination/SKILL.md"
  install -m 644 "$TEMP_DIR/capabilities.json" "$skill_destination/references/capabilities.json"
  install -m 644 "$TEMP_DIR/openai.yaml" "$skill_destination/agents/openai.yaml"
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
