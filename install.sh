#!/bin/sh
set -eu

REPOSITORY=AstroxNetwork/skills
VERSION=v0.6.0
SOURCE_REF=${HOLYCRAB_INSTALL_REF:-$VERSION}
SOURCE_DIR=${HOLYCRAB_INSTALL_SOURCE_DIR:-}
INSTALL_MCP=${HOLYCRAB_INSTALL_MCP-}
INSTALL_AGENTS=${HOLYCRAB_INSTALL_AGENTS-}
PREFIX=${HOLYCRAB_INSTALL_PREFIX:-"$HOME/.local"}
BIN_DIR="$PREFIX/bin"
LIB_DIR="$PREFIX/lib/holycrab"
TEMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/holycrab-install.XXXXXX")
BACKUP_DIR="$TEMP_DIR/backup"
INSTALL_LOCK_HELD=0
INSTALL_LOCK_DIR="$PREFIX/.holycrab-install.lock"
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
SHA256_HOLYCRAB_CLI=ce732cda8548c77b53b513321fad5a52d5c695a02b04a9d5f45b240f302ab2fb
SHA256_CAPABILITIES=9a3b05927bfbc75a13f44bd7ee5483d5f4dc3447b65b6d3b8a5ce477a61bcdee
SHA256_LAUNCHER=e3b4bce3b4b64d32ccefbbe50990c8bb100d9b88bb16cf5cbe821ef3856ef2f1
SHA256_SKILL=69032d13a515fdba63ac79b4bd66a94df131eeec7420c0f2f661fdef724926e8
SHA256_OPENAI_YAML=64bd549cd32e989324d5a17c2550cd54dfecccf70b4637b05b062a2fb709c1a7
SHA256_SEGNO=28c7d081ed0cf935e0411293a465efd4d500704072cdb039778a2ab8736190c7
SHA256_SEGNO_LICENSE=de6c85fccf5d52902aa13dfe2dc6d2a2a106fc3419ed438f3460f0d4b76a6935

progress() {
  if [ -t 2 ]; then printf '%s\n' "$1" >&2; fi
}

progress "[1/5] Checking Python and installation settings..."

command -v python3 >/dev/null 2>&1 || {
  echo "Python 3.10 or newer is required for HolyCrab." >&2
  exit 1
}
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' || {
  echo "Python 3.10 or newer is required for HolyCrab." >&2
  exit 1
}
python3 -c 'import re, sys; raise SystemExit(0 if re.fullmatch(r"(?:[0-9a-f]{40}|v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))", sys.argv[1]) else 1)' "$SOURCE_REF" || {
  echo "HOLYCRAB_INSTALL_REF must be a full commit hash or stable version tag; installation stopped." >&2
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
    progress "PATH is already saved in $PREVIOUS_PATH_PROFILE."
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
    progress "PATH is already saved in $profile."
    return
  fi
  if printf '\n# HolyCrab CLI\n%s\n' "$path_line" >> "$profile"; then
    PATH_REG_ADDED=1
    PATH_REG_ADDED_THIS_RUN=1
    progress "PATH saved in $profile."
  else
    echo "Warning: could not update $profile; add this directory to PATH: $BIN_DIR" >&2
  fi
}

cleanup() {
  cleanup_ok=1
  if [ "$INSTALL_STARTED" = "1" ] && [ "$INSTALL_COMPLETE" != "1" ]; then
    python3 - "$TEMP_DIR/holycrab_cli.py" "$BACKUP_DIR" "$PREFIX" "$INSTALL_AGENTS" <<'PY' || cleanup_ok=0
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("holycrab_rollback", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
cli.restore_installer_files(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]),
    [a for a in sys.argv[4].split(",") if a != "none"])
