# daybrite/actions

Reusable GitHub workflows for [Day](https://daybrite.dev) projects.

## build-day-app

Builds a conventional Day project for a set of platform-toolkit targets, runs its dayscripts
(capturing screenshots), and packages it for distribution with `day pack`. On a semantic-version
tag (`vX.Y.Z`), a final job attaches every package and a per-target screenshot zip — plus a
`SHA256SUMS` manifest — to the GitHub release for that tag.

Release assets are packed with `day pack --no-version-in-name`, so their filenames carry no
version, and each is tagged with its platform-toolkit combo — `app-fair-android-mdc.aab`,
`app-fair-linux-gtk-x86_64.flatpak`, `app-fair-windows-xaml-setup.exe`, `app-fair-harmony-arkui.hap`.
Each is therefore reachable at a stable "latest release" URL —
`https://github.com/<owner>/<repo>/releases/latest/download/<name>` (e.g.
`.../releases/latest/download/app-fair-android-mdc.aab`) — that always redirects to the newest
tagged release.

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
```

### Inputs

| input | default | meaning |
|---|---|---|
| `targets` | (required) | Platform-toolkit pairs to build, comma- or space-separated. |
| `day-version` | `latest` | Day CLI to install: `latest` (newest crates.io release), `v1.2.3`/`1.2.3` (that crates.io release), a 40-hex commit, or a branch name of the day repo (built from git). |
| `day-git` | `https://github.com/daybrite/day.git` | Day repo URL for branch/commit installs. |
| `project-path` | `.` | Directory of the Day project within the repository. |
| `setup-command` | — | Shell command run at the repo root after the CLI installs (e.g. `day new app …`). |
| `scripts` | `auto` | Dayscripts to run per target; `auto` = every `dayscript/*.yaml` or `scripts/*.yaml`; `none` disables. |
| `launch-env` | — | Space-separated `KEY=VALUE` pairs passed to every scripted launch as `--env`. |
| `locales` | — | Locales to run each dayscript under (each gets its own screenshot variant). |
| `android-abis` | `arm64-v8a x86_64` | Android ABIs packed into the `android-mdc` APK/AAB (each adds its own `lib/<abi>/`), comma- or space-separated. Supported: `arm64-v8a`, `armeabi-v7a`, `x86`, `x86_64`. |
| `deploy-web` | `false` | Publish the `web-dom` build to the caller's GitHub Pages after the matrix (see [Web deploy](#web-deploy)). Requires `web-dom` in `targets`. |
| `web-deploy-tag-pattern` | — | When `deploy-web` is set: empty deploys on a push to the repo's default branch; a bash regex (e.g. `^v[0-9]+\.[0-9]+\.[0-9]+$`) deploys **only** on a tag matching it. Ignored unless `deploy-web` is true. |

### Targets and runners

| target | runner | notes |
|---|---|---|
| `macos-appkit` | macos-latest | packs a `.dmg` |
| `ios-uikit` | macos-latest | Simulator scripts; packs an unsigned device `.ipa` for sideloading/self-signing (a signed `.ipa` with signing secrets) |
| `linux-gtk`, `linux-qt` | ubuntu-latest | scripts under xvfb / offscreen; pack a `.flatpak` |
| `android-mdc` | ubuntu-latest | scripts on a KVM emulator (best-effort); packs `.apk` + `.aab` |
| `harmony-arkui` | ubuntu-latest | build + pack (`.hap`) only — no emulator scripts yet |
| `windows-xaml` | windows-latest | packs `.msix` + NSIS installer |
| `macos-gtk`, `macos-qt`, `windows-qt`, `windows-gtk` | (home OS) | portable-toolkit coverage builds; pack and scripts are best-effort |

### Signing

`day pack` degrades to the dev tier (ad-hoc / dev keystore / self-signed) when signing secrets are
absent — it never fails for that reason. On semantic-version tags, the same `DAY_*` secret names
[daybrite/day's ci.yml](https://github.com/daybrite/day) uses light up release signing when they
exist and the caller forwards them with `secrets: inherit`. Branch and PR builds always pack
dev-signed, even when the secrets exist.

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

## Validation

`validate.yml` runs on every push and pull request: it scaffolds a fresh app with `day new app`
and drives it through the reusable workflow for all 7 primary platform-toolkit pairs, with
`day-version: main` so the CLI and the framework come from the same tree. (It exercises
`build-day-app`'s build/pack path; the web deploy publishes to a live Pages site and so isn't part
of the validation run.)
