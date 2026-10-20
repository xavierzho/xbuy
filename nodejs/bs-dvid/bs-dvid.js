/**
 * 百度浩天SDK BS-DVID 获取工具 (JavaScript 移植版)
 * 通过网络请求获取 BS-DVID token
 *
 * 权威实现本文件；Python 见仓库 bs_dvid/（与 JS 逻辑对齐，JS 保留作对照）。
 */

const crypto = require('crypto');
const path = require('path');
const https = require('https');
const zlib = require('zlib');
const { HttpsProxyAgent } = require('https-proxy-agent');
const { SocksProxyAgent } = require('socks-proxy-agent');
const { loadVersion } = require('../shared/paths.cjs');
const { appVersion: DEFAULT_APP_VERSION } = loadVersion();

// ==================== 常量配置 ====================

const APP_KEY = '953300288';
const SEC_KEY = 'a287f8172fdf58f0a5d61aa7fbd0bac3';
const BASE_URL = 'haotian.baidu.com';
const AES_IV = Buffer.alloc(16, 0); // 16字节全0的IV

// 设备配置基础池：机型与固件号可对公开资料核对；浩天链路是否成功还取决于网络、账号与 SDK 全流程，无法仅靠本表「保证」通过。
const DEVICE_CONFIGS_BASE = [
    // ==================== Samsung 系列 ====================
    // Samsung Galaxy S24 Ultra (SM-S928B)
    {
        manufacturer: "samsung",
        device: "SM-S928B",
        codename: "dm3q",
        cpu_arch: "arm64-v8a",
        chipset: "s5e9945",
        chip_vendor: "samsung",
        android_version: "14",
        sdk_version: "34",
        build_id: "UP1A.231005.007.S928BXXS4AYAA",
        firmware: "S928BXXS4AYAA",
        build_tags: "user/release-keys"
    },
    // Samsung Galaxy S24+ (SM-S926B)
    {
        manufacturer: "samsung",
        device: "SM-S926B",
        codename: "dm2q",
        cpu_arch: "arm64-v8a",
        chipset: "s5e9945",
        chip_vendor: "samsung",
        android_version: "14",
        sdk_version: "34",
        build_id: "UP1A.231005.007.S926BXXS3AXL1",
        firmware: "S926BXXS3AXL1",
        build_tags: "user/release-keys"
    },
    // Samsung Galaxy S24 (SM-S921B)
    {
        manufacturer: "samsung",
        device: "SM-S921B",
        codename: "dm1q",
        cpu_arch: "arm64-v8a",
        chipset: "s5e9945",
        chip_vendor: "samsung",
        android_version: "14",
        sdk_version: "34",
        build_id: "UP1A.231005.007.S921BXXS3AXL1",
        firmware: "S921BXXS3AXL1",
        build_tags: "user/release-keys"
    },
    // Samsung Galaxy S23 Ultra 国行
    {
        manufacturer: "samsung",
        device: "SM-S9180",
        codename: "dm3q",
        cpu_arch: "arm64-v8a",
        chipset: "lahaina",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "UP1A.231005.007.S9180ZCUBXL1",
        firmware: "S9180ZCUBXL1",
        build_tags: "user/release-keys"
    },
    // Samsung Galaxy Z Fold5
    {
        manufacturer: "samsung",
        device: "SM-F946B",
        codename: "q5q",
        cpu_arch: "arm64-v8a",
        chipset: "kalama",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "UP1A.231005.007.F946BXXS3BXL1",
        firmware: "F946BXXS3BXL1",
        build_tags: "user/release-keys"
    },

    // ==================== Xiaomi 系列 ====================
    // Xiaomi 14 (houji)
    {
        manufacturer: "xiaomi",
        device: "houji",
        codename: "houji",
        cpu_arch: "arm64-v8a",
        chipset: "shima",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "UKQ1.231108.001.OS1.0.33.0.UNCCNXM",
        firmware: "OS1.0.33.0.UNCCNXM",
        build_tags: "user/release-keys"
    },
    // Xiaomi 14 Pro (shennong)
    {
        manufacturer: "xiaomi",
        device: "shennong",
        codename: "shennong",
        cpu_arch: "arm64-v8a",
        chipset: "shima",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "UKQ1.231108.001.OS1.0.28.0.UNACNXM",
        firmware: "OS1.0.28.0.UNACNXM",
        build_tags: "user/release-keys"
    },
    // Xiaomi 13 (fuxi)
    {
        manufacturer: "xiaomi",
        device: "fuxi",
        codename: "fuxi",
        cpu_arch: "arm64-v8a",
        chipset: "taro",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "UKQ1.231108.001.V816.0.10.0.UMCCNXM",
        firmware: "V816.0.10.0.UMCCNXM",
        build_tags: "user/release-keys"
    },
    // Xiaomi 13 Ultra (ishtar)
    {
        manufacturer: "xiaomi",
        device: "ishtar",
        codename: "ishtar",
        cpu_arch: "arm64-v8a",
        chipset: "kalama",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "UKQ1.231108.001.V816.0.8.0.UMACNXM",
        firmware: "V816.0.8.0.UMACNXM",
        build_tags: "user/release-keys"
    },
    // Redmi K70 Pro (corot)
    {
        manufacturer: "xiaomi",
        device: "corot",
        codename: "corot",
        cpu_arch: "arm64-v8a",
        chipset: "shima",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "UKQ1.231108.001.V816.0.5.0.UNKFNXM",
        firmware: "V816.0.5.0.UNKFNXM",
        build_tags: "user/release-keys"
    },

    // ==================== OPPO 系列 ====================
    // OPPO Find X7 (PHV110)
    {
        manufacturer: "oppo",
        device: "PHV110",
        codename: "PHV110",
        cpu_arch: "arm64-v8a",
        chipset: "mt6989",
        chip_vendor: "mtk",
        android_version: "14",
        sdk_version: "34",
        build_id: "PHV110_14.0.1.435(CN01)",
        firmware: "PHV110_14.0.1.435",
        build_tags: "user/release-keys"
    },
    // OPPO Find X7 Ultra (PHY110)
    {
        manufacturer: "oppo",
        device: "PHY110",
        codename: "PHY110",
        cpu_arch: "arm64-v8a",
        chipset: "mt6989",
        chip_vendor: "mtk",
        android_version: "14",
        sdk_version: "34",
        build_id: "PHY110_14.0.1.540(CN01)",
        firmware: "PHY110_14.0.1.540",
        build_tags: "user/release-keys"
    },
    // OPPO Reno11 Pro (PJZ110)
    {
        manufacturer: "oppo",
        device: "PJZ110",
        codename: "PJZ110",
        cpu_arch: "arm64-v8a",
        chipset: "mt6895",
        chip_vendor: "mtk",
        android_version: "14",
        sdk_version: "34",
        build_id: "PJZ110_14.0.0.860(CN01)",
        firmware: "PJZ110_14.0.0.860",
        build_tags: "user/release-keys"
    },

    // ==================== vivo 系列 ====================
    // vivo X100 Pro (V2310A)
    {
        manufacturer: "vivo",
        device: "V2310A",
        codename: "V2310A",
        cpu_arch: "arm64-v8a",
        chipset: "mt6989",
        chip_vendor: "mtk",
        android_version: "14",
        sdk_version: "34",
        build_id: "V2310A_A_14.0.19.0.W10",
        firmware: "V2310A_A_14.0.19.0",
        build_tags: "user/release-keys"
    },
    // vivo X100 (V2309A)
    {
        manufacturer: "vivo",
        device: "V2309A",
        codename: "V2309A",
        cpu_arch: "arm64-v8a",
        chipset: "mt6989",
        chip_vendor: "mtk",
        android_version: "14",
        sdk_version: "34",
        build_id: "V2309A_A_14.0.18.0.W10",
        firmware: "V2309A_A_14.0.18.0",
        build_tags: "user/release-keys"
    },
    // vivo X90 Pro+ (V2227A)
    {
        manufacturer: "vivo",
        device: "V2227A",
        codename: "V2227A",
        cpu_arch: "arm64-v8a",
        chipset: "taro",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "V2227A_A_14.0.12.0.W10",
        firmware: "V2227A_A_14.0.12.0",
        build_tags: "user/release-keys"
    },
    // iQOO 12 Pro (V2307A)
    {
        manufacturer: "vivo",
        device: "V2307A",
        codename: "V2307A",
        cpu_arch: "arm64-v8a",
        chipset: "shima",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "V2307A_A_14.0.16.0.W10",
        firmware: "V2307A_A_14.0.16.0",
        build_tags: "user/release-keys"
    },

    // ==================== Huawei 系列 ====================
    // Huawei Mate 60 Pro (ALN-AL00)
    {
        manufacturer: "huawei",
        device: "ALN-AL00",
        codename: "ALN",
        cpu_arch: "arm64-v8a",
        chipset: "kirin9000s",
        chip_vendor: "kirin",
        android_version: "12",
        sdk_version: "32",
        build_id: "ALN-AL00 4.0.0.162(C00E160R3P6)",
        firmware: "ALN-AL00 4.0.0.162",
        build_tags: "user/release-keys"
    },
    // Huawei Mate 50 Pro (DCO-AL00)
    {
        manufacturer: "huawei",
        device: "DCO-AL00",
        codename: "DCO",
        cpu_arch: "arm64-v8a",
        chipset: "lahaina",
        chip_vendor: "qcom",
        android_version: "12",
        sdk_version: "32",
        build_id: "DCO-AL00 4.0.0.128(C00E125R2P1)",
        firmware: "DCO-AL00 4.0.0.128",
        build_tags: "user/release-keys"
    },
    // Honor Magic6 Pro (BVL-AN00)
    {
        manufacturer: "honor",
        device: "BVL-AN00",
        codename: "BVL",
        cpu_arch: "arm64-v8a",
        chipset: "shima",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "BVL-AN00 8.0.0.168(C00E160R3P1)",
        firmware: "BVL-AN00 8.0.0.168",
        build_tags: "user/release-keys"
    },

    // ==================== Google Pixel 系列 ====================
    // Google Pixel 8 Pro (husky)
    {
        manufacturer: "google",
        device: "husky",
        codename: "husky",
        cpu_arch: "arm64-v8a",
        chipset: "zuma",
        chip_vendor: "google",
        android_version: "14",
        sdk_version: "34",
        build_id: "AP2A.240805.005",
        firmware: "AP2A.240805.005",
        build_tags: "user/release-keys"
    },
    // Google Pixel 8 (shiba)
    {
        manufacturer: "google",
        device: "shiba",
        codename: "shiba",
        cpu_arch: "arm64-v8a",
        chipset: "zuma",
        chip_vendor: "google",
        android_version: "14",
        sdk_version: "34",
        build_id: "AP2A.240805.005",
        firmware: "AP2A.240805.005",
        build_tags: "user/release-keys"
    },
    // Google Pixel 7 Pro (cheetah)
    {
        manufacturer: "google",
        device: "cheetah",
        codename: "cheetah",
        cpu_arch: "arm64-v8a",
        chipset: "cloudripper",
        chip_vendor: "google",
        android_version: "14",
        sdk_version: "34",
        build_id: "AP2A.240805.005",
        firmware: "AP2A.240805.005",
        build_tags: "user/release-keys"
    },

    // ==================== OnePlus 系列 ====================
    // OnePlus 12 (CPH2581)
    {
        manufacturer: "oneplus",
        device: "CPH2581",
        codename: "salami",
        cpu_arch: "arm64-v8a",
        chipset: "shima",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "CPH2581_14.0.0.820(EX01)",
        firmware: "CPH2581_14.0.0.820",
        build_tags: "user/release-keys"
    },
    // OnePlus 11 (CPH2449)
    {
        manufacturer: "oneplus",
        device: "CPH2449",
        codename: "aston",
        cpu_arch: "arm64-v8a",
        chipset: "kalama",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "CPH2449_14.0.0.620(CN01)",
        firmware: "CPH2449_14.0.0.620",
        build_tags: "user/release-keys"
    },

    // ==================== 其他热门机型 ====================
    // Realme GT5 Pro (RMX3888)
    {
        manufacturer: "realme",
        device: "RMX3888",
        codename: "RMX3888",
        cpu_arch: "arm64-v8a",
        chipset: "shima",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "RMX3888_14.0.0.680(CN01)",
        firmware: "RMX3888_14.0.0.680",
        build_tags: "user/release-keys"
    },
    // Nothing Phone (2)
    {
        manufacturer: "nothing",
        device: "A065",
        codename: "pong",
        cpu_arch: "arm64-v8a",
        chipset: "taro",
        chip_vendor: "qcom",
        android_version: "14",
        sdk_version: "34",
        build_id: "A065_2.5.6.2",
        firmware: "A065_2.5.6.2",
        build_tags: "user/release-keys"
    },

    // ==================== 2025年新机型 ====================

    // ========== Samsung 2025 ==========
    // 固件号：SamMobile 等公开固件库可查（例：S938BXXS4AYF1 / S936BXXS4AYF1 / S931BXXS4AYF1）。
    // build_id：按常见 Android fingerprint 习惯与固件号拼接，便于与模板内其它字段自洽（非从真机 getprop 逐字拷贝）。
    // Samsung Galaxy S25 Ultra (SM-S938B) - 2025年1月发布
    {
        manufacturer: "samsung",
        device: "SM-S938B",
        codename: "e3q",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "AP4A.250612.002.S938BXXS4AYF1",
        firmware: "S938BXXS4AYF1",
        build_tags: "user/release-keys"
    },
    // Samsung Galaxy S25+ (SM-S936B)
    {
        manufacturer: "samsung",
        device: "SM-S936B",
        codename: "e2q",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "AP4A.250612.002.S936BXXS4AYF1",
        firmware: "S936BXXS4AYF1",
        build_tags: "user/release-keys"
    },
    // Samsung Galaxy S25 (SM-S931B)
    {
        manufacturer: "samsung",
        device: "SM-S931B",
        codename: "e1q",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "AP4A.250612.002.S931BXXS4AYF1",
        firmware: "S931BXXS4AYF1",
        build_tags: "user/release-keys"
    },
    // 同型号 SM-S938B 的另一官方构建分支（Android 16，Build Number 见 Samsung Notify Update 文档）
    {
        manufacturer: "samsung",
        device: "SM-S938B",
        codename: "e3q",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "16",
        sdk_version: "36",
        build_id: "BP2A.260409.002.S938BXXS9BZCH",
        firmware: "S938BXXS9BZCH",
        build_tags: "user/release-keys"
    },
    // Samsung Galaxy Z Fold6 (SM-F956B)
    {
        manufacturer: "samsung",
        device: "SM-F956B",
        codename: "q6q",
        cpu_arch: "arm64-v8a",
        chipset: "sm8650",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "AP4A.250105.002.F956BXXU2BYA1",
        firmware: "F956BXXU2BYA1",
        build_tags: "user/release-keys"
    },

    // ========== Xiaomi 2025 ==========
    // Xiaomi 15 (dijia) - 2024年10月发布，2025年主力
    {
        manufacturer: "xiaomi",
        device: "dijia",
        codename: "dijia",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "VKQ1.250106.001.OS2.0.8.0.VNDCNXM",
        firmware: "OS2.0.8.0.VNDCNXM",
        build_tags: "user/release-keys"
    },
    // Xiaomi 15 Pro (haotian)
    {
        manufacturer: "xiaomi",
        device: "haotian",
        codename: "haotian",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "VKQ1.250106.001.OS2.0.216.0.VOBCNXM",
        firmware: "OS2.0.216.0.VOBCNXM",
        build_tags: "user/release-keys"
    },
    // Xiaomi 15 Ultra (xuanyuan) - 2025年2月发布
    {
        manufacturer: "xiaomi",
        device: "xuanyuan",
        codename: "xuanyuan",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "VKQ1.250106.001.OS2.0.5.0.VNECNXM",
        firmware: "OS2.0.5.0.VNECNXM",
        build_tags: "user/release-keys"
    },
    // Redmi K80 Pro (chenfeng)
    {
        manufacturer: "xiaomi",
        device: "chenfeng",
        codename: "chenfeng",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "VKQ1.250106.001.OS2.0.3.0.VNLCNXM",
        firmware: "OS2.0.3.0.VNLCNXM",
        build_tags: "user/release-keys"
    },

    // ========== OPPO 2025 ==========
    // OPPO Find X8 (CPH2651) - 2024年10月发布
    {
        manufacturer: "oppo",
        device: "CPH2651",
        codename: "CPH2651",
        cpu_arch: "arm64-v8a",
        chipset: "mt6991",
        chip_vendor: "mtk",
        android_version: "15",
        sdk_version: "35",
        build_id: "CPH2651_15.0.0.840(EX01)",
        firmware: "CPH2651_15.0.0.840",
        build_tags: "user/release-keys"
    },
    // OPPO Find X8 Pro (CPH2641)
    {
        manufacturer: "oppo",
        device: "CPH2641",
        codename: "CPH2641",
        cpu_arch: "arm64-v8a",
        chipset: "mt6991",
        chip_vendor: "mtk",
        android_version: "15",
        sdk_version: "35",
        build_id: "CPH2641_15.0.0.820(EX01)",
        firmware: "CPH2641_15.0.0.820",
        build_tags: "user/release-keys"
    },
    // OPPO Find N5 (PKD110) - 2025年2月发布
    {
        manufacturer: "oppo",
        device: "PKD110",
        codename: "PKD110",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "PKD110_15.0.0.310(CN01)",
        firmware: "PKD110_15.0.0.310",
        build_tags: "user/release-keys"
    },

    // ========== vivo 2025 ==========
    // vivo X200 Pro (PD2405A) - 2024年10月发布
    {
        manufacturer: "vivo",
        device: "PD2405A",
        codename: "PD2405A",
        cpu_arch: "arm64-v8a",
        chipset: "mt6991",
        chip_vendor: "mtk",
        android_version: "15",
        sdk_version: "35",
        build_id: "PD2405A_A_15.0.25.30.W10",
        firmware: "PD2405A_A_15.0.25.30",
        build_tags: "user/release-keys"
    },
    // vivo X200 (PD2404A)
    {
        manufacturer: "vivo",
        device: "PD2404A",
        codename: "PD2404A",
        cpu_arch: "arm64-v8a",
        chipset: "mt6991",
        chip_vendor: "mtk",
        android_version: "15",
        sdk_version: "35",
        build_id: "PD2404A_A_15.0.22.0.W10",
        firmware: "PD2404A_A_15.0.22.0",
        build_tags: "user/release-keys"
    },
    // iQOO 13 (V2329A) - 2024年10月发布
    {
        manufacturer: "vivo",
        device: "V2329A",
        codename: "V2329A",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "V2329A_A_15.0.8.0.W10",
        firmware: "V2329A_A_15.0.8.0",
        build_tags: "user/release-keys"
    },

    // ========== Huawei/Honor 2025 ==========
    // Huawei Pura 70 Ultra (LNA-AL00) - 2024年4月发布，2025年热门
    {
        manufacturer: "huawei",
        device: "LNA-AL00",
        codename: "LNA",
        cpu_arch: "arm64-v8a",
        chipset: "kirin9010",
        chip_vendor: "kirin",
        android_version: "12",
        sdk_version: "32",
        build_id: "LNA-AL00 4.2.0.225(C00E220R1P1)",
        firmware: "LNA-AL00 4.2.0.225",
        build_tags: "user/release-keys"
    },
    // Huawei Pura 70 Pro (HBN-AL00)
    {
        manufacturer: "huawei",
        device: "HBN-AL00",
        codename: "HBN",
        cpu_arch: "arm64-v8a",
        chipset: "kirin9010",
        chip_vendor: "kirin",
        android_version: "12",
        sdk_version: "32",
        build_id: "HBN-AL00 4.2.0.186(C00E185R1P1)",
        firmware: "HBN-AL00 4.2.0.186",
        build_tags: "user/release-keys"
    },
    // Honor Magic7 Pro (PTP-AN00) - 2024年10月发布
    {
        manufacturer: "honor",
        device: "PTP-AN00",
        codename: "PTP",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "PTP-AN00 8.0.0.185(C00E182R1P1)",
        firmware: "PTP-AN00 8.0.0.185",
        build_tags: "user/release-keys"
    },

    // ========== Google Pixel 2025 ==========
    // Google Pixel 9 Pro (caiman) - 2024年8月发布
    {
        manufacturer: "google",
        device: "caiman",
        codename: "caiman",
        cpu_arch: "arm64-v8a",
        chipset: "zumapro",
        chip_vendor: "google",
        android_version: "15",
        sdk_version: "35",
        build_id: "AP4A.250105.002",
        firmware: "AP4A.250105.002",
        build_tags: "user/release-keys"
    },
    // Google Pixel 9 Pro XL (komodo)
    {
        manufacturer: "google",
        device: "komodo",
        codename: "komodo",
        cpu_arch: "arm64-v8a",
        chipset: "zumapro",
        chip_vendor: "google",
        android_version: "15",
        sdk_version: "35",
        build_id: "AP4A.250105.002",
        firmware: "AP4A.250105.002",
        build_tags: "user/release-keys"
    },
    // Google Pixel 9 (tokay)
    {
        manufacturer: "google",
        device: "tokay",
        codename: "tokay",
        cpu_arch: "arm64-v8a",
        chipset: "zumapro",
        chip_vendor: "google",
        android_version: "15",
        sdk_version: "35",
        build_id: "AP4A.250105.002",
        firmware: "AP4A.250105.002",
        build_tags: "user/release-keys"
    },
    // Google Pixel 9 Pro Fold (comet)
    {
        manufacturer: "google",
        device: "comet",
        codename: "comet",
        cpu_arch: "arm64-v8a",
        chipset: "zumapro",
        chip_vendor: "google",
        android_version: "15",
        sdk_version: "35",
        build_id: "AP4A.250105.002",
        firmware: "AP4A.250105.002",
        build_tags: "user/release-keys"
    },

    // ========== OnePlus 2025 ==========
    // OnePlus 13 (CPH2655) - 2024年10月发布
    {
        manufacturer: "oneplus",
        device: "CPH2655",
        codename: "taro",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "CPH2655_15.0.0.850(EX01)",
        firmware: "CPH2655_15.0.0.850",
        build_tags: "user/release-keys"
    },
    // OnePlus 13R (CPH2645)
    {
        manufacturer: "oneplus",
        device: "CPH2645",
        codename: "aston",
        cpu_arch: "arm64-v8a",
        chipset: "sm8650",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "CPH2645_15.0.0.520(CN01)",
        firmware: "CPH2645_15.0.0.520",
        build_tags: "user/release-keys"
    },

    // ========== 其他2025热门 ==========
    // Realme GT7 Pro (RMX5000) - 2024年11月发布
    {
        manufacturer: "realme",
        device: "RMX5000",
        codename: "RMX5000",
        cpu_arch: "arm64-v8a",
        chipset: "sm8750",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "RMX5000_15.0.0.580(CN01)",
        firmware: "RMX5000_15.0.0.580",
        build_tags: "user/release-keys"
    },
    // Nothing Phone (3)
    {
        manufacturer: "nothing",
        device: "A073",
        codename: "pacman",
        cpu_arch: "arm64-v8a",
        chipset: "sm8650",
        chip_vendor: "qcom",
        android_version: "15",
        sdk_version: "35",
        build_id: "A073_3.0.2.0",
        firmware: "A073_3.0.2.0",
        build_tags: "user/release-keys"
    }

];

