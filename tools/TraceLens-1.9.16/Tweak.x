#import <UIKit/UIKit.h>
#import <os/log.h>
#import <CoreFoundation/CoreFoundation.h>
#import <CFNetwork/CFNetwork.h>
#import <objc/runtime.h>
#import <objc/message.h>
#include <mach-o/dyld.h>
#include <mach-o/dyld_images.h>
#include <mach-o/loader.h>
#include <string.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <sys/sysctl.h>
#include <sys/ucontext.h>
#include <pthread.h>
#include <mach/mach.h>
#include <mach/vm_region.h>
#include <libkern/OSCacheControl.h>

// Set to 1 to log every dispatch block invoke/caller (very noisy, ~70% of log).
#define FL_DEBUG_DISPATCH 0

static os_log_t tweakLog = NULL;
static _Thread_local int TraceLens_yxReenterGuard = 0;

void TraceLensLogMsg(NSString *msg);
int TraceLensTraceLogLevel(void);
void TraceLensInstallTraceHooks(void);
static uintptr_t moutai_base(void);
static uintptr_t moutai_text_size(void);
static void fl_dli_save(uintptr_t imp);
static void fl_dli_alias(uintptr_t alias, uintptr_t origImp);
static void fl_dli_record_methods(void);
static void fl_dli_bind_current(void);
static void install_dladdr_hook(void);
static void fl_install_scene_configuration(void);
static void breadcrumb(const char *msg);
static void arm_protector_for_imp(IMP imp);

static void fault_breadcrumb(int sig, siginfo_t *info, void *ctx) {
    // The protector's suicide destroys SP before the fatal jump, so this must
    // run on the alternate stack (SA_ONSTACK) to log anything at all.
    ucontext_t *uc = (ucontext_t *)ctx;
    uintptr_t pc = uc && uc->uc_mcontext ? (uintptr_t)uc->uc_mcontext->__ss.__pc : 0;
    uintptr_t lr = uc && uc->uc_mcontext ? (uintptr_t)uc->uc_mcontext->__ss.__lr : 0;
    uintptr_t sp = uc && uc->uc_mcontext ? (uintptr_t)uc->uc_mcontext->__ss.__sp : 0;
    uintptr_t base = moutai_base();
    char line[224];
    snprintf(line, sizeof(line),
             "FAULT sig=%d pc=+0x%lx lr=+0x%lx sp=+0x%lx addr=%p",
             sig, (unsigned long)(pc >= base ? pc - base : pc),
             (unsigned long)(lr >= base ? lr - base : lr),
             (unsigned long)(sp >= base ? sp - base : sp),
             info ? info->si_addr : 0);
    breadcrumb(line);
    if (uc && uc->uc_mcontext) {
        uintptr_t regs[] = {
            (uintptr_t)uc->uc_mcontext->__ss.__x[0],  (uintptr_t)uc->uc_mcontext->__ss.__x[1],
            (uintptr_t)uc->uc_mcontext->__ss.__x[2],  (uintptr_t)uc->uc_mcontext->__ss.__x[3],
            (uintptr_t)uc->uc_mcontext->__ss.__x[4],  (uintptr_t)uc->uc_mcontext->__ss.__x[5],
            (uintptr_t)uc->uc_mcontext->__ss.__x[6],  (uintptr_t)uc->uc_mcontext->__ss.__x[7],
            (uintptr_t)uc->uc_mcontext->__ss.__x[8],  (uintptr_t)uc->uc_mcontext->__ss.__x[9],
            (uintptr_t)uc->uc_mcontext->__ss.__x[10], (uintptr_t)uc->uc_mcontext->__ss.__x[11],
            (uintptr_t)uc->uc_mcontext->__ss.__x[12], (uintptr_t)uc->uc_mcontext->__ss.__x[13],
            (uintptr_t)uc->uc_mcontext->__ss.__x[14], (uintptr_t)uc->uc_mcontext->__ss.__x[15],
            (uintptr_t)uc->uc_mcontext->__ss.__x[16], (uintptr_t)uc->uc_mcontext->__ss.__x[17],
            (uintptr_t)uc->uc_mcontext->__ss.__x[18], (uintptr_t)uc->uc_mcontext->__ss.__x[19],
            (uintptr_t)uc->uc_mcontext->__ss.__x[20], (uintptr_t)uc->uc_mcontext->__ss.__x[21],
            (uintptr_t)uc->uc_mcontext->__ss.__x[22], (uintptr_t)uc->uc_mcontext->__ss.__x[23],
            (uintptr_t)uc->uc_mcontext->__ss.__x[24], (uintptr_t)uc->uc_mcontext->__ss.__x[25],
            (uintptr_t)uc->uc_mcontext->__ss.__x[26], (uintptr_t)uc->uc_mcontext->__ss.__x[27],
            (uintptr_t)uc->uc_mcontext->__ss.__x[28], (uintptr_t)uc->uc_mcontext->__ss.__fp
        };
        for (unsigned i = 0; i < sizeof(regs) / sizeof(regs[0]); i += 2) {
            char rline[192];
            snprintf(rline, sizeof(rline), "REG x%u=+0x%llx x%u=+0x%llx",
                     i, (unsigned long long)(regs[i] >= base ? regs[i] - base : regs[i]),
                     i + 1, (unsigned long long)(regs[i+1] >= base ? regs[i+1] - base : regs[i+1]));
            breadcrumb(rline);
        }
        // Walk the frame chain when FP/SP survived (non-sink faults like the
        // null derefs): each frame's saved LR points into the call path.
        uintptr_t fp = (uintptr_t)uc->uc_mcontext->__ss.__fp;
        uintptr_t spv = (uintptr_t)uc->uc_mcontext->__ss.__sp;
        for (unsigned i = 0; i < 12 && fp > spv && fp - spv < 0x100000 &&
             (fp & 0xF) == 0; i++) {
            uintptr_t prev = *(volatile uintptr_t *)fp;
            uintptr_t flr  = *(volatile uintptr_t *)(fp + 8);
            char fline[128];
            snprintf(fline, sizeof(fline), "FRAME%u lr=+0x%lx", i,
                     (unsigned long)(flr >= base ? flr - base : flr));
            breadcrumb(fline);
            if (prev <= fp) break;
            fp = prev;
        }
    }
    if (getenv("MT_NORECOVER") == NULL) {
        // A background protector sink must not kill the process or unwind the
        // main thread's business stack. Retire only that detector worker.
        if (pc >= 0xB5A90000 && pc < 0xB5AA0000 && !pthread_main_np()) {
            breadcrumb("FAULT retire protected worker");
            pthread_exit(NULL);
        }
    } else {
        breadcrumb("FAULT norecover, let it die");
    }
    signal(sig, SIG_DFL);
    raise(sig);
}

static char g_fault_stack[SIGSTKSZ * 4];

