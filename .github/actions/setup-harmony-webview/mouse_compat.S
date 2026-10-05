# x86_64 OpenHarmony 7.0.0.39 / ArkWeb 5.0.1.106sp40 emulator compatibility.
# See INPUT-COMPAT.md. This is NOT a general-purpose replacement for newer engines.
# SysV ABI: rdi = ArkWebNWebWrapper*, rsi = const shared_ptr<NWebMouseEvent>&.
# The shared_ptr is borrowed; its first word points at NWebMouseEvent. Its first
# two vtable slots are destructors, followed by X/Y/button/action/click-count.
# Keep the original callee-saved registers, stack canary, and epilogue locations
# so the existing library's unwind information remains valid.
# Assemble at 0x1070e0, legacy_mouse=0x1027a0, stack_chk_fail=0x1c8de0.
.text
.globl arkweb_mouse_compat
.type arkweb_mouse_compat,@function
arkweb_mouse_compat:
    push %r15
    push %r14
    push %r12
    push %rbx
    sub $0x28, %rsp
    mov %fs:0x28, %rax
    mov %rax, 0x20(%rsp)
    mov %rdi, (%rsp)
    mov (%rsi), %rbx
    test %rbx, %rbx
    je finish
    mov %rbx, %rdi
    mov (%rbx), %rax
    call *0x10(%rax)
    mov %eax, %r12d
    mov %rbx, %rdi
    mov (%rbx), %rax
    call *0x18(%rax)
    mov %eax, %r14d
    mov %rbx, %rdi
    mov (%rbx), %rax
    call *0x20(%rax)
    mov %eax, %r15d
    mov %rbx, %rdi
    mov (%rbx), %rax
    call *0x28(%rax)
    mov %eax, 0x8(%rsp)
    mov %rbx, %rdi
    mov (%rbx), %rax
    call *0x30(%rax)
    mov %eax, %r9d
    mov (%rsp), %rdi
    mov %r12d, %esi
    mov %r14d, %edx
    mov %r15d, %ecx
    mov 0x8(%rsp), %r8d
    call legacy_mouse
finish:
    mov %fs:0x28, %rax
    cmp 0x20(%rsp), %rax
    jne fail
    # Keep the original epilogue offsets and unwind metadata valid.
    .org 0x115, 0x90
    add $0x28, %rsp
    pop %rbx
    pop %r12
    pop %r14
    pop %r15
    ret
fail:
    call stack_chk_fail
.size arkweb_mouse_compat, .-arkweb_mouse_compat
.section .note.GNU-stack,"",@progbits
