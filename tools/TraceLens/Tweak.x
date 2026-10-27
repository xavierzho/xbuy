#import <UIKit/UIKit.h>
#import <os/log.h>
#import <CoreFoundation/CoreFoundation.h>
#import <CFNetwork/CFNetwork.h>
#import <WebKit/WebKit.h>
#import <objc/runtime.h>
#include <mach-o/dyld.h>
#include <dlfcn.h>
#include <string.h>

// Dopamine：Boot 仅 getenv；dyld 过滤延至 UIApplicationMain；Sciapodous +load 用 constructor(101) RET 跳过 dyld-init dladdr 扫描（ips 224143）。

static os_log_t tweakLog = NULL;
static void LogMsg(NSString *msg);
static void LogNoise(NSString *msg);
static _Thread_local int TraceLens_yxReenterGuard = 0;
static _Thread_local int TraceLens_dyldReentry = 0;

static uint32_t (*TraceLens_raw_dyld_image_count)(void);
static const char *(*TraceLens_raw_dyld_get_image_name)(uint32_t);
static const struct mach_header *(*TraceLens_raw_dyld_get_image_header)(uint32_t);

static void TraceLensResolveRawDyld(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        TraceLens_raw_dyld_image_count =
            (uint32_t (*)(void))dlsym(RTLD_DEFAULT, "_dyld_image_count");
        TraceLens_raw_dyld_get_image_name =
            (const char *(*)(uint32_t))dlsym(RTLD_DEFAULT, "_dyld_get_image_name");
        TraceLens_raw_dyld_get_image_header =
            (const struct mach_header *(*)(uint32_t))dlsym(RTLD_DEFAULT, "_dyld_get_image_header");
    });
}

static BOOL TweakShouldHideGetenv(const char *name) {
    if (!name) return NO;
    if (strncmp(name, "DYLD_", 5) == 0) return YES;
    return NO;
}

static BOOL TweakShouldHideDyldImage(const char *name) {
    if (!name || !name[0]) return NO;
    static const char *needles[] = {
        "TraceLens", "tracelens", "MobileSubstrate", "substrate",
        "ellekit", "libhooker", "systemhook", "libinjector",
        "TweakInject", "cycript", "frida", "dopamine", "procursus",
        NULL
    };
    for (int i = 0; needles[i]; i++) {
        if (strstr(name, needles[i]) != NULL) return YES;
    }
    return NO;
}

static const char *TraceLensDyldNameForVisibleIndex(uint32_t index, uint32_t *rawOut) {
    TraceLensResolveRawDyld();
    if (!TraceLens_raw_dyld_image_count || !TraceLens_raw_dyld_get_image_name) return NULL;

    uint32_t raw = TraceLens_raw_dyld_image_count();
    uint32_t visible = 0;
    for (uint32_t i = 0; i < raw; i++) {
        const char *name = TraceLens_raw_dyld_get_image_name(i);
        if (TweakShouldHideDyldImage(name)) continue;
        if (visible == index) {
            if (rawOut) *rawOut = i;
            return name;
        }
        visible++;
    }
    return NULL;
}

static const struct mach_header *TraceLensDyldHeaderForVisibleIndex(uint32_t index) {
    uint32_t raw = 0;
    if (!TraceLensDyldNameForVisibleIndex(index, &raw)) return NULL;
    if (!TraceLens_raw_dyld_get_image_header) return NULL;
    return TraceLens_raw_dyld_get_image_header(raw);
}

static void TraceLensInstallMainHooks(void);
static void TraceLensArmUIApplicationMainGate(void);
static _Atomic bool TraceLens_mainHooksInstalled = false;
static _Atomic bool TraceLens_sciapodousNuked = false;

extern void MSHookFunction(void *symbol, void *replace, void **result);

static void (*TraceLens_orig_sciapodous_load)(id, SEL);

static void TraceLens_sciapodous_load_replacement(id self, SEL _cmd) {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    os_log(tweakLog, "[TraceLens] Sciapodous +load RET (skip Bangcle dyld-init scan)");
    [[NSUserDefaults standardUserDefaults] setObject:@"load_nuked" forKey:@"tweak_sciapodous_status"];
    [[NSUserDefaults standardUserDefaults] synchronize];
}

static void TraceLensNukeSciapodousLoadIfNeeded(void) {
    if (TraceLens_sciapodousNuked) return;

    Class sciapodousClass = objc_getClass("Sciapodous");
    if (!sciapodousClass) return;

    Method loadMethod = class_getClassMethod(sciapodousClass, @selector(load));
    if (!loadMethod) return;

    void *target_addr = (void *)method_getImplementation(loadMethod);
    if (!target_addr) return;

    MSHookFunction(target_addr,
                   (void *)TraceLens_sciapodous_load_replacement,
                   (void **)&TraceLens_orig_sciapodous_load);
    TraceLens_sciapodousNuked = true;
    LogNoise([NSString stringWithFormat:@"Sciapodous +load RET @ %p", target_addr]);
}

__attribute__((constructor(101)))
static void TraceLensEarlySciapodousNuke(void) {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    TraceLensNukeSciapodousLoadIfNeeded();
}

static int (*TraceLens_orig_UIApplicationMain)(int argc, char **argv, NSString *principalClassName, NSString *defaultName);

static int TraceLens_hook_UIApplicationMain(int argc, char **argv, NSString *principalClassName, NSString *defaultName) {
    TraceLensInstallMainHooks();
    return TraceLens_orig_UIApplicationMain(argc, argv, principalClassName, defaultName);
}

static uint32_t (*TraceLens_orig_dyld_image_count)(void);
static const char *(*TraceLens_orig_dyld_get_image_name)(uint32_t);
static const struct mach_header *(*TraceLens_orig_dyld_get_image_header)(uint32_t);

static uint32_t TraceLens_hook_dyld_image_count(void) {
    if (TraceLens_dyldReentry > 0) {
        return TraceLens_orig_dyld_image_count ? TraceLens_orig_dyld_image_count() : 0;
    }
    TraceLensResolveRawDyld();
    if (!TraceLens_raw_dyld_image_count || !TraceLens_raw_dyld_get_image_name) {
        return TraceLens_orig_dyld_image_count ? TraceLens_orig_dyld_image_count() : 0;
    }
    TraceLens_dyldReentry++;
    uint32_t raw = TraceLens_raw_dyld_image_count();
    uint32_t visible = 0;
    for (uint32_t i = 0; i < raw; i++) {
        const char *name = TraceLens_raw_dyld_get_image_name(i);
        if (!TweakShouldHideDyldImage(name)) visible++;
    }
    TraceLens_dyldReentry--;
    return visible;
}

static const char *TraceLens_hook_dyld_get_image_name(uint32_t index) {
    if (TraceLens_dyldReentry > 0) {
        return TraceLens_orig_dyld_get_image_name ? TraceLens_orig_dyld_get_image_name(index) : NULL;
    }
    TraceLens_dyldReentry++;
    const char *name = TraceLensDyldNameForVisibleIndex(index, NULL);
    TraceLens_dyldReentry--;
    if (name) return name;
    return TraceLens_orig_dyld_get_image_name ? TraceLens_orig_dyld_get_image_name(index) : NULL;
}

static const struct mach_header *TraceLens_hook_dyld_get_image_header(uint32_t index) {
    if (TraceLens_dyldReentry > 0) {
        return TraceLens_orig_dyld_get_image_header ? TraceLens_orig_dyld_get_image_header(index) : NULL;
    }
    TraceLens_dyldReentry++;
    const struct mach_header *hdr = TraceLensDyldHeaderForVisibleIndex(index);
    TraceLens_dyldReentry--;
    if (hdr) return hdr;
    return TraceLens_orig_dyld_get_image_header ? TraceLens_orig_dyld_get_image_header(index) : NULL;
}

static void TraceLensInstallLateDyldFilter(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        TraceLensResolveRawDyld();
        MSHookFunction((void *)_dyld_image_count,
                       (void *)TraceLens_hook_dyld_image_count,
                       (void **)&TraceLens_orig_dyld_image_count);
        MSHookFunction((void *)_dyld_get_image_name,
                       (void *)TraceLens_hook_dyld_get_image_name,
                       (void **)&TraceLens_orig_dyld_get_image_name);
        MSHookFunction((void *)_dyld_get_image_header,
                       (void *)TraceLens_hook_dyld_get_image_header,
                       (void **)&TraceLens_orig_dyld_get_image_header);
        LogNoise(@"TraceLens: dyld image filter armed (post-dyld-init)");
    });
}

%group TraceLensBoot
%hookf(char *, getenv, const char *name) {
    if (TweakShouldHideGetenv(name)) {
        return NULL;
    }
    return %orig(name);
}

%end

%group TraceLensMain

// 前向声明 App 私有方法
@interface NSString (MoutaiHook)
- (id)yx_headerEncryptString;
@end

@interface MassComnunicetion : NSObject
- (void)howhareyou:(id)arg1;
@end

static void LogMsg(NSString *msg) {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    os_log(tweakLog, "[TraceLens] %{public}@", msg);
}

// tracelens_log_level（NSUserDefaults，宿主 com.moutai.mall）：1=api 2=standard 3=full，默认 1
// api：仅 [AFN] 业务请求/响应 + [PAY] payapi；无 setHeader/[STACK]/[JSON]/WK/UI 噪音
static int TraceLensLogLevel(void) {
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

static void LogNoise(NSString *msg) {
    if (TraceLensLogLevel() >= 3) LogMsg(msg);
}

static BOOL TraceLensUrlIsCaptureTarget(NSString *urlText) {
    if (urlText.length == 0) return NO;
    return [urlText containsString:@"moutai519.com.cn"] ||
           [urlText containsString:@"payapi.moutai519"] ||
           [urlText containsString:@"mcgw.alipay.com"];
}

static NSString *TweakFlattenLogText(NSString *text) {
    if (!text) return @"<nil>";

    text = [text stringByReplacingOccurrencesOfString:@"\r" withString:@" "];
    text = [text stringByReplacingOccurrencesOfString:@"\n" withString:@" "];
    text = [text stringByReplacingOccurrencesOfString:@"\t" withString:@" "];
    return text;
}

// ===========================================================
// 1. 代理/VPN 绕过 (完整版)
// ===========================================================
%hookf(CFDictionaryRef, CFNetworkCopySystemProxySettings) {
    CFDictionaryRef orig = %orig;
    if (!orig) return orig;

    NSMutableDictionary *dict = [(__bridge NSDictionary *)orig mutableCopy];
    CFRelease(orig);

    // HTTP 代理
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesHTTPEnable];
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesHTTPProxy];
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesHTTPPort];

    // HTTPS 代理
    [dict removeObjectForKey:@"HTTPSEnable"];
    [dict removeObjectForKey:@"HTTPSProxy"];
    [dict removeObjectForKey:@"HTTPSPort"];

    // PAC 自动代理
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesProxyAutoConfigEnable];
    [dict removeObjectForKey:(__bridge NSString *)kCFNetworkProxiesProxyAutoConfigURLString];

    // 过滤 __SCOPED__ 中的 VPN/TUN 接口
    NSDictionary *scoped = dict[@"__SCOPED__"];
    if ([scoped isKindOfClass:[NSDictionary class]]) {
        NSMutableDictionary *newScoped = [NSMutableDictionary dictionary];
        [scoped enumerateKeysAndObjectsUsingBlock:^(NSString *key, id obj, BOOL *stop) {
            if ([key rangeOfString:@"utun"].location != NSNotFound ||
                [key rangeOfString:@"tun"].location  != NSNotFound ||
                [key rangeOfString:@"tap"].location   != NSNotFound ||
                [key rangeOfString:@"ipsec"].location != NSNotFound ||
                [key rangeOfString:@"ppp"].location   != NSNotFound) {
                LogNoise([NSString stringWithFormat:@"🚫 Filtering VPN interface: %@", key]);
                return;
            }
            newScoped[key] = obj;
        }];
        dict[@"__SCOPED__"] = newScoped;
    }

    LogNoise(@"✅ Proxy/VPN settings cleaned");
    return (__bridge_retained CFDictionaryRef)dict;
}