// 对外使用的设备池：固定为基础池 53 条，不扩展变体。
const DEVICE_CONFIGS = DEVICE_CONFIGS_BASE.slice();

// 输入法服务列表
const IME_SERVICES = [
    "1-com.samsung.android.honeyboard/.service.HoneyBoardService;2-com.samsung.android.honeyboard-com.samsung.android.honeyboard.settings.common.HoneyBoardSettingsActivity-0;",
    "1-com.baidu.input/.ImeService;2-com.baidu.input-com.baidu.input.settings.SettingsActivity-0;",
    "1-com.sohu.input/.ImeService;2-com.sohu.input-com.sohu.input.settings.SettingsActivity-0;",
    "1-com.tencent.qqpinyin/.QQPYInputMethodService;2-com.tencent.qqpinyin-com.tencent.qqpinyin.settings.SettingsActivity-0;",
    "1-com.google.android.inputmethod.pinyin/.PinyinIME;2-com.google.android.inputmethod.pinyin-com.google.android.inputmethod.pinyin.settings.SettingsActivity-0;"
];

// 应用签名
const APP_SIGNATURE = "308204a830820390a003020102020900d7cb412f75f4887e300d06092a864886f70d01010b0500308194310b3009060355040613025553311330110603550408130a43616c69666f726e6961311630140603550407130d53616e204672616e636973636f310d300b060355040a13044d6f7574616931133011060355040b130a4d6f757461692041707031143012060355040313";

