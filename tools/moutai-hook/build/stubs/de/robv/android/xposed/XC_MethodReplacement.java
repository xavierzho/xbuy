package de.robv.android.xposed;
public abstract class XC_MethodReplacement extends XC_MethodHook {
 public static final XC_MethodReplacement DO_NOTHING=returnConstant(null);
 public static XC_MethodReplacement returnConstant(final Object x){return new XC_MethodReplacement(){protected Object replaceHookedMethod(MethodHookParam p){return x;}};}
 protected abstract Object replaceHookedMethod(MethodHookParam p) throws Throwable;
}
