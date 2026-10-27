-include device.mk
THEOS_DEVICE_IP ?= 192.168.1.100
THEOS_DEVICE_PORT ?= 22

ARCHS = arm64
TARGET := iphone:clang:latest:15.0
export THEOS_PACKAGE_SCHEME=rootless
THEOS_PACKAGE_INSTALL_PREFIX=/var/jb

# DEBUG = 0

include $(THEOS)/makefiles/common.mk

TWEAK_NAME = tweakTest

tweakTest_FILES = Tweak.x decrypted_1424.c decrypted_1429.c
tweakTest_CFLAGS = -fobjc-arc
tweakTest_FRAMEWORKS = UIKit CoreFoundation CFNetwork

include $(THEOS_MAKE_PATH)/tweak.mk
