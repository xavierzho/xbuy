# TraceLens 偏移审计（i茅台 `moutai-mall` 1.9.14）

- 样本路径：`downloads/ios/moutai-mall`
- SHA256：`49754779fb9f3e672f482977b616b7ad944c30d1a5a11f646d54b52877ff8d81`
- 大小：约 37 MB（arm64 Mach-O）
- App Store 版本：**1.9.14**（`com.moutai.mall`）
- Mach-O：`__TEXT` @ `0x100000000`，`__text` @ `0x100004000`（file off `0x4000`）
- 说明：主程序带邦邦/混淆，静态 disasm 低地址多为 `.long` 密文；**运行时**会解密，但 **固定文件偏移 hook 仍可能指向密文/错误段**。

## 结论（为何闪退）

| TraceLens 常量 | 含义 | 1.9.14 结论 |
|----------------|------|-------------|
| `mach_header + 0x4864` | `MSHookFunction` → `sub_100004864` | **不是函数**，为密文数据（`.long 0x4fadf68a`）。Hook 必崩。 |
| `mach_header + 0x4A8C` | `MSHookFunction` → `sub_100004A8C` | 附近有 `bl`，但整体仍在混淆区，**不宜再硬编码**。 |
| `mach_header + 0x2206C2C` | 定时器写 `volatile char` | VA `0x102206C2C` **已超出 `__TEXT`**（`__TEXT` 止于 `0x102088000`），落在 `__DATA_CONST` 一带，**写错地址必崩**。 |
| `+[Sciapodous load]` 动态 hook | `MSHookFunction(IMP)` | 仍可用；当前 IMP **文件偏移约 `0x4478`**（VA `0x100004478`），**不是 `0x4864`**。 |
| ObjC `%hook` | `NSString` / `MassComnunicetion` / `CONCuhcHdGGlGhKK` | 类名仍在（`Sciapodous`、`CONCuhcHdGGlGhKK`、`MassComnunicetion`），**继续用 runtime hook，不要改偏移**。 |

**修复原则：** 1.9.14 上应 **删掉或默认关闭** 第 5 节 C 函数 hook 与 `%ctor` 里 `0x2206C2C` 定时写；保留 ObjC hook + `Sciapodous` 动态 `+load` 拦截。

## 仍有效的符号（无需文件偏移）

- 字符串：`root/%d;debug/%d;proxy/%d;inject/%d`
- Selector（`__objc_methname`）：`yx_headerEncryptString`、`howhareyou:`、`doAbortAction`、`checkPoliticIfExecute`、`alertUserInMainThreadWithType:...`
- 类名：`Sciapodous`、`CONCuhcHdGGlGhKK`、`MassComnunicetion`

## `+[Sciapodous load]`（meta class method list）

- `baseMethods` @ VA `0x101ce2870`，count = 1
- 解析 relative method list 得 IMP VA **`0x100004478`** → **距 mach header 偏移 `0x4478`**

## `CONCuhcHdGGlGhKK` 实例方法 IMP（距 mach header）

> 方法名在静态元数据中加密，需按 **IMP 顺序** 或运行时 `class_copyMethodList` 对齐；下表仅作逆向记录，**TraceLens 已用 `%hook CONCuhcHdGGlGhKK`，不必再 MSHookFunction**。

| # | IMP VA | 偏移 |
|---|--------|------|
| 0 | `0x1018051b8` | `0x18051b8` |
| 1 | `0x1018053c4` | `0x18053c4` |
| 2 | `0x101805444` | `0x1805444` |
| 3 | `0x1018056fc` | `0x18056fc` |
| 4 | `0x101805804` | `0x1805804` |
| 5 | `0x101805cc0` | `0x1805cc0` |
| 6 | `0x101806a64` | `0x1806a64` |
| 7 | `0x101806bc0` | `0x1806bc0` |
| 8 | `0x101806cb4` | `0x1806cb4` |
| 9 | `0x101806e40` | `0x1806e40` |
| 10 | `0x101806f84` | `0x1806f84` |
| 11 | `0x1018072c8` | `0x18072c8` |
| 12 | `0x10180776c` | `0x180776c` |

## `0x2206C2C` 异常标志位

- 旧偏移对应 VA **`0x102206C2C`**，相对 **`__TEXT` 结束超出约 `0x17EC2C`**
- **未能在静态样本中安全定位等价全局变量**；应在 **App 运行且解密后** 用 Frida/LLDB 对 `inject/1`、`checkRootStatus` 等路径做动态 xref，或暂时 **关闭该 timer**

## 建议的 Tweak 改动（代码层）

1. **删除** `immortal_c_func_nuke()`（`0x4864` / `0x4A8C`）— TraceLens **0.0.49+** 已移除
2. **删除** `abnormal_byte = base + 0x2206C2C` 定时器 — **0.0.49+** 已移除
3. **保留**：ObjC `%hook`、`+[Sciapodous load]` 用 Logos 并 **`%orig`**（勿 constructor+MSHook RET）
4. **Dopamine**：`getenv(DYLD_*)` 立即生效，其余 hook **`dispatch_async(main)`** 延后
5. 仅装 **TraceLens** 或 **BBWasmLens** 之一，避免双 dylib

## REA 说明

- 已对样本 `open_binary`；部分 REA 工具（IDA 字符串/rizin）在本机不可用
- 本报告主要依据：`otool`、`nm`、`objc_dump`（`otool -ov`）、手工 relative method list 解析
