//go:build windows

package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"golang.org/x/net/http2"
)

type testConnIDKey struct{}

type trackedTargetStats struct {
	nextID int64

	mu       sync.Mutex
	requests []int
	closes   map[int]string
	protos   map[int]string
}

func (s *trackedTargetStats) newConnID() int {
	return int(atomic.AddInt64(&s.nextID, 1))
}

func (s *trackedTargetStats) recordRequest(id int) {
	s.mu.Lock()
	s.requests = append(s.requests, id)
	s.mu.Unlock()
}

func (s *trackedTargetStats) requestIDs() []int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]int(nil), s.requests...)
}

func (s *trackedTargetStats) recordProto(id int, proto string) {
	s.mu.Lock()
	if s.protos == nil {
		s.protos = map[int]string{}
	}
	s.protos[id] = proto
	s.mu.Unlock()
}

func (s *trackedTargetStats) protoFor(id int) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.protos[id]
}

func (s *trackedTargetStats) recordClose(id int, err error) {
	if err == nil {
		return
	}
	if ne, ok := err.(net.Error); ok && ne.Timeout() {
		return
	}
	s.mu.Lock()
	if s.closes == nil {
		s.closes = map[int]string{}
	}
	if _, exists := s.closes[id]; !exists {
		s.closes[id] = err.Error()
	}
	s.mu.Unlock()
}

func (s *trackedTargetStats) waitClose(id int, timeout time.Duration) (string, bool) {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		s.mu.Lock()
		errText, ok := s.closes[id]
		s.mu.Unlock()
		if ok {
			return errText, true
		}
		time.Sleep(20 * time.Millisecond)
	}
	return "", false
}

type trackingTLSListener struct {
	net.Listener
	cfg   *tls.Config
	stats *trackedTargetStats
}

func (l *trackingTLSListener) Accept() (net.Conn, error) {
	conn, err := l.Listener.Accept()
	if err != nil {
		return nil, err
	}
	id := l.stats.newConnID()
	return &trackingTLSConn{
		Conn:  tls.Server(conn, l.cfg),
		id:    id,
		stats: l.stats,
	}, nil
}

type trackingTLSConn struct {
	*tls.Conn
	id    int
	stats *trackedTargetStats
}

func (c *trackingTLSConn) Read(p []byte) (int, error) {
	n, err := c.Conn.Read(p)
	if err != nil {
		c.stats.recordClose(c.id, err)
	}
	return n, err
}

type testTLSTarget struct {
	addr   string
	stats  *trackedTargetStats
	close  func()
	doneCh chan struct{}
}

func startTrackedTLSTarget(t *testing.T) *testTLSTarget {
	t.Helper()
	cert := selfSignedTestCert(t)
	rawLn, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	stats := &trackedTargetStats{closes: map[int]string{}, protos: map[int]string{}}
	tlsCfg := &tls.Config{
		Certificates: []tls.Certificate{cert},
		NextProtos:   []string{"h2", "http/1.1"},
	}
	var reqNo atomic.Int64
	bizCodes := []int{429, 1, 2, 4030, 2000}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		id, _ := r.Context().Value(testConnIDKey{}).(int)
		stats.recordRequest(id)
		_, _ = io.ReadAll(r.Body)
		_ = r.Body.Close()

		n := reqNo.Add(1)
		if n <= int64(len(bizCodes)) {
			code := bizCodes[n-1]
			w.Header().Set("Content-Type", "application/json")
			_, _ = fmt.Fprintf(w, `{"code":%d,"message":"biz-%d"}`, code, code)
			return
		}
		if n == int64(len(bizCodes)+1) {
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = io.WriteString(w, "1")
			return
		}
		if n == int64(len(bizCodes)+2) {
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = io.WriteString(w, "2")
			return
		}
		if n == int64(len(bizCodes)+3) {
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = io.WriteString(w, "gateway throttled")
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprintf(w, `{"code":%d,"message":"sim-%d"}`, n, n)
	})
	doneCh := make(chan struct{})
	go func() {
		defer close(doneCh)
		for {
			rawConn, err := rawLn.Accept()
			if err != nil {
				return
			}
			id := stats.newConnID()
			conn := &trackingTLSConn{
				Conn:  tls.Server(rawConn, tlsCfg),
				id:    id,
				stats: stats,
			}
			go serveTrackedTLSConn(conn, id, handler)
		}
	}()
	return &testTLSTarget{
		addr:   rawLn.Addr().String(),
		stats:  stats,
		doneCh: doneCh,
		close: func() {
			_ = rawLn.Close()
		},
	}
}

