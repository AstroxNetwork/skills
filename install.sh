#!/bin/sh
set -eu

REPOSITORY=AstroxNetwork/skills
VERSION=v0.4.1
SOURCE_DIR=${HOLYCRAB_INSTALL_SOURCE_DIR:-}
INSTALL_MCP=${HOLYCRAB_INSTALL_MCP:-1}
INSTALL_AGENTS=${HOLYCRAB_INSTALL_AGENTS:-codex,claude}
PREFIX=${HOLYCRAB_INSTALL_PREFIX:-"$HOME/.local"}
BIN_DIR="$PREFIX/bin"
LIB_DIR="$PREFIX/lib/holycrab"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/holycrab-install.XXXXXX")
BACKUP_DIR="$TEMP_DIR/backup"
INSTALL_STARTED=0
INSTALL_COMPLETE=0
HAD_LIB=0
HAD_LAUNCHER=0
HAD_CODEX_SKILL=0
HAD_CLAUDE_SKILL=0
PATH_REG_KIND=none
PATH_REG_DIR="$BIN_DIR"
PATH_REG_PROFILE=
PATH_REG_ADDED=0
PATH_REG_ADDED_THIS_RUN=0
PREVIOUS_PATH_PROFILE=
SHA256_HOLYCRAB_CLI=4b462ab2eb757ae3ee609ed3617094f0e37423a087cfe0924b099473e5752a14
SHA256_CAPABILITIES=75b18984adacec0444252a8e8a841520fe0f2ceddf05b3d0f9aeba0bb59c4308
SHA256_LAUNCHER=e3b4bce3b4b64d32ccefbbe50990c8bb100d9b88bb16cf5cbe821ef3856ef2f1
SHA256_SKILL=74ac0726e3c7b2f3d735ea3d060e1bafd5a3d852d0e78efb19f77d0157c88d01
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

remove_managed_profile_block() {
  rollback_profile=$1
  python3 - "$rollback_profile" <<'PY'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
try:
    text = path.read_text(encoding="utf-8")
except FileNotFoundError:
    raise SystemExit(0)
block = '\n# HolyCrab CLI\nexport PATH="$HOME/.local/bin:$PATH"\n'
if block in text:
    path.write_text(text.replace(block, '\n', 1), encoding="utf-8")
PY
}

persist_path() {
  if [ "$PREFIX" != "$HOME/.local" ]; then
    echo "Custom install prefix detected; add this directory to your shell PATH: $BIN_DIR"
    return
  fi

  if [ -n "$PREVIOUS_PATH_PROFILE" ] && [ -f "$PREVIOUS_PATH_PROFILE" ] \
    && grep -F 'export PATH="$HOME/.local/bin:$PATH"' "$PREVIOUS_PATH_PROFILE" >/dev/null 2>&1; then
    PATH_REG_KIND=shell-profile
    PATH_REG_PROFILE=$PREVIOUS_PATH_PROFILE
    PATH_REG_ADDED=1
    echo "PATH is already saved in $PREVIOUS_PATH_PROFILE."
    return
  fi
  PATH_REG_ADDED=0

  shell_name=$(basename "${SHELL:-sh}")
  case "$shell_name" in
    zsh) profile="$HOME/.zshrc" ;;
    bash)
      if [ "$(uname -s)" = "Darwin" ]; then
        profile="$HOME/.bash_profile"
      else
        profile="$HOME/.bashrc"
      fi
      ;;
    sh|dash|ksh) profile="$HOME/.profile" ;;
    *)
      echo "Could not select a profile for $shell_name; add this directory to PATH: $BIN_DIR"
      return
      ;;
  esac

  PATH_REG_KIND=shell-profile
  PATH_REG_PROFILE=$profile
  path_line='export PATH="$HOME/.local/bin:$PATH"'
  if [ -f "$profile" ] && grep -F "$path_line" "$profile" >/dev/null 2>&1; then
    echo "PATH is already saved in $profile."
    return
  fi
  if printf '\n# HolyCrab CLI\n%s\n' "$path_line" >> "$profile"; then
    PATH_REG_ADDED=1
    PATH_REG_ADDED_THIS_RUN=1
    echo "PATH saved in $profile."
  else
    echo "Warning: could not update $profile; add this directory to PATH: $BIN_DIR" >&2
  fi
}