static void install_fault_logger(void) {
    static stack_t ss;
    ss.ss_sp = g_fault_stack;
    ss.ss_size = sizeof(g_fault_stack);
    ss.ss_flags = 0;
    sigaltstack(&ss, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_sigaction = fault_breadcrumb;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK; // diagnostics and detector-worker retirement
    sigemptyset(&sa.sa_mask);
    sigaction(SIGILL, &sa, NULL);
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
}

// 1.9.11 leftovers.  1.9.12 recompiled the protector, so these are only used
// after an instruction/mapping check; a miss is a no-op instead of a crash.
#define OFF_DEFAULT_ABORT 0x1B2D29C
#define OFF_DYLD_GUARD    0x2A983C   // 1.9.12: sub_1002A983C (registers add_image guard)

static IMP s_imp_encrypt;

@interface NSString (MoutaiHook)
- (id)yx_headerEncryptString;
@end

@interface MassComnunicetion : NSObject
- (id)init;
- (void)howhareyou:(id)arg1;
- (BOOL)wipeOut;
- (BOOL)spreadOut;
- (id)onBusiness;
@end

extern void MSHookFunction(void *symbol, void *replace, void **result);
extern void MSHookMessageEx(Class _class, SEL sel, IMP imp, IMP *result);
extern void MSHookMemory(void *target, const void *data, size_t size);

static char g_logpath[280];

static int breadcrumb_try(const char *path, const char *msg) {
    int fd = open(path, O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (fd < 0) return -1;
    write(fd, msg, strlen(msg));
    write(fd, "\n", 1);
    close(fd);
    if (!g_logpath[0] && path) {
        strncpy(g_logpath, path, sizeof(g_logpath) - 1);
    }
    return 0;
}

static void breadcrumb(const char *msg) {
    if (g_logpath[0] && breadcrumb_try(g_logpath, msg) == 0) return;
    NSString *tmp = NSTemporaryDirectory();
    if (tmp.length) {
        char path[280];
        snprintf(path, sizeof(path), "%s/tracelens.log", tmp.fileSystemRepresentation);
        if (breadcrumb_try(path, msg) == 0) return;
    }
    if (breadcrumb_try("/tmp/tracelens.log", msg) == 0) return;
    breadcrumb_try("/var/mobile/tracelens.log", msg);
}

int TraceLensTraceLogLevel(void) {
    static int level = 0;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        id v = [[NSUserDefaults standardUserDefaults] objectForKey:@"tracelens_log_level"];
        if ([v isKindOfClass:[NSString class]]) {
            NSString *s = [(NSString *)v lowercaseString];
            if ([s isEqualToString:@"full"]) level = 3;
            else if ([s isEqualToString:@"standard"]) level = 2;
            else level = 1;
        } else if ([v isKindOfClass:[NSNumber class]]) {
            level = [(NSNumber *)v intValue];
        } else {
            level = 1;
        }
        if (level < 1) level = 1;
        if (level > 3) level = 3;
    });
    return level;
}

void TraceLensLogMsg(NSString *msg) {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    os_log(tweakLog, "[TraceLens] %{public}@", msg);
    breadcrumb([[NSString stringWithFormat:@"%@", msg] UTF8String]);
}

static void LogMsg(NSString *msg) {
    TraceLensLogMsg(msg);
}

static uintptr_t moutai_base(void) {
    for (uint32_t i = 0; i < _dyld_image_count(); i++) {
        const char *name = _dyld_get_image_name(i);
        if (name && strstr(name, "moutai-mall") != NULL)
            return (uintptr_t)_dyld_get_image_header(i);
    }
    return (uintptr_t)_dyld_get_image_header(0);
}

static uintptr_t moutai_text_size(void) {
    const struct mach_header_64 *mh = (const struct mach_header_64 *)moutai_base();
    if (!mh || mh->magic != MH_MAGIC_64) return 0x2100000;
    const uint8_t *p = (const uint8_t *)(mh + 1);
    for (uint32_t i = 0; i < mh->ncmds; i++) {
        const struct load_command *lc = (const struct load_command *)p;
        if (lc->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *sg = (const struct segment_command_64 *)p;
            if (strncmp(sg->segname, "__TEXT", 16) == 0)
                return (uintptr_t)sg->vmsize;
        }
        p += lc->cmdsize;
    }
    return 0x2100000;
}

static int moutai_contains(uintptr_t p) {
    uintptr_t base = moutai_base();
    uintptr_t sz = moutai_text_size();
    return p >= base && p - base < sz;
}

// class_getClassMethod performs the metaclass lookup itself. Always pass the
// ordinary class here; MSHookMessageEx still takes the metaclass for +methods.
static Method fl_lookup_method(const char *clsName, SEL sel, int meta) {
    Class cls = objc_getClass(clsName);
    return cls ? (meta ? class_getClassMethod(cls, sel) : class_getInstanceMethod(cls, sel)) : NULL;
}

static void capture_imp(const char *clsName, SEL sel, IMP *slot, int meta) {
    Class cls = objc_getClass(clsName);
    if (!cls) {
        LogMsg([NSString stringWithFormat:@"⚠️ no class %s", clsName]);
        return;
    }
    Method m = fl_lookup_method(clsName, sel, meta);
    if (!m) {
        LogMsg([NSString stringWithFormat:@"⚠️ no method %s %s", clsName, sel_getName(sel)]);
        return;
    }
    IMP imp = method_getImplementation(m);
    if (slot) *slot = imp;
    uintptr_t base = moutai_base();
    uintptr_t off = (uintptr_t)imp >= base ? (uintptr_t)imp - base : 0;
    LogMsg([NSString stringWithFormat:@"📍 %s%s %s +0x%lx",
            meta ? "+" : "-", clsName, sel_getName(sel), (unsigned long)off]);
}

static void patch_saved_imp(IMP imp) {
    if (imp) arm_protector_for_imp(imp);
}

static void hook_msg(const char *clsName, SEL sel, IMP imp, IMP *orig, int meta) {
    Class cls = meta ? objc_getMetaClass(clsName) : objc_getClass(clsName);
    if (!cls) return;
    Method m = fl_lookup_method(clsName, sel, meta);
    if (!m) return;
    if (m) {
        uintptr_t oi = (uintptr_t)method_getImplementation(m);
        fl_dli_save(oi);
        fl_dli_alias((uintptr_t)imp, oi);
    }
    MSHookMessageEx(cls, sel, imp, orig);
    LogMsg([NSString stringWithFormat:@"✅ hooked %s%s %s", meta ? "+" : "-", clsName, sel_getName(sel)]);
}

static void generic_load_ret(id self, SEL _cmd) {
    const char *n = class_getName(self);
    LogMsg([NSString stringWithFormat:@"RET +[%s load]", n ? n : "?"]);
}

static IMP s_load_origs[8];
static void nuke_plus_load(const char *clsName, int slot) {
    Class cls = objc_getClass(clsName);
    if (!cls) return;
    Method m = class_getClassMethod(cls, @selector(load));
    if (!m) return;
    void *imp = (void *)method_getImplementation(m);
    if (!imp || imp == (void *)generic_load_ret) return;
    fl_dli_save((uintptr_t)imp);
    MSHookFunction(imp, (void *)generic_load_ret, (void **)&s_load_origs[slot]);
    fl_dli_alias((uintptr_t)generic_load_ret, (uintptr_t)imp);
    LogMsg([NSString stringWithFormat:@"✅ nuked +[%s load]", clsName]);
}

static void my_default_abort(void) {
    LogMsg(@"🛡️ BLOCKED default abort block");
}
static void (*orig_default_abort)(void);

static void my_dyld_guard(void) { LogMsg(@"🛡️ BLOCKED common dyld guard registration"); }

static void hook_if(uintptr_t base, uintptr_t off, uint32_t expect, void *rep, void **orig, const char *tag) {
    if (off >= moutai_text_size()) {
        LogMsg([NSString stringWithFormat:@"⚠️ %s +0x%lx outside TEXT, skip", tag, (unsigned long)off]);
        return;
    }
    void *p = (void *)(base + off);
    uint32_t w = *(volatile uint32_t *)p;
    if (expect && w != expect) {
        LogMsg([NSString stringWithFormat:@"⚠️ %s insn=0x%08x expect=0x%08x, skip", tag, w, expect]);
        return;
    }
    fl_dli_save((uintptr_t)p);
    MSHookFunction(p, rep, orig);
    fl_dli_alias((uintptr_t)rep, (uintptr_t)p);
    LogMsg([NSString stringWithFormat:@"✅ hooked %s @+0x%lx", tag, (unsigned long)off]);
}

