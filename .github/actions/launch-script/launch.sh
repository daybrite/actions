#!/usr/bin/env bash
#
# Download and run this release of the app, without installing anything permanent.
#
#   curl -fsSL https://github.com/<owner>/<repo>/releases/latest/download/launch.sh | bash
#
# The script is a release asset, so it is published once per release and knows which release it
# belongs to. That is also how versions are chosen: `latest/download/launch.sh` runs the newest
# release, `download/v1.2.0/launch.sh` runs that one, and each downloads only its own artifacts —
# so two of them side by side really are two versions, not the same one twice.
#
# Options (pass them after `bash -s --` when piping):
#   curl -fsSL <url> | bash -s -- --target linux-qt --yes
#
# It asks before downloading anything, and nothing lands outside a fresh temporary directory.

# --- release facts (regenerated at release time; the values below are the template's defaults) ---
REPO="daybrite/example"
TAG="v0.0.0"
# One `<target>=<asset>` per line, listing exactly what this release shipped.
ASSETS="macos-appkit=example-macos-appkit.dmg"
# --- end release facts ---

set -euo pipefail

usage() {
    cat <<EOF
Download and launch $REPO $TAG.

Usage: launch.sh [--target <target>] [--yes]
       curl -fsSL <url> | bash -s -- [--target <target>] [--yes]

  --target <target>  Which build to run. Available in $TAG: $(targets)
  -y, --yes          Skip the confirmation prompt.
  -h, --help         Show this.

Environment: DAY_LAUNCH_TARGET, DAY_LAUNCH_YES (same meanings).
EOF
}

# The targets this release shipped a runnable build for, space separated.
targets() {
    printf '%s\n' "$ASSETS" | cut -d= -f1 | xargs
}

# The asset filename for one target, or empty when this release has no build for it.
asset_for() {
    printf '%s\n' "$ASSETS" | sed -n "s/^$1=//p"
}

# Which build suits this machine. macOS has one desktop build; Linux has two, and the
# right one is whichever toolkit the running desktop already uses — Qt under Plasma and
# LXQt, GTK everywhere else, since that is where the theme and the system libraries match.
detect_target() {
    case "$(uname -s)" in
        Darwin)
            echo macos-appkit
            ;;
        Linux)
            case "${XDG_CURRENT_DESKTOP:-${DESKTOP_SESSION:-}}" in
                *KDE* | *kde* | *Plasma* | *plasma* | *LXQt* | *lxqt*) echo linux-qt ;;
                *) echo linux-gtk ;;
            esac
            ;;
        *)
            echo ""
            ;;
    esac
}

# Ask on the terminal, not on stdin: under `curl … | bash` stdin IS the script. Opening /dev/tty
# is what gets a real answer — and when there is no terminal at all (CI, a nested pipeline) that
# open fails, which is the moment to say so rather than hang or quietly proceed.
confirm() {
    if [ -n "$ASSUME_YES" ]; then
        return 0
    fi
    if ! { exec 3< /dev/tty; } 2> /dev/null; then
        echo "error: no terminal to ask on. Re-run with --yes (or DAY_LAUNCH_YES=1) to skip the prompt." >&2
        exit 1
    fi
    printf 'Continue? [y/N] '
    read -r reply <&3 || reply=""
    exec 3<&-
    case "$reply" in
        y | Y | yes | Yes | YES) return 0 ;;
        *)
            echo "Cancelled — nothing was downloaded."
            exit 1
            ;;
    esac
}

download() { # $1 = url, $2 = destination file
    if command -v curl > /dev/null 2>&1; then
        curl -fL --progress-bar -o "$2" "$1"
    elif command -v wget > /dev/null 2>&1; then
        wget -q --show-progress -O "$2" "$1"
    else
        echo "error: this script needs curl or wget to download the app." >&2
        exit 1
    fi
}

