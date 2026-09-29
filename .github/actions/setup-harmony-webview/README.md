# Automatic ArkWeb setup for Harmony CI

The [failing run](https://github.com/daybrite/day-piece-webview/actions/runs/36593624511)
builds the application successfully but rejects the emulator's ArkWeb package: its
`libarkweb_engine.so` is AArch64, while the emulator and app are x86_64. This is
consistent with [OpenHarmony's packaging rule](https://github.com/openharmony/web_webview/blob/9b7461db8defbcd98f2e4b1f11c274896b5855f6/ohos_nweb/BUILD.gn),
which selects the ARM64 HAP for x86_64. The available OpenHarmony 7 image inspected
during this investigation had the same problem.

## A working x86_64 engine

Huawei's HarmonyOS 5.0.1.120 phone emulator archive contains a real x86_64
ArkWeb package. The download was located through this
[emulator dump's source reference](https://github.com/SunsetMkt/HarmonyOS-Next-5.0.1.120-Phone-x86-Emulator-Dump).
The helper extracts the original signed HAP directly from the
[Huawei-hosted archive](https://update.dbankcdn.com/download/data/pub_13/HWHOTA_hota_900_9/4a/v3/SurZ60PYSryhwf0W4QEK1g/system-image-phone-x86.zip),
without mounting disk images or repacking the bundle. HTTP GET works; HEAD returned
403 during testing. The download is approximately 2.16 GB; extraction additionally
needs several GB of temporary disk space.

| Artifact | SHA256 |
| --- | --- |
| `system-image-phone-x86.zip` | `874e359f7f07b93c2b6761b9052fc2681ac2da0202eed7fa0c299a2179487d4a` |
| Original `ArkWebCore.hap` | `7120ee8df5207d592cfdf7448da79ac16a6f791f916ec060b55525d43f24f4a7` |
| `libarkweb_engine.so` | `eaa70293550c974f8d2a715ecbde6e4177b075dbfd3475b59271d857f3a97303` |

The HAP is `com.huawei.hmos.arkwebcore` version `5.0.1.106sp40`, API 12.
Its engine, renderer, crash handler, and FFmpeg libraries all have ELF machine
62 (x86_64). The engine reports Chromium `114.0.5735.197`. Keep this old runtime
confined to disposable emulators and the demo's bundled offline content. It is a
test fixture, not a supported production browser distribution. The repository
does not vendor or republish the Huawei binaries.

## Two additional Oniro 6.1 problems

Installing the x86_64 package alone is insufficient:

1. Oniro's tested kernel exposes neither PID nor network namespaces. The renderer
   and GPU sandbox rules request both; `nwebspawn` fails `clone` with `EINVAL` (22).
   The installer removes only unavailable PID/network namespace flags from the
   two internal renderer/GPU rules. This reduces their isolation and is suitable
   only for these disposable offline tests.
2. Oniro's EGL wrapper advertises `EGL_EXT_create_context_robustness`, but creating
   a context with the corresponding attributes returns `EGL_BAD_ATTRIBUTE`.
   JavaScript can then pass all checks while the page remains blank. The installer
   replaces that one advertisement with equal-length spaces in the exact tested
   wrapper binary. It refuses unknown wrapper hashes. Rebooting is required so
   appspawn does not retain the old library mapping.

The extension is hardcoded in the
[OpenHarmony EGL wrapper](https://github.com/openharmony/graphic_graphic_2d/blob/OpenHarmony-6.1-Release/frameworks/opengl_wrapper/src/EGL/egl_wrapper_display.cpp).
[Chromium 114's context creation](https://github.com/chromium/chromium/blob/114.0.5735.197/ui/gl/gl_context_egl.cc)
uses that advertisement to request robustness attributes. A proper upstream fix
would advertise only capabilities supported by the selected EGL implementation.

These are image workarounds; no WebView Rust or ArkTS changes were needed.

## Shared workflow behavior

`dayapp.yml` automatically invokes `setup-harmony-webview` after a fresh Oniro
emulator boots and before running any Harmony dayscripts. Applications need no
additional setup inputs. Phone and tablet jobs each prepare their own guest.
Build-only jobs do not download a browser runtime.

The action caches only the extracted HAP (about 97 MB), keyed by its SHA256. A
cache miss downloads the 2.16 GB source archive into temporary storage, checks
its hash, extracts and validates the HAP, then removes the archive and disk
images. Every cache hit revalidates the HAP hash and ELF architectures. Corrupt
cache entries fail rather than being installed. The cache contains the original
signed package; no guest disks or app state are cached.

Setup errors fail the Harmony leg by default. `tolerate-failures` retains its
existing opt-in behavior, but a failed setup never proceeds into the walkthrough.
Original and patched system files and installation output are uploaded as runtime
diagnostics. A reboot loads the patched EGL wrapper before the app starts.

The action is for **fresh disposable Oniro 6.1 CI guests**. Do not use it on a
physical device or an everyday emulator. It requires Python 3.11+, `hdc`, `curl`,
and `debugfs` (`e2fsprogs`, installed automatically by the action when missing).
It rejects unknown EGL binaries instead of attempting an unverified patch.

To exercise the helper locally against a separate disposable emulator:

```sh
python3 .github/actions/setup-harmony-webview/arkweb.py prepare /tmp/arkweb-cache
python3 .github/actions/setup-harmony-webview/arkweb.py install \
  /tmp/arkweb-cache/ArkWebCore.hap --target 127.0.0.1:55556 \
  --disposable-emulator --diagnostics /tmp/arkweb-diagnostics
```

For a custom workflow that boots its own fresh Oniro instance after installing
the Harmony SDK, the action is also directly reusable:

```yaml
- uses: daybrite/actions/.github/actions/setup-harmony-webview@main
```

`day-piece-webview` uses one `dayscript/webview.yaml` on all primary targets,
including Harmony. It checks bundled JavaScript and CSS, Unicode evaluation,
the console, custom links, and reload. It also paints a distinctive browser-only
background; its CI checks at least 1,000 matching pixels in the Harmony captures.
This catches a working JavaScript engine with a blank GPU surface. The runtime
installer itself has no dependency on any application or its test suite.

Consumers using `dayapp.yml@v1` receive these workflow changes when that ref is
updated through the normal release process. Editing the local actions checkout
does not change existing remote CI runs.

The modern source build is another possible route: OpenHarmony's
[Chromium ArkWeb build script](https://github.com/openharmony-tpc/chromium_arkweb/blob/bb8d152fe1a763a21c27dc106dea693f23aabe45/build/build.sh)
supports `-A x86_64`. A complete source build was not attempted; an x86_64 V8
library alone is not a WebView engine.