// Diagnostic only for the incremental 1.9.11 adaptation.  This does not
// suppress or alter a block; it records the app-relative invoke and caller so
// Console can identify the first post-load integrity check.
struct FLBlockLiteral {
    void *isa;
    int32_t flags;
    int32_t reserved;
    void (*invoke)(void *);
    void *descriptor;
};
typedef void (*fl_dispatch_after_t)(dispatch_time_t, dispatch_queue_t, dispatch_block_t);
static fl_dispatch_after_t orig_dispatch_after;
typedef void (*fl_dispatch_async_t)(dispatch_queue_t, dispatch_block_t);
static fl_dispatch_async_t orig_dispatch_async;
static void fl_neutralized_void_call(void);

// Match the protector's pure task_threads/name probe, not merely the presence
// of an integrity sink. Other blocks are business continuations and must run.
static BOOL fl_is_pure_thread_detector(uintptr_t invoke, uintptr_t base) {
    if (invoke < base || invoke - base >= moutai_text_size()) return NO;
    BOOL task = NO, names = NO;
    for (uintptr_t pc = invoke; pc < invoke + 0x180; pc += 4) {
        uint32_t insn = *(uint32_t *)pc;
        if ((insn & 0xFC000000) != 0x94000000) continue;
        int32_t imm = (int32_t)(insn & 0x03FFFFFF);
        if (imm & 0x02000000) imm |= ~0x03FFFFFF;
        uintptr_t target = pc + ((int64_t)imm << 2);
        if (target == base + 0x1A90E34) task = YES;
        if (target == base + 0x1A90864) names = YES;
    }
    return task && names;
}

static void fl_log_dropped_detector(uintptr_t invoke, uintptr_t base) {
    // The caller already drops this submission. Installing a permanent hook
    // here is redundant and can deadlock with dyld/ObjC initialization locks.
    // Keep this dispatch hot path free of hook installation and ObjC logging.
    static unsigned count = 0;
    if (__sync_fetch_and_add(&count, 1) >= 64) return;
    char line[96];
    snprintf(line, sizeof(line), "DISPATCH dropped pure thread detector +0x%lx",
             (unsigned long)(invoke - base));
    breadcrumb(line);
}

static void observe_dispatch_after(dispatch_time_t when, dispatch_queue_t queue, dispatch_block_t block) {
    struct FLBlockLiteral *literal = (__bridge struct FLBlockLiteral *)block;
    uintptr_t base = moutai_base();
    uintptr_t invoke = literal ? (uintptr_t)literal->invoke : 0;
    if (FL_DEBUG_DISPATCH && invoke >= base && invoke - base < 0x2600000) {
        uintptr_t caller = (uintptr_t)__builtin_return_address(0);
        LogMsg([NSString stringWithFormat:@"🧪 after invoke=+0x%lx caller=+0x%lx",
                (unsigned long)(invoke - base),
                (unsigned long)(caller >= base ? caller - base : UINTPTR_MAX)]);
    }
    if (fl_is_pure_thread_detector(invoke, base)) {
        fl_log_dropped_detector(invoke, base);
        return;
    }
    // Step 2: Console consistently shows this as the final scheduled block
    // before SIGILL after lifecycle discovery. IDA confirms it only enumerates
    // task threads and traps; it has no initialization side effects.
    if (invoke >= base && (invoke - base == 0xCC9B70 || invoke - base == 0x2B1050 ||
                           invoke - base == 0xC8CFF4 || invoke - base == 0xF8DF98 ||
                           invoke - base == 0xDE7FAC || invoke - base == 0xDE8384)) {
        LogMsg([NSString stringWithFormat:@"🧪 step2 blocked stale-off +0x%lx",
                (unsigned long)(invoke - base)]);
        return;
    }
    orig_dispatch_after(when, queue, block);
}

// 1.9.11 splash crash: the watchdog at +0x1B2AE30 sleeps in the background and
// then kills the process on the main thread.  In 1.9.10 the same dispatch path
// was filtered here; port that coverage back.
static __attribute__((unused)) void observe_dispatch_async(dispatch_queue_t queue, dispatch_block_t block) {
    struct FLBlockLiteral *literal = (__bridge struct FLBlockLiteral *)block;
    uintptr_t base = moutai_base();
    uintptr_t invoke = literal ? (uintptr_t)literal->invoke : 0;
    uintptr_t off = moutai_contains(invoke) ? invoke - base : 0;
    if (FL_DEBUG_DISPATCH && off) {
        uintptr_t caller = (uintptr_t)__builtin_return_address(0);
        LogMsg([NSString stringWithFormat:@"🧪 async invoke=+0x%lx caller=+0x%lx",
                (unsigned long)off,
                (unsigned long)(caller >= base ? caller - base : UINTPTR_MAX)]);
    }
    if (off && fl_is_pure_thread_detector(invoke, base)) {
        fl_log_dropped_detector(invoke, base);
        return;
    }
    if (off == 0x1B2AE30 || off == 0xF6F280 ||
        off == 0x5F32C || off == 0x57404) {
        LogMsg([NSString stringWithFormat:@"🧱 drop async detector +0x%lx",
                (unsigned long)off]);
        return;
    }
    orig_dispatch_async(queue, block);
}

static void install_dispatch_observer(void) {
    void *p = dlsym(RTLD_DEFAULT, "dispatch_after");
    void *da = dlsym(RTLD_DEFAULT, "dispatch_async");
    if (p) {
        fl_dli_save((uintptr_t)p);
        MSHookFunction(p, (void *)observe_dispatch_after, (void **)&orig_dispatch_after);
        fl_dli_alias((uintptr_t)observe_dispatch_after, (uintptr_t)p);
    }
    if (da) {
        fl_dli_save((uintptr_t)da);
        MSHookFunction(da, (void *)observe_dispatch_async, (void **)&orig_dispatch_async);
        fl_dli_alias((uintptr_t)observe_dispatch_async, (uintptr_t)da);
    }
    LogMsg(@"✅ hooked dispatch_after + dispatch_async");
}

