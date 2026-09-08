# daybrite/actions

Reusable GitHub workflows for [Day](https://daybrite.dev) projects. Public open-source
repositories use them freely; private and closed-source repositories need a
[daybrite sponsorship](https://github.com/sponsors/daybrite).

## dayapp

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
    uses: daybrite/actions/.github/workflows/dayapp.yml@main
    secrets: inherit
    with:
      targets: windows-xaml, macos-appkit, linux-gtk, linux-qt, ios-uikit, android-mdc, harmony-arkui, web-dom
      scripts: dayscript/walkthrough.yaml
      locales: en fr
      # preflight-checks: fmt clippy   # opt into clippy before the matrix (fmt alone is the default)
```

### Inputs

Every input the workflow declares, in the order it declares them. Only `targets` is required.

| input | type | default | meaning |
|---|---|---|---|
| `targets` | string | (required) | Platform-toolkit pairs to build, comma- or space-separated: `macos-appkit`, `macos-gtk`, `macos-qt`, `windows-xaml`, `linux-gtk`, `linux-qt`, `ios-uikit`, `android-mdc`, `harmony-arkui`, `web-dom`. |
| `day-version` | string | `latest` | Day CLI to install: `latest` (newest crates.io release), `v1.2.3`/`1.2.3` (that release), a 40-hex commit, or a branch name of the day repository (built from git). |
| `day-git` | string | `https://github.com/daybrite/day.git` | Git URL of the day repository, for branch and commit installs. |
| `day-verbose` | boolean | `True` | Run the day CLI with `DAY_VERBOSE=1`, so every `day build`/`launch`/`pack`/`rebuild` forwards the raw cargo, gradle, xcodebuild and hvigor output. `false` keeps the quiet status lines. |
| `project-path` | string | `.` | Directory of the Day project within the repository. |
| `setup-command` | string | — | Shell command run at the repository root after the CLI installs and before anything else, for example a `day new app …` that scaffolds the project the run builds. |
| `scripts` | string | `auto` | Dayscripts to run on each target, comma- or space-separated paths relative to the project. `auto` runs every `dayscript/*.yaml` (or `scripts/*.yaml`); `none` runs nothing. |
| `launch-env` | string | — | Space-separated `KEY=VALUE` pairs passed to every scripted launch as `--env`; values must not contain spaces. |
| `locales` | string | — | Locales to run each dayscript under, comma- or space-separated (`en fr ar zh-CN`). Each locale captures its own screenshot variant. |
| `android-abis` | string | `arm64-v8a x86_64` | Android ABIs packed into the `android-mdc` APK and AAB, comma- or space-separated; each adds its own `lib/<abi>/`. Supported: `arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64`. |
| `day-source` | string | `install` | Where the day CLI comes from: `install` builds it with cargo per `day-version`; `artifact` downloads the `day-<os>-<arch>` artifact an earlier job in the same run uploaded. |
| `artifact-prefix` | string | — | Prefix for the package artifact names (`<prefix>dist-<target>`), so a repository that already publishes `dist-<target>` from another workflow can keep both. |
| `themes` | string | — | Themes to run each dayscript under (`light dark`), expanded against `locales` into one run per combination. Each theme captures its own screenshot variant. |
| `ios-profiles` | string | — | The older device-only form of `ios-devices`, comma-separated name prefixes with optional `=<slug>`. Setting both is an error. |
| `ios-devices` | string | phone + tablet | Device profiles for `ios-uikit`, one per line as `device=…, os=…, orientation=…, slug=…`. Each becomes its own parallel job, screenshot artifact and gallery column; the first one packs. `device` is a name prefix in which `*` matches anything, so `iPhone * Pro Max` is the largest iPhone the runner image has. Unset runs `iPhone * Pro Max` portrait and `iPad Pro 13-inch` landscape; naming any replaces the pair (see [Device profiles](#device-profiles)). |
| `android-profiles` | string | — | The older device-only form of `android-devices`: `avdmanager list device` ids such as `pixel_7` or `Nexus 7 2013`, comma-separated. |
| `android-devices` | string | phone + tablet | Device profiles for `android-mdc`, one per line in the same shape as `ios-devices`, plus a `density` field; `os` is the API level. Unset runs `pixel_7` portrait and `Nexus 7 2013` landscape at `density=240`; naming any replaces the pair (see [Device profiles](#device-profiles)). |
| `app-id` | string | — | The app's bundle id. When set, the Linux legs verify that the packed flatpak installs and reports that id, and the macOS legs that the `.app` carries it. |
| `lint` | boolean | `True` | Run `day lint` before building: fluent coverage, ids, routes, and the store listing. |
| `assert-pristine` | boolean | `True` | Fail if the checkout has uncommitted changes before packing. An artifact packed from a dirty tree records a commit that cannot reproduce it. |
| `signing-environment` | string | — | GitHub environment holding the macOS release-signing secrets (`DAY_MACOS_CERT_P12`, `DAY_MACOS_CERT_PASSWORD`, `DAY_SIGN_MACOS_IDENTITY`, `DAY_NOTARY_KEY_ID`, `DAY_NOTARY_ISSUER`, `DAY_NOTARY_KEY_B64`). When set, a tag build signs and notarizes the `.dmg` in a separate job that checks out no code, and fails rather than shipping unsigned when the material is missing. |
| `validate-rebuild` | boolean | `False` | After packing, rebuild each artifact from its own recorded provenance and compare (`day rebuild --strict`). Off by default: the check needs a fixed day revision to be meaningful. |
| `publish-release` | boolean | `True` | Publish the GitHub release for the tag. `false` leaves it as a fully assembled draft, packages, checksums, launch scripts and notes in place, for a human to review and publish. A public release is never un-published. |
| `deploy-web` | boolean | `False` | Publish the `web-dom` build to the caller's GitHub Pages after the matrix, reusing the dist the build job packed (see [Web deploy](#web-deploy)). Requires `web-dom` in `targets`. |
| `daysite-version` | string | `main` | Git ref of daybrite/daysite the website job builds with (branch, tag, or SHA). Used only when the repository has a `website/site.toml`. |
| `web-deploy-tag-pattern` | string | — | With `deploy-web`: empty deploys on a push to the default branch; a bash regex such as `^v[0-9]+\.[0-9]+\.[0-9]+$` deploys only on a tag matching it. |
| `preflight-checks` | string | `fmt` | Rust checks the `preflight` job runs before the matrix, from `fmt`, `clippy`, `check`, `test`, comma- or space-separated. `fmt` takes seconds; the others compile the whole workspace and delay every leg. Empty skips them. |
| `update-day-deps` | boolean | `False` | Refresh the day crates in `Cargo.lock` to the tip of what the app's git dependency tracks, instead of building the locked revision. |
| `upload-ios` | string | — | Upload the packed `.ipa` to App Store Connect on semantic-version tags through `ios-upload-lane`. Empty auto-detects: on when the repository has a `fastlane/Fastfile` (or `platform/ios/fastlane/Fastfile`) with `platform :ios`, or a `store/app.toml` listing that `day store stage` turns into lanes. `"true"`/`"false"` override. |
| `upload-macos` | string | — | Upload the `macos-appkit` build products to the Mac App Store on semantic-version tags through `macos-upload-lane`. Empty auto-detects on a `fastlane/Fastfile` with `platform :mac`; `"true"`/`"false"` override. |
| `upload-play` | string | — | Upload the packed `.aab` to Google Play on semantic-version tags through `play-upload-lane`. Empty auto-detects on a `fastlane/Fastfile` (or `platform/android/fastlane/Fastfile`) with `platform :android`, or a `store/app.toml` listing that `day store stage` turns into lanes; `"true"`/`"false"` override. |
| `ios-upload-lane` | string | `ios upload` | The fastlane arguments the `appstore-ios` job runs (platform + lane). The staged lanes are `ios validate`, `ios upload`, and `ios release`, which also submits the version for review. |
| `macos-upload-lane` | string | `mac upload` | The fastlane arguments the `appstore-macos` job runs. |
| `play-upload-lane` | string | `android upload` | The fastlane arguments the `playstore-android` job runs. The staged lanes are `android validate`, `android upload` (internal track, draft), and `android release` (production track, completed, which is Play's submission). |

### Targets and runners

<a id="macos-runner"></a>
Every macOS job — the Apple targets, the portable-toolkit builds on macOS, code signing and both
store uploads — runs on **`xcode-27`**, and never on an older image. That is a floor rather than a
preference: turning a simulator headlessly needs a `devicectl` that can see simulators, and a
machine gets that from the CoreDevice framework its **Xcode** installs, not from its macOS. Xcode
26.6 ships CoreDevice 518.33, which cannot see a simulator at all; Xcode 27 ships 642.15, which
turns them. Every default GitHub macOS image stops at 26.6, so on those a device profile asking for
`landscape` captured portrait and reported success. The label is defined once, as the preflight
job's `macos_runner` output.

That image carries a single iOS runtime, so device profiles should leave `os=` unset and take the
newest installed rather than pinning a major that the next image drops.

| target | runner | notes |
|---|---|---|
| `macos-appkit` | xcode-27 | packs a `.dmg` |
| `ios-uikit` | xcode-27 | Simulator scripts; packs an unsigned device `.ipa` for sideloading/self-signing (a signed `.ipa` with signing secrets) |
| `linux-gtk`, `linux-qt` | ubuntu-latest | scripts under xvfb / offscreen; pack a `.flatpak` **and** a `.appimage`, and the release check installs the one and runs the other |
| `android-mdc` | ubuntu-latest | scripts on a KVM emulator (best-effort); packs `.apk` + `.aab` |
| `harmony-arkui` | ubuntu-latest | scripts on the Oniro QEMU emulator (best-effort); packs `.hap` |
| `windows-xaml` | windows-latest | packs `.msix` + NSIS installer |
| `web-dom` | ubuntu-latest | scripts in headless Chromium through day-cli's bundled page-driver (needs a day CLI with `day web driver`), all of them in one launch — web storage lasts only as long as the launch; ships the built dist as a zip |
| `macos-gtk`, `macos-qt`, `windows-qt`, `windows-gtk` | (home OS) | portable-toolkit coverage builds; pack and scripts are best-effort |

### Device profiles

**Both mobile targets run on a phone and a tablet by default**, with no configuration in the
calling workflow. `ios-uikit` runs `iPhone * Pro Max` in portrait and `iPad Pro 13-inch` in
landscape; `android-mdc` runs `pixel_7` in portrait and `Nexus 7 2013` in landscape at
`density=240`. Each is its own parallel job, its own screenshot artifact and its own gallery
column, and captures land under `<target>/<slug>/<variant>/` — `ios-uikit/iphone/`,
`ios-uikit/ipad/`, `android-mdc/phone/`, `android-mdc/tablet/`. Those are the two device classes
a store listing asks for and the two an adaptive UI has to be looked at on, and the four panels
are ones the App Store and Play accept as screenshots.

Naming `ios-devices` or `android-devices` replaces that target's whole list, which is how an app
runs on one device:

```yaml
with:
  targets: ios-uikit, android-mdc
  ios-devices: |
    device=iPhone, orientation=portrait
  android-devices: |
    device=pixel_7, os=36, orientation=portrait
```

Two jobs, `ios-uikit · iPhone` and `android-mdc · pixel_7`, each capturing to
`<target>/<variant>/` with no device level, since neither target runs on more than one device.

`ios-devices` names each device by device, OS and orientation. One profile per **line**; the comma
separates the fields *inside* a profile:

```yaml
with:
  targets: macos-appkit, ios-uikit
  ios-devices: |
    device=iPhone,           os=iOS 26, orientation=portrait
    device=iPad Pro 13-inch, os=iOS 26, orientation=landscape, slug=ipad
```

Three jobs: `macos-appkit`, `ios-uikit · iPhone`, `ios-uikit · iPad Pro 13-inch`.

- **`device` is a prefix and `os` a major version.** `iPad Pro` takes the first iPad Pro; `iOS 26`
  takes the newest 26.x the image has. Runner images retire exact device names and runtimes, so a
  pinned "iPhone 15" on "iOS 26.2" would start failing on its own. Unmatched fails the job and
  lists what the image does have.
- **`orientation`** is `portrait` (default) or `landscape`, applied headlessly through
  `devicectl device orientation set`. Nothing extra is needed to make it work: every macOS job runs
  on `xcode-27` (see [macOS runner](#macos-runner)).

On Android the same fields mean the same things, with two differences worth knowing. `device` is an
exact `avdmanager list device` id rather than a prefix — those ids are stable, so an unknown one
should fail loudly — and `os` is an API level (`36`, `API 36`, `android-36`), defaulting to 36.

**Check a candidate id against the runner, not against a laptop.** The device catalog ships inside
the Android command-line tools, and the image carries version 12.0 while a current Android Studio
carries 23.0 — 66 profiles against 96. `pixel_8` and later, `small_tablet` and `medium_tablet`
exist only in the newer catalog, and naming one fails the job with `No device found matching
--device`. To see what the runner has, install that exact version and ask it:

```sh
sdkmanager "cmdline-tools;12.0"
"$ANDROID_HOME/cmdline-tools/12.0/bin/avdmanager" list device
```

```yaml
with:
  targets: android-mdc
  android-devices: |
    device=pixel_7,     os=36, orientation=portrait,  slug=phone
    device=Nexus 7 2013, os=36, orientation=landscape, slug=tablet, density=240
```

Android takes one field iOS does not: **`density`**, the dpi the panel is read at. It moves the
layout's size in points and leaves the capture's pixels alone.

**Pick Android profiles Google Play accepts as store screenshots.** These two go in as captured:

| profile | capture | points | Play |
|---|---|---|---|
| `pixel_7` | 1080x2400 portrait | 411x914 | phone slot |
| `Nexus 7 2013`, `density=240` | 1920x1200 landscape | 1280x800 | both tablet slots |

Play's help page says a screenshot's long side may be at most twice its short side, which would
rule out every phone made since about 2018. The publishing API is looser, and these are the
limits it actually enforces, each one measured by offering the API an image and reading its
answer:

| offered | phone slot | ten-inch slot |
|---|---|---|
| 100x100 | refused | — |
| 1080x2400 (2.22:1) | accepted | — |
| 1080x2424 (2.24:1) | **accepted** | — |
| 1080x2500 (2.32:1) | refused | — |
| 320x2000 (6.25:1) | refused | — |
| 1280x800 | — | refused |
| 1920x1200 | — | **accepted** |
| 2560x1600 | — | accepted |

So a tall modern phone is fine: the cutoff sits between 2.24:1 and 2.32:1, and `pixel_7` is
2.22:1. The ten-inch tablet slot holds a floor of 1,080 px on the short side, while the
seven-inch slot took 1280x800 and 800x1280 without complaint.

`pixel_7` is the newest phone in the runner's catalog; the newer Pixels that would also pass are
not on the image. `Nexus 7 2013` is the only tablet there that clears the ten-inch floor.

That floor is why the tablet profile is not a modern one — `day devices boot` halves a headless panel past three million
pixels (both axes and the density, so the size in points holds) to keep the emulator answering,
so `pixel_tablet` and `medium_tablet` at 2560x1600 come back at 1280x800, which the ten-inch slot
refuses. At 2.30 Mpx `Nexus 7 2013` sits below that line and is captured whole.

That leaves one problem, which `density` solves. A tablet profile clearing Play's 1,080 does it at
320 dpi, so `Nexus 7 2013` lays out as 960x600 points: short enough that Day-Showcase's Query page
collapses its list, failing three walkthrough steps that pass on a taller screen.
`density=240` reads the same 1920x1200 panel as a 1280x800-point tablet — the layout the CI tablet
had before — while the capture stays 1920x1200. Pixels are what the emulator rasterizes, so this
costs nothing at boot.

The emulator itself is stood up by the `day` CLI rather than a third-party action: `day devices
setup` creates the AVD from the profile (installing the system image if the cache missed), and
`day devices boot --wait --headless` starts it, blocks on `sys.boot_completed`, applies the
orientation and prints the serial the walkthrough then drives. Only the system image is cached,
keyed by API level — the AVD is rebuilt each run in about a second, so it cannot go stale against a
changed profile.

- **`slug`** fixes the artifact suffix *and* the capture directory; it defaults to the kebab-cased
  device name. Captures land in `<target>/<slug>/<variant>/`, which is what stops two form factors
  of one target overwriting each other when the site job merges every screenshot artifact into one
  tree — and gives each its own **column in the published gallery**.
- **The first profile is primary.** It packs, uploads the packages and release assets, and keeps
  the plain `screenshots-ios-uikit` artifact name; later profiles upload
  `screenshots-ios-uikit-<slug>` and never pack.

A single profile writes no device level at all, so a one-device project's captures and gallery are
exactly what they were.

#### The older `ios-profiles` / `android-profiles`

Both are still supported and unchanged. `ios-devices` supersedes `ios-profiles` and
`android-devices` supersedes `android-profiles`; setting a pair together is an error rather than a
silent preference.

`ios-profiles` and `android-profiles` run a mobile target's dayscripts on more than one device.
Each profile becomes its own **parallel job**, so a second device costs wall clock only for its own
build and script run, not the first device's:

```yaml
with:
  targets: macos-appkit, ios-uikit, android-mdc
  ios-profiles: "iPhone 16, iPad Pro=ipad"
  android-profiles: "pixel_7, Nexus 7 2013=tablet"
```

That is five jobs: `macos-appkit`, `ios-uikit · iPhone 16`, `ios-uikit · iPad Pro`,
`android-mdc · pixel_7`, `android-mdc · Nexus 7 2013`.

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
- **Naming no profiles gives a mobile target the default pair** — a phone and a tablet, two jobs
  named `<target> · <device>`. Every other target is one job named `<target>`, exactly as before.

**Replacing `tablet-walkthroughs`.** That input is gone (2026-08). It ran the scripts a second time
inside the phone's job, on a hard-coded iPad and `pixel_tablet`, adding its whole wall clock to a
job that was already the slowest in the matrix. Profiles do the same work as parallel jobs, so
`tablet-walkthroughs: true` becomes:

```yaml
  ios-profiles: "iPhone, iPad=ipad"
  android-profiles: "pixel_7, Nexus 7 2013=tablet"
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

An App Store `.ipa` takes `DAY_APPLE_TEAM`, the API key trio `DAY_ASC_KEY_ID`, `DAY_ASC_ISSUER`,
`DAY_ASC_KEY_B64`, the distribution certificate as `DAY_APPLE_CERT_P12` with
`DAY_APPLE_CERT_PASSWORD`, and the App Store provisioning profile as `DAY_IOS_PROFILE_B64`. The
profile is installed where Xcode looks and `day pack` exports over it with the imported
certificate; an API key cannot use Xcode's cloud-managed signing, so without the profile the
export has nothing to sign with and the pack degrades to an unsigned `.ipa`, which the upload
job then refuses.

### Store uploads

On semantic-version tags, three independent jobs upload the packed artifacts to the stores by
running a lane from the app's own fastlane config:

| job | store | artifact it downloads | env it sets |
|---|---|---|---|
| `appstore-ios` | App Store Connect | `dist-ios-uikit` (the `.ipa`) | `DAY_IPA` |
| `appstore-macos` | Mac App Store | `dist-macos-appkit` (notarized when `signing-environment` is set) | `DAY_PKG_OR_APP` |
| `playstore-android` | Google Play | `dist-android-mdc` (the `.aab`) | `DAY_AAB` |

The `upload-*` and `*-upload-lane` inputs that drive them are in [Inputs](#inputs).

With an `upload-*` input left empty, the upload runs exactly when the repo has a fastlane config
for that platform — a `fastlane/Fastfile` under `project-path` (for iOS also
`platform/ios/fastlane/Fastfile`, for Play also `platform/android/fastlane/Fastfile`) containing
the literal `platform :ios`, `platform :mac`, or `platform :android` (case-sensitive). For iOS and Play a
`store/app.toml` listing counts as well: the job runs `day store stage` and uses the lanes it
writes (`ios validate`, `ios upload`, and `ios release`, which also submits the version for
review; `android validate`, `android upload`, and `android release`). The `preflight` job prints
a `::notice` for each auto decision.

Each job checks out the repo, downloads the built artifact, points its `DAY_*` variable at it
(an absolute path), and runs the lane from the directory holding `fastlane/` — with
`bundle install && bundle exec fastlane <lane>` when a `Gemfile` is present, plain
`fastlane <lane>` otherwise (installed with `gem install fastlane` on ubuntu; macOS runners ship
it). The iOS job hands the lane the App Store Connect API key as `DAY_ASC_KEY_ID`,
`DAY_ASC_ISSUER`, and `DAY_ASC_KEY` (the `.p8` written from `DAY_ASC_KEY_B64`), which is what
the staged lanes read; a repository's own Fastfile may read the same names or its own. The Play
job writes `DAY_PLAY_JSON_KEY` (the service-account JSON) to a file and hands it to the lane as
`SUPPLY_JSON_KEY`. The macOS job sets no store credentials: forward yours with `secrets: inherit`
and have the Fastfile read them. A Mac App Store submission needs a `.pkg`
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
    uses: daybrite/actions/.github/workflows/dayapp.yml@main
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

- The calling repository must be public. `preflight` reads the caller's visibility and stops the
  run there otherwise, before it builds anything: the workflows are free for public open-source
  projects, and private, internal, and closed-source repositories need a
  [daybrite sponsorship](https://github.com/sponsors/daybrite).
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

`dayapp` is the whole pipeline. When you only want a piece of it, the actions it is built
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
`dayapp`'s build/pack path; the web deploy publishes to a live Pages site and so isn't part
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

