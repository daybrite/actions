# x86 emulator mouse compatibility

The pinned ArkWeb 5.0.1.106sp40 engine implements the API-12 mouse entry points.
OpenHarmony 7.0.0.39's ArkUI sends mouse input through newer entry points. The
engine renders normally and accepts touch, but logs
`function WebSendMouseEvent isn't existing` for real mouse events. This made
Stanza's reader appear frozen, including its HTML navigation/settings controls.
`uitest uiInput click` injects touch and does not reproduce this failure.

The installer adapts the system glue on exactly the tested 7.0 x86_64 image:

- `WebSendMouseEvent(shared_ptr<NWebMouseEvent> const&)` extracts X, Y, button,
  action and click count and calls the existing `SendMouseEvent(int, int, int,
  int, int)` wrapper.
- `WebSendMouseWheelEvent(double, double, double, double, vector<int> const&)`
  jumps to `SendMouseWheelEvent(double, double, double, double)`.

The event getters and legacy signatures are defined in OpenHarmony's
[nweb.h](https://github.com/openharmony/web_webview/blob/master/ohos_interface/include/ohos_nweb/nweb.h);
the system-side wrappers are in
[ark_web_nweb_wrapper.cpp](https://github.com/openharmony/web_webview/blob/master/ohos_interface/ohos_glue/ohos_nweb/bridge/webview/ark_web_nweb_wrapper.cpp).
The exact installed binary was inspected to establish addresses and calling
conventions; these addresses must not be carried over to another build.

This is an emulator compatibility adapter, not an engine upgrade. The legacy
API cannot forward newer raw-motion fields or the wheel pressed-key vector.
Production ARM devices and the 6.1 setup are unchanged. A matching newer x86
engine is preferable when one becomes available.

## Reproducibility and guards

`arkweb.py` checks the complete original library SHA-256 before editing and the
complete expected SHA-256 afterward. Already patched input is accepted unchanged;
unknown input is rejected before installing the HAP or writing guest system files.
Only the two wrapper bodies change. The mouse body is 294 bytes; the wheel jump
is five bytes. No ELF sections, exported symbols, or ownership rules change.

`mouse_compat.S` is the readable source of the embedded mouse bytes. It preserves
callee-saved registers, stack alignment, the stack canary, and the original
prologue/epilogue offsets used by the library's unwind information. The event's
shared pointer is borrowed. The wheel adapter leaves the four floating-point
arguments intact and ignores the extra borrowed vector argument.

Run the reproducibility, ABI, and installer guard tests from the actions root:

```sh
python3 -m unittest discover -s tests -p test_arkweb.py -v
```

On Linux x86_64 with clang/binutils, this assembles/links the adapter at its actual
address, compares every emitted byte, and runs a C++ ABI harness covering negative
coordinates, button/action values, click counts, null events and pointer ownership.
To also verify the complete real library transformation, set `ARKWEB_TEST_BRIDGE`
to an original `libarkweb_core_loader_glue.z.so` pulled from the tested image.
The proprietary binary is not stored in this repository.

The installer retains original and patched libraries in its diagnostics directory,
stages and renames the new input library atomically, and reboots because appspawn
already maps it. To undo it on a persistent local emulator, atomically restore
the saved original at `/system/lib64/libarkweb_core_loader_glue.z.so` and reboot;
do not overwrite a live mapped inode. Fresh CI guests are discarded after use.

## Interactive validation

On the OpenHarmony 7.0.0.39 phone emulator, Stanza's reader was exercised with
`hdc shell uinput -M` (actual mouse input): open HTML settings, close native
settings, turn pages, drag-select EPUB text, and wheel-scroll. DOM observers
confirmed trusted mouse pointer events and trusted wheel events. Touch and the
native Back button are checked separately. Neither JavaScript-generated clicks
nor dayscript `tap` establish mouse compatibility.