// ===========================================================
// 2. 注入检测绕过与多重指标清零
//    Hook yx_headerEncryptString, 将可能导致越狱/代理/注入检测的指标强制清零，并写入 NSUserDefaults
// ===========================================================
%hook NSString
- (id)yx_headerEncryptString {
    if (TraceLens_yxReenterGuard > 0) {
        return %orig;
    }

    NSString *input = (NSString *)self;
    
    // 记录原始输入到 NSUserDefaults
    [[NSUserDefaults standardUserDefaults] setObject:input forKey:@"tweak_encrypt_input"];
    
    NSString *fixed = input;
    if ([fixed containsString:@"inject/1"]) {
        fixed = [fixed stringByReplacingOccurrencesOfString:@"inject/1" withString:@"inject/0"];
    }
    if ([fixed containsString:@"root/1"]) {
        fixed = [fixed stringByReplacingOccurrencesOfString:@"root/1" withString:@"root/0"];
    }
    if ([fixed containsString:@"proxy/1"]) {
        fixed = [fixed stringByReplacingOccurrencesOfString:@"proxy/1" withString:@"proxy/0"];
    }
    if ([fixed containsString:@"debug/1"]) {
        fixed = [fixed stringByReplacingOccurrencesOfString:@"debug/1" withString:@"debug/0"];
    }
    
    if (![fixed isEqualToString:input]) {
        LogNoise([NSString stringWithFormat:@"🛡️ Encrypt bypass: %@ -> %@", input, fixed]);
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

// ===========================================================
// 2.1 越狱与安全环境检测绕过
//    Hook -[MassComnunicetion howhareyou:], 拦截并直接忽略其底层的安全检测逻辑
// ===========================================================
%hook MassComnunicetion
- (void)howhareyou:(id)arg1 {
    LogNoise(@"🛡️ MassComnunicetion howhareyou: intercepted, bypassing jailbreak and security checks.");
}
%end

// Sciapodous +load：constructor(101) RET（见 TraceLensNukeSciapodousLoadIfNeeded），勿 hook 0x4864/0x4A8C

// ===========================================================
// 4. 彻底屏蔽反调试与设备异常的弹窗来源类 (CONCuhcHdGGlGhKK)
//    由 Frida 测试证实，该类会发起主线程设备异常弹窗并强制 _exit(0)
// ===========================================================
%hook CONCuhcHdGGlGhKK

- (void)alertUserInMainThreadWithType:(int)type withMessage:(id)msg withTitle:(id)title {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    os_log(tweakLog, "[TraceLens] 🛡️ BLOCKED CONCuhcHdGGlGhKK alertUserInMainThreadWithType");
}

- (void)alertUserWithAction:(int)action withMessage:(id)msg withTitle:(id)title {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    os_log(tweakLog, "[TraceLens] 🛡️ BLOCKED CONCuhcHdGGlGhKK alertUserWithAction");
}

- (void)addAlertHandlerToAlertQueueWithAction:(int)action message:(id)msg title:(id)title {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    os_log(tweakLog, "[TraceLens] 🛡️ BLOCKED CONCuhcHdGGlGhKK addAlertHandler");
}

- (void)doAbortAction {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    os_log(tweakLog, "[TraceLens] 🛡️ BLOCKED CONCuhcHdGGlGhKK doAbortAction");
}

- (void)checkPoliticIfExecute {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    os_log(tweakLog, "[TraceLens] 🛡️ BLOCKED CONCuhcHdGGlGhKK checkPoliticIfExecute");
}

%end

// ===========================================================
// 5. API 请求/响应日志
//    覆盖 NSURLSessionTask、NSMutableURLRequest 和常见 completion 回调。
// ===========================================================
static NSString *TweakPreviewData(NSData *data) {
    if (!data || data.length == 0) return @"<empty>";

    NSUInteger limit = MIN((NSUInteger)4096, data.length);
    NSData *slice = [data subdataWithRange:NSMakeRange(0, limit)];
    NSString *text = [[NSString alloc] initWithData:slice encoding:NSUTF8StringEncoding];

    if (!text) {
        return [NSString stringWithFormat:@"<binary %lu bytes>", (unsigned long)data.length];
    }
    text = TweakFlattenLogText(text);
    if (data.length > limit) {
        return [text stringByAppendingFormat:@"... <truncated %lu/%lu bytes>",
                (unsigned long)limit,
                (unsigned long)data.length];
    }
    return text;
}

static BOOL TweakLooksLikeEncryptedGateway(NSURL *url, NSData *body) {
    NSString *urlText = url.absoluteString ?: @"";
    if ([urlText containsString:@"123.56.205.151:6279"] || [urlText containsString:@"/2/2"]) {
        return YES;
    }

    NSString *bodyText = TweakPreviewData(body);
    if (bodyText.length < 80 || [bodyText containsString:@"{"] || [bodyText containsString:@"["]) {
        return NO;
    }

    NSCharacterSet *base64Chars = [NSCharacterSet characterSetWithCharactersInString:@"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="];
    return [[bodyText stringByTrimmingCharactersInSet:base64Chars] length] == 0;
}

static void TweakLogScriptHits(NSString *tag, NSURL *url, NSData *data) {
    if (data.length == 0) return;

    NSString *urlText = url.absoluteString ?: @"";
    BOOL scriptLike = [urlText containsString:@".js"] ||
                      [urlText containsString:@"chunk"] ||
                      [urlText containsString:@"bundle"] ||
                      [urlText containsString:@"mt-wap"];
    if (!scriptLike) return;

    NSString *text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    if (text.length == 0) return;

    NSArray<NSString *> *keys = @[
        @"farRightBtn",
        @"已售罄",
        @"purchaseInfo",
        @"forbiddenBuyDesc",
        @"retailPriceShowTime",
        @"isJumpStraight",
        @"shoppingcart",
        @"getVerifyStatus",
        @"priorityRecordId",
        @"cartCount",
        @"20:00"
    ];

    NSUInteger emitted = 0;
    for (NSString *key in keys) {
        NSRange hit = [text rangeOfString:key];
        if (hit.location == NSNotFound) continue;

        NSUInteger start = hit.location > 900 ? hit.location - 900 : 0;
        NSUInteger end = MIN(text.length, hit.location + 1800);
        NSString *snippet = [text substringWithRange:NSMakeRange(start, end - start)];
        LogNoise([NSString stringWithFormat:@"[JS_HIT] %@ url=%@ key=%@ pos=%lu len=%lu snippet=%@",
                tag,
                urlText,
                key,
                (unsigned long)hit.location,
                (unsigned long)text.length,
                TweakFlattenLogText(snippet)]);

        emitted++;
        if (emitted >= 8) break;
    }
}

static void TweakLogStack(NSString *tag) {
    if (TraceLensLogLevel() < 3) return;
    NSArray *symbols = [NSThread callStackSymbols];
    NSUInteger maxFrames = MIN((NSUInteger)18, symbols.count);
    for (NSUInteger i = 0; i < maxFrames; i++) {
        LogMsg([NSString stringWithFormat:@"[STACK] %@ #%lu %@", tag, (unsigned long)i, symbols[i]]);
    }
}

static void TweakLogRequest(NSString *tag, NSURLRequest *request) {
    if (!request) return;
    int lvl = TraceLensLogLevel();
    if (lvl < 2) return;
    NSString *urlText = request.URL.absoluteString ?: @"";
    if (lvl < 3 && !TraceLensUrlIsCaptureTarget(urlText)) return;

    LogMsg([NSString stringWithFormat:@"[API] %@ %@ %@",
            tag,
            request.HTTPMethod ?: @"GET",
            request.URL.absoluteString ?: @"<nil-url>"]);

    NSDictionary *headers = request.allHTTPHeaderFields;
    if (headers.count > 0) {
        LogMsg([NSString stringWithFormat:@"[API] %@ headers=%@", tag, headers]);
    }

    NSData *body = request.HTTPBody;
    if (body.length > 0) {
        LogMsg([NSString stringWithFormat:@"[API] %@ body=%@", tag, TweakPreviewData(body)]);
        if (TweakLooksLikeEncryptedGateway(request.URL, body)) {
            TweakLogStack([NSString stringWithFormat:@"%@.encryptedGateway", tag]);
        }
    }

    NSInputStream *bodyStream = request.HTTPBodyStream;
    if (bodyStream) {
        LogMsg([NSString stringWithFormat:@"[API] %@ bodyStream=%@", tag, bodyStream]);
    }
}

static void TweakLogResponse(NSString *tag, NSURLResponse *response, NSData *data, NSError *error) {
    NSHTTPURLResponse *http = [response isKindOfClass:[NSHTTPURLResponse class]] ? (NSHTTPURLResponse *)response : nil;
    int lvl = TraceLensLogLevel();
    NSString *urlText = response.URL.absoluteString ?: @"";
    if (lvl < 2) {
        if (![urlText containsString:@"payapi.moutai519"]) return;
        LogMsg([NSString stringWithFormat:@"[PAY] response status=%ld url=%@ body=%@ error=%@",
                (long)http.statusCode,
                urlText,
                data.length > 0 ? TweakPreviewData(data) : @"<empty>",
                error ?: (NSError *)@"<nil>"]);
        return;
    }
    if (lvl < 3 && !TraceLensUrlIsCaptureTarget(urlText)) return;

    LogMsg([NSString stringWithFormat:@"[API] %@ response status=%ld url=%@ error=%@",
            tag,
            (long)http.statusCode,
            response.URL.absoluteString ?: @"<nil-url>",
            error ?: (NSError *)@"<nil>"]);

    if (http.allHeaderFields.count > 0) {
        LogMsg([NSString stringWithFormat:@"[API] %@ responseHeaders=%@", tag, http.allHeaderFields]);
    }
    if (data.length > 0) {
        LogMsg([NSString stringWithFormat:@"[API] %@ responseBody=%@", tag, TweakPreviewData(data)]);
        TweakLogScriptHits(tag, response.URL, data);
    }
}

%hook NSMutableURLRequest

- (void)setHTTPMethod:(NSString *)method {
    LogNoise([NSString stringWithFormat:@"[API] setHTTPMethod %@ url=%@", method, self.URL.absoluteString]);
    %orig;
}

- (void)setURL:(NSURL *)URL {
    LogNoise([NSString stringWithFormat:@"[API] setURL %@", URL.absoluteString]);
    %orig;
}

- (void)setHTTPBody:(NSData *)body {
    LogNoise([NSString stringWithFormat:@"[API] setHTTPBody url=%@ body=%@",
            self.URL.absoluteString ?: @"<nil-url>",
            TweakPreviewData(body)]);
    if (TweakLooksLikeEncryptedGateway(self.URL, body)) {
        TweakLogStack(@"setHTTPBody.encryptedGateway");
    }
    %orig;
}

- (void)setHTTPBodyStream:(NSInputStream *)inputStream {
    LogNoise([NSString stringWithFormat:@"[API] setHTTPBodyStream url=%@ stream=%@",
            self.URL.absoluteString ?: @"<nil-url>",
            inputStream]);
    %orig;
}

- (void)setValue:(NSString *)value forHTTPHeaderField:(NSString *)field {
    LogNoise([NSString stringWithFormat:@"[API] setHeader %@=%@ url=%@",
            field,
            value,
            self.URL.absoluteString ?: @"<nil-url>"]);
    %orig;
}

- (void)addValue:(NSString *)value forHTTPHeaderField:(NSString *)field {
    LogNoise([NSString stringWithFormat:@"[API] addHeader %@=%@ url=%@",
            field,
            value,
            self.URL.absoluteString ?: @"<nil-url>"]);
    %orig;
}

%end

%hook NSURLSessionTask

- (void)resume {
    NSURLRequest *request = nil;
    @try {
        request = [self valueForKey:@"currentRequest"];
        if (!request) request = [self valueForKey:@"originalRequest"];
    } @catch (NSException *e) {}

    if (TraceLensLogLevel() < 2 && request) {
        NSString *u = request.URL.absoluteString ?: @"";
        if ([u containsString:@"payapi.moutai519"]) {
            LogMsg([NSString stringWithFormat:@"[PAY] %@ %@ headers=%@ body=%@",
                    request.HTTPMethod ?: @"GET",
                    u,
                    request.allHTTPHeaderFields.count > 0 ? request.allHTTPHeaderFields : @{},
                    TweakPreviewData(request.HTTPBody)]);
        }
    } else {
        TweakLogRequest(@"task.resume", request);
    }
    %orig;
}

%end

%hook NSURLSession

- (NSURLSessionDataTask *)dataTaskWithRequest:(NSURLRequest *)request
                            completionHandler:(void (^)(NSData *data, NSURLResponse *response, NSError *error))completionHandler {
    TweakLogRequest(@"dataTaskWithRequest", request);

    return %orig(request, ^(NSData *data, NSURLResponse *response, NSError *error) {
        TweakLogResponse(@"dataTaskWithRequest", response, data, error);
        if (completionHandler) completionHandler(data, response, error);
    });
}

- (NSURLSessionDataTask *)dataTaskWithURL:(NSURL *)url
                        completionHandler:(void (^)(NSData *data, NSURLResponse *response, NSError *error))completionHandler {
    LogNoise([NSString stringWithFormat:@"[API] dataTaskWithURL %@", url.absoluteString]);

    return %orig(url, ^(NSData *data, NSURLResponse *response, NSError *error) {
        TweakLogResponse(@"dataTaskWithURL", response, data, error);
        if (completionHandler) completionHandler(data, response, error);
    });
}

- (NSURLSessionUploadTask *)uploadTaskWithRequest:(NSURLRequest *)request
                                         fromData:(NSData *)bodyData
                                completionHandler:(void (^)(NSData *data, NSURLResponse *response, NSError *error))completionHandler {
    TweakLogRequest(@"uploadTaskWithRequest", request);
    if (bodyData.length > 0) {
        LogNoise([NSString stringWithFormat:@"[API] uploadTask body=%@", TweakPreviewData(bodyData)]);
        if (TweakLooksLikeEncryptedGateway(request.URL, bodyData)) {
            TweakLogStack(@"uploadTask.encryptedGateway");
        }
    }

    return %orig(request, bodyData, ^(NSData *data, NSURLResponse *response, NSError *error) {
        TweakLogResponse(@"uploadTaskWithRequest", response, data, error);
        if (completionHandler) completionHandler(data, response, error);
    });
}

%end

%hook NSJSONSerialization

+ (NSData *)dataWithJSONObject:(id)obj options:(NSJSONWritingOptions)opt error:(NSError **)error {
    NSData *data = %orig;
    NSString *preview = TweakPreviewData(data);

    if ([obj isKindOfClass:[NSDictionary class]] || [obj isKindOfClass:[NSArray class]]) {
        BOOL interesting = [preview containsString:@"api"] ||
                           [preview containsString:@"xhr"] ||
                           [preview containsString:@"url"] ||
                           [preview containsString:@"item"] ||
                           [preview containsString:@"mall"] ||
                           [preview containsString:@"order"] ||
                           [preview containsString:@"reserve"] ||
                           [preview containsString:@"reservation"] ||
                           [preview containsString:@"session"] ||
                           [preview containsString:@"act"] ||
                           [preview containsString:@"goods"] ||
                           [preview containsString:@"product"] ||
                           [preview containsString:@"shop"] ||
                           [preview containsString:@"user"] ||
                           [preview containsString:@"token"];

        if (interesting) {
            LogNoise([NSString stringWithFormat:@"[JSON] dataWithJSONObject class=%@ json=%@",
                    NSStringFromClass([obj class]),
                    preview]);
            TweakLogStack(@"NSJSONSerialization.interesting");
        }
    }

    return data;
}

+ (id)JSONObjectWithData:(NSData *)data options:(NSJSONReadingOptions)opt error:(NSError **)error {
    id obj = %orig;
    NSString *preview = TweakPreviewData(data);

    BOOL interesting = [preview containsString:@"api"] ||
                       [preview containsString:@"xhr"] ||
                       [preview containsString:@"url"] ||
                       [preview containsString:@"item"] ||
                       [preview containsString:@"mall"] ||
                       [preview containsString:@"order"] ||
                       [preview containsString:@"reserve"] ||
                       [preview containsString:@"reservation"] ||
                       [preview containsString:@"goods"] ||
                       [preview containsString:@"product"] ||
                       [preview containsString:@"shop"] ||
                       [preview containsString:@"user"] ||
                       [preview containsString:@"token"];

    if (interesting) {
        LogNoise([NSString stringWithFormat:@"[JSON] JSONObjectWithData class=%@ json=%@",
                obj ? NSStringFromClass([obj class]) : @"<nil>",
                preview]);
        TweakLogStack(@"NSJSONSerialization.parseInteresting");
    }

    return obj;
}

%end

// ===========================================================
// 7. AFNetworking 上层参数日志
//    在 request serializer / session manager 阶段抓 URLString 与 parameters。
// ===========================================================
static NSString *TweakPreviewObject(id obj) {
    if (!obj) return @"<nil>";

    NSString *text = TweakFlattenLogText([NSString stringWithFormat:@"%@", obj]);
    NSUInteger limit = MIN((NSUInteger)12000, text.length);
    NSString *slice = [text substringToIndex:limit];
    if (text.length > limit) {
        return [slice stringByAppendingFormat:@"... <truncated %lu/%lu chars>",
                (unsigned long)limit,
                (unsigned long)text.length];
    }
    return slice;
}

static BOOL TweakTextLooksLikeTimeField(NSString *text) {
    if (text.length == 0) return NO;

    NSString *lower = [text lowercaseString];
    return [lower containsString:@"20:00"] ||
           [lower containsString:@"sale"] ||
           [lower containsString:@"sell"] ||
           [lower containsString:@"time"] ||
           [lower containsString:@"start"] ||
           [lower containsString:@"end"] ||
           [lower containsString:@"button"] ||
           [lower containsString:@"stock"] ||
           [lower containsString:@"reserve"] ||
           [lower containsString:@"reservation"] ||
           [lower containsString:@"purchaseinfo"] ||
           [lower containsString:@"priorityrecordid"] ||
           [lower containsString:@"userverifystatus"] ||
           [lower containsString:@"allowedcounts"] ||
           [lower containsString:@"notinshop"] ||
           [text containsString:@"投放"] ||
           [text containsString:@"预约"] ||
           [text containsString:@"库存"] ||
           [text containsString:@"可约"] ||
           [text containsString:@"不可"] ||
           [text containsString:@"purchaseInfoMap"] ||
           [text containsString:@"forbiddenBuyDesc"] ||
           [text containsString:@"priorityRecordId"];
}

static BOOL TweakLooksLikeItemResponse(NSString *urlText, id responseObject) {
    NSString *preview = TweakPreviewObject(responseObject);
    return [urlText containsString:@"/xhr/front/mall/item"] ||
           [preview containsString:@"purchaseInfoMap"] ||
           [preview containsString:@"forbiddenBuyDesc"] ||
           [preview containsString:@"priorityRecordId"] ||
           [preview containsString:@"allowedCounts"] ||
           [preview containsString:@"userVerifyStatus"] ||
           [preview containsString:@"notInShopTimDesc"];
}

static void TweakScanObjectForTimeFields(id obj, NSString *path, NSUInteger depth) {
    if (TraceLensLogLevel() < 3) return;
    if (!obj || depth > 8) return;

    if ([obj isKindOfClass:[NSDictionary class]]) {
        NSDictionary *dict = (NSDictionary *)obj;
        for (id key in dict) {
            NSString *keyText = [NSString stringWithFormat:@"%@", key];
            id value = dict[key];
            NSString *nextPath = path.length > 0 ? [path stringByAppendingFormat:@".%@", keyText] : keyText;

            BOOL keyHit = TweakTextLooksLikeTimeField(keyText);
            BOOL valueText = [value isKindOfClass:[NSString class]] ||
                             [value isKindOfClass:[NSNumber class]] ||
                             [value isKindOfClass:[NSDate class]];
            if (keyHit || (valueText && TweakTextLooksLikeTimeField([NSString stringWithFormat:@"%@", value]))) {
                LogMsg([NSString stringWithFormat:@"[FIELD] %@ = %@", nextPath, TweakPreviewObject(value)]);
            }

            if ([value isKindOfClass:[NSDictionary class]] || [value isKindOfClass:[NSArray class]]) {
                TweakScanObjectForTimeFields(value, nextPath, depth + 1);
            }
        }
        return;
    }

    if ([obj isKindOfClass:[NSArray class]]) {
        NSArray *array = (NSArray *)obj;
        NSUInteger count = MIN((NSUInteger)array.count, (NSUInteger)80);
        for (NSUInteger i = 0; i < count; i++) {
            id value = array[i];
            NSString *nextPath = [path stringByAppendingFormat:@"[%lu]", (unsigned long)i];
            if ([value isKindOfClass:[NSDictionary class]] || [value isKindOfClass:[NSArray class]]) {
                TweakScanObjectForTimeFields(value, nextPath, depth + 1);
            } else if (TweakTextLooksLikeTimeField([NSString stringWithFormat:@"%@", value])) {
                LogMsg([NSString stringWithFormat:@"[FIELD] %@ = %@", nextPath, TweakPreviewObject(value)]);
            }
        }
    }
}

%hook AFHTTPRequestSerializer

- (NSMutableURLRequest *)requestWithMethod:(NSString *)method
                                 URLString:(NSString *)URLString
                                parameters:(id)parameters
                                     error:(NSError **)error {
    LogNoise([NSString stringWithFormat:@"[AFN] requestWithMethod %@ %@", method, URLString]);
    if (parameters) {
        LogNoise([NSString stringWithFormat:@"[AFN] requestWithMethod parameters=%@", TweakPreviewObject(parameters)]);
    }
    return %orig;
}

%end

%hook AFJSONRequestSerializer

- (NSURLRequest *)requestBySerializingRequest:(NSURLRequest *)request
                               withParameters:(id)parameters
                                        error:(NSError **)error {
    LogNoise([NSString stringWithFormat:@"[AFN] JSON serialize %@ %@",
            request.HTTPMethod ?: @"<nil-method>",
            request.URL.absoluteString ?: @"<nil-url>"]);
    if (parameters) {
        LogNoise([NSString stringWithFormat:@"[AFN] JSON parameters=%@", TweakPreviewObject(parameters)]);
    }
    return %orig;
}

%end

%hook AFHTTPSessionManager

- (NSURLSessionDataTask *)dataTaskWithHTTPMethod:(NSString *)method
                                       URLString:(NSString *)URLString
                                      parameters:(id)parameters
                                         headers:(NSDictionary *)headers
                                  uploadProgress:(void (^)(NSProgress *uploadProgress))uploadProgress
                                downloadProgress:(void (^)(NSProgress *downloadProgress))downloadProgress
                                         success:(void (^)(NSURLSessionDataTask *task, id responseObject))success
                                         failure:(void (^)(NSURLSessionDataTask *task, NSError *error))failure {
    LogMsg([NSString stringWithFormat:@"[AFN] dataTaskWithHTTPMethod %@ %@", method, URLString]);
    if (parameters) {
        LogMsg([NSString stringWithFormat:@"[AFN] dataTask parameters=%@", TweakPreviewObject(parameters)]);
    }
    if (headers.count > 0) {
        LogMsg([NSString stringWithFormat:@"[AFN] dataTask headers=%@", TweakPreviewObject(headers)]);
    }

    return %orig(method, URLString, parameters, headers, uploadProgress, downloadProgress,
                 ^(NSURLSessionDataTask *task, id responseObject) {
                     LogMsg([NSString stringWithFormat:@"[AFN] success %@ response=%@",
                             URLString,
                             TweakPreviewObject(responseObject)]);
                     if (TweakLooksLikeItemResponse(URLString ?: @"", responseObject)) {
                         LogNoise([NSString stringWithFormat:@"[AFN_ITEM] %@ response=%@",
                                 URLString ?: @"<nil-url>",
                                 TweakPreviewObject(responseObject)]);
                     }
                     TweakScanObjectForTimeFields(responseObject, URLString ?: @"<nil-url>", 0);
                     if (success) success(task, responseObject);
                 },
                 ^(NSURLSessionDataTask *task, NSError *error) {
                     LogMsg([NSString stringWithFormat:@"[AFN] failure %@ error=%@", URLString, error]);
                     if (failure) failure(task, error);
                 });
}

%end

// ===========================================================
// 8. WKWebView / JSBridge 日志
//    按 H5 URL、JS 执行、bridge 注册、script message body 查按钮状态。
// ===========================================================
static BOOL TweakIsInterestingH5Text(NSString *text) {
    if (text.length == 0) return NO;

    return [text containsString:@"moutai519"] ||
           [text containsString:@"reservation"] ||
           [text containsString:@"reserve"] ||
           [text containsString:@"shop"] ||
           [text containsString:@"store"] ||
           [text containsString:@"item"] ||
           [text containsString:@"goods"] ||
           [text containsString:@"product"] ||
           [text containsString:@"order"] ||
           [text containsString:@"submit"] ||
           [text containsString:@"button"] ||
           [text containsString:@"disable"] ||
           [text containsString:@"enable"] ||
           [text containsString:@"disabled"] ||
           [text containsString:@"can"] ||
           [text containsString:@"status"] ||
           [text containsString:@"WKUNLOCK"] ||
           [text containsString:@"time"] ||
           [text containsString:@"start"] ||
           [text containsString:@"end"] ||
           [text containsString:@"sale"] ||
           [text containsString:@"20:00"] ||
           [text containsString:@"库存"] ||
           [text containsString:@"预约"] ||
           [text containsString:@"投放"] ||
           [text containsString:@"门店"] ||
           [text containsString:@"不可"] ||
           [text containsString:@"可约"];
}

static NSString *TweakPreviewString(NSString *text) {
    if (!text) return @"<nil>";

    NSUInteger limit = MIN((NSUInteger)12000, text.length);
    NSString *slice = [text substringToIndex:limit];
    if (text.length > limit) {
        return [slice stringByAppendingFormat:@"... <truncated %lu/%lu chars>",
                (unsigned long)limit,
                (unsigned long)text.length];
    }
    return slice;
}

static NSString *TraceLensEarlyWebpackScript(void) {
    return @"(function(){"
           "if(window.__TraceLensEarlyInstalled)return;"
           "window.__TraceLensEarlyInstalled=1;"
           "window.__TraceLensWebpackModules=window.__TraceLensWebpackModules||{};"
           "window.__TraceLensWebpackPushes=window.__TraceLensWebpackPushes||[];"
           "window.__TraceLensNetEvents=window.__TraceLensNetEvents||[];"
           "function netHit(u,t){return /\\/xhr\\/front\\/mall\\/item|purchaseInfo|detailV2|checkRushPurchase|purchaseInfoMap|forbiddenBuyDesc|priorityRecordId|userVerifyStatus|allowedCounts|notInShop/i.test((u||'')+' '+(t||''));}"
           "function netLog(o){try{if(window.__TraceLensNetEvents.length<120)window.__TraceLensNetEvents.push(o);}catch(e){}}"
           "function cnTime(x){try{var d=new Date(Number(x)+28800000);return d.toISOString().slice(0,19).replace('T',' ');}catch(e){return String(x);}}"
           "function netSummarize(kind,u,status,t){try{var j=JSON.parse(t),d=j&&j.data;if(!d)return;if(/purchaseInfoV2/i.test(u)||d.purchaseInfoMap){var sk=[],m=d.purchaseInfoMap||{};Object.keys(m).slice(0,12).forEach(function(k){var p=(m[k]&&m[k].purchaseInfo)||{};sk.push({mapKey:k,skuId:p.skuId,inventory:p.inventory,presellInventory:p.presellInventory,canAddCart:p.canAddCart,forbiddenBuyDesc:p.forbiddenBuyDesc,limitCount:p.limitCount,inDeliveryArea:p.inDeliveryArea,disable:p.disable,priorityRecordId:p.priorityRecordId,itemPriorityActId:p.itemPriorityActId,itemPriorityActV2Switch:p.itemPriorityActV2Switch,startTimeList:p.startTimeList||[],startTimes:(p.startTimeList||[]).map(cnTime)});});netLog({tag:'PURCHASE_SUMMARY',kind:kind,url:u,status:status,itemId:d.itemId,offline:d.offline,showSaleUnit:d.showSaleUnit,nationWide:d.nationWide,skus:sk,ts:Date.now()});}else if(/detailV2/i.test(u)||d.item){var it=d.item||{},sm=it.skuMap||{},sk2=[];Object.keys(sm).slice(0,12).forEach(function(k){var s=sm[k]||{};sk2.push({mapKey:k,skuId:s.skuId,skuName:s.skuName,spuId:s.spuId,saleModel:s.saleModel,status:s.status,retailPriceShowTime:s.retailPriceShowTime,retailPriceShowText:s.retailPriceShowText});});netLog({tag:'DETAIL_SUMMARY',kind:kind,url:u,status:status,hot:d.hot,spuId:it.spuId,defaultSkuId:it.defaultSkuId,primarySkuId:it.primarySkuId,saleModel:it.saleModel,status:it.status,price:it.price,skus:sk2,ts:Date.now()});}}catch(e){if(/purchaseInfoV2|detailV2/i.test(u||''))netLog({tag:'SUMMARY_ERR',kind:kind,url:u,error:String(e),ts:Date.now()});}}"
           "try{if(window.fetch&&!window.fetch.__tlNetWrapped){var oldFetch=window.fetch;var f=function(input,init){var u=(typeof input==='string')?input:(input&&input.url)||'',m=(init&&init.method)||'GET',b=(init&&init.body)?String(init.body).slice(0,800):'';if(netHit(u,b))netLog({tag:'FETCH_REQ',url:u,method:m,body:b,ts:Date.now()});return oldFetch.apply(this,arguments).then(function(resp){try{var c=resp.clone();c.text().then(function(t){if(netHit(u,t)){netLog({tag:'FETCH_RES',url:u,status:resp.status,body:String(t).slice(0,4000),ts:Date.now()});netSummarize('fetch',u,resp.status,String(t));}});}catch(e){}return resp;});};f.__tlNetWrapped=1;window.fetch=f;}}catch(e){netLog({tag:'FETCH_WRAP_ERR',error:String(e),ts:Date.now()});}"
           "try{if(window.XMLHttpRequest&&!window.XMLHttpRequest.prototype.__tlNetWrapped){var oldOpen=XMLHttpRequest.prototype.open,oldSend=XMLHttpRequest.prototype.send;XMLHttpRequest.prototype.open=function(method,url){this.__tlMethod=method;this.__tlUrl=String(url||'');return oldOpen.apply(this,arguments);};XMLHttpRequest.prototype.send=function(body){var xhr=this,b=body?String(body).slice(0,800):'';if(netHit(xhr.__tlUrl,b))netLog({tag:'XHR_REQ',url:xhr.__tlUrl,method:xhr.__tlMethod||'GET',body:b,ts:Date.now()});xhr.addEventListener('loadend',function(){try{var t=String(xhr.responseText||'');if(netHit(xhr.__tlUrl,t)){netLog({tag:'XHR_RES',url:xhr.__tlUrl,status:xhr.status,body:t.slice(0,4000),ts:Date.now()});netSummarize('xhr',xhr.__tlUrl,xhr.status,t);}}catch(e){}});return oldSend.apply(this,arguments);};window.XMLHttpRequest.prototype.__tlNetWrapped=1;}}catch(e){netLog({tag:'XHR_WRAP_ERR',error:String(e),ts:Date.now()});}"
           "function saveMods(mods,name){try{if(mods&&typeof mods==='object'){Object.keys(mods).forEach(function(id){try{window.__TraceLensWebpackModules[id]=String(mods[id]);}catch(e){}});}}catch(e){}}"
           "function wrapArray(arr,name){try{if(!arr||arr.__tlWrapped||String(name).indexOf('__TraceLens')===0)return arr;var old=arr.push;arr.__tlWrapped=1;arr.push=function(chunk){try{if(window.__TraceLensWebpackPushes.length<80)window.__TraceLensWebpackPushes.push({name:name,kind:'array',head:String(chunk).slice(0,160)});if(chunk&&chunk[1])saveMods(chunk[1],name);}catch(e){}return old.apply(this,arguments);};try{arr.forEach(function(chunk){if(chunk&&chunk[1])saveMods(chunk[1],name);});}catch(e){}return arr;}catch(e){return arr;}}"
           "function wrapFunc(fn,name){try{if(!fn||fn.__tlWrapped||String(name).indexOf('__TraceLens')===0)return fn;var w=function(chunkIds,mods){try{if(window.__TraceLensWebpackPushes.length<80)window.__TraceLensWebpackPushes.push({name:name,kind:'function',ids:String(chunkIds).slice(0,120)});saveMods(mods,name);}catch(e){}return fn.apply(this,arguments);};w.__tlWrapped=1;return w;}catch(e){return fn;}}"
           "function defineTrap(name){try{var val=window[name];Object.defineProperty(window,name,{configurable:true,get:function(){return val;},set:function(v){try{val=(typeof v==='function')?wrapFunc(v,name):v;if(Array.isArray(val))wrapArray(val,name);}catch(e){val=v;}}});if(typeof val==='function')val=wrapFunc(val,name);if(Array.isArray(val))wrapArray(val,name);}catch(e){}}"
           "['webpackJsonp','webpackChunk','webpackChunks','webpackJsonpmt','webpackJsonpMT'].forEach(defineTrap);"
           "var n=0,t=setInterval(function(){n++;try{Object.keys(window).forEach(function(k){if(k.indexOf('__TraceLens')===0)return;var v=window[k];if(/webpack|jsonp|chunk/i.test(k)){if(Array.isArray(v))wrapArray(v,k);else if(typeof v==='function')window[k]=wrapFunc(v,k);}});}catch(e){}if(n>240)clearInterval(t);},50);"
           "})();";
}

%hook WKWebView

- (WKNavigation *)loadRequest:(NSURLRequest *)request {
    NSString *url = request.URL.absoluteString ?: @"<nil-url>";
    if (TweakIsInterestingH5Text(url)) {
        LogNoise([NSString stringWithFormat:@"[WK] loadRequest %@", url]);

        WKWebView *webView = self;
        @try {
            WKUserScript *earlyScript = [[WKUserScript alloc] initWithSource:TraceLensEarlyWebpackScript()
                                                               injectionTime:WKUserScriptInjectionTimeAtDocumentStart
                                                            forMainFrameOnly:NO];
            [webView.configuration.userContentController addUserScript:earlyScript];
            LogNoise(@"[WK] TraceLens early webpack userScript installed");
        } @catch (NSException *e) {
            LogNoise([NSString stringWithFormat:@"[WK] early webpack userScript error=%@", e]);
        }
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(3 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
            NSString *script =
                @"(function(){"
                 "function tx(el){return ((el.innerText||el.textContent||'')+'').trim();}"
                 "function interesting(t){return /20:00|投放|预约|库存|可约|不可|购买|下单|已售罄|button|sale|time|stock|order|submit/i.test(t||'');}"
                 "function unlock(){"
                   "var changed=0;"
                   "var nodes=document.querySelectorAll('button,[role=button],a,div,span,p');"
                   "for(var i=0;i<nodes.length;i++){"
                     "var el=nodes[i], t=tx(el), cs=getComputedStyle(el), mark=(el.className||'')+' '+t;"
                     "if(interesting(mark)||el.disabled||el.getAttribute('disabled')!==null||el.getAttribute('aria-disabled')==='true'||cs.pointerEvents==='none'){"
                       "if(el.disabled){el.disabled=false;changed++;}"
                       "if(el.getAttribute('disabled')!==null){el.removeAttribute('disabled');changed++;}"
                       "if(el.getAttribute('aria-disabled')==='true'){el.setAttribute('aria-disabled','false');changed++;}"
                       "if(cs.pointerEvents==='none'){el.style.pointerEvents='auto';changed++;}"
                       "var cls=(' '+(el.className||'')+' ').replace(/\\s(disabled|disable|soldout|sold-out|gray|grey|btn-disabled|is-disabled)\\s/ig,' ');"
                       "if(cls!==(' '+(el.className||'')+' ')){el.className=cls.trim();changed++;}"
                     "}"
                   "}"
                   "var blockers=document.querySelectorAll('[class*=mask],[class*=overlay],[class*=disabled],[class*=sold],[class*=modal]');"
                   "for(var j=0;j<blockers.length;j++){"
                     "var b=blockers[j], bt=tx(b), bs=getComputedStyle(b);"
                     "if(!bt&&bs.position==='fixed'&&parseFloat(bs.opacity||'1')<0.2){b.style.pointerEvents='none';changed++;}"
                   "}"
                   "return changed;"
                 "}"
                 "var changed=0;"
                 "if(window.__TraceLensDiagVersion!=='0.0.45'){"
                   "window.__TraceLensDiagVersion='0.0.45';"
                   "window.__TraceLensDiagInstalled=1;"
                   "window.__TraceLensBuffer=window.__TraceLensBuffer||[];"
                   "window.__TraceLensLog=function(tag,obj){try{window.__TraceLensBuffer.push({tag:tag,data:obj,ts:Date.now()});}catch(e){window.__TraceLensBuffer.push({tag:tag,data:String(obj),ts:Date.now()});}};"
                   "window.__TraceLensLog('TRACE_VERSION',{version:'0.0.45',href:location.href,mode:'minimal-passive'});"
                   "document.addEventListener('click',function(e){"
                     "var el=e.target, path=[];"
                     "for(var p=el;p&&path.length<8;p=p.parentElement){path.push(p.tagName+'#'+(p.id||'')+'.'+(p.className||'')+'='+tx(p).slice(0,120));}"
                     "var pt=document.elementFromPoint(e.clientX,e.clientY);"
                     "window.__TraceLensLog('CLICK',{x:e.clientX,y:e.clientY,target:path[0],path:path,elementFromPoint:pt?(pt.tagName+'#'+(pt.id||'')+'.'+(pt.className||'')+'='+tx(pt).slice(0,120)):null,defaultPrevented:e.defaultPrevented});"
                     "try{"
                       "function aks(o){var m={},out=[];Object.keys(o||{}).concat(Object.getOwnPropertyNames(o||{})).forEach(function(k){if(!m[k]){m[k]=1;out.push(k);}});return out;}"
                       "function short(v){try{if(v==null)return String(v);if(typeof v==='function')return '[Function '+(v.name||'anonymous')+']';if(typeof v==='string'||typeof v==='number'||typeof v==='boolean')return String(v).slice(0,120);if(Array.isArray(v))return '[Array '+v.length+']';return Object.prototype.toString.call(v);}catch(e){return '<err>';}}"
                       "function props(p){var out={keys:aks(p).slice(0,20)};['disabled','className','children','text','title','type','role','id','value'].forEach(function(k){try{if(p&&p[k]!==undefined)out[k]=short(p[k]);}catch(e){}});try{aks(p).forEach(function(k){if(/^on[A-Z]/.test(k))out[k]=short(p[k]);});}catch(e){}return out;}"
                       "function fiberOf(n){var ks=aks(n);for(var i=0;i<ks.length;i++){if(ks[i].indexOf('__reactFiber$')===0||ks[i].indexOf('__reactInternalInstance$')===0)return n[ks[i]];}return null;}"
                       "function eventsOf(n){var ks=aks(n),out={keys:[]};for(var i=0;i<ks.length;i++){if(ks[i].indexOf('__reactEventHandlers$')===0){out.keys=aks(n[ks[i]]).slice(0,20);try{out.props=props(n[ks[i]]);}catch(e){}break;}}return out;}"
                       "function handlerSrcOf(n){var ks=aks(n);for(var i=0;i<ks.length;i++){if(ks[i].indexOf('__reactEventHandlers$')===0){var ev=n[ks[i]];var out={keys:aks(ev).slice(0,20)};aks(ev).forEach(function(k){try{if(/^on[A-Z]/.test(k)&&typeof ev[k]==='function')out[k]=String(ev[k]).slice(0,1800);}catch(e){}});return out;}}return {};}"
                       "function fnode(f,name){if(!f)return null;return {name:name,tag:f.tag,type:short(f.elementType||f.type),stateNode:f.stateNode?((f.stateNode.tagName||'')+'#'+(f.stateNode.id||'')+'.'+(f.stateNode.className||'')):'',pendingProps:props(f.pendingProps),memoizedProps:props(f.memoizedProps)};}"
                       "function interesting(o){var out=[];function add(k,v){if(out.length<40)out.push({key:k,value:short(v)});}function walk(v,path,depth,seen){if(!v||depth>3||out.length>=40)return;if(typeof v!=='object'&&typeof v!=='function'){if(/disabled|disable|status|sale|sold|stock|sku|spu|item|count|allow|buy|order|submit|已售罄|购买|下单|库存/i.test(path+' '+short(v)))add(path,v);return;}if(seen.indexOf(v)>=0)return;seen.push(v);aks(v).slice(0,60).forEach(function(k){var nv;try{nv=v[k];}catch(e){return;}var p=path+'.'+k;if(/disabled|disable|status|sale|sold|stock|sku|spu|item|count|allow|buy|order|submit|children|className/i.test(k)||/已售罄|购买|下单|库存|IMTP1000313/i.test(short(nv)))add(p,nv);if(nv&&typeof nv==='object')walk(nv,p,depth+1,seen);});}walk(o,'$',0,[]);return out;}"
                       "function actionDetail(a,idx){var out={idx:idx,keys:aks(a).slice(0,30)};['status','text','title','children','className','disabled','type','url','action','name','id'].forEach(function(k){try{if(a&&a[k]!==undefined)out[k]=short(a[k]);}catch(e){}});try{aks(a).forEach(function(k){if(/^on[A-Z]/.test(k)&&typeof a[k]==='function')out[k]=String(a[k]).slice(0,900);});}catch(e){}return out;}"
                       "function itemDetail(it){var out={keys:aks(it).slice(0,40)};['id','itemId','spuId','defaultSkuId','primarySkuId','saleModel','cartCount','allowedCounts','buyCount','limitCount','status','stock','inventory'].forEach(function(k){try{if(it&&it[k]!==undefined)out[k]=short(it[k]);}catch(e){}});try{if(it&&it.itemDetail){out.itemDetail={keys:aks(it.itemDetail).slice(0,30)};['id','itemId','spuId','title','name','subTitle','status','saleModel'].forEach(function(k){if(it.itemDetail[k]!==undefined)out.itemDetail[k]=short(it.itemDetail[k]);});}}catch(e){}try{if(it&&it.skuMap){var sk={},ks=aks(it.skuMap).slice(0,8);ks.forEach(function(k){var s=it.skuMap[k];sk[k]={keys:aks(s).slice(0,30)};['skuId','skuName','spuId','saleModel','status','stock','inventory','saleStock','quantity','allowedCounts','limitCount'].forEach(function(x){try{if(s&&s[x]!==undefined)sk[k][x]=short(s[x]);}catch(e){}});});out.skuMap=sk;}}catch(e){}return out;}"
                       "var chain=[],cur=fiberOf(el);for(var d=0;cur&&d<8;d++,cur=cur.return){chain.push(fnode(cur,'return'+d));}"
                       "window.__TraceLensLog('CLICK_FIBER',{target:path[0],eventHandlers:eventsOf(el),fiberKey:!!fiberOf(el),chain:chain});"
                       "window.__TraceLensLog('CLICK_HANDLER_SRC',{target:path[0],handlers:handlerSrcOf(el)});"
                       "var stateChain=[],sc=fiberOf(el);for(var sd=0;sc&&sd<8;sd++,sc=sc.return){stateChain.push({name:'return'+sd,type:short(sc.elementType||sc.type),stateNode:sc.stateNode?((sc.stateNode.tagName||'')+'#'+(sc.stateNode.id||'')+'.'+(sc.stateNode.className||'')):'',props:interesting(sc.pendingProps).slice(0,18),state:interesting(sc.memoizedState).slice(0,18)});}"
                       "window.__TraceLensLog('CLICK_STATE',{target:path[0],chain:stateChain});"
                       "stateChain.forEach(function(n,idx){window.__TraceLensLog('CLICK_STATE_NODE',{target:path[0],idx:idx,node:n});});"
                       "var fc=fiberOf(el);for(var fd=0;fc&&fd<10;fd++,fc=fc.return){try{var pp=fc.pendingProps||{};if(Array.isArray(pp.actions)){window.__TraceLensLog('CLICK_ACTIONS_DETAIL',{target:path[0],idx:fd,type:short(fc.elementType||fc.type),actions:pp.actions.map(actionDetail)});}if(pp.item){window.__TraceLensLog('CLICK_ITEM_DETAIL',{target:path[0],idx:fd,type:short(fc.elementType||fc.type),id:short(pp.id),cartCount:short(pp.cartCount),item:itemDetail(pp.item)});}}catch(e){window.__TraceLensLog('CLICK_DETAIL_ERR',{idx:fd,error:String(e)});}}"
                       "var cs=fiberOf(el);for(var cd=0;cs&&cd<12;cd++,cs=cs.return){try{var fn=cs.elementType||cs.type;if(typeof fn==='function'){var src=String(fn),keys=['Je(','farRightBtn','retailPriceShowTime','isJumpStraight','shoppingcart','actions','status','已售罄','20:00','skuMap','saleModel','cartCount','disabled'];window.__TraceLensLog('CLICK_COMPONENT_SRC',{target:path[0],idx:cd,name:fn.name||'',len:src.length,head:src.slice(0,500)});keys.forEach(function(k){var pos=src.indexOf(k);if(pos>=0){window.__TraceLensLog('CLICK_COMPONENT_SNIP',{target:path[0],idx:cd,name:fn.name||'',key:k,pos:pos,len:src.length,snippet:src.slice(Math.max(0,pos-900),Math.min(src.length,pos+1600))});}});}}catch(e){window.__TraceLensLog('CLICK_COMPONENT_SRC_ERR',{idx:cd,error:String(e)});}}"
                     "}catch(ex){window.__TraceLensLog('CLICK_FIBER_ERR',String(ex));}"
                   "},true);"
                   "var oldAdd=EventTarget.prototype.addEventListener;"
                   "EventTarget.prototype.addEventListener=function(type,listener,opt){"
                     "if(type==='click'){try{window.__TraceLensLog('ADD_CLICK',{target:this.tagName?this.tagName+'#'+(this.id||'')+'.'+(this.className||''):String(this),text:this.innerText?String(this.innerText).slice(0,120):''});}catch(e){}}"
                     "return oldAdd.call(this,type,listener,opt);"
                   "};"
                   "if(window.fetch){"
                     "var oldFetch=window.fetch;"
                     "window.fetch=function(input,init){"
                       "var u=(typeof input==='string')?input:(input&&input.url);"
                       "window.__TraceLensLog('FETCH_REQ',{url:u,method:(init&&init.method)||'GET',body:(init&&init.body)?String(init.body).slice(0,1000):''});"
                       "return oldFetch.apply(this,arguments).then(function(resp){"
                         "try{var c=resp.clone();c.text().then(function(t){window.__TraceLensLog('FETCH_RES',{url:u,status:resp.status,body:String(t).slice(0,1500)});});}catch(e){}"
                         "return resp;"
                       "});"
                     "};"
                   "}"
                   "var oldOpen=XMLHttpRequest.prototype.open;"
                   "var oldSend=XMLHttpRequest.prototype.send;"
                   "XMLHttpRequest.prototype.open=function(method,url){this.__tl_method=method;this.__tl_url=url;return oldOpen.apply(this,arguments);};"
                   "XMLHttpRequest.prototype.send=function(body){"
                     "var xhr=this;"
                     "window.__TraceLensLog('XHR_REQ',{url:xhr.__tl_url,method:xhr.__tl_method,body:body?String(body).slice(0,1000):''});"
                     "xhr.addEventListener('loadend',function(){window.__TraceLensLog('XHR_RES',{url:xhr.__tl_url,status:xhr.status,body:String(xhr.responseText||'').slice(0,1500)});});"
                     "return oldSend.apply(this,arguments);"
                   "};"
                   "window.__TraceLensScanState=function(){"
                     "var seen=[],hits=[],re=/sold|stock|canbuy|buy|sale|status|disabled|disable|itemid|itemId|sku|spu|reserve|reservation|submit|order|已售罄|库存|购买|下单|预约|IMTP1000313/i;"
                     "function val(v){try{if(v==null)return String(v);if(typeof v==='string'||typeof v==='number'||typeof v==='boolean')return String(v);return Object.prototype.toString.call(v);}catch(e){return '<err>';}}"
                     "function add(path,v){if(hits.length<120)hits.push({path:path,value:val(v).slice(0,300)});}"
                     "function scan(o,path,depth){"
                       "if(!o||depth>5||hits.length>=120)return;"
                       "if(typeof o!=='object'&&typeof o!=='function'){if(re.test(path)||re.test(val(o)))add(path,o);return;}"
                       "if(seen.indexOf(o)>=0)return;seen.push(o);"
                       "var keys=[];try{keys=Object.keys(o).slice(0,120);}catch(e){return;}"
                       "for(var i=0;i<keys.length&&hits.length<120;i++){"
                         "var k=keys[i],v;try{v=o[k];}catch(e){continue;}"
                         "var p=path+'.'+k;"
                         "if(re.test(k)||re.test(val(v)))add(p,v);"
                         "if(v&&typeof v==='object')scan(v,p,depth+1);"
                       "}"
                     "}"
                     "try{document.querySelectorAll('*').forEach(function(el,idx){if(idx>500)return;if(el.__vue__)scan(el.__vue__,'el['+idx+'].__vue__',0);if(el.__vueParentComponent)scan(el.__vueParentComponent,'el['+idx+'].__vueParentComponent',0);});}catch(e){}"
                     "['__INITIAL_STATE__','__NEXT_DATA__','__NUXT__','app','store','vue','Vue','$vm','$store'].forEach(function(k){try{if(window[k])scan(window[k],'window.'+k,0);}catch(e){}});"
                     "try{for(var i=0;i<localStorage.length;i++){var k=localStorage.key(i),v=localStorage.getItem(k);if(re.test(k)||re.test(v))add('localStorage.'+k,v);}}catch(e){}"
                     "try{for(var j=0;j<sessionStorage.length;j++){var sk=sessionStorage.key(j),sv=sessionStorage.getItem(sk);if(re.test(sk)||re.test(sv))add('sessionStorage.'+sk,sv);}}catch(e){}"
                     "window.__TraceLensLog('STATE',{href:location.href,count:hits.length,hits:hits});"
                   "};"
                   "window.__TraceLensDumpReact=function(){"
                     "function txt(el){return ((el&&el.innerText)||'').trim();}"
                     "function allKeys(o){try{var m={},out=[];Object.keys(o||{}).concat(Object.getOwnPropertyNames(o||{})).forEach(function(k){if(!m[k]){m[k]=1;out.push(k);}});return out;}catch(e){return [];}}"
                     "function fiberOf(el){if(!el)return null;var ks=allKeys(el);for(var i=0;i<ks.length;i++){if(ks[i].indexOf('__reactFiber$')===0||ks[i].indexOf('__reactInternalInstance$')===0)return el[ks[i]];}return null;}"
                     "function eventsOf(el){if(!el)return {};var ks=allKeys(el),out={keys:[]};for(var i=0;i<ks.length;i++){if(ks[i].indexOf('__reactEventHandlers$')===0){out.keys=Object.keys(el[ks[i]]||{});try{Object.keys(el[ks[i]]||{}).forEach(function(k){if(/^on[A-Z]/.test(k))out[k]='[Function]';else if(k==='className'||k==='children'||k==='disabled')out[k]=String(el[ks[i]][k]).slice(0,120);});}catch(e){}break;}}return out;}"
                     "function propSummary(p){"
                       "var out={};if(!p)return out;"
                       "['disabled','className','children','text','title','type','href','role','id'].forEach(function(k){try{if(p[k]!==undefined)out[k]=(typeof p[k]==='function')?'[Function]':String(p[k]).slice(0,200);}catch(e){}});"
                       "try{Object.keys(p).forEach(function(k){if(/^on[A-Z]/.test(k))out[k]='[Function]';});}catch(e){}"
                       "return out;"
                     "}"
                     "function nodeSummary(f,tag){if(!f)return null;return {tag:tag,elementType:String(f.elementType||f.type||''),key:String(f.key||''),flags:f.flags,subtreeFlags:f.subtreeFlags,pendingProps:propSummary(f.pendingProps),memoizedProps:propSummary(f.memoizedProps),memoizedState:(function(){try{return String(f.memoizedState).slice(0,200);}catch(e){return '';}})()};}"
                     "var out=[];"
                     "document.querySelectorAll('button,[role=button],a,div,span').forEach(function(el,idx){"
                       "var t=txt(el);if(out.length>=30)return;"
                       "if(/已售罄|购买|下单|库存|预约|20:00|投放/.test(t)||/(btn|sold|disabled|disable|submit|order)/i.test(el.className||'')){"
                         "var f=fiberOf(el), chain=[], cur=f;"
                         "for(var d=0;cur&&d<8;d++,cur=cur.return){chain.push(nodeSummary(cur,'self/return'+d));}"
                         "out.push({idx:idx,dom:el.tagName+'#'+(el.id||'')+'.'+(el.className||''),text:t.slice(0,120),fiberKey:!!f,eventHandlers:eventsOf(el),chain:chain});"
                       "}"
                     "});"
                     "window.__TraceLensLog('REACT',{href:location.href,count:out.length,nodes:out});"
                   "};"
                   "window.__TraceLensDumpFiberTarget=function(){"
                     "function short(v){try{if(v==null)return String(v);if(typeof v==='function')return '[Function '+(v.name||'anonymous')+']';if(typeof v==='string'||typeof v==='number'||typeof v==='boolean')return String(v).slice(0,240);if(Array.isArray(v))return '[Array '+v.length+']';return Object.prototype.toString.call(v);}catch(e){return '<err>';}}"
                     "function keys(o){try{var m={},out=[];Object.keys(o||{}).concat(Object.getOwnPropertyNames(o||{})).forEach(function(k){if(!m[k]){m[k]=1;out.push(k);}});return out.slice(0,120);}catch(e){return [];}}"
                     "function props(p){var out={keys:keys(p)};if(!p)return out;['disabled','className','children','text','title','type','role','id','style'].forEach(function(k){try{if(p[k]!==undefined)out[k]=short(p[k]);}catch(e){}});try{Object.keys(p).forEach(function(k){if(/^on[A-Z]/.test(k))out[k]=short(p[k]);});}catch(e){}return out;}"
                     "function node(f,name){if(!f)return null;var sn='';try{if(f.stateNode){sn=(f.stateNode.tagName||f.stateNode.constructor&&f.stateNode.constructor.name||'')+'#'+(f.stateNode.id||'')+'.'+(f.stateNode.className||'');}}catch(e){}return {name:name,tag:f.tag,key:short(f.key),type:short(f.elementType||f.type),flags:f.flags,subtreeFlags:f.subtreeFlags,stateNode:sn,pendingProps:props(f.pendingProps),memoizedProps:props(f.memoizedProps),memoizedState:short(f.memoizedState)};}"
                     "function rootCandidates(){var out=[],seen=[];function add(el){if(el&&seen.indexOf(el)<0){seen.push(el);out.push(el);}}add(window.app);add(document.getElementById('app'));add(document.querySelector('#app'));add(document.body);try{document.querySelectorAll('div,button,[role=button]').forEach(function(el){var t=((el.innerText||'')+'').trim();if(/已售罄|购买|下单|预约|投放|20:00/.test(t)||/(btn|sold|disabled|disable|submit|order)/i.test(el.className||''))add(el);});}catch(e){}return out;}"
                     "function keyReport(){var out=[];rootCandidates().forEach(function(el,idx){if(out.length>=12)return;var ks=keys(el), rk=[];ks.forEach(function(k){if(/react/i.test(k))rk.push(k);});out.push({idx:idx,dom:el.tagName+'#'+(el.id||'')+'.'+(el.className||''),reactKeys:rk.slice(0,8),keyCount:ks.length,text:((el.innerText||'')+'').trim().slice(0,48)});});return out;}"
                     "function findRoot(){var cs=rootCandidates();for(var c=0;c<cs.length;c++){var el=cs[c],ks=keys(el);for(var i=0;i<ks.length;i++){if(/react/i.test(ks[i]))return {el:el,key:ks[i],fiber:el[ks[i]]};}}return {el:cs[0]||null,key:null,fiber:null};}"
                     "function scanMini(o,path,depth,out,seen){if(!o||depth>4||out.length>80)return;if(typeof o!=='object'&&typeof o!=='function')return;if(seen.indexOf(o)>=0)return;seen.push(o);keys(o).forEach(function(k){var v;try{v=o[k];}catch(e){return;}var p=path+'.'+k;if(/disabled|onClick|className|children|sold|stock|buy|submit|order|已售罄|购买|下单|库存/i.test(k)||/已售罄|购买|下单|库存|IMTP1000313/i.test(short(v)))out.push({path:p,value:short(v)});if(v&&typeof v==='object')scanMini(v,p,depth+1,out,seen);});}"
                     "window.__TraceLensLog('ROOT_KEYS',{href:location.href,nodes:keyReport()});"
                     "var r=findRoot(), f=r.fiber, target=null;"
                     "try{target=f&&f.child&&f.child.return&&f.child.return.firstEffect;}catch(e){}"
                     "var mini=[];scanMini(target,'firstEffect',0,mini,[]);"
                     "window.__TraceLensLog('FIBER',{href:location.href,rootKey:r.key,rootNode:r.el?(r.el.tagName+'#'+(r.el.id||'')+'.'+(r.el.className||'')):null,hasRoot:!!f,hasTarget:!!target,nodes:[node(f,'root'),node(f&&f.child,'root.child'),node(f&&f.child&&f.child.return,'root.child.return'),node(target,'firstEffect'),node(target&&target.child,'firstEffect.child'),node(target&&target.sibling,'firstEffect.sibling'),node(target&&target.return,'firstEffect.return'),node(target&&target.alternate,'firstEffect.alternate')],hits:mini});"
                   "};"
                   "try{window.__TraceLensLog('FIBER_BOOT',{href:location.href,ready:true});window.__TraceLensDumpFiberTarget();}catch(e){window.__TraceLensLog('FIBER_BOOT_ERR',String(e));}"
	                   "window.__TraceLensScanScripts=function(){"
	                     "try{"
                       "var urls=[],seen={};"
                       "function add(u){try{if(u&&/\\.js(\\?|$)|chunk|bundle|umi|app/i.test(u)&&!seen[u]){seen[u]=1;urls.push(u);}}catch(e){}}"
                       "try{document.querySelectorAll('script[src]').forEach(function(s){add(s.src);});}catch(e){}"
                       "try{performance.getEntriesByType('resource').forEach(function(r){add(r.name);});}catch(e){}"
                       "window.__TraceLensLog('SCRIPT_LIST',{href:location.href,count:urls.length,urls:urls.slice(0,30)});"
                       "urls=urls.filter(function(u){return /hxm\\/mt-wap|chunk|pages|item|detail/i.test(u)&&!/vendor|jquery|react|jweixin|stat\\.min|bs_h5/i.test(u);}).concat(urls.filter(function(u){return !/hxm\\/mt-wap|chunk|pages|item|detail/i.test(u);}));"
                       "window.__TraceLensLog('SCRIPT_SCAN_TARGETS',{count:urls.length,urls:urls.slice(0,18)});"
                       "var probes=['farRightBtn','isJumpStraight','shoppingcart','actions','status','retailPriceShowTime','m-btnGroup','已售罄','20:00投放','IMTP1000313','farRight','disabled','saleModel','skuMap'];"
                       "var re=/farRightBtn|isJumpStraight|shoppingcart|20:00\\s*投放|已售罄|actions|status\\s*[:=]|retailPriceShowTime|static\\/resource|IMTP1000313|m-btnGroup|farRight|saleModel|skuMap|disabled/g;"
                       "urls.slice(0,18).forEach(function(u,idx){"
                         "window.__TraceLensLog('SCRIPT_PENDING',{idx:idx,url:u});"
                         "var req=(oldFetch||fetch)(u,{credentials:'include',cache:'force-cache'}).then(function(r){"
                           "window.__TraceLensLog('SCRIPT_META',{idx:idx,url:u,status:r.status,ok:r.ok,type:r.type,redirected:r.redirected});"
                           "return r.text();"
                         "}).then(function(t){"
                           "var hits=[],m,guard=0;"
                           "re.lastIndex=0;"
                           "while((m=re.exec(t))&&hits.length<8&&guard++<80){var s=Math.max(0,m.index-500),e=Math.min(t.length,m.index+900);hits.push({match:m[0],pos:m.index,snippet:t.slice(s,e)});}"
                           "var found={};probes.forEach(function(p){found[p]=t.indexOf(p)>=0;});"
                           "window.__TraceLensLog('SCRIPT_DONE',{idx:idx,url:u,len:t.length,hitCount:hits.length,found:found,sample:t.slice(0,220)});"
                           "if(hits.length)window.__TraceLensLog('SCRIPT_HIT',{idx:idx,url:u,len:t.length,hits:hits});"
                         "});"
                         "var timeout=new Promise(function(_,rej){setTimeout(function(){rej(new Error('SCRIPT_TIMEOUT 4500ms'));},4500);});"
                         "Promise.race([req,timeout]).catch(function(e){window.__TraceLensLog('SCRIPT_ERR',{idx:idx,url:u,error:String(e)});});"
                       "});"
	                     "}catch(e){window.__TraceLensLog('SCRIPT_SCAN_ERR',String(e));}"
	                   "};"
	                   "window.__TraceLensScanWebpack=function(){"
	                     "try{"
		                       "if(window.__TraceLensWebpackScanned)return;window.__TraceLensWebpackScanned=1;"
		                       "var req=null,chunkNames=[],early=window.__TraceLensWebpackModules||{},pushes=window.__TraceLensWebpackPushes||[];"
		                       "Object.keys(window).forEach(function(k){try{var v=window[k];if(v&&Array.isArray(v)&&/webpack|chunk|jsonp/i.test(k)){chunkNames.push(k);if(!req&&typeof v.push==='function'){try{v.push([[\"tracelens_\"+Date.now()],{},function(r){req=r;}]);}catch(e){}}}}catch(e){}});"
		                       "window.__TraceLensLog('WEBPACK_CAPTURE',{chunks:chunkNames.slice(0,20),hasRequire:!!req,hasModules:!!(req&&req.m),hasCache:!!(req&&req.c),earlyCount:Object.keys(early).length,pushCount:pushes.length,pushes:pushes.slice(0,8)});"
		                       "var seen={},keys=['farRightBtn','已售罄','purchaseInfo','forbiddenBuyDesc','retailPriceShowTime','isJumpStraight','shoppingcart','getVerifyStatus','priorityRecordId','cartCount'];"
		                       "function scanSource(id,kind,src){try{if(!src||seen[kind+id])return;seen[kind+id]=1;keys.forEach(function(k){var pos=src.indexOf(k);if(pos>=0){window.__TraceLensLog('WEBPACK_HIT',{id:String(id),kind:kind,key:k,pos:pos,len:src.length,snippet:src.slice(Math.max(0,pos-900),Math.min(src.length,pos+1800))});}});}catch(e){window.__TraceLensLog('WEBPACK_HIT_ERR',{id:String(id),kind:kind,error:String(e)});}}"
		                       "try{Object.keys(early).slice(0,5000).forEach(function(id){scanSource(id,'earlyFactory',early[id]);});}catch(e){window.__TraceLensLog('WEBPACK_EARLY_ERR',String(e));}"
		                       "try{if(req)Object.keys(req.m||{}).slice(0,3000).forEach(function(id){scanSource(id,'factory',String(req.m[id]));});}catch(e){window.__TraceLensLog('WEBPACK_MODULES_ERR',String(e));}"
		                       "try{if(req)Object.keys(req.c||{}).slice(0,3000).forEach(function(id){var mod=req.c[id],exp=mod&&mod.exports;scanSource(id,'exports',String(exp));if(exp&&typeof exp==='object'){Object.keys(exp).slice(0,80).forEach(function(k){try{scanSource(id+'.'+k,'exportProp',String(exp[k]));}catch(e){}});}});}catch(e){window.__TraceLensLog('WEBPACK_CACHE_ERR',String(e));}"
	                     "}catch(e){window.__TraceLensLog('WEBPACK_SCAN_ERR',String(e));}"
	                   "};"
                   "setTimeout(function(){try{window.__TraceLensScanState();}catch(e){window.__TraceLensLog('STATE_ERR',String(e));}},1200);"
                   "setTimeout(function(){try{window.__TraceLensDumpReact();}catch(e){window.__TraceLensLog('REACT_ERR',String(e));}},1800);"
                   "setTimeout(function(){try{window.__TraceLensDumpFiberTarget();}catch(e){window.__TraceLensLog('FIBER_ERR',String(e));}},2200);"
                 "}"
                 "if(!window.__TraceLensPassiveMode){window.__TraceLensPassiveMode=1;}"
                 "var text=(document.body&&document.body.innerText)||'';"
                 "var hit=[];"
                 "var nodes=document.querySelectorAll('button,a,div,span,p');"
                 "for(var i=0;i<nodes.length&&hit.length<30;i++){"
                   "var t=tx(nodes[i]);"
                   "if(t&&interesting(t))"
                     "hit.push(nodes[i].tagName+'#'+(nodes[i].id||'')+'.'+(nodes[i].className||'')+'='+t);"
                 "}"
                 "return JSON.stringify({href:location.href,title:document.title,changed:changed,body:text.slice(0,3000),hits:hit});"
                "})();";
            [webView evaluateJavaScript:script completionHandler:^(id result, NSError *error) {
                NSString *preview = TweakPreviewObject(result);
                LogNoise([NSString stringWithFormat:@"[WKUNLOCK] %@", preview]);
                if (TweakTextLooksLikeTimeField(preview)) {
                    LogNoise([NSString stringWithFormat:@"[WKDOM] %@", preview]);
                }
                if (error) {
                    LogNoise([NSString stringWithFormat:@"[WKDOM] error=%@", error]);
                }
            }];

            for (int i = 1; i <= 120; i++) {
                dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(i * 500000000LL)), dispatch_get_main_queue(), ^{
                    [webView evaluateJavaScript:@"(function(){var b=window.__TraceLensNetEvents||[];var out=b.splice(0,1);return out.length?JSON.stringify(out[0]):'';})();"
                              completionHandler:^(id result, NSError *error) {
                        NSString *preview = TweakPreviewObject(result);
                        if (preview.length > 0 && ![preview isEqualToString:@"<nil>"]) {
                            NSString *tag = ([preview containsString:@"PURCHASE_SUMMARY"] || [preview containsString:@"DETAIL_SUMMARY"]) ? @"[WKSUMMARY]" : @"[WKNET]";
                            LogNoise([NSString stringWithFormat:@"%@ %@", tag, preview]);
                        }
                        if (error) {
                            LogNoise([NSString stringWithFormat:@"[WKNET_ERR] %@", error]);
                        }
                    }];
                    [webView evaluateJavaScript:@"(function(){var b=window.__TraceLensBuffer||[];var out=b.splice(0,1);return out.length?JSON.stringify(out[0]):'';})();"
                              completionHandler:^(id result, NSError *error) {
                        NSString *preview = TweakPreviewObject(result);
                        if (preview.length > 0 && ![preview isEqualToString:@"<nil>"]) {
                            LogNoise([NSString stringWithFormat:@"[H5LOG] %@", preview]);
                        }
                    }];
                });
            }
        });
    }
    return %orig;
}

