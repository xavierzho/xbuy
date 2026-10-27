# TraceLens 偏移审计（i茅台 `moutai-mall` 1.9.16）

- 样本路径：`/Users/zho/Builder/StudioLLC/rushbuy/downloads/ios/moutai-mall-1.9.16`
- SHA256：`b853b23e658f7a521dde02f00a6dc7a934fc48e6aba9a0fb5b96f7b6dfa2d04f`
- 大小：约 35.0 MB（arm64 Mach-O）
- 说明：TraceLens **0.0.49+** 已移除 mach-o 硬编码 hook；本表供版本对比与回归记录。

## 结论

| 项 | 建议 |
|----|------|
| `mach_header + 0x4864` / `0x4A8C` | **不要** MSHookFunction |
| `mach_header + 0x2206C2C` 定时写 | **不要** |
| `+[Sciapodous load]` | 运行时 `method_getImplementation` + MSHook（TraceLens 已如此） |
| ObjC `%hook` | `CONCuhcHdGGlGhKK`；`MassComnunicetion` 或 1.9.16 `MassCommunlacation`；`+load` 见 `Sciapodous` / `MassCommumization` |

## TraceLens 依赖符号（`strings`）

| 符号 | 存在 |
|------|------|
| `Sciapodous` | **否** |
| `MassComnunicetion` | **否** |
| `MassCommunlacation` | 是 |
| `MassCommumization` | 是 |
| `CONCuhcHdGGlGhKK` | 是 |
| `yx_headerEncryptString` | 是 |
| `howhareyou:` | 是 |
| `doAbortAction` | 是 |
| `checkPoliticIfExecute` | 是 |

## `+[Sciapodous load]` IMP（距 mach header）

- **文件偏移 `0xB880C`**（VA `0x1000B880C`）

## `CONCuhcHdGGlGhKK` IMP 采样（前若干）

| # | 偏移 |
|---|------|
| 0 | `0x18580E8` |
| 1 | `0x18582F4` |
| 2 | `0x1858374` |
| 3 | `0x185862C` |
| 4 | `0x1858734` |
| 5 | `0x1858BF0` |
| 6 | `0x1859994` |
| 7 | `0x1859AF0` |
| 8 | `0x1859BE4` |
| 9 | `0x1859D70` |
| 10 | `0x1859EB4` |
| 11 | `0x185A1F8` |
| 12 | `0x185A69C` |

## 已废弃 TraceLens 常量（勿恢复）

| 偏移 | 备注 |
|------|------|
| `0x4864` | immortal_c_func hook (ciphertext on 1.9.14) |
| `0x4A8C` | secondary C hook (obfuscated) |
| `0x2206C2C` | abnormal_byte timer (VA past __TEXT on 1.9.14) |