// Raw-syscall file checks: 1.9.12's CFF probes jailbreak artifacts via
// mac_syscall wrappers (direct and SYS_syscall-indirect).  The inline svc
// wash only covers ptrace/sysctl templates, so access/stat/open on jb paths
// still succeeds and flips the state machine onto failure branches.  Filter
// those at the libc syscall() funnel and answer ENOENT.
static int (*orig_syscall_fn)(int, ...);
static BOOL fl_is_jb_path(const char *p) {
    if (!p || !*p) return NO;
    static const char *const prefixes[] = {
        "/var/jb", "/usr/lib/TweakInject", "/Library/MobileSubstrate",
        "/var/lib/dpkg", "/var/lib/apt", "/var/cache/apt", "/etc/apt",
        "/bin/sh", "/bin/bash", "/usr/sbin/sshd", "/usr/bin/ssh",
        "/usr/libexec/ssh", "/usr/lib/substrate", "/private/var/lib/dpkg",
        "/private/var/cache/apt", "/private/etc/apt",
        "/Applications/Cydia.app", "/Applications/Sileo.app",
        "/Applications/Zebra.app", "/Applications/Filza.app",
    };
    for (unsigned i = 0; i < sizeof(prefixes) / sizeof(prefixes[0]); i++) {
        if (strncmp(p, prefixes[i], strlen(prefixes[i])) == 0) return YES;
    }
    static const char *const needles[] = {
        "frida", "cycript", "Cydia", "Sileo", "Substrate", "ellekit",
        "TraceLens", "tracelens", "tweakTest", "jbdev", "gum-js",
    };
    for (unsigned i = 0; i < sizeof(needles) / sizeof(needles[0]); i++) {
        if (strstr(p, needles[i])) return YES;
    }
    return NO;
}
static BOOL fl_path_syscall(int n) {
    // access, open, stat, lstat, stat64, lstat64, openat
    return n == 33 || n == 5 || n == 188 || n == 190 ||
           n == 338 || n == 340 || n == 398;
}
#include <stdarg.h>
#include <errno.h>
static int my_syscall(int number, ...) {
    va_list ap;
    va_start(ap, number);
    unsigned long a1 = va_arg(ap, unsigned long);
    unsigned long a2 = va_arg(ap, unsigned long);
    unsigned long a3 = va_arg(ap, unsigned long);
    unsigned long a4 = va_arg(ap, unsigned long);
    unsigned long a5 = va_arg(ap, unsigned long);
    unsigned long a6 = va_arg(ap, unsigned long);
    va_end(ap);
    int real = number;
    unsigned long pathArg = a1;
    if (number == 0) {          // SYS_syscall: indirect, real number is a1
        real = (int)a1;
        pathArg = a2;
    }
    if (fl_path_syscall(real) && fl_is_jb_path((const char *)pathArg)) {
        static int logged = 0;
        if (logged < 20) {
            logged++;
            LogMsg([NSString stringWithFormat:@"🛡️ syscall(%d) ENOENT %s",
                    real, (const char *)pathArg]);
        }
        errno = ENOENT;
        return -1;
    }
    return orig_syscall_fn(number, a1, a2, a3, a4, a5, a6);
}
static int (*orig_mac_syscall_fn)(int, ...);
static int my_mac_syscall(int number, ...) {
    va_list ap;
    va_start(ap, number);
    unsigned long a1 = va_arg(ap, unsigned long);
    unsigned long a2 = va_arg(ap, unsigned long);
    unsigned long a3 = va_arg(ap, unsigned long);
    unsigned long a4 = va_arg(ap, unsigned long);
    unsigned long a5 = va_arg(ap, unsigned long);
    unsigned long a6 = va_arg(ap, unsigned long);
    va_end(ap);
    int real = number;
    unsigned long pathArg = a1;
    if (number == 0) {
        real = (int)a1;
        pathArg = a2;
    }
    if (fl_path_syscall(real) && fl_is_jb_path((const char *)pathArg)) {
        static int logged = 0;
        if (logged < 20) {
            logged++;
            LogMsg([NSString stringWithFormat:@"🛡️ mac_syscall(%d) ENOENT %s",
                    real, (const char *)pathArg]);
        }
        errno = ENOENT;
        return -1;
    }
    return orig_mac_syscall_fn(number, a1, a2, a3, a4, a5, a6);
}

static void install_syscall_filter(void) {
    void *sc = dlsym(RTLD_DEFAULT, "syscall");
    if (sc) {
        fl_dli_save((uintptr_t)sc);
        MSHookFunction(sc, (void *)my_syscall, (void **)&orig_syscall_fn);
        fl_dli_alias((uintptr_t)my_syscall, (uintptr_t)sc);
    }
    // The protector calls this libsystem entry directly; hooking syscall()
    // alone does not cover its jailbreak-path probes.
    void *mac = dlsym(RTLD_DEFAULT, "mac_syscall");
    if (mac) {
        fl_dli_save((uintptr_t)mac);
        MSHookFunction(mac, (void *)my_mac_syscall, (void **)&orig_mac_syscall_fn);
        fl_dli_alias((uintptr_t)my_mac_syscall, (uintptr_t)mac);
    }
}

// dladdr integrity spoof.  1.9.12 guards critical methods by dladdr()'ing
// their live IMP and requiring dli_sname to look like "-[Class sel]".  Our
// hooks replace those IMPs with tweak functions, so the check fails and the
// CFF takes its state-computed suicide path (the +0x3B9A74 strncmp site).
// Strategy: record the original Dl_info of every hooked target before
// hooking, then alias the post-hook IMP to the same record and answer
// dladdr queries from the saved data.
#define FL_DLI_MAX 160
static struct {
    uintptr_t imp;
    uintptr_t fbase;
    uintptr_t saddr;
    char sname[96];
    char fname[256];
} s_dli[FL_DLI_MAX];
static unsigned s_dli_n;

static int (*orig_dladdr_fn)(const void *, Dl_info *);

static unsigned fl_dli_store(uintptr_t imp, uintptr_t fbase, uintptr_t saddr,
                             const char *sname, const char *fname) {
    if (!imp || s_dli_n >= FL_DLI_MAX) return 0;
    for (unsigned i = 0; i < s_dli_n; i++) if (s_dli[i].imp == imp) return i;
    unsigned i = s_dli_n++;
    s_dli[i].imp = imp;
    s_dli[i].fbase = fbase;
    s_dli[i].saddr = saddr;
    strlcpy(s_dli[i].sname, sname ? sname : "", sizeof(s_dli[i].sname));
    strlcpy(s_dli[i].fname, fname ? fname : "", sizeof(s_dli[i].fname));
    return i;
}

// Record the CURRENT dladdr data for imp (call before it is hooked).
static void fl_dli_save(uintptr_t imp) {
    if (!imp) return;
    for (unsigned i = 0; i < s_dli_n; i++) if (s_dli[i].imp == imp) return;
    Dl_info info;
    memset(&info, 0, sizeof(info));
    int (*rawDladdr)(const void *, Dl_info *) = orig_dladdr_fn ? orig_dladdr_fn : dladdr;
    if (!rawDladdr(0, 0)) {}
    if (!rawDladdr((const void *)imp, &info)) return;
    fl_dli_store(imp, (uintptr_t)info.dli_fbase, (uintptr_t)info.dli_saddr,
                 info.dli_sname, info.dli_fname);
}

// Point `alias` (typically the replacement IMP) at origImp's saved record.
static void fl_dli_alias(uintptr_t alias, uintptr_t origImp) {
    if (!alias || !origImp || alias == origImp) return;
    for (unsigned i = 0; i < s_dli_n; i++) {
        if (s_dli[i].imp == origImp) {
            fl_dli_store(alias, s_dli[i].fbase, s_dli[i].saddr,
                         s_dli[i].sname, s_dli[i].fname);
            return;
        }
    }
}

static void fl_dli_set_objc_name(uintptr_t imp, const char *cls,
                                 const char *sel, int meta) {
    for (unsigned i = 0; i < s_dli_n; i++) {
        if (s_dli[i].imp != imp) continue;
        snprintf(s_dli[i].sname, sizeof(s_dli[i].sname),
                 "%c[%s %s]", meta ? '+' : '-', cls, sel);
        return;
    }
}

static int my_dladdr(const void *addr, Dl_info *info) {
    uintptr_t a = (uintptr_t)addr;
    // Integrity-probe tripwire: reveal which of our replacement IMPs is being
    // dladdr-checked from inside the app binary.
    static uintptr_t tweakLo, tweakHi;
    if (!tweakLo) {
        Dl_info ti;
        memset(&ti, 0, sizeof(ti));
        if (orig_dladdr_fn((const void *)my_dladdr, &ti) && ti.dli_fbase) {
            tweakLo = (uintptr_t)ti.dli_fbase;
            tweakHi = tweakLo + 0x40000;
        }
    }
    if (a >= tweakLo && a < tweakHi) {
        uintptr_t ra = (uintptr_t)__builtin_return_address(0);
        uintptr_t base = moutai_base();
        if (ra >= base && ra - base < moutai_text_size()) {
            static int probeLogs = 0;
            if (probeLogs < 30) {
                probeLogs++;
                char line[160];
                snprintf(line, sizeof(line), "DLI-PROBE ra=+0x%lx addr=+0x%lx",
                         (unsigned long)(ra - base),
                         (unsigned long)(a - tweakLo));
                breadcrumb(line);
            }
        }
    }
    for (unsigned i = 0; i < s_dli_n; i++) {
        if (s_dli[i].imp == a) {
            if (info) {
                memset(info, 0, sizeof(*info));
                info->dli_fbase = (void *)s_dli[i].fbase;
                // The protector compares dli_saddr with the CURRENT method
                // IMP, while requiring the original ObjC symbol name. Returning
                // the pre-hook address here makes a hooked method look forged.
                info->dli_saddr = (void *)a;
                info->dli_sname = s_dli[i].sname;
                info->dli_fname = s_dli[i].fname;
            }
            return 1;
        }
    }
    return orig_dladdr_fn(addr, info);
}