- (WKNavigation *)loadHTMLString:(NSString *)string baseURL:(NSURL *)baseURL {
    NSString *url = baseURL.absoluteString ?: @"<nil-baseURL>";
    LogNoise([NSString stringWithFormat:@"[WK] loadHTMLString baseURL=%@ html=%@", url, TweakPreviewString(string)]);
    return %orig;
}

- (void)evaluateJavaScript:(NSString *)javaScriptString
         completionHandler:(void (^)(id result, NSError *error))completionHandler {
    BOOL interesting = TweakIsInterestingH5Text(javaScriptString);
    if ([javaScriptString containsString:@"[TraceLensH5]"]) {
        LogNoise([NSString stringWithFormat:@"[H5LOG] %@", TweakPreviewString(javaScriptString)]);
    } else if (interesting) {
        LogNoise([NSString stringWithFormat:@"[WK] evaluateJavaScript %@", TweakPreviewString(javaScriptString)]);
    }

    %orig(javaScriptString, ^(id result, NSError *error) {
        BOOL hasResult = result && (![result isKindOfClass:[NSString class]] || [(NSString *)result length] > 0);
        if (hasResult || error) {
            LogNoise([NSString stringWithFormat:@"[WK] evaluateJavaScript result=%@ error=%@",
                    TweakPreviewObject(result),
                    error ?: (NSError *)@"<nil>"]);
        }
        if (completionHandler) completionHandler(result, error);
    });
}