func serveTrackedTLSConn(conn *trackingTLSConn, id int, handler http.Handler) {
	ctx := context.WithValue(context.Background(), testConnIDKey{}, id)
	if err := conn.Handshake(); err != nil {
		conn.stats.recordClose(id, err)
		_ = conn.Close()
		return
	}
	proto := conn.ConnectionState().NegotiatedProtocol
	conn.stats.recordProto(id, proto)
	switch proto {
	case "h2":
		var srv http2.Server
		srv.ServeConn(conn, &http2.ServeConnOpts{
			Handler: handler,
			Context: ctx,
		})
	default:
		srv := &http.Server{
			Handler: handler,
			ConnContext: func(base context.Context, c net.Conn) context.Context {
				return context.WithValue(base, testConnIDKey{}, id)
			},
		}
		_ = srv.Serve(&singleConnListener{conn: conn})
	}
}

type singleConnListener struct {
	conn net.Conn
	used atomic.Bool
}

func (l *singleConnListener) Accept() (net.Conn, error) {
	if l.used.Swap(true) {
		return nil, net.ErrClosed
	}
	return l.conn, nil
}

func (l *singleConnListener) Close() error {
	return nil
}

func (l *singleConnListener) Addr() net.Addr {
	return l.conn.LocalAddr()
}

func selfSignedTestCert(t *testing.T) tls.Certificate {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		t.Fatal(err)
	}
	tmpl := x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			CommonName: "h5.moutai519.com.cn",
		},
		NotBefore: time.Now().Add(-time.Hour),
		NotAfter:  time.Now().Add(time.Hour),
		KeyUsage:  x509.KeyUsageKeyEncipherment | x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{
			x509.ExtKeyUsageServerAuth,
		},
		DNSNames: []string{"h5.moutai519.com.cn"},
	}
	der, err := x509.CreateCertificate(rand.Reader, &tmpl, &tmpl, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "RSA PRIVATE KEY", Bytes: x509.MarshalPKCS1PrivateKey(key)})
	cert, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		t.Fatal(err)
	}
	return cert
}

type testConnectProxy struct {
	url      string
	ln       net.Listener
	connects atomic.Int64
}

func startTestConnectProxy(t *testing.T, targetAddr string) *testConnectProxy {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	p := &testConnectProxy{
		url: "http://" + ln.Addr().String(),
		ln:  ln,
	}
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go p.handle(conn, targetAddr)
		}
	}()
	return p
}

func (p *testConnectProxy) handle(conn net.Conn, targetAddr string) {
	defer conn.Close()
	br := bufio.NewReader(conn)
	req, err := http.ReadRequest(br)
	if err != nil {
		return
	}
	if req.Method != http.MethodConnect {
		return
	}
	p.connects.Add(1)
	target, err := net.Dial("tcp", targetAddr)
	if err != nil {
		_, _ = io.WriteString(conn, "HTTP/1.1 502 Bad Gateway\r\n\r\n")
		return
	}
	defer target.Close()
	_, _ = io.WriteString(conn, "HTTP/1.1 200 Connection established\r\n\r\n")

	done := make(chan struct{}, 2)
	go func() {
		_, _ = io.Copy(target, br)
		done <- struct{}{}
	}()
	go func() {
		_, _ = io.Copy(conn, target)
		done <- struct{}{}
	}()
	<-done
}

func (p *testConnectProxy) close() {
	_ = p.ln.Close()
}

