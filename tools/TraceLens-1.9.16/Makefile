-include device.mk
THEOS_DEVICE_IP ?= localhost
THEOS_DEVICE_PORT ?= 22

ARCHS = arm64
TARGET := iphone:clang:latest:15.0
export THEOS_PACKAGE_SCHEME=rootless
THEOS_PACKAGE_INSTALL_PREFIX=/var/jb

# DEBUG = 0

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = TraceLens

TraceLens_FILES = Tweak.x TraceLensTrace.x decrypted_1424.c decrypted_1429.c
TraceLens_CFLAGS = -fobjc-arc
TraceLens_FRAMEWORKS = UIKit CoreFoundation CFNetwork WebKit

include $(THEOS_MAKE_PATH)/tweak.mk
