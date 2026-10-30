<!-- Copyright © The Daybrite Project -->
<!-- SPDX-License-Identifier: MPL-2.0 -->

# Renderer IPC startup deadline

The pinned x86_64 ArkWeb runtime gives the system application manager (AMS) only
100 ms to deliver a new renderer's IPC descriptors. A busy emulator can miss this
deadline even though the connection is available shortly afterward. The renderer
then exits, and the app receives `onRenderExited` with reason 0.

## Reproduction

Day CI [job 123769521442](https://github.com/daybrite/day/actions/runs/40963048807/job/123769521442)
failed Showcase's embedded WebView check in the dark Arabic tablet variant.
On 2026-10-29, the same failure reproduced on an x86_64 KVM host using the CI image
and runtime, Day `31e9fa7`, and day-piece-webview `a84c541`.
The first variant passed; light French failed on the next renderer startup.
The full guest log showed:

| Guest time | Event |
| --- | --- |
| 07:13:58.107 | Renderer 3762 requests its IPC descriptors from AMS. |
| 07:13:58.209 | Renderer reports `timeout(100ms)` and exits. |
| 07:13:58.235 | AMS begins handling the queued attachment. |
| 07:13:58.236 | AMS tries to deliver the descriptors to renderer 3762. |
| 07:13:58.244 | App receives `renderer exited: 0`. |

The guest had more than 2 GB of available memory. This occurrence was a startup
timeout, not an out-of-memory kill. The main app remained alive.

## Emulator-only patch

`patch_engine_startup` changes the verified engine's retry limit from ten to
100 condition-variable waits of 10 ms each: a nominal one-second deadline.
The notification still wakes the wait immediately. Missing IPC still fails;
no page assertions, application retries, or renderer error checks are bypassed.

The routine at virtual address `0x9373d14` in `libarkweb_engine.so` has four
constant uses that must agree. Executable virtual addresses are file offsets
plus `0x1000` in this binary:

| File offset | Original instruction | Replacement |
| --- | --- | --- |
| `0x9372e76` | `cmp $10, %r13d` | `cmp $100, %r13d` |
| `0x9372e88` | `push $10; pop %rbp` | `push $100; pop %rbp` |
| `0x9372ec0` | `push $100; pop %rsi` | `imul $10, %ebp, %esi` |
| `0x9372f16` | `cmp $10, %ebp` | `cmp $100, %ebp` |

These update the loop bound, exhausted count, timeout log, and success boundary.
All replacements have the same length. The 10 ms wait and its notification
predicate are unchanged. No environment variable or parameter lookup exists in
this wait routine.

| Library | SHA256 |
| --- | --- |
| Original | `eaa70293550c974f8d2a715ecbde6e4177b075dbfd3475b59271d857f3a97303` |
| Patched | `2dbc8a8045a796598ff184ece7bbc5d337ab42fb38622051368db67f9c181ca7` |

The installer validates the original signed HAP, the complete engine hash, and
each instruction before applying the change. It replaces only the extracted
library in the disposable guest, using a staged file and rename, then reboots
and verifies the installed hash. The signed HAP and download cache remain
unchanged. `renderer-startup.json` records the installed hash and deadline in
the setup diagnostics. Unknown binaries fail setup.

`tests/test_arkweb.py` checks rejection of unknown engines and bad installed
hashes. Setting `ARKWEB_TEST_ENGINE` to the original extracted library also
checks the real patch, unchanged bytes outside its four ranges, idempotence,
and rejection of corrupted input and output. No proprietary binary is vendored.

## Validation

On the same disposable tablet, the unchanged Showcase walkthrough passed all
eight light/dark × English/French/Arabic/Chinese variants with the patched runtime:
866 executed steps and 51 screenshots per variant, with no renderer exits.
The full installer then reinstalled the original signed HAP, applied the patch,
rebooted, and verified the installed hash. A further dark Arabic walkthrough
passed all 866 steps after that installation.

The shared-action suite passed on Linux: 132 tests passed and six Windows-only
tests were skipped. All 16 ArkWeb tests passed, including the optional checks
against the original engine and input bridge. The application and its walkthrough
were unchanged; Day's separate coverage/retry fix was not needed for these passes.
