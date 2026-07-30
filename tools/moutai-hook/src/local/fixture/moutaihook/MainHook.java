package local.fixture.moutaihook;

import android.os.Process;
import java.io.File;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import org.json.JSONArray;
import org.json.JSONObject;

import de.robv.android.xposed.IXposedHookLoadPackage;
import de.robv.android.xposed.XC_MethodHook;
import de.robv.android.xposed.XC_MethodReplacement;
import de.robv.android.xposed.XposedBridge;
import de.robv.android.xposed.XposedHelpers;
import de.robv.android.xposed.callbacks.XC_LoadPackage;

public final class MainHook implements IXposedHookLoadPackage {
    private static final String TARGET = "com.moutai.mall";

    private static final Set<String> ROOT_PATHS = new HashSet<>(Arrays.asList(
            "/system/app/Superuser.apk",
            "/system/app/Magisk.apk",
            "/system/xbin/su",
            "/system/bin/su",
            "/sbin/su",
            "/su/bin/su",
            "/data/local/xbin/su",
            "/data/local/bin/su",
            "/data/adb/magisk",
            "/sbin/.magisk"
    ));

    @Override
    public void handleLoadPackage(XC_LoadPackage.LoadPackageParam lpparam) {
        if (!TARGET.equals(lpparam.packageName)) {
            return;
        }
        XposedBridge.log("[MoutaiHook] process=" + lpparam.processName);
        hookFrameworkChecks();
        hookCoralline(lpparam.classLoader);
    }

    private static void replaceAll(
            ClassLoader loader,
            String className,
            String method,
            XC_MethodReplacement replacement) {
        try {
            Class<?> type = XposedHelpers.findClassIfExists(className, loader);
            if (type != null) {
                XposedBridge.hookAllMethods(type, method, replacement);
            }
        } catch (Throwable error) {
            XposedBridge.log("[MoutaiHook] skip " + className + "." + method
                    + ": " + error);
        }
    }

    private static void hookCoralline(ClassLoader loader) {
        XC_MethodReplacement falseValue =
                XC_MethodReplacement.returnConstant(false);
        XC_MethodReplacement emptyObject = new XC_MethodReplacement() {
            @Override
            protected Object replaceHookedMethod(MethodHookParam param) {
                return new JSONObject();
            }
        };
        XC_MethodReplacement emptyArray = new XC_MethodReplacement() {
            @Override
            protected Object replaceHookedMethod(MethodHookParam param) {
                return new JSONArray();
            }
        };

        replaceAll(loader,
                "com.coralline.sea.checkers.RiskRealTimeAPI",
                "checkMagiskDeltaRiskStatus",
                XC_MethodReplacement.returnConstant(null));
        replaceAll(loader,
                "com.coralline.sea.checkers.RiskRealTimeAPI",
                "checkRootStatus",
                emptyObject);
        replaceAll(loader,
                "com.coralline.sea.checkers.RiskRealTimeAPI",
                "checkRiskFrameStatus",
                emptyObject);

        for (String method : new String[]{
                "m1801a", "m1802a", "m1803b"
        }) {
            replaceAll(loader, "com.coralline.sea.C0237t5", method, falseValue);
        }
        replaceAll(loader, "com.coralline.sea.C0237t5", "m1804c", emptyObject);

        for (String method : new String[]{
                "m1951a", "m1952a", "m1953a", "m1954a", "m1955b", "m1956b",
                "m1957b", "m1958b", "m1960c", "m1961d", "m1971n", "m1972o",
                "m1973p", "m1975r", "m1976s", "m1977t", "m1979v", "m1980w"
        }) {
            replaceAll(loader, "com.coralline.sea.C0274w7", method, falseValue);
        }
        for (String method : new String[]{
                "m1950a", "m1965h", "m1969l", "m1970m", "m1974q", "m1978u",
                "m1981x"
        }) {
            replaceAll(loader, "com.coralline.sea.C0274w7", method, emptyArray);
        }
        for (String method : new String[]{
                "m1948A", "m1949B", "m1962e", "m1963f", "m1964g", "m1966i",
                "m1967j", "m1968k", "m1982y", "m1983z"
        }) {
            replaceAll(loader, "com.coralline.sea.C0274w7", method, emptyObject);
        }
    }

    private static void hookFrameworkChecks() {
        try {
            XposedHelpers.findAndHookMethod(File.class, "exists",
                    new XC_MethodHook() {
                        @Override
                        protected void beforeHookedMethod(MethodHookParam param) {
                            String path = ((File) param.thisObject).getAbsolutePath();
                            if (ROOT_PATHS.contains(path) || path.endsWith("/su")) {
                                param.setResult(false);
                            }
                        }
                    });
        } catch (Throwable error) {
            XposedBridge.log("[MoutaiHook] File.exists: " + error);
        }

        try {
            XposedHelpers.findAndHookMethod(System.class, "exit", int.class,
                    XC_MethodReplacement.DO_NOTHING);
            XposedHelpers.findAndHookMethod(Process.class, "killProcess", int.class,
                    new XC_MethodHook() {
                        @Override
                        protected void beforeHookedMethod(MethodHookParam param) {
                            if ((int) param.args[0] == Process.myPid()) {
                                param.setResult(null);
                            }
                        }
                    });
        } catch (Throwable error) {
            XposedBridge.log("[MoutaiHook] exit hooks: " + error);
        }
    }
}