func TestTransportSessionsKeepSimplified429AndCloseOnHTTP429NonJSONInBothModes(t *testing.T) {
	if _, err := reqrioToken(); err != nil {
		t.Skipf("reqrio token unavailable: %v", err)
	}
	if !reqrioDLLAvailable() {
		t.Skip("reqrio dll unavailable")
	}

	type sender func(context.Context, Attempt) (int, []byte, string, error)
	modes := []struct {
		name        string
		before      func()
		after       func()
		send        sender
		fingerprint func(Attempt) string
	}{
		{
			name: "tls-client-14-profile",
			before: func() {
				resetTLSClientSessionsForTest()
				tlsClientRotationCounter.Store(0)
				tlsClientInsecureSkipVerifyForTest.Store(true)
			},
			after: func() {
				tlsClientInsecureSkipVerifyForTest.Store(false)
				resetTLSClientSessionsForTest()
			},
			send: doRequestTLSClientCtxWithProxy,
			fingerprint: func(att Attempt) string {
				key := transportSessionKey("tls-client", att)
				tlsClientSessionMu.Lock()
				defer tlsClientSessionMu.Unlock()
				if sess := tlsClientSessions[key]; sess != nil {
					return sess.entry.Name
				}
				return ""
			},
		},
		{
			name: "reqrio-dynamic-fingerprint",
			before: func() {
				resetReqrioSessionsForTest()
				reqrioInsecureSkipVerifyForTest.Store(true)
			},
			after: func() {
				reqrioInsecureSkipVerifyForTest.Store(false)
				resetReqrioSessionsForTest()
			},
			send: doRequestReqrioCtxWithProxy,
			fingerprint: func(att Attempt) string {
				key := transportSessionKey("reqrio", att)
				reqrioSessionMu.Lock()
				defer reqrioSessionMu.Unlock()
				if sess := reqrioSessions[key]; sess != nil {
					return sess.plan.CustomTLSSHA256
				}
				return ""
			},
		},
	}

	for _, mode := range modes {
		t.Run(mode.name, func(t *testing.T) {
			mode.before()
			defer mode.after()

			target := startTrackedTLSTarget(t)
			defer target.close()
			proxyA := startTestConnectProxy(t, target.addr)
			defer proxyA.close()
			proxyB := startTestConnectProxy(t, target.addr)
			defer proxyB.close()

			body := []byte(`{"actParam":"abc"}`)
			base := Attempt{
				Method:     "POST",
				URL:        "https://h5.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
				Host:       "h5.moutai519.com.cn",
				AccountKey: "integration-account",
				HeadersOrdered: []HeaderPair{
					{Name: "Content-Type", Value: "application/json;charset=UTF-8"},
					{Name: "User-Agent", Value: "Mozilla/5.0"},
					{Name: "Cookie", Value: "MT-Device-ID-Wap=dev-integration"},
				},
				BodyB64:   base64.StdEncoding.EncodeToString(body),
				TimeoutMs: 5000,
			}
			proxies := []string{proxyA.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url}
			wantActual := []string{proxyA.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url, proxyB.url}
			wantStatus := []int{200, 200, 200, 200, 200, 429, 429, 429, 200}
			wantBizBodies := []string{`"code":429`, `"code":1`, `"code":2`, `"code":4030`, `"code":2000`, "1", "2", "gateway throttled"}

			var firstConn int
			var firstFingerprint string
			var proxyBConn int
			var proxyBFingerprint string
			for i := range proxies {
				att := base
				att.ProxyURL = proxies[i]
				status, respBody, actualProxy, err := mode.send(context.Background(), att)
				if err != nil {
					t.Fatalf("shot %d error: %v", i+1, err)
				}
				if status != wantStatus[i] {
					t.Fatalf("shot %d status=%d body=%q want %d", i+1, status, string(respBody), wantStatus[i])
				}
				if actualProxy != wantActual[i] {
					t.Fatalf("shot %d actualProxy=%s want %s", i+1, actualProxy, wantActual[i])
				}
				if i < len(wantBizBodies) && !strings.Contains(string(respBody), wantBizBodies[i]) {
					t.Fatalf("shot %d business body=%q want token %s", i+1, string(respBody), wantBizBodies[i])
				}
				ids := target.stats.requestIDs()
				if len(ids) != i+1 {
					t.Fatalf("shot %d target request count=%d ids=%v", i+1, len(ids), ids)
				}
				if i == 0 {
					firstConn = ids[0]
					firstFingerprint = mode.fingerprint(att)
					if firstFingerprint == "" {
						t.Fatalf("shot 1 did not create a fingerprint session")
					}
				}
				if i == 1 {
					proxyBConn = ids[1]
					proxyBFingerprint = mode.fingerprint(att)
					if proxyBConn == firstConn {
						t.Fatalf("shot 2 should use a different proxy/TLS connection; ids=%v", ids)
					}
					if proxyBFingerprint == "" {
						t.Fatalf("shot 2 did not create a proxyB fingerprint session")
					}
				}
				if i >= 1 && i < 8 && ids[i] != proxyBConn {
					t.Fatalf("shot %d conn=%d want proxyB conn %d; ids=%v", i+1, ids[i], proxyBConn, ids)
				}
				if i == 7 {
					closeErr, ok := target.stats.waitClose(proxyBConn, 3*time.Second)
					if !ok {
						t.Fatalf("first TLS connection did not close after non-JSON 429; ids=%v", target.stats.requestIDs())
					}
					if closeErr != "EOF" || strings.Contains(strings.ToLower(closeErr), "unexpected") {
						t.Fatalf("proxyB TLS close error=%q; want EOF from close_notify", closeErr)
					}
				}
			}

			ids := target.stats.requestIDs()
			if len(ids) != 9 || ids[0] == ids[1] || ids[1] != ids[2] || ids[2] != ids[3] || ids[3] != ids[4] || ids[4] != ids[5] || ids[5] != ids[6] || ids[6] != ids[7] || ids[8] == ids[1] {
				t.Fatalf("bad connection reuse/switch ids=%v", ids)
			}
			if mode.name == "reqrio-dynamic-fingerprint" {
				for _, id := range []int{ids[0], ids[1], ids[8]} {
					if proto := target.stats.protoFor(id); proto != reqrioWireALPN {
						t.Fatalf("reqrio connection %d negotiated ALPN=%q want %q", id, proto, reqrioWireALPN)
					}
				}
			}
			lastAtt := base
			lastAtt.ProxyURL = proxyB.url
			nextFingerprint := mode.fingerprint(lastAtt)
			if nextFingerprint == "" {
				t.Fatal("new proxy did not create a replacement fingerprint session")
			}
			if nextFingerprint == proxyBFingerprint {
				t.Fatalf("fingerprint did not change after TLS rebuild: %s", nextFingerprint)
			}
			if got := proxyA.connects.Load(); got != 1 {
				t.Fatalf("proxyA CONNECT count=%d want 1", got)
			}
			if got := proxyB.connects.Load(); got != 2 {
				t.Fatalf("proxyB CONNECT count=%d want 2", got)
			}
		})
	}
}