// Every Objective-C method this tweak hooks (Logos %hook sections), as
// {className, selectorName, isClassMethod}.
struct FLHookedMethod { const char *cls; const char *sel; int meta; };
static const struct FLHookedMethod s_hooked_methods[] = {
    {"NSString", "yx_headerEncryptString", 0},
    {"MassComnunicetion", "howhareyou:", 0},
    {"CONCuhcHdGGlGhKK", "alertUserInMainThreadWithType:withMessage:withTitle:", 0},
    {"CONCuhcHdGGlGhKK", "alertUserWithAction:withMessage:withTitle:", 0},
    {"CONCuhcHdGGlGhKK", "addAlertHandlerToAlertQueueWithAction:message:title:", 0},
    {"CONCuhcHdGGlGhKK", "doAbortAction", 0},
    {"CONCuhcHdGGlGhKK", "checkPoliticIfExecute", 0},
    {"dorUdgXxUlbzJOpA", "abortProcess", 1},
    {"YsoTwDufGghbEumc", "dangerousApps", 1},
    {"YsoTwDufGghbEumc", "dangerousEnvInfo", 1},
    {"YsoTwDufGghbEumc", "isCycriptBinExist", 1},
    {"RiskStub", "checkRootStatusWithUserLocalScenario:", 1},
    {"MTInstallUtils", "setInstalledWithClearInfo:", 1},
};

static uintptr_t s_orig_imps[sizeof(s_hooked_methods) / sizeof(s_hooked_methods[0])];

static void fl_dli_record_methods(void) {
    for (unsigned i = 0; i < sizeof(s_hooked_methods) / sizeof(s_hooked_methods[0]); i++) {
        const struct FLHookedMethod *e = &s_hooked_methods[i];
        SEL sel = sel_registerName(e->sel);
        Method m = fl_lookup_method(e->cls, sel, e->meta);
        if (m) {
            uintptr_t imp = (uintptr_t)method_getImplementation(m);
            s_orig_imps[i] = imp;
            fl_dli_save(imp);
            fl_dli_set_objc_name(imp, e->cls, e->sel, e->meta);
        }
    }
}

// After Logos installed its hooks, alias each method's NEW imp to the record
// captured for its original imp (exact per-method mapping).
static void fl_dli_bind_current(void) {
    unsigned bound = 0, missed = 0;
    for (unsigned i = 0; i < sizeof(s_hooked_methods) / sizeof(s_hooked_methods[0]); i++) {
        if (!s_orig_imps[i]) continue;
        const struct FLHookedMethod *e = &s_hooked_methods[i];
        SEL sel = sel_registerName(e->sel);
        Method m = fl_lookup_method(e->cls, sel, e->meta);
        if (!m) continue;
        uintptr_t cur = (uintptr_t)method_getImplementation(m);
        if (cur != s_orig_imps[i]) {
            fl_dli_alias(cur, s_orig_imps[i]);
            fl_dli_set_objc_name(cur, e->cls, e->sel, e->meta);
            bound++;
        } else {
            missed++;
        }
    }
    char line[128];
    snprintf(line, sizeof(line), "DLI records=%u bound=%u missed=%u",
             s_dli_n, bound, missed);
    breadcrumb(line);
}

static void install_dladdr_hook(void) {
    void *p = dlsym(RTLD_DEFAULT, "dladdr");
    if (p) MSHookFunction(p, (void *)my_dladdr, (void **)&orig_dladdr_fn);
}

// Catch silent terminations: log the exit site instead of dying quietly.
static void (*orig_exit)(int) __attribute__((noreturn));
static void (*orig__exit)(int) __attribute__((noreturn));
static void log_terminal(const char *tag) {
    NSArray *syms = [NSThread callStackSymbols];
    if (syms.count > 12) syms = [syms subarrayWithRange:NSMakeRange(0, 12)];
    LogMsg([NSString stringWithFormat:@"💀 %s %@", tag, [syms componentsJoinedByString:@" | "]]);
}
static void my_exit(int code) { log_terminal("exit"); orig_exit(code); }
static void my__exit(int code) { log_terminal("_exit"); orig__exit(code); }
static void install_exit_traps(void) {
    void *e = dlsym(RTLD_DEFAULT, "exit");
    void *ue = dlsym(RTLD_DEFAULT, "_exit");
    if (e) {
        fl_dli_save((uintptr_t)e);
        MSHookFunction(e, (void *)my_exit, (void **)&orig_exit);
        fl_dli_alias((uintptr_t)my_exit, (uintptr_t)e);
    }
    if (ue) {
        fl_dli_save((uintptr_t)ue);
        MSHookFunction(ue, (void *)my__exit, (void **)&orig__exit);
        fl_dli_alias((uintptr_t)my__exit, (uintptr_t)ue);
    }
}

static void fl_write_page(uintptr_t page, const uint8_t *buf) {
    // MSHookMemory remaps through mach_vm_map, which can deadlock on 1.9.16.
    // A protection change plus an in-place copy avoids that path when allowed.
    kern_return_t kr = vm_protect(mach_task_self(), page, 0x4000, 0,
                                  VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE);
    if (kr == KERN_SUCCESS) {
        memcpy((void *)page, buf, 0x4000);
        sys_icache_invalidate((void *)page, 0x4000);
        vm_protect(mach_task_self(), page, 0x4000, 0,
                   VM_PROT_READ | VM_PROT_EXECUTE);
        return;
    }
    MSHookMemory((void *)page, buf, 0x4000);
}

static void fl_restore_guard_page(void) {
    extern const unsigned char fl_decrypted_1424a4[1308];
    extern const unsigned char fl_decrypted_1429c0[3056];
    uintptr_t fn = moutai_base() + 0x1424A4;
    uintptr_t page = fn & ~(uintptr_t)0x3FFF;
    uint8_t buf[0x4000];
    memcpy(buf, (const void *)page, 0x4000);
    memcpy(buf + (fn - page), fl_decrypted_1424a4, 1308);
    memcpy(buf + 0x29C0, fl_decrypted_1429c0, 3056);
    fl_write_page(page, buf);
    // This specially mapped page is already restored. A second in-place wash
    // raises SIGBUS; later method hooks must not rewrite it again.
}

static BOOL fl_decode_adrp(uint32_t insn, uintptr_t pc,
                           uintptr_t *page, unsigned *reg) {
    if ((insn & 0x9F000000) != 0x90000000) return NO;
    uint64_t imm = ((insn >> 29) & 3) | (((uint64_t)(insn >> 5) & 0x7FFFF) << 2);
    if (imm & (1ULL << 20)) imm |= ~((1ULL << 21) - 1);
    *page = (pc & ~(uintptr_t)0xFFF) + (uintptr_t)((int64_t)imm * 0x1000);
    *reg = insn & 31;
    return YES;
}

static BOOL fl_decode_mem64(uint32_t insn, unsigned address_reg, uintptr_t page,
                            uintptr_t *address, unsigned *value_reg) {
    if ((insn & 0xFFC00000) != 0xF9400000 || ((insn >> 5) & 31) != address_reg)
        return NO;
    *address = page + (((insn >> 10) & 0xFFF) * 8);
    *value_reg = insn & 31;
    return YES;
}

