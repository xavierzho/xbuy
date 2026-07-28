//go:build windows

package main

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"unsafe"

	"golang.org/x/sys/windows"
)

type reqrioDLLHandle struct {
	dll                    *syscall.LazyDLL
	fingerprintClientHello *syscall.LazyProc
	fingerprintCustom      *syscall.LazyProc
	urlNew                 *syscall.LazyProc
	urlSetSNI              *syscall.LazyProc
	urlDrop                *syscall.LazyProc
	bodyNew                *syscall.LazyProc
	bodyNone               *syscall.LazyProc
	bodyDrop               *syscall.LazyProc
	responseStatusCode     *syscall.LazyProc
	responseBytes          *syscall.LazyProc
	responseDrop           *syscall.LazyProc
	scReqNew               *syscall.LazyProc
	scReqSetHeaderJSON     *syscall.LazyProc
	scReqSetALPN           *syscall.LazyProc
	scReqSetVerify         *syscall.LazyProc
	scReqSetRedirect       *syscall.LazyProc
	scReqSetFingerprint    *syscall.LazyProc
	scReqSetProxy          *syscall.LazyProc
	scReqSetTimeout        *syscall.LazyProc
	scReqCloseStream       *syscall.LazyProc
	scReqStreamIO          *syscall.LazyProc
	scReqDrop              *syscall.LazyProc
	charFree               *syscall.LazyProc
}

type reqrioPersistentSession struct {
	mu       sync.Mutex
	key      string
	proxyURL string
	h        *reqrioDLLHandle
	scReq    uintptr
	plan     reqrioFingerprintPlan
	closed   bool
}

var (
	reqrioSessionMu                 sync.Mutex
	reqrioSessions                  = map[string]*reqrioPersistentSession{}
	reqrioInsecureSkipVerifyForTest atomic.Bool
)

func reqrioDLLAvailable() bool {
	_, err := loadReqrioDLL()
	return err == nil
}

func loadReqrioDLL() (*reqrioDLLHandle, error) {
	dllPath := reqrioDLLPath()
	h := &reqrioDLLHandle{dll: syscall.NewLazyDLL(dllPath)}
	h.fingerprintClientHello = h.dll.NewProc("Fingerprint_from_client_hello")
	h.fingerprintCustom = h.dll.NewProc("Fingerprint_custom")
	h.urlNew = h.dll.NewProc("Url_new")
	h.urlSetSNI = h.dll.NewProc("Url_set_sni")
	h.urlDrop = h.dll.NewProc("Url_drop")
	h.bodyNew = h.dll.NewProc("Body_new")
	h.bodyNone = h.dll.NewProc("Body_none")
	h.bodyDrop = h.dll.NewProc("Body_drop")
	h.responseStatusCode = h.dll.NewProc("Response_status_code")
	h.responseBytes = h.dll.NewProc("Response_bytes")
	h.responseDrop = h.dll.NewProc("Response_drop")
	h.scReqNew = h.dll.NewProc("ScReq_new")
	h.scReqSetHeaderJSON = h.dll.NewProc("ScReq_set_header_json")
	h.scReqSetALPN = h.dll.NewProc("ScReq_set_alpn")
	h.scReqSetVerify = h.dll.NewProc("ScReq_set_verify")
	h.scReqSetRedirect = h.dll.NewProc("ScReq_set_redirect")
	h.scReqSetFingerprint = h.dll.NewProc("ScReq_set_fingerprint")
	h.scReqSetProxy = h.dll.NewProc("ScReq_set_proxy")
	h.scReqSetTimeout = h.dll.NewProc("ScReq_set_timeout")
	h.scReqCloseStream = h.dll.NewProc("ScReq_close_stream")
	h.scReqStreamIO = h.dll.NewProc("ScReq_stream_io")
	h.scReqDrop = h.dll.NewProc("ScReq_drop")
	h.charFree = h.dll.NewProc("char_free")
	if err := h.dll.Load(); err != nil {
		return nil, fmt.Errorf("load reqrio dll %s: %w", dllPath, err)
	}
	for name, proc := range map[string]*syscall.LazyProc{
		"Fingerprint_from_client_hello": h.fingerprintClientHello,
		"Fingerprint_custom":            h.fingerprintCustom,
		"Url_new":                       h.urlNew,
		"Url_set_sni":                   h.urlSetSNI,
		"Url_drop":                      h.urlDrop,
		"Body_new":                      h.bodyNew,
		"Body_none":                     h.bodyNone,
		"Body_drop":                     h.bodyDrop,
		"Response_status_code":          h.responseStatusCode,
		"Response_bytes":                h.responseBytes,
		"Response_drop":                 h.responseDrop,
		"ScReq_new":                     h.scReqNew,
		"ScReq_set_header_json":         h.scReqSetHeaderJSON,
		"ScReq_set_alpn":                h.scReqSetALPN,
		"ScReq_set_verify":              h.scReqSetVerify,
		"ScReq_set_redirect":            h.scReqSetRedirect,
		"ScReq_set_fingerprint":         h.scReqSetFingerprint,
		"ScReq_set_proxy":               h.scReqSetProxy,
		"ScReq_set_timeout":             h.scReqSetTimeout,
		"ScReq_close_stream":            h.scReqCloseStream,
		"ScReq_stream_io":               h.scReqStreamIO,
		"ScReq_drop":                    h.scReqDrop,
		"char_free":                     h.charFree,
	} {
		if err := proc.Find(); err != nil {
			return nil, fmt.Errorf("reqrio dll missing %s: %w", name, err)
		}
	}
	return h, nil
}