// DNS服务器列表
const DNS_SERVERS = ["223.5.5.5", "114.114.114.114", "8.8.8.8", "180.76.76.76", "119.29.29.29"];

// 网关IP列表
const GATEWAY_IPS = ["192.168.1.1", "192.168.0.1", "192.168.3.1", "192.168.31.1", "10.0.0.1"];

// ==================== 工具函数 ====================

/**
 * 计算MD5哈希
 */
function md5Hash(data) {
    return crypto.createHash('md5').update(data, 'utf8').digest('hex');
}

/**
 * 从数组中随机选择一个元素
 */
function randomChoice(arr) {
    return arr[Math.floor(Math.random() * arr.length)];
}

/**
 * 生成随机整数
 */
function randomInt(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}

/**
 * 生成随机字符串密钥
 */
function generateRandomKey(length = 16) {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let result = '';
    for (let i = 0; i < length; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }
    return Buffer.from(result, 'utf8');
}

/**
 * 计算Luhn校验位
 */
function calculateLuhnCheckDigit(digits) {
    let total = 0;
    for (let i = 0; i < digits.length; i++) {
        let digit = parseInt(digits[i], 10);
        if ((digits.length - i) % 2 === 0) {
            digit *= 2;
            if (digit > 9) digit -= 9;
        }
        total += digit;
    }
    return String((10 - (total % 10)) % 10);
}

/**
 * 生成随机IMEI（带Luhn校验）
 */
function generateImei() {
    let imei = '35';
    for (let i = 0; i < 12; i++) {
        imei += String(randomInt(0, 9));
    }
    imei += calculateLuhnCheckDigit(imei);
    return imei;
}

// ==================== 设备指纹生成 ====================

function getRandomDeviceConfig() {
    return { ...randomChoice(DEVICE_CONFIGS) };
}

function generateBuildFingerprint(config) {
    return `${config.manufacturer}/${config.device}/${config.codename}:${config.android_version}/${config.build_id}/${config.build_tags}`;
}

function generateDeviceHardwareInfo(config) {
    return {
        "0": config.cpu_arch,
        "2": config.sdk_version,
        "3": config.build_id,
        "4": config.device,
        "6": config.chipset,
        "10": config.firmware,
        "11": config.chip_vendor
    };
}

function generateHostsContent() {
    return "127.0.0.1       localhost;::1             ip6-localhost";
}

function generateNetworkConfig() {
    return {
        "1": generateHostsContent(),
        "2": randomChoice(DNS_SERVERS)
    };
}

function generateCertificateHashes(includeSystemCerts = false) {
    const appCertHash = md5Hash(APP_SIGNATURE);
    const hash1 = [appCertHash];
    const hash2 = includeSystemCerts
        ? [appCertHash, "f0eddd1d306b6e402b0fe5b49e4e4017"]
        : [appCertHash];
    return { hash1, hash2 };
}

function generateDetectionResults() {
    return {
        "1": "-1:oops*abort",
        "2": "-1:tracer:*c",
        "12": {
            "0": "0", "1": "0", "2": "0", "3": "0",
            "4": "0", "5": "0", "6": "0", "7": "0",
            "8": "0", "9": "0", "10": "0", "11": "0",
            "12": "0", "13": "0", "14": "0", "15": "0"
        }
    };
}

