THEOS_DEVICE_IP=localhost
THEOS_DEVICE_PORT=2222

ARCHS = arm64
TARGET := iphone:clang:latest:15.0
export THEOS_PACKAGE_SCHEME=rootless
THEOS_PACKAGE_INSTALL_PREFIX=/var/jb
THEOS_DEVICE_USER=mobile

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = BBWasmLens

BBWasmLens_FILES = Tweak.x
BBWasmLens_CFLAGS = -fobjc-arc
BBWasmLens_FRAMEWORKS = UIKit CoreFoundation CFNetwork WebKit

include $(THEOS_MAKE_PATH)/tweak.mk