PY
    if [ "$PATH_REG_ADDED_THIS_RUN" = "1" ] && [ -n "$PATH_REG_PROFILE" ]; then
      remove_managed_profile_block "$PATH_REG_PROFILE" || cleanup_ok=0
    fi
    if [ "$cleanup_ok" = "1" ]; then
      echo "HolyCrab installation failed; previous managed files were restored." >&2
    else
      echo "HolyCrab installation failed; recovery is incomplete. Backup retained: $BACKUP_DIR" >&2
    fi
  fi
  if [ "$INSTALL_LOCK_HELD" = "1" ]; then rmdir "$INSTALL_LOCK_DIR" || true; fi
  if [ "$cleanup_ok" = "1" ]; then rm -rf "$TEMP_DIR"; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 129' HUP
trap 'exit 143' TERM

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
  if [ "$fetch_relative" = "holycrab/scripts/holycrab_cli.py" ]; then
    progress "[2/5] Downloading and verifying installation files..."
  fi
  if [ -n "$SOURCE_DIR" ]; then
    cp "$SOURCE_DIR/$fetch_relative" "$fetch_destination"
  else
    command -v curl >/dev/null 2>&1 || {
      echo "curl is required to install HolyCrab." >&2
      exit 1
    }
    curl -fsSL "https://raw.githubusercontent.com/$REPOSITORY/$SOURCE_REF/$fetch_relative" -o "$fetch_destination"
  fi
  verify_sha256 "$fetch_destination" "$fetch_sha256" "$fetch_relative"
}