function generateNormalStackTrace() {
    return "java.lang.Throwable;;at com.baidu.haotian.x0.EngineImpl.ice(Unknown Source:6);;at com.baidu.haotian.x0.EngineImpl.ice(Unknown Source:2);;at java.lang.reflect.Method.invoke(Native Method);;at com.baidu.haotian.r.a(EngineFactory.java:10);;at com.baidu.haotian.engine.EngineImpl.ice(EngineImpl.java:9);;at java.lang.reflect.Method.invoke(Native Method);;at com.baidu.haotian.b0.a(CommonMethods.java:128);;at com.baidu.haotian.c.a(ForHostApp.java:51);;at com.baidu.haotian.d$b.run(MethodImpl.java:45);;at java.util.concurrent.ThreadPoolExecutor.runWorker(ThreadPoolExecutor.java:1145);;at java.util.concurrent.ThreadPoolExecutor$Worker.run(ThreadPoolExecutor.java:644);;at java.lang.Thread.run(Thread.java:1012);";
}

function generateGatewayIp() {
    return randomChoice(GATEWAY_IPS);
}

function generateDeviceFingerprint(includeSystemCerts = false) {
    const config = getRandomDeviceConfig();
    return generateDeviceFingerprintFromConfig(config, { includeSystemCerts });
}

function generateDeviceFingerprintFromConfig(deviceConfig, options = {}) {
    const config = { ...deviceConfig };
    const overrides = options.overrides || {};
    const { hash1, hash2 } = generateCertificateHashes(!!options.includeSystemCerts);
    const dns = overrides.dns || randomChoice(DNS_SERVERS);
    const gatewayIp = overrides.gateway_ip || options.gatewayIp || randomChoice(GATEWAY_IPS);
    const battery = overrides.battery_level || `${randomInt(20, 100)}%`;
    const sensors = overrides.sensor_count || String(randomInt(2, 4));
    const ime = overrides.ime_service || randomChoice(IME_SERVICES);
    return {
        device_config: config,
        hardware_info: generateDeviceHardwareInfo(config),
        build_fingerprint: generateBuildFingerprint(config),
        network_config: { "1": generateHostsContent(), "2": dns },
        certificate_hash_91: hash1,
        certificate_hash_92: hash2,
        ime_service: ime,
        detection_results: generateDetectionResults(),
        stack_trace: generateNormalStackTrace(),
        battery_level: battery,
        sensor_count: sensors,
        gateway_ip: gatewayIp,
    };
}

// ==================== 加密函数 ====================

/**
 * RC4 Key Scheduling Algorithm (KSA)
 */
function rc4Ksa(key) {
    const S = Array.from({ length: 256 }, (_, i) => i);
    let j = 0;
    for (let i = 0; i < 256; i++) {
        j = (j + S[i] + key[i % key.length]) % 256;
        [S[i], S[j]] = [S[j], S[i]];
    }
    return S;
}

/**
 * RC4 PRGA with XOR 0x2A (变种)
 */
function rc4PrgaXor(S, data) {
    S = [...S];
    let i = 0, j = 0;
    const result = Buffer.alloc(data.length);
    for (let idx = 0; idx < data.length; idx++) {
        i = (i + 1) % 256;
        j = (j + S[i]) % 256;
        [S[i], S[j]] = [S[j], S[i]];
        const k = S[(S[i] + S[j]) % 256];
        // 关键: XOR 0x2A
        result[idx] = data[idx] ^ k ^ 0x2A;
    }
    return result;
}

/**
 * RC4 变种加密
 */
function rc4Encrypt(data, key) {
    const S = rc4Ksa(key);
    return rc4PrgaXor(S, data);
}

/**
 * RC4 变种解密 (对称的)
 */
function rc4Decrypt(data, key) {
    return rc4Encrypt(data, key);
}

/**
 * AES-128-CBC 加密 (PKCS7 填充) + MD5 校验
 */
function aesEncryptCbc(data, key) {
    // 确保密钥长度为16字节
    let keyBuffer = Buffer.isBuffer(key) ? key : Buffer.from(key, 'utf8');
    if (keyBuffer.length < 16) {
        keyBuffer = Buffer.concat([keyBuffer, Buffer.alloc(16 - keyBuffer.length, 0)]);
    } else if (keyBuffer.length > 16) {
        keyBuffer = keyBuffer.slice(0, 16);
    }

    const cipher = crypto.createCipheriv('aes-128-cbc', keyBuffer, AES_IV);
    const encrypted = Buffer.concat([cipher.update(data), cipher.final()]);

    // 追加 MD5 校验 (原始数据的 MD5)
    const md5Checksum = crypto.createHash('md5').update(data).digest();
    return Buffer.concat([encrypted, md5Checksum]);
}

/**
 * AES-128-CBC 解密 (PKCS7 填充)
 */
function aesDecryptCbc(data, key) {
    // 确保密钥长度为16字节
    let keyBuffer = Buffer.isBuffer(key) ? key : Buffer.from(key, 'utf8');
    if (keyBuffer.length < 16) {
        keyBuffer = Buffer.concat([keyBuffer, Buffer.alloc(16 - keyBuffer.length, 0)]);
    } else if (keyBuffer.length > 16) {
        keyBuffer = keyBuffer.slice(0, 16);
    }

    // 去除末尾 16 字节 MD5 校验
    let encryptedData = data;
    if (data.length > 16) {
        encryptedData = data.slice(0, -16);
    }

    try {
        const decipher = crypto.createDecipheriv('aes-128-cbc', keyBuffer, AES_IV);
        return Buffer.concat([decipher.update(encryptedData), decipher.final()]);
    } catch (e) {
        // 如果解密失败，尝试不去掉padding
        const decipher = crypto.createDecipheriv('aes-128-cbc', keyBuffer, AES_IV);
        decipher.setAutoPadding(false);
        return Buffer.concat([decipher.update(encryptedData), decipher.final()]);
    }
}

// ==================== 本地ID生成 ====================

/**
 * 生成本地临时ID
 * 格式: MD5(device_id).upper() + "|0"
 */
function generateLocalId(deviceId = null) {
    if (!deviceId) {
        deviceId = generateImei();
    }
    const md5DeviceId = md5Hash(deviceId).toUpperCase();
    return md5DeviceId + "|0";
}

/**
 * 获取应用签名的MD5
 */
function getAppSignatureMd5() {
    return md5Hash(APP_SIGNATURE);
}

// ==================== 请求体构建 ====================

/**
 * 构建基础请求体
 */
function buildRequestBody(localId, appVersion = DEFAULT_APP_VERSION) {
    return {
        "1": {
            "1": localId,
            "3": "34",
            "4": "3.6.8.0",
            "5": "i茅台",
            "6": "com.moutai.mall",
            "7": getAppSignatureMd5(),
            "8": appVersion
        }
    };
}

/**
 * 构建SDK事件上报数据
 */
function buildReportEvents(localId, appVersion = DEFAULT_APP_VERSION, timestampMs = null) {
    timestampMs = timestampMs == null ? Date.now() : Number(timestampMs);
    const eventId = md5Hash(`${localId}${timestampMs}`);
    const eventTag = `1|${eventId}`;

    const baseEvent = {
        "1": "i茅台",
        "2": "com.moutai.mall",
        "3": appVersion,
        "4": localId,
        "7": "0",
        "8": APP_KEY,
        "9": "haotian",
        "10": "3.6.8.0",
        "11": "",
        "12": "",
        "13": 1,
        "14": 1,
        "28": eventTag,
    };

    return [
        { ...baseEvent, "5": "1003003", "6": timestampMs, module_section: [{ "0": "[]", "1": "[]" }] },
        { ...baseEvent, "5": "1003002", "6": timestampMs - 10, module_section: [{}] }
    ];
}

/**
 * 构建设备环境检测上报数据
 */
function buildDeviceEnvReport(localId, timestampMs, appVersion = DEFAULT_APP_VERSION, gatewayIp = null) {
    const localIdUpper = localId.includes('|') ? localId.split('|')[0] : localId;
    const deviceTag = localIdUpper + "F9";
    gatewayIp = gatewayIp || generateGatewayIp();
    const stackTrace = generateNormalStackTrace();

    return {
        "1": "",
        "2": "com.moutai.mall",
        "3": appVersion,
        "4": localId,
        "5": "0",
        "7": "0",
        "8": APP_KEY,
        "9": "haotian_x0",
        "10": "4.4.3.4.3",
        "11": "",
        "12": "",
        "13": "1",
        "30": "",
        "z": localId,
        "t": deviceTag,
        "6": String(timestampMs),
        "14": "1",
        "module_section": [{
            [localId]: {
                "22": [{ "0": "4000", "2": String(timestampMs - 239), "1": "" }],
                "86": "-1",
                "87": stackTrace,
                "53": "", "54": "", "58": "", "59": "", "60": "",
                "96": deviceTag,
                "990": "",
                "989": "3.6.8.0",
                "988": [gatewayIp]
            }
        }]
    };
}

/**
 * 构建完整设备指纹上报数据
 */
