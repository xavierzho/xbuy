#!/bin/zsh
set -euo pipefail
ROOT=${0:A:h}
SDK=${ANDROID_HOME:-$HOME/Library/Android/sdk}
BT=$(find "$SDK/build-tools" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -1)
ANDROID_JAR=$(find "$SDK/platforms" -name android.jar | sort -V | tail -1)
OUT="$ROOT/build"
rm -rf "$OUT"
mkdir -p "$OUT/stubs/de/robv/android/xposed/callbacks" "$OUT/classes" "$OUT/dex"

cat > "$OUT/stubs/de/robv/android/xposed/IXposedHookLoadPackage.java" <<'EOF'
package de.robv.android.xposed;
import de.robv.android.xposed.callbacks.XC_LoadPackage;
public interface IXposedHookLoadPackage { void handleLoadPackage(XC_LoadPackage.LoadPackageParam p) throws Throwable; }
EOF
cat > "$OUT/stubs/de/robv/android/xposed/XC_MethodHook.java" <<'EOF'
package de.robv.android.xposed;
public abstract class XC_MethodHook {
 protected void beforeHookedMethod(MethodHookParam p) throws Throwable {}
 protected void afterHookedMethod(MethodHookParam p) throws Throwable {}
 public static class MethodHookParam { public Object thisObject; public Object[] args; public void setResult(Object x){} public Object getResult(){return null;} }
}
EOF
cat > "$OUT/stubs/de/robv/android/xposed/XC_MethodReplacement.java" <<'EOF'
package de.robv.android.xposed;
public abstract class XC_MethodReplacement extends XC_MethodHook {
 public static final XC_MethodReplacement DO_NOTHING=returnConstant(null);
 public static XC_MethodReplacement returnConstant(final Object x){return new XC_MethodReplacement(){protected Object replaceHookedMethod(MethodHookParam p){return x;}};}
 protected abstract Object replaceHookedMethod(MethodHookParam p) throws Throwable;
}
EOF
cat > "$OUT/stubs/de/robv/android/xposed/XposedBridge.java" <<'EOF'
package de.robv.android.xposed;
public final class XposedBridge { public static void log(String s){} public static java.util.Set<?> hookAllMethods(Class<?> c,String n,XC_MethodHook h){return null;} }
EOF
cat > "$OUT/stubs/de/robv/android/xposed/XposedHelpers.java" <<'EOF'
package de.robv.android.xposed;
public final class XposedHelpers { public static Class<?> findClassIfExists(String n,ClassLoader l){return null;} public static Object findAndHookMethod(Class<?> c,String n,Object... a){return null;} }
EOF
cat > "$OUT/stubs/de/robv/android/xposed/callbacks/XC_LoadPackage.java" <<'EOF'
package de.robv.android.xposed.callbacks;
public final class XC_LoadPackage { public static class LoadPackageParam { public String packageName,processName; public ClassLoader classLoader; } }
EOF

javac -source 8 -target 8 -cp "$ANDROID_JAR" -d "$OUT/classes" \
  $(find "$OUT/stubs" -name '*.java') "$ROOT/src/local/fixture/moutaihook/MainHook.java"
jar cf "$OUT/module.jar" -C "$OUT/classes" local
"$BT/d8" --lib "$ANDROID_JAR" --min-api 27 --output "$OUT/dex" "$OUT/module.jar"
"$BT/aapt2" link -I "$ANDROID_JAR" --manifest "$ROOT/AndroidManifest.xml" -A "$ROOT/assets" -o "$OUT/moutai-hook-unsigned.apk"
(cd "$OUT/dex" && zip -q "$OUT/moutai-hook-unsigned.apk" classes.dex)

KEY="$ROOT/debug.keystore"
if [[ ! -f "$KEY" ]]; then
  keytool -genkeypair -keystore "$KEY" -storepass android -keypass android -alias androiddebugkey \
    -dname 'CN=Android Debug,O=Local Fixture,C=CN' -keyalg RSA -keysize 2048 -validity 10000 >/dev/null 2>&1
fi
"$BT/zipalign" -f 4 "$OUT/moutai-hook-unsigned.apk" "$OUT/moutai-hook-aligned.apk"
"$BT/apksigner" sign --ks "$KEY" --ks-pass pass:android --key-pass pass:android \
  --out "$OUT/moutai-hook.apk" "$OUT/moutai-hook-aligned.apk"
"$BT/apksigner" verify --verbose "$OUT/moutai-hook.apk"
echo "$OUT/moutai-hook.apk"