# macOS: copy the .app out of the disk image and open it. The .dmg is signed and notarized, so
# Gatekeeper admits it without any of the right-click-Open dance.
run_macos() {
    mnt="$WORKDIR/mnt"
    mkdir -p "$mnt"
    echo "==> Mounting $(basename "$FILE")"
    # Detach on the way out however this ends: an image left attached blocks the next run of
    # this script with "Resource temporarily unavailable".
    trap 'hdiutil detach -quiet "$mnt" 2>/dev/null || true' EXIT
    hdiutil attach -nobrowse -readonly -quiet -mountpoint "$mnt" "$FILE"
    app=""
    for candidate in "$mnt"/*.app; do
        if [ -d "$candidate" ]; then
            app="$candidate"
            break
        fi
    done
    if [ -z "$app" ]; then
        echo "error: no .app inside $(basename "$FILE")" >&2
        exit 1
    fi
    dest="$WORKDIR/$(basename "$app")"
    echo "==> Copying $(basename "$app") to $dest"
    # ditto, not cp: it is the bundle-aware copy, and keeps the signature intact.
    ditto "$app" "$dest"
    hdiutil detach -quiet "$mnt"
    trap - EXIT
    echo "==> Launching"
    # -n forces a NEW instance. Without it, macOS activates whichever copy of this bundle id is
    # already running — so comparing two versions side by side would silently show one twice.
    open -n "$dest"
    echo
    echo "Running from $dest"
    echo "Delete $WORKDIR when you are done; nothing was installed."
}

# Linux: an AppImage is a single executable carrying its own GTK or Qt, so chmod +x and run is
# genuinely the whole procedure — no package manager, no runtime download, no root. That is why
# this reaches for the .appimage over the .flatpak the same release ships; the flatpak is the
# desktop-integrated install, and is still there for anyone who wants it.
run_linux() {
    chmod +x "$FILE"
    echo "==> Launching $(basename "$FILE")"
    echo
    "$FILE"
    echo
    echo "Ran from $FILE"
    echo "Delete $WORKDIR when you are done; nothing was installed."
}

# --- what to run -------------------------------------------------------------------------------

TARGET="${DAY_LAUNCH_TARGET:-}"
ASSUME_YES="${DAY_LAUNCH_YES:-}"
while [ $# -gt 0 ]; do
    case "$1" in
        --target)
            if [ $# -lt 2 ]; then
                echo "error: --target needs a value ($(targets))" >&2
                exit 2
            fi
            TARGET="$2"
            shift 2
            ;;
        --target=*)
            TARGET="${1#*=}"
            shift
            ;;
        -y | --yes)
            ASSUME_YES=1
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        *)
            echo "error: unknown option $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

CHOSEN="you chose it"
if [ -z "$TARGET" ]; then
    TARGET="$(detect_target)"
    CHOSEN="detected"
fi
if [ -z "$TARGET" ]; then
    echo "error: $(uname -s) is not supported by this script (macOS and Linux are)." >&2
    echo "       $TAG also ships: $(targets)" >&2
    exit 1
fi

ASSET="$(asset_for "$TARGET")"
if [ -z "$ASSET" ]; then
    echo "error: $TAG has no $TARGET build. It ships: $(targets)" >&2
    exit 1
fi

URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"
WORKDIR="$(mktemp -d)/$(basename "$REPO")-$TAG"
mkdir -p "$WORKDIR"
FILE="$WORKDIR/$ASSET"

echo "$REPO $TAG"
echo
echo "  target    $TARGET ($CHOSEN)"
echo "  download  $URL"
echo "  into      $WORKDIR"
case "$TARGET" in
    macos-appkit) echo "  then      mount the .dmg, copy the .app out, and open it" ;;
    linux-*) echo "  then      chmod +x and run it — no install, no root" ;;
esac

# The Linux bundles carry their CPU architecture in the name. Say so before downloading one that
# cannot run here, rather than after.
ARCH="$(uname -m)"
case "$ASSET" in
    *"$ARCH"*) ;;
    *x86_64* | *aarch64* | *arm64*)
        echo
        echo "  NOTE: this build is not for $ARCH — it may not run on this machine."
        ;;
esac
echo
confirm

echo "==> Downloading $ASSET"
download "$URL" "$FILE"

case "$TARGET" in
    macos-appkit) run_macos ;;
    linux-gtk | linux-qt) run_linux ;;
    *)
        echo "error: no launch procedure for $TARGET" >&2
        exit 1
        ;;
esac