function buildFullDeviceFingerprint(localId, timestampMs, gatewayIp = null, fpData = null, appVersion = DEFAULT_APP_VERSION) {
    const localIdUpper = localId.includes('|') ? localId.split('|')[0] : localId;
    const deviceTag = localIdUpper + "F9";

    fpData = fpData || generateDeviceFingerprint();
    gatewayIp = gatewayIp || fpData.gateway_ip || generateGatewayIp();

    // Camera API v1 调用记录
    const cameraApiV1 = [
        "getCameraInfo|int#android.hardware.Camera$CameraInfo|9",
        "getEmptyParameters||9",
        "getNumberOfCameras||265",
        "open||9",
        "open|int|9",
        "openLegacy|int#int|9",
        "addCallbackBuffer|[B|17",
        "addRawImageCallbackBuffer|[B|17",
        "autoFocus|android.hardware.Camera$AutoFocusCallback|17",
        "cancelAutoFocus||17",
        "enableShutterSound|boolean|17",
        "getParameters||1",
        "lock||273",
        "previewEnabled||273",
        "reconnect||273",
        "release||17",
        "setAutoFocusMoveCallback|android.hardware.Camera$AutoFocusMoveCallback|1",
        "setDisplayOrientation|int|273",
        "setErrorCallback|android.hardware.Camera$ErrorCallback|17",
        "setFaceDetectionListener|android.hardware.Camera$FaceDetectionListener|17",
        "setOneShotPreviewCallback|android.hardware.Camera$PreviewCallback|17",
        "setParameters|android.hardware.Camera$Parameters|1",
        "setPreviewCallback|android.hardware.Camera$PreviewCallback|17",
        "setPreviewCallbackWithBuffer|android.hardware.Camera$PreviewCallback|17",
        "setPreviewDisplay|android.view.SurfaceHolder|17",
        "setPreviewTexture|android.graphics.SurfaceTexture|273",
        "setZoomChangeListener|android.hardware.Camera$OnZoomChangeListener|17",
        "startFaceDetection||17",
        "startPreview||273",
        "startSmoothZoom|int|273",
        "stopFaceDetection||17",
        "stopPreview||17",
        "stopSmoothZoom||273",
        "takePicture|android.hardware.Camera$ShutterCallback#android.hardware.Camera$PictureCallback#android.hardware.Camera$PictureCallback|17",
        "takePicture|android.hardware.Camera$ShutterCallback#android.hardware.Camera$PictureCallback#android.hardware.Camera$PictureCallback#android.hardware.Camera$PictureCallback|17",
        "unlock||273"
    ];

    // Camera2 API 调用记录
    const cameraApiV2 = [
        "abortCaptures||1025",
        "capture|android.hardware.camera2.CaptureRequest#android.hardware.camera2.CameraCaptureSession$CaptureCallback#android.os.Handler|1025",
        "captureBurst|java.util.List#android.hardware.camera2.CameraCaptureSession$CaptureCallback#android.os.Handler|1025",
        "captureBurstRequests|java.util.List#java.util.concurrent.Executor#android.hardware.camera2.CameraCaptureSession$CaptureCallback|1",
        "captureSingleRequest|android.hardware.camera2.CaptureRequest#java.util.concurrent.Executor#android.hardware.camera2.CameraCaptureSession$CaptureCallback|1",
        "close||1025",
        "finalizeOutputConfigurations|java.util.List|1025",
        "getDevice||1025",
        "getInputSurface||1025",
        "isReprocessable||1025",
        "prepare|android.view.Surface|1025",
        "setRepeatingBurst|java.util.List#android.hardware.camera2.CameraCaptureSession$CaptureCallback#android.os.Handler|1025",
        "setRepeatingBurstRequests|java.util.List#java.util.concurrent.Executor#android.hardware.camera2.CameraCaptureSession$CaptureCallback|1",
        "setRepeatingRequest|android.hardware.camera2.CaptureRequest#android.hardware.camera2.CameraCaptureSession$CaptureCallback#android.os.Handler|1025",
        "setSingleRepeatingRequest|android.hardware.camera2.CaptureRequest#java.util.concurrent.Executor#android.hardware.camera2.CameraCaptureSession$CaptureCallback|1",
        "stopRepeating||1025",
        "supportsOfflineProcessing|android.view.Surface|1"
    ];

    return {
        "1": "",
        "2": "com.moutai.mall",
        "3": appVersion,
        "4": localId,
        "5": "0",
        "7": "0",
        "8": APP_KEY,
        "9": "haotian_x0",
        "10": "4.4.3.4.3",
        "11": "",
        "12": "",
        "13": "1",
        "30": "",
        "z": localId,
        "t": deviceTag,
        "6": String(timestampMs),
        "14": "1",
        "module_section": [{
            [localId]: {
                "1": fpData.detection_results["1"],
                "13": [],
                "28": [],
                "12": fpData.detection_results["12"],
                "69": "",
                "9": "",
                "2": fpData.detection_results["2"],
                "10": "",
                "11": [],
                "7": "1",
                "6": "0",
                "27": fpData.battery_level,
                "17": fpData.hardware_info,
                "30": "0",
                "31": "0",
                "32": "",
                "46": "",
                "70": "",
                "50": fpData.sensor_count,
                "51": "",
                "52": "4",
                "57": "",
                "61": "",
                "63": "{\"8\":1}",
                "71": "",
                "76": fpData.build_fingerprint,
                "80": "[]",
                "81": "",
                "83": "0",
                "84": "0",
                "85": fpData.network_config,
                "90": "",
                "91": { "1": fpData.certificate_hash_91, "2": "" },
                "92": { "1": fpData.certificate_hash_92, "2": "" },
                "93": fpData.ime_service,
                "97": cameraApiV1,
                "98": cameraApiV2,
                "988": [gatewayIp],
                "989": "3.6.8.0"
            }
        }]
    };
}

/**
 * 构建签名规则请求体
 */
function buildSigRequestBody(localId) {
    return {
        "4": localId,
        "module_section": [{
            "f": {
                "1": "",
                "2": "fe546279a62683de8ca334b673420696",
                "3": "4e79c21720736d1fdbe6ebb60d7ce288",
                "4": "714e9dfbf7367be71c04e07e0adc6d98",
                "5": "fe546279a62683de8ca334b673420696",
                "6": "714e9dfbf7367be71c04e07e0adc6d98",
                "7": "aab3238922bcc25a6f606eb525ffdc56",
                "9": ""
            }
        }]
    };
}

/**
 * 构建Token请求体
 */
function buildTokenRequestBody(localId, timestampMs, magic = null) {
    const localIdUpper = localId.includes('|') ? localId.split('|')[0] : localId;
    const deviceTag = localIdUpper + "F9";

    magic = magic || "HGosZ2mow3zs8LHuBpJ1Tz7+OpuEwIAZiRKjEB5VM1QZJEuDSrNIgngwoJbgMKqNU";

    return {
        "1": "",
        "2": "com.moutai.mall",
        "3": DEFAULT_APP_VERSION,
        "4": localId,
        "5": "fCB7IHOw",
        "6": timestampMs,
        "7": "",
        "8": APP_KEY,
        "9": "haotian_x6",
        "10": "4.4.3.4.3",
        "11": "",
        "12": "",
        "13": 1,
        "14": 1,
        "module_section": [{
            "token": deviceTag,
            "ut": deviceTag,
            "magic": magic,
            "token_rt": "",
            "mz": localId,
            "ds": "2",
            "zid": localId,
            "act_st": "0",
            "chn_st": "0",
            "os_ver": "34",
            "reason": { "1": 1, "2": 0, "3": timestampMs - 3, "4": 3 },
            "tp": "3",
            "tk": deviceTag + "#",
            "pd": "21043",
            "lrc": "0",
            "cc": "",
            "lre": "",
            "ipo": "1",
            "rmf": "",
            "t1": "211",
            "t2": "107",
            "t3": "",
            "15091": "0",
            "15082": `#${timestampMs - 1000}#1#0`,
            "15083": "",
            "15112": "###0",
            "15006": "1",
            "cuid": ""
        }]
    };
}

// ==================== HTTP 请求封装 ====================

/**
 * 创建代理 Agent
 * @param {Object} proxyConfig - 代理配置 { enabled, type, host, port, username, password }
 * @returns {Object|null} 代理 Agent 或 null
 */
function createProxyAgent(proxyConfig) {
    if (!proxyConfig || !proxyConfig.enabled || !proxyConfig.host || !proxyConfig.port) {
        return null;
    }

    const { type, host, port, username, password } = proxyConfig;

    if (type === 'socks5') {
        // SOCKS5 代理
        const socksUrl = username && password
            ? `socks5://${username}:${password}@${host}:${port}`
            : `socks5://${host}:${port}`;
        return new SocksProxyAgent(socksUrl);
    } else {
        // HTTP/HTTPS 代理
        const proxyUrl = username && password
            ? `http://${username}:${password}@${host}:${port}`
            : `http://${host}:${port}`;
        return new HttpsProxyAgent(proxyUrl, { rejectUnauthorized: false });
    }
}

/**
 * 发送加密请求到百度浩天服务器
 * @param {string} urlPath - 请求路径
 * @param {Object} body - 请求体
 * @param {string} localIdMd5 - localId 的 MD5
 * @param {Object} headers - 请求头
 * @param {number} timeout - 超时时间
 * @param {Object} proxyConfig - 代理配置 (可选)
 */