fetch "holycrab/scripts/holycrab_cli.py" "$TEMP_DIR/holycrab_cli.py" "$SHA256_HOLYCRAB_CLI"
fetch "holycrab/references/capabilities.json" "$TEMP_DIR/capabilities.json" "$SHA256_CAPABILITIES"
fetch "bin/holycrab" "$TEMP_DIR/holycrab" "$SHA256_LAUNCHER"
fetch "holycrab/SKILL.md" "$TEMP_DIR/SKILL.md" "$SHA256_SKILL"
fetch "holycrab/agents/openai.yaml" "$TEMP_DIR/openai.yaml" "$SHA256_OPENAI_YAML"
fetch "holycrab/scripts/vendor/segno-1.6.6-py3-none-any.whl" "$TEMP_DIR/segno.whl" "$SHA256_SEGNO"
fetch "holycrab/scripts/vendor/LICENSE.segno" "$TEMP_DIR/LICENSE.segno" "$SHA256_SEGNO_LICENSE"
# BEGIN GENERATED RUNTIME DOWNLOADS
fetch "runtime-files.json" "$TEMP_DIR/runtime-files.json" "a23da16d43f8a692cbbfe5f5d116e1ebb88fc2852137e8d6ebeb7d2918e16b08"
mkdir -p "$TEMP_DIR/holycrab_runtime"
fetch "holycrab/scripts/holycrab_runtime/__init__.py" "$TEMP_DIR/holycrab_runtime/__init__.py" "a957b310045f02b36e31a8fd60f5a3808b0838b59fd353c2a5318d0cceeb2ffe"
fetch "holycrab/scripts/holycrab_runtime/capabilities.py" "$TEMP_DIR/holycrab_runtime/capabilities.py" "43585789f09a5d1e5f3d215c75f1fc888e6aa9fead3b09d4244f667301fd0f64"
fetch "holycrab/scripts/holycrab_runtime/cli.py" "$TEMP_DIR/holycrab_runtime/cli.py" "c9a50861d2b56ccb691ded00639f6b5d72bdb2b1d9bbfa262cd672d4ba229d90"
fetch "holycrab/scripts/holycrab_runtime/exports.py" "$TEMP_DIR/holycrab_runtime/exports.py" "d0dcf10cb10a719b2d8a5d32510c3a20608d5dd88fb14a4225d2d51d23c4a7c6"
fetch "holycrab/scripts/holycrab_runtime/generation.py" "$TEMP_DIR/holycrab_runtime/generation.py" "578ab3976e6fe04dd66cf2bf542b64989dfbfe3994445c3a98c0f24e60d1c2a1"
fetch "holycrab/scripts/holycrab_runtime/lifecycle.py" "$TEMP_DIR/holycrab_runtime/lifecycle.py" "6f3898140d2e86a6dbc90574814e7a3fa32b5a09dade3ea909f8bae0787f475a"
fetch "holycrab/scripts/holycrab_runtime/mcp.py" "$TEMP_DIR/holycrab_runtime/mcp.py" "c72c8a50aa5a4419dcc2f0c45f46f8cf7dbe6afdddf3a5c31d8b93d06dbb08d9"
fetch "holycrab/scripts/holycrab_runtime/presentation.py" "$TEMP_DIR/holycrab_runtime/presentation.py" "0925e46fbf4ec01d156ab1071c43a8a6e227c22adfed9804316fd32b9b8ee530"
fetch "holycrab/scripts/holycrab_runtime/real_human.py" "$TEMP_DIR/holycrab_runtime/real_human.py" "36ff3c4c5603647ccbc2e3bc12ccd69f0b83bdedf9a385c168e3b9c74af29e74"
fetch "holycrab/scripts/holycrab_runtime/results.py" "$TEMP_DIR/holycrab_runtime/results.py" "8e985392bef51aa6231389b9efc60bcd50f83b803ec5b41002f662b53f2f05a2"
fetch "holycrab/scripts/holycrab_runtime/settings.py" "$TEMP_DIR/holycrab_runtime/settings.py" "88327a0c0c4f3b4a204de07858de6ba0ec74fb0c464370b8746e6681259e5fb1"
fetch "holycrab/scripts/holycrab_runtime/state.py" "$TEMP_DIR/holycrab_runtime/state.py" "c87236d05ecdcefa64249aa3cf3fa4003918a887751f5469386531fefa330946"
fetch "holycrab/scripts/holycrab_runtime/tasks.py" "$TEMP_DIR/holycrab_runtime/tasks.py" "2936d1574e9cb6caa5d7e6a21af5a2a55042972620ccf271264ae35d2fa7bc27"
fetch "holycrab/scripts/holycrab_runtime/transport.py" "$TEMP_DIR/holycrab_runtime/transport.py" "0020469744a96b96959e1df42e7234b964909a90388ba165234cd380e81ca432"
fetch "holycrab/scripts/holycrab_runtime/uploads.py" "$TEMP_DIR/holycrab_runtime/uploads.py" "4a1c21aa5df008841d1e0425991800ff36ed219ca82284f05deaadf8b0b79cc5"
# END GENERATED RUNTIME DOWNLOADS
mkdir -p "$PREFIX"
if ! mkdir "$INSTALL_LOCK_DIR" 2>/dev/null; then
  echo "Another install or uninstall holds $INSTALL_LOCK_DIR. Check that operation before recovering an abandoned lock." >&2
  exit 1