- (void)setNavigationDelegate:(id)navigationDelegate {
    LogNoise([NSString stringWithFormat:@"[WK] setNavigationDelegate %@", navigationDelegate]);
    %orig;
}

- (void)setUIDelegate:(id)UIDelegate {
    LogNoise([NSString stringWithFormat:@"[WK] setUIDelegate %@", UIDelegate]);
    %orig;
}

%end

%hook UILabel

- (void)setText:(NSString *)text {
    if (TweakTextLooksLikeTimeField(text)) {
        LogNoise([NSString stringWithFormat:@"[UI] UILabel setText %@", TweakPreviewString(text)]);
        TweakLogStack(@"UILabel.setText.timeField");
    }
    %orig;
}

- (void)setAttributedText:(NSAttributedString *)attributedText {
    NSString *text = attributedText.string;
    if (TweakTextLooksLikeTimeField(text)) {
        LogNoise([NSString stringWithFormat:@"[UI] UILabel setAttributedText %@", TweakPreviewString(text)]);
        TweakLogStack(@"UILabel.setAttributedText.timeField");
    }
    %orig;
}

%end

%hook UIButton

- (void)setTitle:(NSString *)title forState:(UIControlState)state {
    if (TweakTextLooksLikeTimeField(title)) {
        LogNoise([NSString stringWithFormat:@"[UI] UIButton setTitle state=%lu title=%@",
                (unsigned long)state,
                TweakPreviewString(title)]);
        TweakLogStack(@"UIButton.setTitle.timeField");
    }
    %orig;
}