// Keep the original instructions intact. The protector schedules its ptrace,
// integrity, dladdr and filesystem checks through counter/threshold pairs.
// counter=0x10000, threshold=~0 selects the normal skip path on every call;
// this also avoids turning code-byte fixes into integrity-check failures.
static void arm_protector_range(uintptr_t target, uintptr_t end) {
    uintptr_t base = moutai_base();
    if (target < base || target - base >= moutai_text_size() || end <= target) return;
    if (end > base + moutai_text_size() - 4)
        end = base + moutai_text_size() - 4;
    unsigned armed = 0;
    uintptr_t last_counter = 0;
    for (uintptr_t pc = target; pc + 16 <= end; pc += 4) {
        uintptr_t counter_page = 0, counter = 0;
        unsigned counter_address_reg = 0, counter_value_reg = 0;
        if (!fl_decode_adrp(*(uint32_t *)pc, pc, &counter_page, &counter_address_reg))
            continue;
        if (!fl_decode_mem64(*(uint32_t *)(pc + 4), counter_address_reg,
                             counter_page, &counter, &counter_value_reg))
            continue;
        uint32_t add = *(uint32_t *)(pc + 8);
        uint32_t store = *(uint32_t *)(pc + 12);
        if ((add & 0xFF000000) != 0x91000000 || ((add >> 10) & 0xFFF) != 1 ||
            (store & 0xFFC00000) != 0xF9000000 ||
            ((store >> 5) & 31) != counter_address_reg ||
            counter_page + (((store >> 10) & 0xFFF) * 8) != counter)
            continue;
        BOOL found = NO;
        for (uintptr_t q = pc + 16; q + 8 <= end && q < pc + 0x90 && !found; q += 4) {
            uintptr_t threshold_page = 0, threshold = 0;
            unsigned threshold_address_reg = 0, threshold_value_reg = 0;
            if (!fl_decode_adrp(*(uint32_t *)q, q, &threshold_page, &threshold_address_reg))
                continue;
            if (!fl_decode_mem64(*(uint32_t *)(q + 4), threshold_address_reg,
                                 threshold_page, &threshold, &threshold_value_reg) ||
                threshold == counter)
                continue;
            for (uintptr_t r = q + 8; r < end && r < q + 0x50; r += 4) {
                uint32_t udiv = *(uint32_t *)r;
                if ((udiv & 0x7FE0FC00) == 0x1AC00800 &&
                    ((udiv >> 16) & 31) == threshold_value_reg) {
                    if (counter >= base && counter < base + 0x4000000 &&
                        threshold >= base && threshold < base + 0x4000000 &&
                        !(counter & 7) && !(threshold & 7)) {
                        *(volatile uint64_t *)counter = 0x10000;
                        *(volatile uint64_t *)threshold = ~0ULL;
                        armed++;
                        last_counter = counter;
                    }
                    found = YES;
                    break;
                }
            }
        }
    }
    if (armed) {
        char line[128];
        snprintf(line, sizeof(line), "ARM imp=+0x%lx checks=%u counter=+0x%lx",
                 (unsigned long)(target - base), armed,
                 (unsigned long)(last_counter - base));
        breadcrumb(line);
    }
}

static volatile int s_protector_armed;
static void arm_protector_for_imp(IMP imp) {
    if (!imp || s_protector_armed) return;
    uintptr_t base = moutai_base();
    uintptr_t target = (uintptr_t)imp;
    if (target < base || target - base >= moutai_text_size()) return;
    uintptr_t end = target + 0x2000;
    if (end > base + moutai_text_size() - 4)
        end = base + moutai_text_size() - 4;
    arm_protector_range(target, end);
}

// Arm every scheduled protector check without rewriting any application
// instruction. Unlike the abandoned page wash, this is a single linear scan
// of __text and only changes the identified counter/threshold data pairs.
static void fl_arm_original_ui(void) {
    static int once = 0;
    if (once) return;
    once = 1;
    uintptr_t base = moutai_base();
    const struct mach_header_64 *mh = (const struct mach_header_64 *)base;
    uintptr_t textOff = 0, textEnd = 0;
    const uint8_t *lc = (const uint8_t *)(mh + 1);
    for (uint32_t i = 0; i < mh->ncmds; i++) {
        const struct load_command *cmd = (const struct load_command *)lc;
        if (cmd->cmd == LC_SEGMENT_64) {
            const struct segment_command_64 *seg = (const struct segment_command_64 *)cmd;
            const struct section_64 *sec = (const struct section_64 *)(seg + 1);
            for (uint32_t j = 0; j < seg->nsects; j++) {
                if (!strncmp(sec[j].segname, "__TEXT", 16) &&
                    !strncmp(sec[j].sectname, "__text", 16)) {
                    textOff = sec[j].offset;
                    textEnd = sec[j].offset + sec[j].size;
                    break;
                }
            }
        }
        if (textOff) break;
        if (!cmd->cmdsize) break;
        lc += cmd->cmdsize;
    }
    if (!textOff) {
        LogMsg(@"⚠️ arm: __text section not found");
        return;
    }
    arm_protector_range(base + textOff, base + textEnd);
    __sync_synchronize();
    s_protector_armed = 1;
    LogMsg([NSString stringWithFormat:@"🧪 armed all protector schedules +0x%lx..+0x%lx",
            (unsigned long)textOff, (unsigned long)textEnd]);
}

static void hook_masscom_c(void) {
    static int once = 0;
    if (once) return;
    once = 1;
    uintptr_t base = moutai_base();
    static void *u5 = NULL;
    hook_if(base, OFF_DYLD_GUARD,    0xA9BE4FF4, (void *)my_dyld_guard,    &u5, "dyldGuard");

    void *ab = (void *)(base + OFF_DEFAULT_ABORT);
    if (*(volatile uint32_t *)ab == 0xA9BE4FF4) {
        fl_dli_save((uintptr_t)ab);
        MSHookFunction(ab, (void *)my_default_abort, (void **)&orig_default_abort);
        fl_dli_alias((uintptr_t)my_default_abort, (uintptr_t)ab);
    }
}

static BOOL my_isJB(id self, SEL _cmd) { return NO; }
static void fl_neutralized_void_call(void) {}
static void (*orig_decrypt_stub)(uintptr_t, uintptr_t, uintptr_t);
static void fl_guard_decrypt_stub(uintptr_t a, uintptr_t b, uintptr_t c) {
    uintptr_t target;
    __asm__ volatile("mov %0, x8" : "=r"(target));
    // loc_100077014 ends in BR x8. Only execute real code belonging to the
    // app; jailbreak detection supplies an out-of-image poison target.
    if (orig_decrypt_stub && moutai_contains(target)) orig_decrypt_stub(a, b, c);
}

// The protector VM can bypass the GOT hooks and walk TASK_DYLD_INFO directly.
// Blank injected image paths in that shared list as well.
static void fl_sanitize_all_image_paths(void) {
    task_dyld_info_data_t ti;
    mach_msg_type_number_t cnt = TASK_DYLD_INFO_COUNT;
    if (task_info(mach_task_self(), TASK_DYLD_INFO,
                  (task_info_t)&ti, &cnt) != KERN_SUCCESS) return;
    const struct dyld_all_image_infos *aii =
        (const struct dyld_all_image_infos *)ti.all_image_info_addr;
    if (!aii || !aii->infoArrayCount || aii->infoArrayCount > 4096) return;
    unsigned hidden = 0;
    for (uint32_t i = 0; i < aii->infoArrayCount; i++) {
        const char *p = aii->infoArray[i].imageFilePath;
        if (!p) continue;
        if (strstr(p, "/var/jb") || strstr(p, "TweakInject") ||
            strstr(p, "Substrate") || strstr(p, "substrate") ||
            strstr(p, "ellekit") || strstr(p, "TraceLens") || strstr(p, "tracelens") ||
            strstr(p, "tweakTest") ||
            strstr(p, "Substitute") || strstr(p, "frida")) {
            *(volatile char *)p = '\0';
            hidden++;
        }
    }
    char line[80];
    snprintf(line, sizeof(line), "AII sanitized=%u/%u", hidden,
             aii->infoArrayCount);
    breadcrumb(line);
}

