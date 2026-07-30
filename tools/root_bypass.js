'use strict';

const ROOT_PATHS = [
  '/system/app/Superuser.apk',
  '/system/app/Magisk.apk',
  '/system/xbin/su',
  '/system/bin/su',
  '/sbin/su',
  '/su/bin/su',
  '/data/local/xbin/su',
  '/data/local/bin/su',
  '/data/adb/magisk',
  '/sbin/.magisk',
];

const ROOT_PACKAGES = [
  'com.topjohnwu.magisk',
  'eu.chainfire.supersu',
  'com.noshufou.android.su',
  'com.koushikdutta.superuser',
  'com.thirdparty.superuser',
];

const PROP_OVERRIDES = {
  'ro.build.tags': 'release-keys',
  'ro.debuggable': '0',
  'ro.secure': '1',
};

function isRootPath(path) {
  if (!path) return false;
  const value = String(path);
  return ROOT_PATHS.some(p => value === p || value.endsWith('/su'));
}

function sanitizeCommand(command) {
  const text = String(command);
  if (/(^|\s|\/)(su|magisk)(\s|$)/i.test(text) ||
      /\b(which|command\s+-v)\s+su\b/i.test(text) ||
      /\bgetprop\b.*\b(ro\.debuggable|ro\.secure|ro\.build\.tags)\b/i.test(text)) {
    console.log('[root-bypass] blocked command: ' + text);
    return 'sh -c "exit 1"';
  }
  return command;
}

Java.perform(function () {
  const File = Java.use('java.io.File');
  const fileExists = File.exists.overload();
  fileExists.implementation = function () {
    const path = this.getAbsolutePath();
    if (isRootPath(path)) {
      console.log('[root-bypass] hidden file: ' + path);
      return false;
    }
    return fileExists.call(this);
  };

  const SystemProperties = Java.use('android.os.SystemProperties');
  const get1 = SystemProperties.get.overload('java.lang.String');
  get1.implementation = function (key) {
    if (Object.prototype.hasOwnProperty.call(PROP_OVERRIDES, String(key))) {
      const value = PROP_OVERRIDES[String(key)];
      console.log('[root-bypass] property ' + key + '=' + value);
      return value;
    }
    return get1.call(this, key);
  };
  const get2 = SystemProperties.get.overload('java.lang.String', 'java.lang.String');
  get2.implementation = function (key, def) {
    if (Object.prototype.hasOwnProperty.call(PROP_OVERRIDES, String(key))) {
      return PROP_OVERRIDES[String(key)];
    }
    return get2.call(this, key, def);
  };

  const Runtime = Java.use('java.lang.Runtime');
  const execString = Runtime.exec.overload('java.lang.String');
  execString.implementation = function (command) {
    return execString.call(this, sanitizeCommand(command));
  };
  const execArray = Runtime.exec.overload('[Ljava.lang.String;');
  execArray.implementation = function (command) {
    const joined = Array.prototype.join.call(command, ' ');
    if (sanitizeCommand(joined) !== joined) {
      return execString.call(this, 'sh -c "exit 1"');
    }
    return execArray.call(this, command);
  };

  const ApplicationPackageManager = Java.use(
    'android.app.ApplicationPackageManager'
  );
  const getPackageInfo = ApplicationPackageManager.getPackageInfo.overload(
    'java.lang.String',
    'int'
  );
  getPackageInfo.implementation = function (name, flags) {
    if (ROOT_PACKAGES.indexOf(String(name)) !== -1) {
      console.log('[root-bypass] hidden package: ' + name);
      return getPackageInfo.call(this, 'com.android.__missing__', flags);
    }
    return getPackageInfo.call(this, name, flags);
  };

  function hookAll(className, methodName, returnFactory) {
    try {
      const Cls = Java.use(className);
      if (!Cls[methodName]) return;
      Cls[methodName].overloads.forEach(function (overload) {
        overload.implementation = function () {
          console.log('[root-bypass] ' + className + '.' + methodName);
          return returnFactory();
        };
      });
    } catch (e) {
      console.log('[root-bypass] hook skipped: ' + className + '.' +
        methodName + ': ' + e);
    }
  }

  const JSONObject = Java.use('org.json.JSONObject');
  const JSONArray = Java.use('org.json.JSONArray');

  hookAll(
    'com.coralline.sea.checkers.RiskRealTimeAPI',
    'checkMagiskDeltaRiskStatus',
    function () { return null; }
  );
  hookAll(
    'com.coralline.sea.checkers.RiskRealTimeAPI',
    'checkRootStatus',
    function () { return JSONObject.$new(); }
  );
  hookAll(
    'com.coralline.sea.checkers.RiskRealTimeAPI',
    'checkRiskFrameStatus',
    function () { return JSONObject.$new(); }
  );

  ['m1801a', 'm1802a', 'm1803b'].forEach(function (name) {
    hookAll('com.coralline.sea.C0237t5', name, function () { return false; });
  });
  hookAll(
    'com.coralline.sea.C0237t5',
    'm1804c',
    function () { return JSONObject.$new(); }
  );

  [
    'm1951a', 'm1952a', 'm1953a', 'm1954a', 'm1955b', 'm1956b',
    'm1957b', 'm1958b', 'm1960c', 'm1961d', 'm1971n', 'm1972o',
    'm1973p', 'm1975r', 'm1976s', 'm1977t', 'm1979v', 'm1980w',
  ].forEach(function (name) {
    hookAll('com.coralline.sea.C0274w7', name, function () { return false; });
  });
  [
    'm1950a', 'm1965h', 'm1969l', 'm1970m', 'm1974q', 'm1978u',
    'm1981x',
  ].forEach(function (name) {
    hookAll('com.coralline.sea.C0274w7', name, function () {
      return JSONArray.$new();
    });
  });
  [
    'm1948A', 'm1949B', 'm1962e', 'm1963f', 'm1964g', 'm1966i',
    'm1967j', 'm1968k', 'm1982y', 'm1983z',
  ].forEach(function (name) {
    hookAll('com.coralline.sea.C0274w7', name, function () {
      return JSONObject.$new();
    });
  });

  const JSystem = Java.use('java.lang.System');
  const systemExit = JSystem.exit.overload('int');
  systemExit.implementation = function (status) {
    console.log('[root-bypass] blocked System.exit(' + status + ')');
  };

  try {
    const Process = Java.use('android.os.Process');
    const killProcess = Process.killProcess.overload('int');
    killProcess.implementation = function (pid) {
      if (pid === Process.myPid()) {
        console.log('[root-bypass] blocked killProcess(self)');
        return;
      }
      return killProcess.call(this, pid);
    };
  } catch (e) {
    console.log('[root-bypass] Process.killProcess hook skipped: ' + e);
  }

  console.log('[root-bypass] Java hooks active');
});

['access', 'faccessat', 'stat', 'lstat', 'fopen'].forEach(function (symbol) {
  const address = Module.findGlobalExportByName(symbol);
  if (!address) return;
  Interceptor.attach(address, {
    onEnter(args) {
      const pathArg = symbol === 'faccessat' ? args[1] : args[0];
      try {
        const path = pathArg.readCString();
        if (isRootPath(path)) {
          this.hidden = true;
          pathArg.writeUtf8String('/system/xia');
          console.log('[root-bypass] native hidden: ' + path);
        }
      } catch (_) {}
    },
  });
});
