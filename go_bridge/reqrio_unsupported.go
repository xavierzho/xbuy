//go:build !windows

package main

import (
	"context"
	"errors"
	"strings"
)

func reqrioDLLAvailable() bool {
	return false
}

func reqrioSend(_ reqrioRequest) (int, []byte, error) {
	return 0, nil, errors.New("reqrio transport is only wired for Windows reqrio.dll")
}

func reqrioSendSession(_ context.Context, att Attempt, _ reqrioRequest) (int, []byte, string, error) {
	return 0, nil, strings.TrimSpace(att.ProxyURL), errors.New("reqrio transport is only wired for Windows reqrio.dll")
}