func reqrioSendSession(ctx context.Context, att Attempt, req reqrioRequest) (int, []byte, string, error) {
	sess, err := getReqrioSession(att)
	if err != nil {
		return 0, nil, strings.TrimSpace(att.ProxyURL), err
	}
	req.ProxyURL = sess.proxyURL
	if strings.TrimSpace(req.SNI) == "" {
		req.SNI = sess.plan.SNI
	}
	status, body, err := sess.send(ctx, req)
	if err != nil {
		invalidateReqrioSessionReason(sess.key, sess, "request_error")
		return 0, nil, sess.proxyURL, err
	}
	if reason, ok := http429SessionCloseReason(status, body); ok {
		invalidateReqrioSessionReason(sess.key, sess, reason)
	}
	return status, body, sess.proxyURL, nil
}

func getReqrioSession(att Attempt) (*reqrioPersistentSession, error) {
	key := transportSessionKey("reqrio", att)
	reqrioSessionMu.Lock()
	if sess := reqrioSessions[key]; sess != nil && !sess.closed {
		reqrioSessionMu.Unlock()
		transportTrace("USE engine=reqrio account=%s target=%s lane=%d proxy=%s fingerprint=%s", transportTraceAccount(att), transportTargetAuthority(att), transportLaneID(att), proxyForTrace(sess.proxyURL), shortFingerprint(sess.plan.CustomTLSSHA256))
		return sess, nil
	}
	reqrioSessionMu.Unlock()

	sess, err := newReqrioSession(key, att)
	if err != nil {
		return nil, err
	}

	reqrioSessionMu.Lock()
	if cur := reqrioSessions[key]; cur != nil && !cur.closed {
		reqrioSessionMu.Unlock()
		sess.close()
		transportTrace("USE engine=reqrio account=%s target=%s lane=%d proxy=%s fingerprint=%s", transportTraceAccount(att), transportTargetAuthority(att), transportLaneID(att), proxyForTrace(cur.proxyURL), shortFingerprint(cur.plan.CustomTLSSHA256))
		return cur, nil
	}
	reqrioSessions[key] = sess
	reqrioSessionMu.Unlock()
	transportTrace("NEW engine=reqrio account=%s target=%s lane=%d proxy=%s fingerprint=%s", transportTraceAccount(att), transportTargetAuthority(att), transportLaneID(att), proxyForTrace(sess.proxyURL), shortFingerprint(sess.plan.CustomTLSSHA256))
	return sess, nil
}