function sendEncryptedRequest(urlPath, body, localIdMd5, headers, timeout = 30000, proxyConfig = null) {
    return new Promise((resolve, reject) => {
        const timestamp = String(Math.floor(Date.now() / 1000));
        const sign = md5Hash(APP_KEY + timestamp + SEC_KEY);

        // 生成随机密钥
        const randomKey = generateRandomKey(16);

        // 序列化并压缩请求体
        const bodyJson = Buffer.from(JSON.stringify(body), 'utf8');
        const bodyGzip = zlib.gzipSync(bodyJson);

        // AES加密
        const bodyEncrypted = aesEncryptCbc(bodyGzip, randomKey);

        // RC4加密skey
        const localIdMd5Bytes = Buffer.from(localIdMd5, 'utf8');
        const skeyEncrypted = rc4Encrypt(randomKey, localIdMd5Bytes);
        const skeyB64 = skeyEncrypted.toString('base64') + "\n";

        // 构建完整URL路径
        const fullPath = `${urlPath}/${APP_KEY}/${timestamp}/${sign}?skey=${encodeURIComponent(skeyB64)}`;

        const options = {
            hostname: BASE_URL,
            port: 443,
            path: fullPath,
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': bodyEncrypted.length,
                'Accept': '*/*',
                'Accept-Encoding': 'gzip,deflate',
                'Accept-Language': 'zh-CN',
                'Pragma': 'no-cache',
                'x-device-id': localIdMd5,
                'x-auth-ver': '2',
                ...headers
            },
            timeout: timeout,
            agent: createProxyAgent(proxyConfig)
        };

        // 如果使用代理，打印日志
        if (proxyConfig && proxyConfig.enabled) {
            console.log(`[BS-DVID] 使用代理: ${proxyConfig.host}:${proxyConfig.port}`);
        }

        const req = https.request(options, (res) => {
            let chunks = [];
            res.on('data', (chunk) => chunks.push(chunk));
            res.on('end', () => {
                try {
                    let responseBuffer = Buffer.concat(chunks);

                    // 检查是否是GZIP压缩的响应
                    const contentEncoding = res.headers['content-encoding'];
                    if (contentEncoding === 'gzip' || (responseBuffer[0] === 0x1f && responseBuffer[1] === 0x8b)) {
                        try {
                            responseBuffer = zlib.gunzipSync(responseBuffer);
                        } catch (e) {
                            // 不是GZIP，继续使用原始数据
                        }
                    }

                    const responseText = responseBuffer.toString('utf8');
                    const respJson = JSON.parse(responseText);

                    // 解密响应
                    if (respJson.response && respJson.skey) {
                        // fetch_bs_dvid 的响应格式
                        const respEncrypted = Buffer.from(respJson.response, 'base64');
                        const respSkeyEncrypted = Buffer.from(respJson.skey, 'base64');
                        const respSkey = rc4Decrypt(respSkeyEncrypted, localIdMd5Bytes);
                        const respDecrypted = aesDecryptCbc(respEncrypted, respSkey);

                        try {
                            const respData = JSON.parse(respDecrypted.toString('utf8'));
                            resolve({
                                success: true,
                                data: respData,
                                requestId: respJson.request_id,
                                raw: respJson
                            });
                        } catch (e) {
                            resolve({
                                success: false,
                                error: 'JSON解析失败',
                                rawDecrypted: respDecrypted.toString('utf8')
                            });
                        }
                    } else if (respJson.data && respJson.skey) {
                        // 其他请求的响应格式
                        const respEncrypted = Buffer.from(respJson.data, 'base64');
                        const respSkeyEncrypted = Buffer.from(respJson.skey, 'base64');
                        const respSkey = rc4Decrypt(respSkeyEncrypted, localIdMd5Bytes);
                        const respDecrypted = aesDecryptCbc(respEncrypted, respSkey);

                        let respData;
                        try {
                            // 尝试GZIP解压
                            const decompressed = zlib.gunzipSync(respDecrypted);
                            respData = JSON.parse(decompressed.toString('utf8'));
                        } catch (e) {
                            // 没有GZIP压缩
                            respData = JSON.parse(respDecrypted.toString('utf8'));
                        }

                        resolve({
                            success: true,
                            data: respData,
                            requestId: respJson.request_id,
                            timestamp: respJson.t,
                            raw: respJson
                        });
                    } else if (respJson.response === 1) {
                        // 上报成功的响应
                        resolve({
                            success: true,
                            requestId: respJson.request_id
                        });
                    } else {
                        resolve({
                            success: false,
                            error: '响应格式错误',
                            raw: respJson
                        });
                    }
                } catch (e) {
                    reject(new Error(`响应解析失败: ${e.message}`));
                }
            });
        });

        req.on('error', (e) => reject(e));
        req.on('timeout', () => {
            req.destroy();
            reject(new Error('请求超时'));
        });

        req.write(bodyEncrypted);
        req.end();
    });
}

// ==================== 7个核心请求函数 ====================

/**
 * 步骤1: 获取初始 BS-DVID
 * @param {string} appVersion - 应用版本
 * @param {string} deviceId - 设备ID
 * @param {Object} proxyConfig - 代理配置 (可选)
 */
async function fetchBsDvid(appVersion = DEFAULT_APP_VERSION, deviceId = null, proxyConfig = null) {
    deviceId = deviceId || generateImei();
    const localId = generateLocalId(deviceId);
    const localIdMd5 = md5Hash(localId);

    console.log('[BS-DVID] 步骤1: 获取初始BS-DVID...');

    const body = buildRequestBody(localId);
    const headers = {
        'User-Agent': `haotian/${APP_KEY}/${appVersion}/3.6.8.0`
    };

    try {
        const result = await sendEncryptedRequest('/p/5/aio/210', body, localIdMd5, headers, 30000, proxyConfig);

        if (result.success && result.data) {
            const bsDvid = result.data["0"] || "";
            return {
                success: true,
                bsDvid: bsDvid,
                localId: localId,
                localIdMd5: localIdMd5,
                deviceId: deviceId,
                rawResponse: result.data,
                requestId: result.requestId
            };
        }
        return { success: false, error: result.error || '获取失败', localId, deviceId };
    } catch (e) {
        return { success: false, error: e.message, localId, deviceId };
    }
}

/**
 * 步骤2: 上报SDK初始化事件
 * @param {string} localId - 本地ID
 * @param {string} appVersion - 应用版本
 * @param {Object} proxyConfig - 代理配置 (可选)
 */