fi
INSTALL_LOCK_HELD=1
settings=$(python3 - "$TEMP_DIR/holycrab_cli.py" "$LIB_DIR/installation.json" "$PREFIX" "$INSTALL_AGENTS" "$INSTALL_MCP" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("holycrab_settings", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
settings = cli.installer_settings(cli.read_json_file(pathlib.Path(sys.argv[2]), {}), *sys.argv[3:])
print(settings["agents"] + ":" + settings["mcp"])
PY
)
INSTALL_AGENTS=${settings%:*}
INSTALL_MCP=${settings##*:}
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
if [ -d "$HOME/.agents/skills/holycrab" ]; then cp -R "$HOME/.agents/skills/holycrab" "$BACKUP_DIR/codex-skill"; HAD_CODEX_SKILL=1; fi
if [ -d "$HOME/.claude/skills/holycrab" ]; then cp -R "$HOME/.claude/skills/holycrab" "$BACKUP_DIR/claude-skill"; HAD_CLAUDE_SKILL=1; fi
progress "[3/5] Installing verified program files and configuring PATH..."
INSTALL_STARTED=1
mkdir -p "$BIN_DIR" "$LIB_DIR/references" "$LIB_DIR/vendor"
install -m 755 "$TEMP_DIR/holycrab_cli.py" "$LIB_DIR/holycrab_cli.py"
install -m 644 "$TEMP_DIR/capabilities.json" "$LIB_DIR/references/capabilities.json"
install -m 755 "$TEMP_DIR/holycrab" "$BIN_DIR/holycrab"
install -m 644 "$TEMP_DIR/segno.whl" "$LIB_DIR/vendor/segno-1.6.6-py3-none-any.whl"
install -m 644 "$TEMP_DIR/LICENSE.segno" "$LIB_DIR/vendor/LICENSE.segno"

# BEGIN GENERATED RUNTIME INSTALL
mkdir -p "$LIB_DIR/holycrab_runtime"
install -m 644 "$TEMP_DIR/runtime-files.json" "$LIB_DIR/runtime-files.json"
install -m 644 "$TEMP_DIR/holycrab_runtime/__init__.py" "$LIB_DIR/holycrab_runtime/__init__.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/capabilities.py" "$LIB_DIR/holycrab_runtime/capabilities.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/cli.py" "$LIB_DIR/holycrab_runtime/cli.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/exports.py" "$LIB_DIR/holycrab_runtime/exports.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/generation.py" "$LIB_DIR/holycrab_runtime/generation.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/lifecycle.py" "$LIB_DIR/holycrab_runtime/lifecycle.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/mcp.py" "$LIB_DIR/holycrab_runtime/mcp.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/presentation.py" "$LIB_DIR/holycrab_runtime/presentation.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/real_human.py" "$LIB_DIR/holycrab_runtime/real_human.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/results.py" "$LIB_DIR/holycrab_runtime/results.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/settings.py" "$LIB_DIR/holycrab_runtime/settings.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/state.py" "$LIB_DIR/holycrab_runtime/state.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/tasks.py" "$LIB_DIR/holycrab_runtime/tasks.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/transport.py" "$LIB_DIR/holycrab_runtime/transport.py"
install -m 644 "$TEMP_DIR/holycrab_runtime/uploads.py" "$LIB_DIR/holycrab_runtime/uploads.py"
# END GENERATED RUNTIME INSTALL

python3 - "$TEMP_DIR/holycrab_cli.py" "$LIB_DIR" <<'PYCACHE'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("holycrab_cache", sys.argv[1])
cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
cli.prepare_runtime_imports(pathlib.Path(sys.argv[2]))
PYCACHE

persist_path

python3 - "$LIB_DIR/installation.json" "$PREFIX" "$INSTALL_AGENTS" "$INSTALL_MCP" \
  "$SHA256_HOLYCRAB_CLI" "$SHA256_CAPABILITIES" "$SHA256_SEGNO" "$SHA256_SEGNO_LICENSE" "$SHA256_LAUNCHER" \
  "$SHA256_SKILL" "$SHA256_OPENAI_YAML" "$PATH_REG_KIND" "$PATH_REG_DIR" "$PATH_REG_PROFILE" "$PATH_REG_ADDED" "$VERSION" <<'PY'
import json, os, pathlib, sys, tempfile
target, prefix, agents, mcp, cli, capabilities, segno, license_hash, launcher, skill, openai, path_kind, path_dir, path_profile, path_added, version = sys.argv[1:]
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
    "schemaVersion": 3,
    "managedBy": "holycrab-installer",
    "version": version.removeprefix("v"),
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
release_manifest = json.loads((path.parent / "runtime-files.json").read_text())
core.extend({"path": row["target"], "sha256": row["sha256"]} for row in release_manifest["files"] if row["kind"] == "runtime" and not row.get("legacyVariable"))
core.append({"path": "runtime-files.json", "sha256": __import__("hashlib").sha256((path.parent / "runtime-files.json").read_bytes()).hexdigest()})
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

progress "[4/5] Configuring the selected Agents..."
case ",$INSTALL_AGENTS," in
  *,codex,*) install_skill "$HOME/.agents/skills/holycrab" ;;
esac
case ",$INSTALL_AGENTS," in
  *,claude,*) install_skill "$HOME/.claude/skills/holycrab" ;;