func TestTransportSessionKeyIncludesRushLane(t *testing.T) {
	base := Attempt{
		Method:     "POST",
		URL:        "https://h5.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
		Host:       "h5.moutai519.com.cn",
		AccountKey: "account-a",
		ProxyURL:   "http://user:pass@proxy-a.local:8080",
	}
	lane0 := base
	lane0.LaneID = 0
	lane1 := base
	lane1.LaneID = 1

	if transportSessionKey("reqrio", lane0) == transportSessionKey("reqrio", lane1) {
		t.Fatal("reqrio transport session key should differ by laneId")
	}
	if transportSessionKey("tls-client", lane0) == transportSessionKey("tls-client", lane1) {
		t.Fatal("tls-client transport session key should differ by laneId")
	}
	proxyB := base
	proxyB.ProxyURL = "http://user:pass@proxy-b.local:8080"
	if transportSessionKey("reqrio", base) == transportSessionKey("reqrio", proxyB) {
		t.Fatal("reqrio transport session key should differ by proxyUrl")
	}
	if transportSessionKey("tls-client", base) == transportSessionKey("tls-client", proxyB) {
		t.Fatal("tls-client transport session key should differ by proxyUrl")
	}
	laneNegative := base
	laneNegative.LaneID = -9
	if transportSessionKey("reqrio", laneNegative) != transportSessionKey("reqrio", lane0) {
		t.Fatal("negative laneId should normalize to lane 0")
	}
}