func newReqrioSession(key string, att Attempt) (*reqrioPersistentSession, error) {
	h, err := loadReqrioDLL()
	if err != nil {
		return nil, err
	}
	plan, err := reqrioFingerprintPlanForAttempt(att)
	if err != nil {
		return nil, err
	}
	token, err := reqrioToken()
	if err != nil {
		return nil, err
	}
	scReq, _, _ := h.scReqNew.Call()
	if scReq == 0 {
		return nil, errors.New("reqrio ScReq_new returned nil")
	}
	ok := false
	defer func() {
		if !ok {
			h.scReqDrop.Call(scReq)
		}
	}()
	if err := h.callSetString(h.scReqSetALPN, scReq, reqrioWireALPN); err != nil {
		return nil, err
	}
	var fp uintptr
	if strings.TrimSpace(plan.CustomTLSJSON) != "" {
		fp, err = h.fingerprintCustomHandle(plan.CustomTLSJSON, token)
	} else {
		fp, err = h.fingerprintClientHelloHandle(plan.ClientHello, token)
	}
	if err != nil {
		return nil, err
	}
	if r, _, _ := h.scReqSetFingerprint.Call(scReq, fp); r != 0 {
		return nil, h.charError(r)
	}
	verify := uintptr(1)
	if reqrioInsecureSkipVerifyForTest.Load() {
		verify = 0
	}
	if r, _, _ := h.scReqSetVerify.Call(scReq, verify); r != 0 {
		return nil, h.charError(r)
	}
	if strings.TrimSpace(att.ProxyURL) != "" {
		if err := h.callSetString(h.scReqSetProxy, scReq, strings.TrimSpace(att.ProxyURL)); err != nil {
			return nil, err
		}
	}
	ok = true
	return &reqrioPersistentSession{
		key:      key,
		proxyURL: strings.TrimSpace(att.ProxyURL),
		h:        h,
		scReq:    scReq,
		plan:     plan,
	}, nil
}

func resetReqrioSessionsForTest() {
	reqrioSessionMu.Lock()
	sessions := make([]*reqrioPersistentSession, 0, len(reqrioSessions))
	for _, sess := range reqrioSessions {
		sessions = append(sessions, sess)
	}
	reqrioSessions = map[string]*reqrioPersistentSession{}
	reqrioSessionMu.Unlock()
	for _, sess := range sessions {
		sess.close()
	}
}

func invalidateReqrioSession(key string, sess *reqrioPersistentSession) {
	invalidateReqrioSessionReason(key, sess, "manual")
}

func invalidateReqrioSessionReason(key string, sess *reqrioPersistentSession, reason string) {
	reqrioSessionMu.Lock()
	if cur := reqrioSessions[key]; cur == sess {
		delete(reqrioSessions, key)
	}
	reqrioSessionMu.Unlock()
	if sess != nil {
		transportTrace("CLOSE engine=reqrio reason=%s proxy=%s fingerprint=%s", reason, proxyForTrace(sess.proxyURL), shortFingerprint(sess.plan.CustomTLSSHA256))
		sess.close()
	}
}

func shortFingerprint(v string) string {
	v = strings.TrimSpace(v)
	if len(v) > 12 {
		return v[:12]
	}
	return v
}

func (s *reqrioPersistentSession) close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed || s.scReq == 0 {
		return
	}
	if s.h != nil && s.h.scReqCloseStream != nil {
		if r, _, _ := s.h.scReqCloseStream.Call(s.scReq); r != 0 && s.h.charFree != nil {
			_ = s.h.charError(r)
		}
	}
	s.h.scReqDrop.Call(s.scReq)
	s.scReq = 0
	s.closed = true
}