cleanup() {
  if [ "$INSTALL_STARTED" = "1" ] && [ "$INSTALL_COMPLETE" != "1" ]; then
    rm -rf "$LIB_DIR"
    if [ "$HAD_LIB" = "1" ]; then cp -R "$BACKUP_DIR/lib" "$LIB_DIR"; fi
    rm -f "$BIN_DIR/holycrab"
    if [ "$HAD_LAUNCHER" = "1" ]; then cp "$BACKUP_DIR/holycrab" "$BIN_DIR/holycrab"; fi
    case ",$INSTALL_AGENTS," in
      *,codex,*)
        rm -rf "$HOME/.agents/skills/holycrab"
        if [ "$HAD_CODEX_SKILL" = "1" ]; then cp -R "$BACKUP_DIR/codex-skill" "$HOME/.agents/skills/holycrab"; fi
        ;;
    esac
    case ",$INSTALL_AGENTS," in
      *,claude,*)
        rm -rf "$HOME/.claude/skills/holycrab"
        if [ "$HAD_CLAUDE_SKILL" = "1" ]; then cp -R "$BACKUP_DIR/claude-skill" "$HOME/.claude/skills/holycrab"; fi
        ;;
    esac
    if [ "$PATH_REG_ADDED_THIS_RUN" = "1" ] && [ -n "$PATH_REG_PROFILE" ]; then
      remove_managed_profile_block "$PATH_REG_PROFILE" || true
    fi
    echo "HolyCrab installation failed; previous managed files were restored." >&2
  fi
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

mkdir -p "$BACKUP_DIR"
if [ -f "$LIB_DIR/installation.json" ] && PREVIOUS_PATH_PROFILE=$(python3 - "$LIB_DIR/installation.json" "$BIN_DIR" "$HOME" <<'PY'
import json, pathlib, sys
try:
    value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
    registration = value.get("pathRegistration", {})
    profile = pathlib.Path(registration.get("profile", "")).expanduser().resolve()
    home = pathlib.Path(sys.argv[3]).expanduser().resolve()
    matches = (
        registration.get("addedByInstaller") is True
        and registration.get("kind") == "shell-profile"
        and pathlib.Path(registration.get("directory", "")).expanduser().resolve()
        == pathlib.Path(sys.argv[2]).expanduser().resolve()
        and profile in {(home / name).resolve() for name in (".zshrc", ".bashrc", ".bash_profile", ".profile")}
    )
except (OSError, ValueError, TypeError):
    matches = False
if matches:
    print(profile)
raise SystemExit(0 if matches else 1)
PY
); then
  PATH_REG_ADDED=1
fi
if [ -d "$LIB_DIR" ]; then cp -R "$LIB_DIR" "$BACKUP_DIR/lib"; HAD_LIB=1; fi
if [ -f "$BIN_DIR/holycrab" ]; then cp "$BIN_DIR/holycrab" "$BACKUP_DIR/holycrab"; HAD_LAUNCHER=1; fi
case ",$INSTALL_AGENTS," in
  *,codex,*)
    if [ -d "$HOME/.agents/skills/holycrab" ]; then cp -R "$HOME/.agents/skills/holycrab" "$BACKUP_DIR/codex-skill"; HAD_CODEX_SKILL=1; fi
    ;;
esac
case ",$INSTALL_AGENTS," in
  *,claude,*)
    if [ -d "$HOME/.claude/skills/holycrab" ]; then cp -R "$HOME/.claude/skills/holycrab" "$BACKUP_DIR/claude-skill"; HAD_CLAUDE_SKILL=1; fi
    ;;
esac
INSTALL_STARTED=1
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

persist_path

python3 - "$LIB_DIR/installation.json" "$PREFIX" "$INSTALL_AGENTS" "$INSTALL_MCP" \
  "$SHA256_HOLYCRAB_CLI" "$SHA256_CAPABILITIES" "$SHA256_SEGNO" "$SHA256_SEGNO_LICENSE" "$SHA256_LAUNCHER" \
  "$SHA256_SKILL" "$SHA256_OPENAI_YAML" "$PATH_REG_KIND" "$PATH_REG_DIR" "$PATH_REG_PROFILE" "$PATH_REG_ADDED" <<'PY'