- (void)setAttributedTitle:(NSAttributedString *)title forState:(UIControlState)state {
    NSString *text = title.string;
    if (TweakTextLooksLikeTimeField(text)) {
        LogNoise([NSString stringWithFormat:@"[UI] UIButton setAttributedTitle state=%lu title=%@",
                (unsigned long)state,
                TweakPreviewString(text)]);
        TweakLogStack(@"UIButton.setAttributedTitle.timeField");
    }
    %orig;
}

%end

%hook WKUserContentController

- (void)addScriptMessageHandler:(id <WKScriptMessageHandler>)scriptMessageHandler name:(NSString *)name {
    LogNoise([NSString stringWithFormat:@"[WK] addScriptMessageHandler name=%@ handler=%@",
            name,
            scriptMessageHandler]);
    %orig;
}

%end

%hook WKScriptMessage

- (NSString *)name {
    NSString *name = %orig;
    if (TweakIsInterestingH5Text(name)) {
        LogNoise([NSString stringWithFormat:@"[WKMSG] name=%@", name]);
    }
    return name;
}

- (id)body {
    id body = %orig;
    NSString *preview = TweakPreviewObject(body);
    if (TweakIsInterestingH5Text(preview)) {
        LogNoise([NSString stringWithFormat:@"[WKMSG] name=%@ body=%@",
                self.name,
                preview]);
        TweakLogStack(@"WKScriptMessage.body");
    }
    return body;
}