async function reportSdkEvents(localId, appVersion = DEFAULT_APP_VERSION, proxyConfig = null) {
    const localIdMd5 = md5Hash(localId);

    console.log('[BS-DVID] 步骤2: 上报SDK初始化事件...');

    const events = buildReportEvents(localId, appVersion);
    const headers = {
        'User-Agent': `haotian/${APP_KEY}/${appVersion}/3.6.8.0`
    };

    try {
        const result = await sendEncryptedRequest('/p/1/r/210', events, localIdMd5, headers, 30000, proxyConfig);
        return {
            success: result.success,
            requestId: result.requestId,
            error: result.error
        };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

/**
 * 步骤3: 获取SDK配置和风控规则
 * @param {string} localId - 本地ID
 * @param {string} appVersion - 应用版本
 * @param {Object} proxyConfig - 代理配置 (可选)
 */
async function fetchSdkConfig(localId, appVersion = DEFAULT_APP_VERSION, proxyConfig = null) {
    const localIdMd5 = md5Hash(localId);

    console.log('[BS-DVID] 步骤3: 获取SDK配置...');

    const body = buildRequestBody(localId);
    const headers = {
        'User-Agent': `haotian_x0/${APP_KEY}/${appVersion}/4.4.3.4.3`,
        'x-sdk-ver': 'haotian/3.6.8.0',
        'x-plu-ver': 'haotian_x0/4.4.3.4.3',
        'x-app-ver': `com.moutai.mall/${appVersion}`,
        'x-api-ver': '34'
    };

    try {
        const result = await sendEncryptedRequest('/s/5/aio/210', body, localIdMd5, headers, 30000, proxyConfig);
        return {
            success: result.success,
            config: result.data,
            requestId: result.requestId,
            error: result.error
        };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

/**
 * 步骤4: 上报设备环境检测（基础）
 * @param {string} localId - 本地ID
 * @param {string} appVersion - 应用版本
 * @param {Object} proxyConfig - 代理配置 (可选)
 */
async function reportDeviceEnv(localId, appVersion = DEFAULT_APP_VERSION, proxyConfig = null) {
    const localIdMd5 = md5Hash(localId);
    const timestampMs = Date.now();

    console.log('[BS-DVID] 步骤4: 上报设备环境检测...');

    const body = buildDeviceEnvReport(localId, timestampMs, appVersion);
    const headers = {
        'User-Agent': `haotian_x0/${APP_KEY}/${appVersion}/4.4.3.4.3`,
        'x-sdk-ver': 'haotian/3.6.8.0',
        'x-plu-ver': 'haotian_x0/4.4.3.4.3',
        'x-app-ver': `com.moutai.mall/${appVersion}`,
        'x-api-ver': '34'
    };

    try {
        const result = await sendEncryptedRequest('/r/5/c/210', body, localIdMd5, headers, 30000, proxyConfig);

        if (result.success && result.data) {
            // 提取关键信息
            let resultData = {};
            for (const [key, value] of Object.entries(result.data)) {
                if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
                    resultData = value;
                    break;
                }
            }

            return {
                success: true,
                clientIp: resultData["7"] || "",
                detect: resultData.detect || "-1",
                nonce: resultData.nonce || "",
                requestId: result.requestId
            };
        }
        return { success: false, error: result.error };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

/**
 * 步骤5: 获取签名/风控检测规则
 * @param {string} localId - 本地ID
 * @param {string} appVersion - 应用版本
 * @param {Object} proxyConfig - 代理配置 (可选)
 */
async function fetchSigRules(localId, appVersion = DEFAULT_APP_VERSION, proxyConfig = null) {
    const localIdMd5 = md5Hash(localId);

    console.log('[BS-DVID] 步骤5: 获取签名/风控规则...');

    const body = buildSigRequestBody(localId);
    const headers = {
        'User-Agent': `haotian_x6/${APP_KEY}/${appVersion}/4.4.3.4.3`,
        'x-client-src': 'jar',
        'x-sdk-ver': 'haotian/3.6.8.0',
        'x-plu-ver': 'haotian_x6/4.4.3.4.3',
        'x-app-ver': `com.moutai.mall/${appVersion}`,
        'x-api-ver': '34'
    };

    try {
        const result = await sendEncryptedRequest('/f/2/sig/210', body, localIdMd5, headers, 30000, proxyConfig);

        if (result.success && result.data) {
            let sigRules = [];
            if (result.data.sig) {
                try {
                    const sigCompressed = Buffer.from(result.data.sig, 'base64');
                    const sigDecompressed = zlib.gunzipSync(sigCompressed);
                    sigRules = JSON.parse(sigDecompressed.toString('utf8'));
                } catch (e) {
                    // 解析规则失败
                }
            }

            return {
                success: true,
                sigRules: sigRules,
                sigRulesCount: sigRules.length,
                requestId: result.requestId
            };
        }
        return { success: false, error: result.error };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

/**
 * 步骤6: 上报完整设备指纹（深度）
 * @param {string} localId - 本地ID
 * @param {string} appVersion - 应用版本
 * @param {Object} proxyConfig - 代理配置 (可选)
 */
async function reportFullDeviceFingerprint(localId, appVersion = DEFAULT_APP_VERSION, proxyConfig = null, deviceConfig = null) {
    const localIdMd5 = md5Hash(localId);
    const timestampMs = Date.now();

    console.log('[BS-DVID] 步骤6: 上报完整设备指纹...');

    const fpData = deviceConfig
        ? generateDeviceFingerprintFromConfig(deviceConfig)
        : null;
    const body = buildFullDeviceFingerprint(localId, timestampMs, null, fpData);
    const headers = {
        'User-Agent': `haotian_x0/${APP_KEY}/${appVersion}/4.4.3.4.3`,
        'x-sdk-ver': 'haotian/3.6.8.0',
        'x-plu-ver': 'haotian_x0/4.4.3.4.3',
        'x-app-ver': `com.moutai.mall/${appVersion}`,
        'x-api-ver': '34'
    };

    try {
        const result = await sendEncryptedRequest('/r/5/c/210', body, localIdMd5, headers, 30000, proxyConfig);

        if (result.success && result.data) {
            let resultData = {};
            for (const [key, value] of Object.entries(result.data)) {
                if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
                    resultData = value;
                    break;
                }
            }

            return {
                success: true,
                clientIp: resultData["7"] || "",
                detect: resultData.detect || "-1",
                nonce: resultData.nonce || "",
                requestId: result.requestId
            };
        }
        return { success: false, error: result.error };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

/**
 * 步骤7: 获取最终 BS-DVID Token
 * @param {string} localId - 本地ID
 * @param {string} appVersion - 应用版本
 * @param {string} magic - 魔法值 (可选)
 * @param {Object} proxyConfig - 代理配置 (可选)
 */
async function fetchToken(localId, appVersion = DEFAULT_APP_VERSION, magic = null, proxyConfig = null) {
    const localIdMd5 = md5Hash(localId);
    const timestampMs = Date.now();

    console.log('[BS-DVID] 步骤7: 获取最终BS-DVID Token...');

    const body = buildTokenRequestBody(localId, timestampMs, magic);
    const headers = {
        'User-Agent': `haotian_x6/${APP_KEY}/${appVersion}/4.4.3.4.3`,
        'x-client-src': 'jar',
        'x-sdk-ver': 'haotian/3.6.8.0',
        'x-plu-ver': 'haotian_x6/4.4.3.4.3',
        'x-app-ver': `com.moutai.mall/${appVersion}`,
        'x-api-ver': '34'
    };

    try {
        const result = await sendEncryptedRequest('/c/11/z/210', body, localIdMd5, headers, 30000, proxyConfig);

        if (result.success && result.data) {
            return {
                success: true,
                token: result.data.token || "",
                status: result.data.st || "",
                version: result.data.ver || "",
                nc: result.data.nc || "",
                requestId: result.requestId
            };
        }
        return { success: false, error: result.error };
    } catch (e) {
        return { success: false, error: e.message };
    }
}

// ==================== 主函数 ====================

/**
 * 生成i茅台User-Agent
 */
function generateMoutaiUserAgent(appVersion, deviceId, bsDvid, deviceConfig = null) {
    deviceConfig = deviceConfig || getRandomDeviceConfig();

    // UA中的device-id需要两次MD5
    const deviceIdMd5First = md5Hash(deviceId);
    const deviceIdDoubleMd5 = md5Hash(deviceIdMd5First);

    return `Mozilla/5.0 (Linux; Android ${deviceConfig.android_version}; ${deviceConfig.device} Build/${deviceConfig.build_id}; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.88 Mobile Safari/537.36 moutaiapp/${appVersion} device-id/${deviceIdDoubleMd5} BS-DVID/${bsDvid}`;
}

/**
 * 获取 BS-DVID 完整流程 (包含所有7个步骤)
 *
 * @param {Object} options 配置选项
 * @param {string} options.appVersion 应用版本 (默认 DEFAULT_APP_VERSION)
 * @param {string} options.deviceId 设备ID (IMEI格式，可选)
 * @param {boolean} options.fetchConfig 是否获取风控配置 (默认 true)
 * @param {boolean} options.reportEnv 是否上报设备环境检测 (默认 true)
 * @param {boolean} options.reportFullFingerprint 是否上报完整设备指纹 (默认 true)
 * @param {boolean} options.fetchFinalToken 是否获取最终Token (默认 true)
 * @param {Object} options.proxyConfig 代理配置 (可选) { enabled, type, host, port, username, password }
 * @returns {Promise<Object>} 返回包含 bsDvid, finalBsDvid, userAgent 等信息
 */
async function getBsDvidWithReport(options = {}) {
    const {
        appVersion = DEFAULT_APP_VERSION,
        deviceId = null,
        fetchConfig = true,
        reportEnv = true,
        reportFullFingerprint = true,
        fetchFinalToken = true,
        proxyConfig = null
    } = options;

    console.log('========================================');
    console.log('[BS-DVID] 开始获取 BS-DVID (完整流程)');
    if (proxyConfig && proxyConfig.enabled) {
        console.log(`[BS-DVID] 使用代理: ${proxyConfig.host}:${proxyConfig.port}`);
    } else {
        console.log('[BS-DVID] 使用直连模式');
    }
    console.log('========================================');

    const finalDeviceId = deviceId || generateImei();
    const deviceConfig = getRandomDeviceConfig();

    // 步骤1: 获取初始 BS-DVID
    const step1Result = await fetchBsDvid(appVersion, finalDeviceId, proxyConfig);

    if (!step1Result.success) {
        console.log('[BS-DVID] 步骤1失败:', step1Result.error);
        return { success: false, error: step1Result.error, deviceId: finalDeviceId };
    }

    const result = {
        success: true,
        bsDvid: step1Result.bsDvid,
        localId: step1Result.localId,
        localIdMd5: step1Result.localIdMd5,
        deviceId: finalDeviceId,
        deviceIdMd5: md5Hash(finalDeviceId),
        deviceIdDoubleMd5: md5Hash(md5Hash(finalDeviceId)),
        // 设备配置信息
        deviceConfig: deviceConfig,
        manufacturer: deviceConfig.manufacturer,
        device: deviceConfig.device,
        androidVersion: deviceConfig.android_version,
        sdkVersion: deviceConfig.sdk_version
    };

    const localId = step1Result.localId;

    // 步骤2: 上报SDK初始化事件
    const step2Result = await reportSdkEvents(localId, appVersion, proxyConfig);
    result.reportSuccess = step2Result.success;
    result.reportRequestId = step2Result.requestId;

    // 步骤3: 获取SDK配置
    if (fetchConfig) {
        const step3Result = await fetchSdkConfig(localId, appVersion, proxyConfig);
        result.configSuccess = step3Result.success;
        result.configRequestId = step3Result.requestId;
        if (step3Result.success) {
            result.config = step3Result.config;
        }
    }

    // 步骤4: 上报设备环境检测（基础）
    if (reportEnv) {
        const step4Result = await reportDeviceEnv(localId, appVersion, proxyConfig);
        result.envReportSuccess = step4Result.success;
        result.envRequestId = step4Result.requestId;
        if (step4Result.success) {
            result.clientIp = step4Result.clientIp;
            result.detectResult = step4Result.detect;
            result.nonce = step4Result.nonce;
        }
    }

    // 步骤5: 获取签名规则
    const step5Result = await fetchSigRules(localId, appVersion, proxyConfig);
    result.sigRulesSuccess = step5Result.success;
    result.sigRequestId = step5Result.requestId;
    if (step5Result.success) {
        result.sigRulesCount = step5Result.sigRulesCount;
    }

    // 步骤6: 上报完整设备指纹（深度）
    if (reportFullFingerprint) {
        const step6Result = await reportFullDeviceFingerprint(localId, appVersion, proxyConfig, deviceConfig);
        result.fingerprintSuccess = step6Result.success;
        result.fingerprintRequestId = step6Result.requestId;
        if (step6Result.success) {
            result.fingerprintClientIp = step6Result.clientIp;
            result.fingerprintDetect = step6Result.detect;
            result.fingerprintNonce = step6Result.nonce;
        }
    }

    // 步骤7: 获取最终 BS-DVID Token
    if (fetchFinalToken) {
        const step7Result = await fetchToken(localId, appVersion, null, proxyConfig);
        result.tokenSuccess = step7Result.success;
        result.tokenRequestId = step7Result.requestId;
        if (step7Result.success) {
            result.finalBsDvid = step7Result.token;
            result.token = step7Result.token;
            result.tokenStatus = step7Result.status;
            result.tokenVersion = step7Result.version;
            result.tokenNc = step7Result.nc;
        }
    }

    // 生成完整的User-Agent
    const finalBsDvid = result.finalBsDvid || result.bsDvid;
    if (finalBsDvid && finalDeviceId) {
        result.userAgent = generateMoutaiUserAgent(appVersion, finalDeviceId, finalBsDvid, deviceConfig);
    }

    console.log('========================================');
    console.log('[BS-DVID] 完成! 最终 BS-DVID:', (result.finalBsDvid || result.bsDvid || '').substring(0, 30) + '...');
    console.log('========================================');

    return result;
}

function gzipDeterministic(data) {
    const gz = zlib.gzipSync(data, { level: 9 });
    if (gz.length >= 10) {
        gz[4] = 0;
        gz[5] = 0;
        gz[6] = 0;
        gz[7] = 0;
        gz[9] = 0;
    }
    return gz;
}

function buildEncryptedRequestArtifacts(urlPath, body, localIdMd5, timestamp, randomKey) {
    const sign = md5Hash(APP_KEY + timestamp + SEC_KEY);
    const bodyJson = Buffer.from(JSON.stringify(body), 'utf8');
    const bodyGzip = gzipDeterministic(bodyJson);
    const bodyEncrypted = aesEncryptCbc(bodyGzip, randomKey);
    const skeyEncrypted = rc4Encrypt(randomKey, Buffer.from(localIdMd5, 'utf8'));
    return {
        path: urlPath,
        timestamp: String(timestamp),
        sign,
        bodyPlainJson: bodyJson.toString('utf8'),
        bodyEncryptedB64: bodyEncrypted.toString('base64'),
        skeyB64: skeyEncrypted.toString('base64'),
        randomKeyAscii: randomKey.toString('utf8'),
    };
}

function buildParityArtifacts(fixture = {}) {
    const { installGlobalParityRng } = require(path.join(__dirname, '../shared/parity_rng.cjs'));
    const seed = Number(fixture.deterministicSeed || fixture.deterministic_seed || 42);
    installGlobalParityRng(seed);
    const rng = global.__RUSHBUY_PARITY_RNG;

    const appVersion = String(fixture.appVersion || fixture.app_version || DEFAULT_APP_VERSION);
    const deviceId = String(fixture.deviceId || fixture.device_id || '353456789012345');
    let deviceConfig;
    if (fixture.deviceConfig && typeof fixture.deviceConfig === 'object') {
        deviceConfig = { ...fixture.deviceConfig };
    } else {
        const idx = Number(fixture.deviceConfigIndex || fixture.device_config_index || 0);
        deviceConfig = { ...DEVICE_CONFIGS[idx % DEVICE_CONFIGS.length] };
    }
    const overrides = fixture.fingerprintOverrides || fixture.fingerprint_overrides || {};
    const gatewayIp = String(fixture.gatewayIp || fixture.gateway_ip || overrides.gateway_ip || '192.168.1.1');
    const baseSec = Number(fixture.requestTimestampBase || fixture.request_timestamp_base || 1700000000);
    const baseMs = baseSec * 1000;
    let tsIdx = 0;
    const nextTs = () => String(baseSec + tsIdx++);

    const localId = generateLocalId(deviceId);
    const localIdMd5 = md5Hash(localId);
    const fpData = generateDeviceFingerprintFromConfig(deviceConfig, {
        gatewayIp,
        overrides,
    });

    const steps = [];
    const add = (name, urlPath, body, headers) => {
        steps.push({
            name,
            headers,
            ...buildEncryptedRequestArtifacts(
                urlPath,
                body,
                localIdMd5,
                nextTs(),
                rng.generateRandomKey(16),
            ),
        });
    };

    add(
        'step1_fetch',
        '/p/5/aio/210',
        buildRequestBody(localId, appVersion),
        { 'User-Agent': `haotian/${APP_KEY}/${appVersion}/3.6.8.0` },
    );
    add(
        'step2_report_events',
        '/p/1/r/210',
        buildReportEvents(localId, appVersion, baseMs + 100),
        { 'User-Agent': `haotian/${APP_KEY}/${appVersion}/3.6.8.0` },
    );
    add(
        'step3_config',
        '/s/5/aio/210',
        buildRequestBody(localId, appVersion),
        {
            'User-Agent': `haotian_x0/${APP_KEY}/${appVersion}/4.4.3.4.3`,
            'x-sdk-ver': 'haotian/3.6.8.0',
            'x-plu-ver': 'haotian_x0/4.4.3.4.3',
            'x-app-ver': `com.moutai.mall/${appVersion}`,
            'x-api-ver': '34',
        },
    );
    add(
        'step4_env',
        '/r/5/c/210',
        buildDeviceEnvReport(localId, baseMs + 200, appVersion, gatewayIp),
        {
            'User-Agent': `haotian_x0/${APP_KEY}/${appVersion}/4.4.3.4.3`,
            'x-sdk-ver': 'haotian/3.6.8.0',
            'x-plu-ver': 'haotian_x0/4.4.3.4.3',
            'x-app-ver': `com.moutai.mall/${appVersion}`,
            'x-api-ver': '34',
        },
    );
    add(
        'step5_sig_rules',
        '/f/2/sig/210',
        buildSigRequestBody(localId),
        {
            'User-Agent': `haotian_x6/${APP_KEY}/${appVersion}/4.4.3.4.3`,
            'x-client-src': 'jar',
            'x-sdk-ver': 'haotian/3.6.8.0',
            'x-plu-ver': 'haotian_x6/4.4.3.4.3',
            'x-app-ver': `com.moutai.mall/${appVersion}`,
            'x-api-ver': '34',
        },
    );
    const fpBody = buildFullDeviceFingerprint(localId, baseMs + 300, gatewayIp, fpData, appVersion);
    add(
        'step6_fingerprint',
        '/r/5/c/210',
        fpBody,
        {
            'User-Agent': `haotian_x0/${APP_KEY}/${appVersion}/4.4.3.4.3`,
            'x-sdk-ver': 'haotian/3.6.8.0',
            'x-plu-ver': 'haotian_x0/4.4.3.4.3',
            'x-app-ver': `com.moutai.mall/${appVersion}`,
            'x-api-ver': '34',
        },
    );
    add(
        'step7_token',
        '/c/11/z/210',
        buildTokenRequestBody(localId, baseMs + 400, appVersion),
        {
            'User-Agent': `haotian_x6/${APP_KEY}/${appVersion}/4.4.3.4.3`,
            'x-client-src': 'jar',
            'x-sdk-ver': 'haotian/3.6.8.0',
            'x-plu-ver': 'haotian_x6/4.4.3.4.3',
            'x-app-ver': `com.moutai.mall/${appVersion}`,
            'x-api-ver': '34',
        },
    );

    return {
        schema: 'bs_dvid_parity_v1',
        deviceId,
        localId,
        localIdMd5,
        deviceConfig,
        fingerprintPlainJson: JSON.stringify(fpBody),
        steps,
    };
}

// ==================== 导出 ====================

module.exports = {
    // 主函数
    getBsDvidWithReport,
    buildParityArtifacts,

    // 单步函数
    fetchBsDvid,
    reportSdkEvents,
    fetchSdkConfig,
    reportDeviceEnv,
    fetchSigRules,
    reportFullDeviceFingerprint,
    fetchToken,

    // 工具函数
    generateImei,
    generateLocalId,
    generateMoutaiUserAgent,
    md5Hash,
    getRandomDeviceConfig,

    // 加密函数
    rc4Encrypt,
    rc4Decrypt,
    aesEncryptCbc,
    aesDecryptCbc,

    // 常量（供 Python 同步 / 对照脚本）
    APP_KEY,
    SEC_KEY,
    APP_SIGNATURE,
    DEVICE_CONFIGS,
    IME_SERVICES,
    DNS_SERVERS,
    GATEWAY_IPS,
};

// ==================== 测试入口 ====================

if (require.main === module) {
    // 直接运行测试
    (async () => {
        console.log('开始测试 BS-DVID 获取...\n');

        try {
            const result = await getBsDvidWithReport();

            console.log('\n========================================');
            console.log('测试结果:');
            console.log('========================================');
            console.log('成功:', result.success);
            console.log('设备ID:', result.deviceId);
            console.log('设备配置:');
            console.log('  - 制造商:', result.manufacturer);
            console.log('  - 设备型号:', result.device);
            console.log('  - Android版本:', result.androidVersion);
            console.log('  - SDK版本:', result.sdkVersion);
            console.log('初始BS-DVID:', result.bsDvid?.substring(0, 50) + '...');
            console.log('最终BS-DVID:', result.finalBsDvid?.substring(0, 50) + '...');
            console.log('User-Agent:', result.userAgent?.substring(0, 100) + '...');

            if (result.clientIp) {
                console.log('客户端IP:', result.clientIp);
            }
        } catch (e) {
            console.error('测试失败:', e.message);
        }
    })();
}
