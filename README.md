# daybrite/actions

Reusable GitHub workflows for [Day](https://daybrite.dev) projects.

## build-day-app

Builds a conventional Day project for a set of platform-toolkit targets, runs its dayscripts
(capturing screenshots), and packages it for distribution with `day pack`. A `preflight` job runs
first and gates the whole matrix: `cargo fmt --all -- --check` by default, with clippy, check, and
test available through the `preflight-checks` input — so a formatting slip fails one small ubuntu
job before any build runner starts. On a semantic-version tag (`vX.Y.Z`), a final job attaches
every package and a per-target screenshot zip — plus a `SHA256SUMS` manifest — to the GitHub
release for that tag, and [store-upload jobs](#store-uploads) can hand the packed artifacts to the
app's own fastlane lanes.

Release assets are packed with `day pack --no-version-in-name`, so their filenames carry no
version, and each is tagged with its platform-toolkit combo — `app-fair-android-mdc.aab`,
`app-fair-linux-gtk-x86_64.appimage`, `app-fair-windows-xaml-setup.exe`, `app-fair-harmony-arkui.hap`.
Each is therefore reachable at a stable "latest release" URL —
`https://github.com/<owner>/<repo>/releases/latest/download/<name>` (e.g.
`.../releases/latest/download/app-fair-android-mdc.aab`) — that always redirects to the newest
tagged release.

Each package's provenance travels beside it, named after the package so a release directory
holding seven targets says which file each document describes:
`app-fair-macos-appkit.dmg.buildinfo.json`, `.sbom-cdx.json`, `.sbom-spdx.json`. That is what
`day rebuild <downloaded-package>` reads.

### Try it in one line

Every release that ships a desktop build also gets a launcher beside the packages, so the app has
a try-it path that needs no toolchain and leaves nothing behind:

```sh
# macOS and Linux
curl -fsSL https://github.com/<owner>/<repo>/releases/latest/download/launch.sh | bash
```

```powershell
# Windows
irm https://github.com/<owner>/<repo>/releases/latest/download/launch.ps1 | iex
```

Both are generated per release and pinned to their own tag, so the URL picks the version:
`latest/download/…` runs the newest release, `download/v1.2.0/…` runs that one, and running two of
them gives two versions to compare rather than the same one twice. Each prints what it is about to
download and where, and asks before doing anything.

| | what it downloads | what it does |
| --- | --- | --- |
| macOS | the signed, notarized `.dmg` | copies the `.app` into a temporary directory and opens it |
| Linux | the `.appimage` | `chmod +x` and runs it — no package manager, no runtime, no root |
| Windows | the per-user `-setup.exe` | installs it silently into a temporary folder (no admin prompt) and runs it, printing the uninstall line |

`launch.sh` detects macOS versus Linux, and on Linux reads the desktop to choose the GNOME or KDE
build; `--target <combo>` overrides it and `--yes` skips the prompt (when piping, pass them after
`bash -s --`). `launch.ps1` takes `-Yes`, or `DAY_LAUNCH_YES=1` under `| iex`, which cannot pass
arguments. Neither script is generated when a release ships nothing they can run.

Call it from your app repository:

```yaml
# .github/workflows/ci.yml
name: ci
on:
  push:
    branches: ["**"]
    tags: ["v[0-9]+.[0-9]+.[0-9]+*"]
  workflow_dispatch:
permissions:
  contents: write   # release-asset upload on tag builds
jobs:
  app:
    uses: daybrite/actions/.github/workflows/build-day-app.yml@main
    secrets: inherit
    with:
      targets: windows-xaml, macos-appkit, linux-gtk, linux-qt, ios-uikit, android-mdc, harmony-arkui, web-dom
      scripts: dayscript/walkthrough.yaml
      locales: en fr
      # preflight-checks: fmt clippy   # opt into clippy before the matrix (fmt alone is the default)
```

### Inputs

| input | default | meaning |
|---|---|---|
| `targets` | (required) | Platform-toolkit pairs to build, comma- or space-separated. |
| `day-version` | `latest` | Day CLI to install: `latest` (newest crates.io release), `v1.2.3`/`1.2.3` (that crates.io release), a 40-hex commit, or a branch name of the day repo (built from git). |
| `day-git` | `https://github.com/daybrite/day.git` | Day repo URL for branch/commit installs. |
| `day-verbose` | `true` | Run the day CLI verbose (`DAY_VERBOSE=1`): every `day build`/`launch`/`pack`/`rebuild` forwards its sub-commands' raw output — the cargo/gradle/xcodebuild/hvigor command lines and logs — so a failed build shows the command that broke. `false` restores the quiet status-line output. Installing the CLI itself is cargo's own build either way; a `day-version` that predates `DAY_VERBOSE` ignores it. |
| `project-path` | `.` | Directory of the Day project within the repository. |
| `setup-command` | — | Shell command run at the repo root after the CLI installs (e.g. `day new app …`). |
| `preflight-checks` | `fmt` | Rust checks the `preflight` job runs before the matrix, from `fmt clippy check test` (comma- or space-separated). `fmt` needs no build and takes seconds; the others compile the whole workspace and delay every matrix leg, which is why they are opt-in. Empty skips the checks. |
| `scripts` | `auto` | Dayscripts to run per target; `auto` = every `dayscript/*.yaml` or `scripts/*.yaml`; `none` disables. |
| `launch-env` | — | Space-separated `KEY=VALUE` pairs passed to every scripted launch as `--env`. |
| `locales` | — | Locales to run each dayscript under (each gets its own screenshot variant). |
| `android-abis` | `arm64-v8a x86_64` | Android ABIs packed into the `android-mdc` APK/AAB (each adds its own `lib/<abi>/`), comma- or space-separated. Supported: `arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64`. |
| `ios-profiles` | — | Device profiles to run `ios-uikit`'s dayscripts on, comma-separated — each becomes its own parallel job (see [Device profiles](#device-profiles)). |
| `android-profiles` | — | The same for `android-mdc`, as `avdmanager list device` ids (`pixel_5`, `pixel_tablet`). |
| `publish-release` | `true` | Publish the GitHub release for the tag. `false` leaves it as a fully assembled draft — packages, checksums, launch scripts and notes all in place — for a human to review and publish. Its asset URLs, including the ones in the install commands, answer only once it is published. Never un-publishes a release that is already public. |
| `deploy-web` | `false` | Publish the `web-dom` build to the caller's GitHub Pages after the matrix (see [Web deploy](#web-deploy)). Requires `web-dom` in `targets`. |
| `web-deploy-tag-pattern` | — | When `deploy-web` is set: empty deploys on a push to the repo's default branch; a bash regex (e.g. `^v[0-9]+\.[0-9]+\.[0-9]+$`) deploys **only** on a tag matching it. Ignored unless `deploy-web` is true. |

### Targets and runners

| target | runner | notes |
|---|---|---|
| `macos-appkit` | macos-latest | packs a `.dmg` |
| `ios-uikit` | macos-latest | Simulator scripts; packs an unsigned device `.ipa` for sideloading/self-signing (a signed `.ipa` with signing secrets) |
| `linux-gtk`, `linux-qt` | ubuntu-latest | scripts under xvfb / offscreen; pack a `.flatpak` **and** a `.appimage`, and the release check installs the one and runs the other |
| `android-mdc` | ubuntu-latest | scripts on a KVM emulator (best-effort); packs `.apk` + `.aab` |
| `harmony-arkui` | ubuntu-latest | scripts on the Oniro QEMU emulator (best-effort); packs `.hap` |
| `windows-xaml` | windows-latest | packs `.msix` + NSIS installer |
| `web-dom` | ubuntu-latest | scripts in headless Chromium through day-cli's bundled page-driver (needs a day CLI with `day web driver`); ships the built dist as a zip |
| `macos-gtk`, `macos-qt`, `windows-qt`, `windows-gtk` | (home OS) | portable-toolkit coverage builds; pack and scripts are best-effort |

### Device profiles

`ios-profiles` and `android-profiles` run a mobile target's dayscripts on more than one device.
Each profile becomes its own **parallel job**, so a second device costs wall clock only for its own
build and script run, not the first device's:

```yaml
with:
  targets: macos-appkit, ios-uikit, android-mdc
  ios-profiles: "iPhone 16, iPad Pro=ipad"
  android-profiles: "pixel_5, pixel_tablet=tablet"
```

That is five jobs: `macos-appkit`, `ios-uikit · iPhone 16`, `ios-uikit · iPad Pro`,
`android-mdc · pixel_5`, `android-mdc · pixel_tablet`.

- **iOS profiles are prefixes**, matched against the simulators the runner image has: `iPhone`
  takes the first iPhone, `iPad Pro` the first iPad Pro. Exact names age out with each Xcode image,
  so a pinned "iPhone 15" would start failing on its own. An unmatched prefix fails the job and
  lists the devices the image does have. **Android profiles are exact** `avdmanager list device`
  ids, passed to the emulator action verbatim.
- **`=<slug>` fixes the artifact suffix** for a long device name: `iPad Pro 13-inch (M4)=ipad`
  uploads `screenshots-ios-uikit-ipad` instead of `screenshots-ios-uikit-ipad-pro-13-inch-m4`.
- **The first profile is primary.** It packs, uploads the packages, and feeds the release, signing
  and store-upload jobs; its screenshots keep the plain `screenshots-<target>` name. Every later
  profile builds, runs the scripts, and uploads `screenshots-<target>-<slug>` — it never packs, so
  a tag build cannot race two identical release assets.
- **Naming no profiles changes nothing**: one job per target, named `<target>` exactly as before,
  on the first iPhone / `pixel_5`.

**Replacing `tablet-walkthroughs`.** That input is gone (2026-08). It ran the scripts a second time
inside the phone's job, on a hard-coded iPad and `pixel_tablet`, adding its whole wall clock to a
job that was already the slowest in the matrix. Profiles do the same work as parallel jobs, so
`tablet-walkthroughs: true` becomes:

```yaml
  ios-profiles: "iPhone, iPad=ipad"
  android-profiles: "pixel_5, pixel_tablet=tablet"
```

which uploads the same `screenshots-ios-uikit-ipad` and `screenshots-android-mdc-tablet` the old
input did. Passing `tablet-walkthroughs` now fails the run — GitHub rejects an input a reusable
workflow does not define — so a caller still setting it has to change one of these two lines.

### Signing

`day pack` degrades to the dev tier (ad-hoc / dev keystore / self-signed) when signing secrets are
absent — it never fails for that reason. On semantic-version tags, the same `DAY_*` secret names
[daybrite/day's ci.yml](https://github.com/daybrite/day) uses light up release signing when they
exist and the caller forwards them with `secrets: inherit`. Branch and PR builds always pack
dev-signed, even when the secrets exist.

### Store uploads

On semantic-version tags, three independent jobs upload the packed artifacts to the stores by
running a lane from the app's own fastlane config:

| job | store | artifact it downloads | env it sets |
|---|---|---|---|
| `appstore-ios` | App Store Connect | `dist-ios-uikit` (the `.ipa`) | `DAY_IPA` |
| `appstore-macos` | Mac App Store | `dist-macos-appkit` (notarized when `signing-environment` is set) | `DAY_PKG_OR_APP` |
| `playstore-android` | Google Play | `dist-android-mdc` (the `.aab`) | `DAY_AAB` |

| input | default | meaning |
|---|---|---|
| `upload-ios` | `""` | `""` auto-detects from the Fastfile (see below); `"true"`/`"false"` force the iOS upload on or off. |
| `upload-macos` | `""` | Same, for the Mac App Store upload. |
| `upload-play` | `""` | Same, for the Google Play upload. |
| `ios-upload-lane` | `ios upload` | The fastlane arguments the iOS job runs (platform + lane). |
| `macos-upload-lane` | `mac upload` | The fastlane arguments the macOS job runs. |
| `play-upload-lane` | `android upload` | The fastlane arguments the Play job runs. |

With an `upload-*` input left empty, the upload runs exactly when the repo has a fastlane config
for that platform — a `fastlane/Fastfile` under `project-path` (for iOS also
`platform/ios/fastlane/Fastfile`, for Play also `platform/android/fastlane/Fastfile`) containing
the literal `platform :ios`, `platform :mac`, or `platform :android` (case-sensitive). The
`preflight` job prints a `::notice` for each auto decision.

Each job checks out the repo, downloads the built artifact, points its `DAY_*` variable at it
(an absolute path), and runs the lane from the directory holding `fastlane/` — with
`bundle install && bundle exec fastlane <lane>` when a `Gemfile` is present, plain
`fastlane <lane>` otherwise (installed with `gem install fastlane` on ubuntu; macOS runners ship
it). The workflow sets no store credentials: forward yours with `secrets: inherit` and have the
Fastfile read its own — the App Store Connect API key envs for `upload_to_app_store`/`deliver`,
the JSON key for `upload_to_play_store`/`supply`. A Mac App Store submission needs a `.pkg`
signed with the MAS installer identity; producing or re-signing it from `DAY_PKG_OR_APP` is the
lane's job — the workflow hands over build products, not store policy. Caller permissions are
unchanged: the upload jobs need nothing beyond what the workflow already uses.

```ruby
# fastlane/Fastfile
platform :ios do
  lane :upload do
    upload_to_app_store(ipa: ENV.fetch("DAY_IPA"), skip_screenshots: true, skip_metadata: true)
  end
end
platform :android do
  lane(:upload) { upload_to_play_store(aab: ENV.fetch("DAY_AAB"), track: "internal") }
end
```

### Web deploy

With `deploy-web: true` (and `web-dom` among the `targets`), a final job publishes the `web-dom`
(WebAssembly) build to the calling repository's own
[GitHub Pages](https://docs.github.com/pages). It **reuses the release-profile dist the build
already produced** — no second build — and the dist references every asset by a relative path, so
it serves correctly from a project-Pages subpath (`https://<owner>.github.io/<repo>/`) with no
`<base>` tag or path rewriting.

```yaml
# .github/workflows/ci.yml — build every target and deploy the web build on each push to main
permissions:
  contents: write # release-asset upload on tag builds
  pages: write    # web-dom → GitHub Pages
  id-token: write # deploy-pages OIDC token
jobs:
  app:
    uses: daybrite/actions/.github/workflows/build-day-app.yml@main
    secrets: inherit
    with:
      targets: macos-appkit, ios-uikit, android-mdc, web-dom
      deploy-web: true
      # web-deploy-tag-pattern: '^v[0-9]+\.[0-9]+\.[0-9]+$'   # publish only on version tags instead
```

By default it publishes on a push to the repo's default branch; set `web-deploy-tag-pattern` to a
regex to publish only on matching tags. The native release-assets job (on `vX.Y.Z` tags) is
independent, so one caller can attach packages on tags *and* deploy the web build on every push to
main.

**Re-running a job is safe.** Artifacts belong to the run, not to the attempt, so a re-run used to
leave a second artifact named `github-pages` beside the first and `actions/deploy-pages` refused to
deploy at all — one flaky build leg would take the whole workflow down on its way out
([upload-pages-artifact#97](https://github.com/actions/upload-pages-artifact/issues/97)). The Pages
artifact now carries the run attempt in its name, so each attempt deploys its own; a duplicate left
by the uploader's internal retry is swept before deploying, where the caller's token allows it.
Nothing to configure.

### Requirements

- The project's `Cargo.toml` must resolve its `day` dependencies on a runner — a git dependency
  (`day = { git = "https://github.com/daybrite/day.git" }`, the `day new app --git` default), not
  a local path. For local-checkout development, put a `[patch]` in a gitignored
  `.cargo/config.toml`.
- Attaching release assets needs `permissions: contents: write` in the caller.
- **Web deploy** additionally needs, in the caller: `permissions: pages: write` **and**
  `id-token: write` (the latter lets `actions/deploy-pages` mint the OIDC token it uploads with —
  omitting it fails with a 403), plus the one-time repo setting Settings → Pages → "Build and
  deployment" → **Source = "GitHub Actions"**. No repository secrets are involved.

## Composite actions

`build-day-app` is the whole pipeline. When you only want a piece of it, the actions it is built
from are usable on their own — daybrite/day's own workflows call them directly.

### `setup-day-deps`

Everything one platform-toolkit target needs on a runner, in one step: the toolkit's dev libraries,
the Rust std for its cross-compile, the SDK a mobile target builds through, and the tools
`day pack` needs. What a target needs is a property of the target, so it is derived here rather
than spelled out again in every workflow.

```yaml
- uses: daybrite/actions/.github/actions/setup-day-deps@main
  with:
    target: linux-gtk   # required; any of the 12 combos
    pack: true          # flatpak-builder + linuxdeploy (linux), NSIS (windows-xaml)
    extras: false       # walkthrough extras: web view dev libs, xvfb, imagemagick, CJK fonts
    java: false         # pin JDK 21 + Gradle for android/harmony (else the runner's own JDK)
    rust: true          # rustup target add this target's std
```

Everything that is not "make this target buildable" stays with the caller: checking out the app,
installing the CLI (`setup-day-cli`), emulators, signing material, and the build/pack/script
commands themselves.

### `setup-day-cli`

Installs the `day` CLI and exports `DAY_BIN` — from crates.io, a git ref, or an artifact this run
built (`day-source: artifact`, how daybrite/day tests the CLI it just compiled). Source installs
build cold on purpose: a cached build directory once handed `--branch main` installs a stale
binary labeled with the new commit, and correctness beats the minutes saved.

## Validation

`validate.yml` runs on every push and pull request: it scaffolds a fresh app with `day new app`
and drives it through the reusable workflow for all 7 primary platform-toolkit pairs, with
`day-version: main` so the CLI and the framework come from the same tree. (It exercises
`build-day-app`'s build/pack path; the web deploy publishes to a live Pages site and so isn't part
of the validation run.)

## Project website (daysite)

Add a `website/site.toml` to your repository and the same workflow builds and deploys a full
project site to your GitHub Pages — landing page, screenshot gallery, and download links — using
the [daybrite/daysite](https://github.com/daybrite/daysite) template, with the web-dom build
hosted under the site's `webapp/` subdirectory (`site.toml` `webapp` key names it). The content
comes from what the repo already maintains: `Day.toml`, the `store/` listings, the screenshots
your dayscripts capture in this very workflow, and the latest release's assets.

```toml
# website/site.toml — the only required key:
host = "https://<owner>.github.io/<repo>"
```

Deploys follow the same ref rule as `deploy-web` (pushes to the default branch, or
`web-deploy-tag-pattern` when set) and need the same one-time setup: grant `pages: write` +
`id-token: write` and set Settings → Pages → Source = "GitHub Actions". Pin the template with
`daysite-version` (default `main`). Without a `website/` directory, `deploy-web: true` keeps its
original behavior — the bare web app at the Pages root.