// Hide injected images only from the app's own dyld API bindings.  The
// protector's MAdmiralOfTheFleat monitor enumerates these before main().
static uint32_t (*orig_dcount)(void);
static const char *(*orig_dname)(uint32_t);
static const void *(*orig_dhdr)(uint32_t);
static BOOL fl_hidden_image(uint32_t i) {
    const char *nm = orig_dname(i);
    if (!nm) return YES;
    return strstr(nm, "/var/jb") || strstr(nm, "TweakInject") ||
           strstr(nm, "MobileSubstrate") || strstr(nm, "ellekit") ||
           strstr(nm, "TraceLens") || strstr(nm, "tracelens") || strstr(nm, "tweakTest") ||
           strstr(nm, "Substitute") ||
           strstr(nm, "frida") || strstr(nm, "Frida") ||
           strstr(nm, "substrate") || strstr(nm, "Substrate");
}
static uint32_t fl_visible_index(uint32_t vis) {
    uint32_t n = orig_dcount(), v = 0;
    for (uint32_t i = 0; i < n; i++) {
        if (fl_hidden_image(i)) continue;
        if (v == vis) return i;
        v++;
    }
    return UINT32_MAX;
}
static uint32_t my_dyld_image_count(void) {
    uint32_t n = orig_dcount(), c = 0;
    for (uint32_t i = 0; i < n; i++) if (!fl_hidden_image(i)) c++;
    return c;
}
static const char *my_dyld_get_image_name(uint32_t i) {
    uint32_t r = fl_visible_index(i);
    return r == UINT32_MAX ? NULL : orig_dname(r);
}
static const void *my_dyld_get_image_header(uint32_t i) {
    uint32_t r = fl_visible_index(i);
    return r == UINT32_MAX ? NULL : orig_dhdr(r);
}
static void hook_dyld_image_api(void) {
    uintptr_t mb = moutai_base();
    struct { uintptr_t off; void *repl; void **orig; const char *tag; } t[] = {
        {0x1F6FEA8, (void *)my_dyld_image_count, (void **)&orig_dcount, "dcount-p"},
        {0x22BE290, (void *)my_dyld_image_count, (void **)&orig_dcount, "dcount-g"},
        {0x1F6FE98, (void *)my_dyld_get_image_name, (void **)&orig_dname, "dname-p"},
        {0x22BE280, (void *)my_dyld_get_image_name, (void **)&orig_dname, "dname-g"},
        {0x1F6FE90, (void *)my_dyld_get_image_header, (void **)&orig_dhdr, "dhdr-p"},
        {0x22BE278, (void *)my_dyld_get_image_header, (void **)&orig_dhdr, "dhdr-g"},
    };
    for (unsigned k = 0; k < sizeof(t) / sizeof(t[0]); k++) {
        uintptr_t slot = mb + t[k].off;
        uintptr_t old = *(volatile uintptr_t *)slot;
        if (old < 0x100000000ULL || old >= 0x1000000000000ULL) continue;
        if (!*t[k].orig) *t[k].orig = (void *)old;
        uintptr_t page = slot & ~(uintptr_t)0x3FFF;
        uint8_t buf[0x4000];
        memcpy(buf, (const void *)page, 0x4000);
        *(uintptr_t *)(buf + (slot - page)) = (uintptr_t)t[k].repl;
        MSHookMemory((void *)page, buf, 0x4000);
        char line[80];
        snprintf(line, sizeof(line), "%s: %s", t[k].tag,
                 *(volatile uintptr_t *)slot == (uintptr_t)t[k].repl ? "hooked" : "FAILED");
        breadcrumb(line);
    }
}

static int (*orig_uimain)(int, char **, id, id);
static int my_uimain(int argc, char **argv, id p, id d) {
    LogMsg(@"UIApplicationMain ENTER");
    // All constructors (including Logos hook registration) have run by now:
    // bind hooked methods' current IMPs to their original dladdr records.
    fl_dli_bind_current();
    hook_masscom_c();
    fl_install_scene_configuration();
    // Constructors have completed; arm once before the original delegate chain.
    fl_arm_original_ui();
    TraceLensInstallTraceHooks();
    TraceLensLogMsg([NSString stringWithFormat:@"TraceLens VERSION 0.0.47 LOADED (iMoutai 1.9.16 compat, log_level=%d: 1=api 2=standard 3=full)",
            TraceLensTraceLogLevel()]);
    int r = orig_uimain(argc, argv, p, d);
    LogMsg(@"UIApplicationMain LEAVE");
    return r;
}

__attribute__((constructor(1)))
static void immortal_early(void) {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    breadcrumb("EARLY constructor(1)");
    // Record original dladdr data for every hooked target BEFORE any hooking.
    fl_dli_record_methods();
    install_dispatch_observer();
    nuke_plus_load("Sciapodous", 0);
    nuke_plus_load("_ptrIvEENS1_23foreign_void_adc", 1);
    nuke_plus_load("YXLifecycleSafeShell", 2);
    nuke_plus_load("BMKBacktraceLogger", 3);
    nuke_plus_load("BMKCrashBaseManager", 4);
    nuke_plus_load("BMKCrashFileUploader", 5);
    nuke_plus_load("BLYWCSessionDelegateInterceptor", 6);
    nuke_plus_load("_AFURLSessionTaskSwizzling", 7);
    hook_msg("UIDevice", @selector(isJailBreak), (IMP)my_isJB, NULL, 1);
    capture_imp("NSString", sel_registerName("yx_headerEncryptString"), &s_imp_encrypt, 0);
    hook_masscom_c();
    install_exit_traps();
    install_syscall_filter();
    install_fault_logger();
    void *uimain = dlsym(RTLD_DEFAULT, "UIApplicationMain");
    if (uimain) {
        fl_dli_save((uintptr_t)uimain);
        MSHookFunction(uimain, (void *)my_uimain, (void **)&orig_uimain);
        fl_dli_alias((uintptr_t)my_uimain, (uintptr_t)uimain);
    }
    install_dladdr_hook();
    // Runtime evidence: the early SIGILL always lands on 0x1424a4 because
    // this page is still encrypted. Restore the original decrypted function.
    fl_restore_guard_page();
    {
        uintptr_t fn = moutai_base() + 0x1424A4;
        char line[80];
        snprintf(line, sizeof(line), "TRAP 0x1424a4 first=0x%x",
                 *(volatile uint32_t *)fn);
        breadcrumb(line);
    }
    hook_dyld_image_api();
    fl_sanitize_all_image_paths();
    // sub_1001424E8 runs ptrace/sysctl only when its call counters match the
    // modulo schedule. Force the skip path without touching encrypted text.
    {
        uintptr_t b = moutai_base();
        // Do not register the protector's add-image callback on 1.9.16. The
        // old OFF_DYLD_GUARD points to another version and silently misses it.
        *(volatile uint32_t *)(b + 0x215C7E8) = 1;
        *(volatile uint64_t *)(b + 0x20DD6A8) = ~0ULL;
        *(volatile uint64_t *)(b + 0x20DD6A0) = ~0ULL;
        *(volatile uint64_t *)(b + 0x215C7A8) = 0x10000ULL;
        *(volatile uint64_t *)(b + 0x215C7B0) = 0x10000ULL;
        // willConnect uses a separate schedule for ptrace/sysctl and its
        // dladdr/access checks. Apply the same data-only bypass there.
        *(volatile uint64_t *)(b + 0x20DD898) = ~0ULL;
        *(volatile uint64_t *)(b + 0x20DD6B0) = ~0ULL;
        *(volatile uint64_t *)(b + 0x20DD838) = ~0ULL;
        *(volatile uint64_t *)(b + 0x20DD7D8) = ~0ULL;
        *(volatile uint64_t *)(b + 0x215C7C8) = 0x10000ULL;
        *(volatile uint64_t *)(b + 0x215C7D0) = 0x10000ULL;
        *(volatile uint64_t *)(b + 0x215C7D8) = 0x10000ULL;
        *(volatile uint64_t *)(b + 0x215C7E0) = 0x10000ULL;
        breadcrumb("TRAP 0x1424e8 throttled");
    }
    // This verifier dladdr-checks live IMPs and walks the dyld image list.
    // Neutralize it directly instead of spoofing each newly hooked method.
    static void *uv = NULL;
    hook_if(moutai_base(), 0x54300, 0xD10383FF,
            (void *)fl_neutralized_void_call, &uv, "verifier");
    static void *ua1 = NULL, *ua2 = NULL;
    hook_if(moutai_base(), 0x57E08, 0x294D27E8,
            (void *)fl_neutralized_void_call, &ua1, "auditor1");
    hook_if(moutai_base(), 0x5B75C, 0x294223F6,
            (void *)fl_neutralized_void_call, &ua2, "auditor2");
    hook_if(moutai_base(), 0x77014, 0xD10503FF,
            (void *)fl_guard_decrypt_stub, (void **)&orig_decrypt_stub, "decryptStub");
    breadcrumb("EARLY done");
}