func (s *reqrioPersistentSession) send(ctx context.Context, req reqrioRequest) (int, []byte, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	select {
	case <-ctx.Done():
		return 0, nil, ctx.Err()
	default:
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed || s.scReq == 0 {
		return 0, nil, errors.New("reqrio session is closed")
	}
	if r, _, _ := s.h.scReqSetRedirect.Call(s.scReq, 0); r != 0 {
		return 0, nil, s.h.charError(r)
	}
	if err := s.h.callSetString(s.h.scReqSetTimeout, s.scReq, reqrioTimeoutJSON(req.TimeoutMs)); err != nil {
		return 0, nil, err
	}
	if strings.TrimSpace(req.HeadersJSON) != "" && strings.TrimSpace(req.HeadersJSON) != "{}" {
		if err := s.h.callSetString(s.h.scReqSetHeaderJSON, s.scReq, req.HeadersJSON); err != nil {
			return 0, nil, err
		}
	} else if err := s.h.callSetString(s.h.scReqSetHeaderJSON, s.scReq, "{}"); err != nil {
		return 0, nil, err
	}
	select {
	case <-ctx.Done():
		return 0, nil, ctx.Err()
	default:
	}

	urlHandle, err := s.h.urlHandle(req.URL)
	if err != nil {
		return 0, nil, err
	}
	bodyHandle, err := s.h.bodyHandle(req.Body, req.ContentType)
	if err != nil {
		s.h.urlDrop.Call(urlHandle)
		return 0, nil, err
	}
	consumed := false
	defer func() {
		if !consumed {
			if urlHandle != 0 {
				s.h.urlDrop.Call(urlHandle)
			}
			if bodyHandle != 0 {
				s.h.bodyDrop.Call(bodyHandle)
			}
		}
	}()
	if strings.TrimSpace(req.SNI) != "" {
		if err := s.h.callSetStringOnHandle(s.h.urlSetSNI, urlHandle, req.SNI); err != nil {
			return 0, nil, err
		}
	}
	var errPtr uintptr
	resp, _, _ := s.h.scReqStreamIO.Call(
		s.scReq,
		uintptr(reqrioMethodValue(req.Method)),
		urlHandle,
		bodyHandle,
		uintptr(unsafe.Pointer(&errPtr)),
	)
	consumed = true
	if errPtr != 0 {
		return 0, nil, s.h.charError(errPtr)
	}
	if resp == 0 {
		return 0, nil, errors.New("reqrio ScReq_stream_io returned nil response")
	}
	defer s.h.responseDrop.Call(resp)
	status, err := s.h.responseStatus(resp)
	if err != nil {
		return 0, nil, err
	}
	body, err := s.h.responseBody(resp)
	if err != nil {
		return 0, nil, err
	}
	return status, body, nil
}

func reqrioSend(req reqrioRequest) (int, []byte, error) {
	h, err := loadReqrioDLL()
	if err != nil {
		return 0, nil, err
	}
	scReq, _, _ := h.scReqNew.Call()
	if scReq == 0 {
		return 0, nil, errors.New("reqrio ScReq_new returned nil")
	}
	defer h.scReqDrop.Call(scReq)

	if err := h.callSetString(h.scReqSetALPN, scReq, reqrioWireALPN); err != nil {
		return 0, nil, err
	}
	var fp uintptr
	if strings.TrimSpace(req.CustomTLSJSON) != "" {
		fp, err = h.fingerprintCustomHandle(req.CustomTLSJSON, req.Token)
	} else {
		fp, err = h.fingerprintClientHelloHandle(req.ClientHello, req.Token)
	}
	if err != nil {
		return 0, nil, err
	}
	if r, _, _ := h.scReqSetFingerprint.Call(scReq, fp); r != 0 {
		return 0, nil, h.charError(r)
	}
	if r, _, _ := h.scReqSetVerify.Call(scReq, 1); r != 0 {
		return 0, nil, h.charError(r)
	}
	if r, _, _ := h.scReqSetRedirect.Call(scReq, 0); r != 0 {
		return 0, nil, h.charError(r)
	}
	if strings.TrimSpace(req.ProxyURL) != "" {
		if err := h.callSetString(h.scReqSetProxy, scReq, req.ProxyURL); err != nil {
			return 0, nil, err
		}
	}
	if err := h.callSetString(h.scReqSetTimeout, scReq, reqrioTimeoutJSON(req.TimeoutMs)); err != nil {
		return 0, nil, err
	}
	if strings.TrimSpace(req.HeadersJSON) != "" && strings.TrimSpace(req.HeadersJSON) != "{}" {
		if err := h.callSetString(h.scReqSetHeaderJSON, scReq, req.HeadersJSON); err != nil {
			return 0, nil, err
		}
	}

	urlHandle, err := h.urlHandle(req.URL)
	if err != nil {
		return 0, nil, err
	}
	bodyHandle, err := h.bodyHandle(req.Body, req.ContentType)
	if err != nil {
		h.urlDrop.Call(urlHandle)
		return 0, nil, err
	}
	consumed := false
	defer func() {
		if !consumed {
			if urlHandle != 0 {
				h.urlDrop.Call(urlHandle)
			}
			if bodyHandle != 0 {
				h.bodyDrop.Call(bodyHandle)
			}
		}
	}()
	if strings.TrimSpace(req.SNI) != "" {
		if err := h.callSetStringOnHandle(h.urlSetSNI, urlHandle, req.SNI); err != nil {
			return 0, nil, err
		}
	}
	var errPtr uintptr
	resp, _, _ := h.scReqStreamIO.Call(
		scReq,
		uintptr(reqrioMethodValue(req.Method)),
		urlHandle,
		bodyHandle,
		uintptr(unsafe.Pointer(&errPtr)),
	)
	consumed = true
	if errPtr != 0 {
		return 0, nil, h.charError(errPtr)
	}
	if resp == 0 {
		return 0, nil, errors.New("reqrio ScReq_stream_io returned nil response")
	}
	defer h.responseDrop.Call(resp)
	status, err := h.responseStatus(resp)
	if err != nil {
		return 0, nil, err
	}
	body, err := h.responseBody(resp)
	if err != nil {
		return 0, nil, err
	}
	return status, body, nil
}

func (h *reqrioDLLHandle) fingerprintCustomHandle(customJSON, token string) (uintptr, error) {
	customPtr, err := syscall.BytePtrFromString(customJSON)
	if err != nil {
		return 0, err
	}
	tokenPtr, err := syscall.BytePtrFromString(token)
	if err != nil {
		return 0, err
	}
	var errPtr uintptr
	fp, _, _ := h.fingerprintCustom.Call(
		uintptr(unsafe.Pointer(customPtr)),
		uintptr(unsafe.Pointer(tokenPtr)),
		uintptr(unsafe.Pointer(&errPtr)),
	)
	if errPtr != 0 {
		return 0, h.charError(errPtr)
	}
	if fp == 0 {
		return 0, errors.New("reqrio Fingerprint_custom returned nil")
	}
	return fp, nil
}

func (h *reqrioDLLHandle) fingerprintClientHelloHandle(clientHello []byte, token string) (uintptr, error) {
	if len(clientHello) == 0 {
		return 0, errors.New("reqrio client_hello is empty")
	}
	tokenPtr, err := syscall.BytePtrFromString(token)
	if err != nil {
		return 0, err
	}
	var errPtr uintptr
	fp, _, _ := h.fingerprintClientHello.Call(
		uintptr(unsafe.Pointer(&clientHello[0])),
		uintptr(len(clientHello)),
		uintptr(unsafe.Pointer(tokenPtr)),
		uintptr(unsafe.Pointer(&errPtr)),
	)
	if errPtr != 0 {
		return 0, h.charError(errPtr)
	}
	if fp == 0 {
		return 0, errors.New("reqrio Fingerprint_from_client_hello returned nil")
	}
	return fp, nil
}

func (h *reqrioDLLHandle) urlHandle(rawURL string) (uintptr, error) {
	urlPtr, err := syscall.BytePtrFromString(rawURL)
	if err != nil {
		return 0, err
	}
	var errPtr uintptr
	handle, _, _ := h.urlNew.Call(uintptr(unsafe.Pointer(urlPtr)), uintptr(unsafe.Pointer(&errPtr)))
	if errPtr != 0 {
		return 0, h.charError(errPtr)
	}
	if handle == 0 {
		return 0, errors.New("reqrio Url_new returned nil")
	}
	return handle, nil
}

func (h *reqrioDLLHandle) bodyHandle(body []byte, contentType string) (uintptr, error) {
	if len(body) == 0 {
		handle, _, _ := h.bodyNone.Call()
		if handle == 0 {
			return 0, errors.New("reqrio Body_none returned nil")
		}
		return handle, nil
	}
	ctPtr, err := syscall.BytePtrFromString(contentType)
	if err != nil {
		return 0, err
	}
	var errPtr uintptr
	handle, _, _ := h.bodyNew.Call(
		uintptr(unsafe.Pointer(&body[0])),
		uintptr(len(body)),
		uintptr(unsafe.Pointer(ctPtr)),
		uintptr(unsafe.Pointer(&errPtr)),
	)
	if errPtr != 0 {
		return 0, h.charError(errPtr)
	}
	if handle == 0 {
		return 0, errors.New("reqrio Body_new returned nil")
	}
	return handle, nil
}

func (h *reqrioDLLHandle) responseStatus(resp uintptr) (int, error) {
	var errPtr uintptr
	code, _, _ := h.responseStatusCode.Call(resp, uintptr(unsafe.Pointer(&errPtr)))
	if errPtr != 0 {
		return 0, h.charError(errPtr)
	}
	return int(code), nil
}

func (h *reqrioDLLHandle) responseBody(resp uintptr) ([]byte, error) {
	var size uintptr
	var errPtr uintptr
	ptr, _, _ := h.responseBytes.Call(
		resp,
		uintptr(unsafe.Pointer(&size)),
		uintptr(unsafe.Pointer(&errPtr)),
	)
	if errPtr != 0 {
		return nil, h.charError(errPtr)
	}
	if ptr == 0 || size == 0 {
		return []byte{}, nil
	}
	if size > uintptr(^uint(0)>>1) {
		return nil, errors.New("reqrio response too large")
	}
	src := unsafe.Slice((*byte)(unsafe.Pointer(ptr)), int(size))
	out := make([]byte, len(src))
	copy(out, src)
	return out, nil
}

func (h *reqrioDLLHandle) callSetString(proc *syscall.LazyProc, handle uintptr, value string) error {
	ptr, err := syscall.BytePtrFromString(value)
	if err != nil {
		return err
	}
	if r, _, _ := proc.Call(handle, uintptr(unsafe.Pointer(ptr))); r != 0 {
		return h.charError(r)
	}
	return nil
}

func (h *reqrioDLLHandle) callSetStringOnHandle(proc *syscall.LazyProc, handle uintptr, value string) error {
	return h.callSetString(proc, handle, value)
}

func (h *reqrioDLLHandle) charError(ptr uintptr) error {
	if ptr == 0 {
		return nil
	}
	msg := windows.BytePtrToString((*byte)(unsafe.Pointer(ptr)))
	h.charFree.Call(ptr)
	if msg == "" {
		msg = "reqrio returned an error"
	}
	return errors.New(msg)
}