import json, os, pathlib, sys, tempfile
target, prefix, agents, mcp, cli, capabilities, segno, license_hash, launcher, skill, openai, path_kind, path_dir, path_profile, path_added = sys.argv[1:]
selected = [item for item in agents.split(",") if item and item != "none"]
core = [
    {"path": "holycrab_cli.py", "sha256": cli},
    {"path": "references/capabilities.json", "sha256": capabilities},
    {"path": "vendor/segno-1.6.6-py3-none-any.whl", "sha256": segno},
    {"path": "vendor/LICENSE.segno", "sha256": license_hash},
    {"path": "../../bin/holycrab", "sha256": launcher},
]
skill_roots = {"codex": pathlib.Path.home() / ".agents/skills/holycrab",
               "claude": pathlib.Path.home() / ".claude/skills/holycrab"}
for agent in selected:
    root = skill_roots.get(agent)
    if root is not None:
        core.extend([
            {"path": str(root / "SKILL.md"), "sha256": skill},
            {"path": str(root / "references/capabilities.json"), "sha256": capabilities},
            {"path": str(root / "agents/openai.yaml"), "sha256": openai},
        ])
value = {
    "schemaVersion": 2,
    "managedBy": "holycrab-installer",
    "version": "0.4.1",
    "prefix": prefix,
    "agents": selected,
    "mcp": mcp == "1",
    "coreFiles": core,
    "pathRegistration": {
        "kind": path_kind,
        "directory": path_dir,
        "profile": path_profile or None,
        "addedByInstaller": path_added == "1",
    },
}
path = pathlib.Path(target)
fd, temporary = tempfile.mkstemp(prefix=".installation.", suffix=".tmp", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    try: os.unlink(temporary)
    except FileNotFoundError: pass
PY

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

repair_or_add_mcp() {
  mcp_agent=$1
  mcp_output=
  if mcp_output=$($mcp_agent mcp get holycrab 2>/dev/null); then
    if printf '%s' "$mcp_output" | grep -F "$BIN_DIR/holycrab" >/dev/null 2>&1 \
      && printf '%s' "$mcp_output" | grep -F "mcp" >/dev/null 2>&1 \
      && printf '%s' "$mcp_output" | grep -F "serve" >/dev/null 2>&1; then
      return
    fi
    if printf '%s' "$mcp_output" | grep -E 'holycrab(_cli\.py|[/\\]holycrab)' >/dev/null 2>&1; then
      $mcp_agent mcp remove holycrab >/dev/null 2>&1 || {
        echo "Warning: could not remove the stale HolyCrab MCP entry for $mcp_agent." >&2
        return
      }
    else
      echo "Warning: an unmanaged MCP entry named holycrab already exists for $mcp_agent; it was not changed." >&2
      return
    fi
  fi
  if [ "$mcp_agent" = "codex" ]; then
    $mcp_agent mcp add holycrab -- "$BIN_DIR/holycrab" mcp serve >/dev/null
  else
    $mcp_agent mcp add --scope user holycrab -- "$BIN_DIR/holycrab" mcp serve >/dev/null
  fi
}

if [ "$INSTALL_MCP" = "1" ]; then
  case ",$INSTALL_AGENTS," in
    *,codex,*)
      if command -v codex >/dev/null 2>&1; then
        if ! repair_or_add_mcp codex; then
          echo "Warning: Codex MCP registration failed; run: codex mcp add holycrab -- $BIN_DIR/holycrab mcp serve" >&2
        fi
      fi
      ;;
  esac
  case ",$INSTALL_AGENTS," in
    *,claude,*)
      if command -v claude >/dev/null 2>&1; then
        if ! repair_or_add_mcp claude; then
          echo "Warning: Claude MCP registration failed; run: claude mcp add --scope user holycrab -- $BIN_DIR/holycrab mcp serve" >&2
        fi
      fi
      ;;
  esac
fi

echo "HolyCrab CLI, local MCP, and Skill are installed."
echo "Command: $BIN_DIR/holycrab"
PATH="$BIN_DIR:$PATH"
export PATH
HOLYCRAB_NO_UPDATE_CHECK=1 "$BIN_DIR/holycrab" --version >/dev/null
HOLYCRAB_NO_UPDATE_CHECK=1 "$BIN_DIR/holycrab" doctor --json >/dev/null || {
  echo "Installed files failed HolyCrab doctor; installation stopped." >&2
  exit 1
}
INSTALL_COMPLETE=1
echo "PATH is active inside the installer. If this command was piped to sh, run: export PATH=\"$BIN_DIR:\$PATH\""
echo "Next: holycrab setup"