%hookf(CFDictionaryRef, CFNetworkCopySystemProxySettings) {
    CFDictionaryRef orig = %orig;
    if (!orig) return orig;
    NSMutableDictionary *dict = [(__bridge NSDictionary *)orig mutableCopy];
    CFRelease(orig);
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesHTTPEnable];
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesHTTPProxy];
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesHTTPPort];
    [dict removeObjectForKey:@"HTTPSEnable"];
    [dict removeObjectForKey:@"HTTPSProxy"];
    [dict removeObjectForKey:@"HTTPSPort"];
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesProxyAutoConfigEnable];
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesProxyAutoConfigURLString];
    NSDictionary *scoped = dict[@"__SCOPED__"];
    if ([scoped isKindOfClass:[NSDictionary class]]) {
        NSMutableDictionary *newScoped = [NSMutableDictionary dictionary];
        [scoped enumerateKeysAndObjectsUsingBlock:^(NSString *key, id obj, BOOL *stop) {
            if ([key rangeOfString:@"utun"].location != NSNotFound ||
                [key rangeOfString:@"tun"].location  != NSNotFound ||
                [key rangeOfString:@"tap"].location   != NSNotFound ||
                [key rangeOfString:@"ipsec"].location != NSNotFound ||
                [key rangeOfString:@"ppp"].location   != NSNotFound) {
                return;
            }
            newScoped[key] = obj;
        }];
        dict[@"__SCOPED__"] = newScoped;
    }
    return (__bridge_retained CFDictionaryRef)dict;
}

%hook NSString
- (id)yx_headerEncryptString {
    if (TraceLens_yxReenterGuard > 0) return %orig;
    patch_saved_imp(s_imp_encrypt);
    NSString *input = (NSString *)self;
    [[NSUserDefaults standardUserDefaults] setObject:input forKey:@"tweak_encrypt_input"];
    NSString *fixed = input;
    if ([fixed containsString:@"inject/1"])
        fixed = [fixed stringByReplacingOccurrencesOfString:@"inject/1" withString:@"inject/0"];
    if ([fixed containsString:@"root/1"])
        fixed = [fixed stringByReplacingOccurrencesOfString:@"root/1" withString:@"root/0"];
    if ([fixed containsString:@"proxy/1"])
        fixed = [fixed stringByReplacingOccurrencesOfString:@"proxy/1" withString:@"proxy/0"];
    if ([fixed containsString:@"debug/1"])
        fixed = [fixed stringByReplacingOccurrencesOfString:@"debug/1" withString:@"debug/0"];
    if (![fixed isEqualToString:input]) {
        if (TraceLensTraceLogLevel() >= 3) {
            LogMsg([NSString stringWithFormat:@"[ENCRYPT] bypass %@ -> %@", input, fixed]);
        }
        [[NSUserDefaults standardUserDefaults] setObject:fixed forKey:@"tweak_encrypt_fixed"];
        [[NSUserDefaults standardUserDefaults] synchronize];
        TraceLens_yxReenterGuard++;
        id out = [fixed yx_headerEncryptString];
        TraceLens_yxReenterGuard--;
        return out;
    }
    [[NSUserDefaults standardUserDefaults] synchronize];
    return %orig;
}
%end

%hook MassComnunicetion
- (void)howhareyou:(id)arg1 {
    LogMsg(@"🛡️ Logos howhareyou:");
    NSArray *syms = [NSThread callStackSymbols];
    if (syms.count > 10) syms = [syms subarrayWithRange:NSMakeRange(0, 10)];
    LogMsg([NSString stringWithFormat:@"  bt %@", [syms componentsJoinedByString:@" | "]]);
}
%end

%hook CONCuhcHdGGlGhKK
- (void)alertUserInMainThreadWithType:(int)type withMessage:(id)msg withTitle:(id)title {}
- (void)alertUserWithAction:(int)action withMessage:(id)msg withTitle:(id)title {}
- (void)addAlertHandlerToAlertQueueWithAction:(int)action message:(id)msg title:(id)title {}
- (void)doAbortAction { LogMsg(@"🛡️ BLOCKED doAbortAction"); }
- (void)checkPoliticIfExecute {}
%end

%hook dorUdgXxUlbzJOpA
+ (void)abortProcess { LogMsg(@"🛡️ Logos BLOCKED abortProcess"); }
%end

%hook YsoTwDufGghbEumc
+ (id)dangerousApps { return @[]; }
+ (id)dangerousEnvInfo { return @[]; }
+ (BOOL)isCycriptBinExist { return NO; }
%end

%hook RiskStub
+ (BOOL)checkRootStatusWithUserLocalScenario:(id)scenario { return NO; }
%end

static id fl_scene_configuration(id self, SEL _cmd, UISceneSession *session, id options) {
    NSString *name = @"Default Configuration";
    NSString *delegateName = nil;
    NSDictionary *manifest = [NSBundle mainBundle].infoDictionary[@"UIApplicationSceneManifest"];
    NSDictionary *roles = manifest[@"UIApplicationSceneConfigurations"];
    NSArray *configs = roles[@"UIWindowSceneSessionRoleApplication"];
    if ([configs isKindOfClass:[NSArray class]] && configs.count &&
        [configs[0] isKindOfClass:[NSDictionary class]]) {
        NSDictionary *config = configs[0];
        if ([config[@"UISceneConfigurationName"] isKindOfClass:[NSString class]])
            name = config[@"UISceneConfigurationName"];
        if ([config[@"UISceneDelegateClassName"] isKindOfClass:[NSString class]])
            delegateName = config[@"UISceneDelegateClassName"];
    }
    UISceneConfiguration *configuration =
        [UISceneConfiguration configurationWithName:name
                                       sessionRole:UIWindowSceneSessionRoleApplication];
    Class delegateClass = delegateName ? NSClassFromString(delegateName) : nil;
    if (delegateClass) configuration.delegateClass = delegateClass;
    LogMsg([NSString stringWithFormat:@"🧪 scene configuration name=%@ delegate=%@",
            name, delegateName]);
    return configuration;
}

static void fl_install_scene_configuration(void) {
    static int once = 0;
    if (once) return;
    once = 1;
    Class delegate = objc_getClass("AppDelegate");
    SEL selector = sel_registerName("application:configurationForConnectingSceneSession:options:");
    if (delegate && !class_getInstanceMethod(delegate, selector) &&
        class_addMethod(delegate, selector, (IMP)fl_scene_configuration, "@@:@@")) {
        LogMsg(@"🧪 installed scene configuration provider");
    }
}

%hook MTInstallUtils
+ (void)setInstalledWithClearInfo:(BOOL)v {}
%end