%end

%end

static void TraceLensFinishInit(void) {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    LogMsg([NSString stringWithFormat:@"TraceLens VERSION 0.0.46 LOADED (log_level=%d: 1=api 2=standard 3=full, default api)",
            TraceLensLogLevel()]);
    LogNoise(@"No mach-o 0x4864/0x4A8C/0x2206C2C; late dyld filter; getenv at boot");
}

static void TraceLensArmUIApplicationMainGate(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        MSHookFunction((void *)UIApplicationMain,
                       (void *)TraceLens_hook_UIApplicationMain,
                       (void **)&TraceLens_orig_UIApplicationMain);
        LogNoise(@"TraceLens: UIApplicationMain gate armed (manual MSHook)");
    });
}

static void TraceLensInstallMainHooks(void) {
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
        LogNoise(@"TraceLens: installing main hooks (UIApplicationMain entry)");
        TraceLensInstallLateDyldFilter();
        %init(TraceLensMain);
        TraceLensFinishInit();
        TraceLens_mainHooksInstalled = true;
    });
}

%ctor {
    if (!tweakLog) tweakLog = os_log_create("com.xavier.tracelens", "default");
    TraceLensNukeSciapodousLoadIfNeeded();
    LogNoise(@"TraceLens boot: getenv only (dyld filter deferred to UIApplicationMain)");
    %init(TraceLensBoot);
    dispatch_async(dispatch_get_main_queue(), ^{
        TraceLensArmUIApplicationMainGate();
    });
    dispatch_after(dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2 * NSEC_PER_SEC)), dispatch_get_main_queue(), ^{
        if (TraceLens_mainHooksInstalled) return;
        LogNoise(@"TraceLens: fallback main-hook install (UIApplicationMain not reached)");
        TraceLensInstallMainHooks();
    });
}