esac

repair_or_add_mcp() {
  mcp_agent=$1
  python3 - "$LIB_DIR/holycrab_cli.py" "$mcp_agent" "$BIN_DIR/holycrab" "$BACKUP_DIR/lib/installation.json" <<'PY'
import importlib.util, sys
script, agent, command, previous = sys.argv[1:]
spec = importlib.util.spec_from_file_location("holycrab_installer", script)
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
cli.register_installer_mcp(agent, None, command, ["mcp", "serve"], previous,
                         str(__import__("pathlib").Path(previous).parent.parent / "mcp-transaction.json"))
PY
}

if [ "$INSTALL_MCP" = "1" ]; then
  case ",$INSTALL_AGENTS," in
    *,codex,*)
        if ! repair_or_add_mcp codex; then
          echo "Codex connection recovery failed; rolling back installation." >&2
          exit 1
        fi
      ;;
  esac
  case ",$INSTALL_AGENTS," in
    *,claude,*)
        if ! repair_or_add_mcp claude; then
          echo "Claude connection recovery failed; rolling back installation." >&2
          exit 1
        fi
      ;;
  esac
fi

python3 - "$LIB_DIR/holycrab_cli.py" "$BACKUP_DIR/lib/installation.json" "$BACKUP_DIR/mcp-transaction.json" <<'PYFINAL'
import importlib.util, sys
spec=importlib.util.spec_from_file_location("holycrab_finalize", sys.argv[1])
cli=importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
cli.finalize_installation(sys.argv[2], sys.argv[3])
PYFINAL

progress "[5/5] Verifying the installed CLI version and local health..."
PATH="$BIN_DIR:$PATH"
export PATH
installed_version=$(HOLYCRAB_NO_UPDATE_CHECK=1 "$BIN_DIR/holycrab" --version)
if [ "$installed_version" != "holycrab ${VERSION#v}" ]; then
  echo "Installed CLI version does not match $VERSION; installation stopped." >&2
  exit 1
fi
doctor_report=$(HOLYCRAB_NO_UPDATE_CHECK=1 "$BIN_DIR/holycrab" doctor --json) || true
if ! printf '%s\n' "$doctor_report" | python3 -c 'import json,sys; r=json.load(sys.stdin); raise SystemExit(0 if r.get("runtimeOk") else 1)'; then
  echo "Installed runtime failed verification; installation stopped." >&2
  exit 1
fi
if ! printf '%s\n' "$doctor_report" | python3 -c 'import json,sys; raise SystemExit(0 if json.load(sys.stdin).get("ok") else 1)'; then
  echo "Program installed, but existing local records need attention. Run holycrab doctor; preserve the records." >&2
fi
onboarding_summary=$(printf '%s\n' "$doctor_report" | python3 -c '
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("holycrab_installer", sys.argv[1])
cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cli)
print(cli.format_onboarding(json.load(sys.stdin)["onboarding"], installed=True, upgrading=sys.argv[2] == "1"))
' "$LIB_DIR/holycrab_cli.py" "$HAD_LIB")
INSTALL_COMPLETE=1
printf '\n%s\n' "$onboarding_summary"
printf '\nInstalled command:\n  %s\n' "$BIN_DIR/holycrab"
printf '\nIf holycrab is not found in this terminal, run:\n  export PATH="%s:$PATH"\n' "$BIN_DIR"
