THEOS_DEVICE_IP=localhost#对应设备IP
THEOS_DEVICE_PORT=2222#对应设备端口号

ARCHS = arm64
TARGET := iphone:clang:latest:15.0
export THEOS_PACKAGE_SCHEME=rootless
THEOS_PACKAGE_INSTALL_PREFIX=/var/jb
THEOS_DEVICE_USER=mobile
# DEBUG = 0

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = TraceLens

TraceLens_FILES = Tweak.x
TraceLens_CFLAGS = -fobjc-arc
TraceLens_FRAMEWORKS = UIKit CoreFoundation CFNetwork WebKit

include $(THEOS_MAKE_PATH)/tweak.mk
