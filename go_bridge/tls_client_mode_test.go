package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	jhttp "github.com/smallerqiu/ja3-client/http"
)

func TestTLSClientProfileRotatesAcrossFourteenFingerprints(t *testing.T) {
	t.Setenv("MT_GO_TLS_CLIENT_PROFILE", "legacy_profile_should_be_ignored")
	tlsClientRotationCounter.Store(0)
	if got := tlsClientProfileName(); got != tlsClientRotationName {
		t.Fatalf("profile=%s want %s", got, tlsClientRotationName)
	}
	summary := tlsClientProfilesSummary()
	if got := summary["selected"]; got != tlsClientRotationName {
		t.Fatalf("summary selected=%v want %s", got, tlsClientRotationName)
	}
	if got := summary["fixed"]; got != false {
		t.Fatalf("summary fixed=%v want false", got)
	}
	if got := summary["rotationCount"]; got != len(tlsClientRotationPool) {
		t.Fatalf("rotationCount=%v want %d", got, len(tlsClientRotationPool))
	}
	if _, name, err := selectedTLSClientProfile(); err != nil || name != "chrome_131" {
		t.Fatalf("selected profile=%s err=%v", name, err)
	}
	want := []struct {
		name    string
		uaToken string
	}{
		{"chrome_131", "Chrome/131.0.0.0"},
		{"chrome_129", "Chrome/129.0.0.0"},
		{"edge_138", "Edg/138.0.0.0"},
		{"firefox_135", "Firefox/135.0"},
		{"safari_18_5", "Version/18.5 Safari"},
		{"chrome_131_android", "Chrome/131.0.0.0 Mobile"},
		{"firefox_132", "Firefox/132.0"},
		{"chrome_117", "Chrome/117.0.0.0"},
		{"safari_ios_26_0", "Version/26.0 Mobile"},
		{"safari_15_3", "Version/15.3 Safari"},
		{"firefox_123", "Firefox/123.0"},
		{"safari_26_0", "Version/26.0 Safari"},
		{"firefox_120", "Firefox/120.0"},
		{"firefox_117", "Firefox/117.0"},
	}
	if len(want) != len(tlsClientRotationPool) {
		t.Fatalf("test table len=%d rotation pool len=%d", len(want), len(tlsClientRotationPool))
	}
	for _, w := range want {
		entry := tlsClientProfileEntryForRequest()
		if entry.Name != w.name {
			t.Fatalf("rotation profile=%s want %s", entry.Name, w.name)
		}
		if !strings.Contains(entry.UserAgent, w.uaToken) {
			t.Fatalf("empty user agent for %s", entry.Name)
		}
	}
	entry := tlsClientProfileEntryForRequest()
	if entry.Name != "chrome_131" {
		t.Fatalf("rotation did not wrap: got %s want chrome_131", entry.Name)
	}
}

func TestTLSClientUserAgentMatchesRotatedProfileAndOverridesAccountUA(t *testing.T) {
	tlsClientRotationCounter.Store(0)
	for i := range tlsClientRotationPool {
		entry := tlsClientProfileEntryForRequest()
		att := Attempt{
			Method: "GET",
			URL:    "https://h5.moutai519.com.cn/",
			HeadersOrdered: []HeaderPair{
				{Name: "User-Agent", Value: "OLD-ACCOUNT-UA"},
				{Name: "Accept", Value: "*/*"},
			},
			Host: "h5.moutai519.com.cn",
		}
		req, err := newTLSClientRequest(context.Background(), att, nil, 0, entry)
		if err != nil {
			t.Fatal(err)
		}
		if got := req.Header.Get("User-Agent"); got != entry.UserAgent {
			t.Fatalf("[%d] user-agent=%q want %q for %s", i, got, entry.UserAgent, entry.Name)
		}
		order := req.Header[jhttp.HeaderOrderKey]
		count := 0
		for _, item := range order {
			if item == "user-agent" {
				count++
			}
		}
		if count != 1 {
			t.Fatalf("[%d] user-agent header order count=%d order=%v", i, count, order)
		}
	}
}

func TestTLSClientPreservesRushBusinessUserAgent(t *testing.T) {
	entry := tlsClientProfileEntry{
		Name:      "chrome_131",
		JA3NHash:  "3118d34415923e98f69776f47a249f38",
		UserAgent: tlsClientProfileUserAgent("chrome_131"),
	}
	rushUA := "Mozilla/5.0 (Linux; Android 12; SM-S9180 Build/TP1A.220624.014; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/138.0.7204.168 Mobile Safari/537.36 moutaiapp/1.9.10 device-id/dev-1 BS-DVID/bs-1"
	att := Attempt{
		Method: "POST",
		URL:    "https://h5.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
		HeadersOrdered: []HeaderPair{
			{Name: "User-Agent", Value: rushUA},
			{Name: "Accept", Value: "application/json, text/javascript, */*; q=0.01"},
			{Name: "Content-Web-Bb", Value: "bb-test"},
		},
		Host: "h5.moutai519.com.cn",
	}
	req, err := newTLSClientRequest(context.Background(), att, nil, 0, entry)
	if err != nil {
		t.Fatal(err)
	}
	if got := req.Header.Get("User-Agent"); got != rushUA {
		t.Fatalf("rush user-agent changed: got %q want %q", got, rushUA)
	}
	wireUA, source := tlsClientWireUserAgent(att, entry)
	if wireUA != rushUA || source != "attempt_business" {
		t.Fatalf("wire ua/source=%q/%q want rush ua/attempt_business", wireUA, source)
	}
}

func TestTLSClientPreviewPreservesAttemptValues(t *testing.T) {
	t.Setenv("MT_GO_TLS_CLIENT_PROFILE", "safari17_0")
	tlsClientRotationCounter.Store(0)
	body := []byte(`{"actParam":"abc","n":1}`)
	att := Attempt{
		Method: "POST",
		URL:    "https://app.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
		HeadersOrdered: []HeaderPair{
			{Name: "Host", Value: "app.moutai519.com.cn"},
			{Name: "Content-Type", Value: "application/json;charset=UTF-8"},
			{Name: "MT-K", Value: "k-test"},
			{Name: "MT-V", Value: "v-test"},
			{Name: "Content-Web-Bb", Value: "bb-test"},
			{Name: "Cookie", Value: "MT-Token-Wap=tok; MT-Device-ID-Wap=dev; _sdk_v_=1.0; _bs_device_id=bid-1; _d_u=du-1"},
			{Name: "Content-Length", Value: "26"},
		},
		BodyB64:       base64.StdEncoding.EncodeToString(body),
		Host:          "app.moutai519.com.cn",
		ContentLength: int64(len(body)),
		TimeoutMs:     3000,
	}
	preview, err := tlsClientAttemptPreview(att)
	if err != nil {
		t.Fatal(err)
	}
	if got := preview["profile"]; got != "chrome_131" {
		t.Fatalf("profile=%v want chrome_131", got)
	}
	if got := preview["rotation"]; got != tlsClientRotationName {
		t.Fatalf("rotation=%v", got)
	}
	if got := preview["host"]; got != att.Host {
		t.Fatalf("host mismatch: %v", got)
	}
	if got := preview["contentLength"]; got != int64(len(body)) {
		t.Fatalf("contentLength mismatch: %v", got)
	}
	if got := preview["bodyLen"]; got != int64(len(body)) {
		t.Fatalf("bodyLen mismatch: %v", got)
	}
	sum := sha256.Sum256(body)
	if got := preview["bodySha256"]; got != hex.EncodeToString(sum[:]) {
		t.Fatalf("body sha mismatch: %v", got)
	}
	order, ok := preview["headerOrder"].([]string)
	if !ok {
		t.Fatalf("bad headerOrder type: %T", preview["headerOrder"])
	}
	wantOrder := []string{"user-agent", "host", "content-type", "mt-k", "mt-v", "content-web-bb", "cookie"}
	if len(order) != len(wantOrder) {
		t.Fatalf("header order len=%d want=%d order=%v", len(order), len(wantOrder), order)
	}
	for i := range wantOrder {
		if order[i] != wantOrder[i] {
			t.Fatalf("header order[%d]=%s want=%s all=%v", i, order[i], wantOrder[i], order)
		}
	}
	headers, ok := preview["headers"].(map[string][]string)
	if !ok {
		t.Fatalf("bad headers type: %T", preview["headers"])
	}
	if got := headers["Cookie"][0]; got != "MT-Token-Wap=tok; MT-Device-ID-Wap=dev; _sdk_v_=1.0; _bs_device_id=bid-1; _d_u=du-1" {
		t.Fatalf("cookie changed: %s", got)
	}
	if got := headers["Content-Web-Bb"][0]; got != "bb-test" {
		t.Fatalf("bb changed: %s", got)
	}
	if got := headers["User-Agent"][0]; !strings.Contains(got, "Chrome/131.0.0.0") {
		t.Fatalf("user-agent did not match selected profile: %s", got)
	}
}

func TestTLSClientHTTPRoundTripPreservesValues(t *testing.T) {
	t.Setenv("MT_GO_TLS_CLIENT_PROFILE", "legacy_profile_should_be_ignored")
	resetTLSClientSessionsForTest()
	body := []byte(`{"actParam":"abc"}`)
	type seenReq struct {
		Host          string `json:"host"`
		ContentLength int64  `json:"contentLength"`
		Cookie        string `json:"cookie"`
		BB            string `json:"bb"`
		MTK           string `json:"mtk"`
		Body          string `json:"body"`
	}
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotBody, _ := io.ReadAll(r.Body)
		_ = json.NewEncoder(w).Encode(seenReq{
			Host:          r.Host,
			ContentLength: r.ContentLength,
			Cookie:        r.Header.Get("Cookie"),
			BB:            r.Header.Get("Content-Web-Bb"),
			MTK:           r.Header.Get("MT-K"),
			Body:          string(gotBody),
		})
	}))
	defer srv.Close()

	att := Attempt{
		Method: "POST",
		URL:    srv.URL,
		HeadersOrdered: []HeaderPair{
			{Name: "Host", Value: "mock.local"},
			{Name: "Content-Type", Value: "application/json;charset=UTF-8"},
			{Name: "MT-K", Value: "k-test"},
			{Name: "Content-Web-Bb", Value: "bb-test"},
			{Name: "Cookie", Value: "MT-Token-Wap=tok; MT-Device-ID-Wap=dev; _sdk_v_=1.0; _bs_device_id=bid-1; _d_u=du-1"},
			{Name: "Content-Length", Value: "18"},
		},
		BodyB64:       base64.StdEncoding.EncodeToString(body),
		Host:          "mock.local",
		ContentLength: int64(len(body)),
		TimeoutMs:     3000,
	}
	entry := tlsClientProfileEntry{
		Name:      "edge_138",
		JA3NHash:  "4cc9d9021e1f6b341f186aa7fa099388",
		UserAgent: tlsClientProfileUserAgent("edge_138"),
	}
	req, err := newTLSClientRequest(context.Background(), att, body, int64(len(body)), entry)
	if err != nil {
		t.Fatal(err)
	}
	var buf bytes.Buffer
	if err := req.Write(&buf); err != nil {
		t.Fatal(err)
	}
	wire := buf.String()
	if !bytes.Contains([]byte(wire), []byte("Host: mock.local\r\n")) {
		t.Fatalf("wire host missing: %s", wire)
	}
	if !bytes.Contains([]byte(wire), []byte("Content-Length: 18\r\n")) {
		t.Fatalf("wire content-length missing: %s", wire)
	}
	if !bytes.Contains([]byte(wire), []byte("Edg/138.0.0.0")) {
		t.Fatalf("wire user-agent not matched to edge_138: %s", wire)
	}

	status, respBody, err := doRequestTLSClientCtx(context.Background(), att)
	if err != nil {
		t.Fatal(err)
	}
	if status != http.StatusOK {
		t.Fatalf("status=%d body=%s", status, string(respBody))
	}
	var seen seenReq
	if err := json.Unmarshal(respBody, &seen); err != nil {
		t.Fatal(err)
	}
	if seen.Host != "mock.local" {
		t.Fatalf("host=%s", seen.Host)
	}
	if seen.ContentLength != int64(len(body)) {
		t.Fatalf("contentLength=%d", seen.ContentLength)
	}
	if seen.Cookie != "MT-Token-Wap=tok; MT-Device-ID-Wap=dev; _sdk_v_=1.0; _bs_device_id=bid-1; _d_u=du-1" {
		t.Fatalf("cookie=%s", seen.Cookie)
	}
	if seen.BB != "bb-test" || seen.MTK != "k-test" {
		t.Fatalf("headers changed: bb=%s mtk=%s", seen.BB, seen.MTK)
	}
	if seen.Body != string(body) {
		t.Fatalf("body=%s", seen.Body)
	}
}

func TestTLSClientSessionReusesSameProxyAndSplitsDifferentProxy(t *testing.T) {
	resetTLSClientSessionsForTest()
	tlsClientRotationCounter.Store(0)
	defer resetTLSClientSessionsForTest()

	base := Attempt{
		Method:     "POST",
		URL:        "https://h5.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
		Host:       "h5.moutai519.com.cn",
		AccountKey: "account-a",
		ProxyURL:   "http://user:pass@proxy-a.local:8080",
	}
	s1, err := getTLSClientSession(base, 3000)
	if err != nil {
		t.Fatal(err)
	}
	s1b, err := getTLSClientSession(base, 3000)
	if err != nil {
		t.Fatal(err)
	}
	if s1 != s1b {
		t.Fatal("same account/target/proxy/lane should reuse the active TLS client session")
	}
	if s1b.proxyURL != base.ProxyURL {
		t.Fatalf("proxy=%s want %s", s1b.proxyURL, base.ProxyURL)
	}
	if s1b.entry.Name != "chrome_131" {
		t.Fatalf("profile=%s want first rotation chrome_131", s1b.entry.Name)
	}
	if got := tlsClientRotationCounter.Load(); got != 1 {
		t.Fatalf("rotation counter=%d want 1 while session is reused", got)
	}

	next := base
	next.ProxyURL = "http://user:pass@proxy-b.local:8080"
	s2, err := getTLSClientSession(next, 3000)
	if err != nil {
		t.Fatal(err)
	}
	if s2 == s1 {
		t.Fatal("different proxyUrl should create a separate TLS client session")
	}
	if s2.proxyURL != next.ProxyURL {
		t.Fatalf("proxy=%s want new proxy %s", s2.proxyURL, next.ProxyURL)
	}
	if s2.entry.Name != "chrome_129" {
		t.Fatalf("profile=%s want next rotation chrome_129", s2.entry.Name)
	}
	if got := tlsClientRotationCounter.Load(); got != 2 {
		t.Fatalf("rotation counter=%d want 2 after different proxy session", got)
	}

	invalidateTLSClientSession(s2.key, s2)
	s3, err := getTLSClientSession(next, 3000)
	if err != nil {
		t.Fatal(err)
	}
	if s3 == s2 {
		t.Fatal("invalidated session should not be reused")
	}
	if s3.proxyURL != next.ProxyURL {
		t.Fatalf("proxy=%s want new proxy %s", s3.proxyURL, next.ProxyURL)
	}
}

func TestTLSClientSessionSeparatesRushLanes(t *testing.T) {
	resetTLSClientSessionsForTest()
	tlsClientRotationCounter.Store(0)
	defer resetTLSClientSessionsForTest()

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

	s0, err := getTLSClientSession(lane0, 3000)
	if err != nil {
		t.Fatal(err)
	}
	s0b, err := getTLSClientSession(lane0, 3000)
	if err != nil {
		t.Fatal(err)
	}
	if s0b != s0 {
		t.Fatal("same account/target/lane should reuse the TLS client session")
	}
	s1, err := getTLSClientSession(lane1, 3000)
	if err != nil {
		t.Fatal(err)
	}
	if s1 == s0 {
		t.Fatal("different rush lanes should use independent TLS client sessions")
	}
	if transportSessionKey("tls-client", lane0) == transportSessionKey("tls-client", lane1) {
		t.Fatal("transport session key must include laneId")
	}
	if got := tlsClientRotationCounter.Load(); got != 2 {
		t.Fatalf("rotation counter=%d want 2 for two lane sessions", got)
	}
}

func TestHTTP429SessionCloseReason(t *testing.T) {
	if !isHTTP429Empty(429, nil) || !isHTTP429Empty(429, []byte(" \r\n\t")) {
		t.Fatal("empty 429 should trigger connection invalidation")
	}
	if isHTTP429Empty(429, []byte(`{"code":1}`)) || isHTTP429Empty(200, nil) {
		t.Fatal("only HTTP 429 with an empty body should trigger connection invalidation")
	}
	if reason, ok := http429SessionCloseReason(429, nil); !ok || reason != "http429_empty" {
		t.Fatalf("empty HTTP 429 close reason=(%q,%v), want http429_empty,true", reason, ok)
	}
	if reason, ok := http429SessionCloseReason(429, []byte("<html>too many</html>")); !ok || reason != "http429_non_json" {
		t.Fatalf("non-JSON HTTP 429 close reason=(%q,%v), want http429_non_json,true", reason, ok)
	}
	if reason, ok := http429SessionCloseReason(429, []byte("1")); ok || reason != "" {
		t.Fatalf("simplified business HTTP 429 body=1 close reason=(%q,%v), want empty,false", reason, ok)
	}
	if reason, ok := http429SessionCloseReason(429, []byte(" 2\r\n")); ok || reason != "" {
		t.Fatalf("simplified business HTTP 429 body=2 close reason=(%q,%v), want empty,false", reason, ok)
	}
	if reason, ok := http429SessionCloseReason(429, []byte("gateway throttled")); !ok || reason != "http429_non_json" {
		t.Fatalf("non-business HTTP 429 text close reason=(%q,%v), want http429_non_json,true", reason, ok)
	}
	if reason, ok := http429SessionCloseReason(429, []byte(`{"code":429,"message":"rate"}`)); ok || reason != "" {
		t.Fatalf("business JSON HTTP 429 close reason=(%q,%v), want empty,false", reason, ok)
	}
	if reason, ok := http429SessionCloseReason(200, nil); ok || reason != "" {
		t.Fatalf("HTTP 200 close reason=(%q,%v), want empty,false", reason, ok)
	}
}

func TestReqrioFingerprintRandomPerConnectionEntropy(t *testing.T) {
	att := Attempt{
		Method:     "POST",
		URL:        "https://h5.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
		Host:       "h5.moutai519.com.cn",
		AccountKey: "account-a",
	}
	a1, err := reqrioFingerprintPlanFromEntropy(att, []byte("request-entropy-a"))
	if err != nil {
		t.Fatal(err)
	}
	a2, err := reqrioFingerprintPlanFromEntropy(att, []byte("request-entropy-b"))
	if err != nil {
		t.Fatal(err)
	}
	if a1.CustomTLSSHA256 == a2.CustomTLSSHA256 || a1.ProfileID == a2.ProfileID || a1.RandomID == a2.RandomID {
		t.Fatalf("same account reused custom tls: %s/%s/%s vs %s/%s/%s", a1.ProfileID, a1.RandomID, a1.CustomTLSSHA256, a2.ProfileID, a2.RandomID, a2.CustomTLSSHA256)
	}
	a1Again, err := reqrioFingerprintPlanFromEntropy(att, []byte("request-entropy-a"))
	if err != nil {
		t.Fatal(err)
	}
	if a1.CustomTLSSHA256 != a1Again.CustomTLSSHA256 || a1.ProfileID != a1Again.ProfileID {
		t.Fatalf("same entropy should be reproducible in tests: %s/%s vs %s/%s", a1.ProfileID, a1.CustomTLSSHA256, a1Again.ProfileID, a1Again.CustomTLSSHA256)
	}
	if a1.AccountHash != a2.AccountHash || a1.AccountSource != a2.AccountSource {
		t.Fatalf("account metadata should remain stable for logging: %s/%s vs %s/%s", a1.AccountSource, a1.AccountHash, a2.AccountSource, a2.AccountHash)
	}
	if a1.ClientHelloSHA == a2.ClientHelloSHA {
		t.Fatalf("same account reused client_hello: %s vs %s", a1.ClientHelloSHA, a2.ClientHelloSHA)
	}
	if a1.ClientHelloSHA != a1Again.ClientHelloSHA {
		t.Fatalf("same entropy should reproduce client_hello in tests: %s vs %s", a1.ClientHelloSHA, a1Again.ClientHelloSHA)
	}
}

func TestReqrioAccountKeyCookieFallback(t *testing.T) {
	att := Attempt{
		URL: "https://h5.moutai519.com.cn/",
		HeadersOrdered: []HeaderPair{
			{Name: "Cookie", Value: "MT-Token-Wap=tok; MT-Device-ID-Wap=dev-123; _bs_device_id=bs-1; _d_u=du-1"},
		},
	}
	key, source := reqrioAccountKey(att)
	if key != "dev-123" {
		t.Fatalf("key=%q want dev-123", key)
	}
	if source != "cookie.MT-Device-ID-Wap" {
		t.Fatalf("source=%q", source)
	}
}

func TestReqrioPreviewRandomPerConnection(t *testing.T) {
	t.Setenv("MT_TLS_ENGINE", "reqrio")
	att := Attempt{
		Method:     "POST",
		URL:        "https://h5.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
		AccountKey: "account-a",
		HeadersOrdered: []HeaderPair{
			{Name: "User-Agent", Value: "Mozilla/5.0"},
			{Name: "Cookie", Value: "MT-Device-ID-Wap=dev-123"},
			{Name: "Content-Length", Value: "2"},
		},
		BodyB64: base64.StdEncoding.EncodeToString([]byte("{}")),
	}
	p1, err := tlsClientAttemptPreview(att)
	if err != nil {
		t.Fatal(err)
	}
	p2, err := tlsClientAttemptPreview(att)
	if err != nil {
		t.Fatal(err)
	}
	if p1["mode"] != "reqrio" || p1["profile"] != reqrioRandomProfileName {
		t.Fatalf("bad preview mode/profile: %v %v", p1["mode"], p1["profile"])
	}
	if p1["stablePerAccount"] != false || p1["randomPerRequest"] != false || p1["randomPerConnection"] != true {
		t.Fatalf("bad preview randomness flags: stable=%v randomPerRequest=%v randomPerConnection=%v", p1["stablePerAccount"], p1["randomPerRequest"], p1["randomPerConnection"])
	}
	fp1 := p1["fingerprint"].(map[string]interface{})
	fp2 := p2["fingerprint"].(map[string]interface{})
	if fp1["customTLSSha256"] == fp2["customTLSSha256"] || fp1["randomId"] == fp2["randomId"] {
		t.Fatalf("preview reused custom tls: %v/%v vs %v/%v", fp1["randomId"], fp1["customTLSSha256"], fp2["randomId"], fp2["customTLSSha256"])
	}
	order := p1["headerOrder"].([]string)
	if len(order) != 2 || order[0] != "user-agent" || order[1] != "cookie" {
		t.Fatalf("unexpected reqrio header order: %v", order)
	}
}

func TestReqrioCustomTLSJSONMatchesMTSpecialShape(t *testing.T) {
	plan := reqrioFingerprintPlan{
		SNI:           "h5.moutai519.com.cn",
		GreaseCipher:  0x0a0a,
		GreaseGroup:   0x1a1a,
		GreaseExt1:    0x2a2a,
		GreaseVersion: 0x3a3a,
		GreaseExt2:    0x4a4a,
		Padding:       173 + len("h5.moutai519.com.cn"),
	}
	raw := reqrioCustomTLSJSON(plan)
	wantOrder := []string{
		`"10794":null`,
		`"0":null`,
		`"23":null`,
		`"65281":null`,
		`"10":[6682,29,23,24]`,
		`"11":[0]`,
		`"35":null`,
		`"16":["http/1.1"]`,
		`"5":null`,
		`"13":[1027,2052,1025,1283,2053,1281,2054,1537]`,
		`"18":null`,
		`"51":[6682,29]`,
		`"45":1`,
		`"43":[14906,772,771,770,769]`,
		`"27":[2]`,
		`"19018":[0]`,
		`"21":192`,
	}
	prev := -1
	for _, token := range wantOrder {
		idx := strings.Index(raw, token)
		if idx < 0 {
			t.Fatalf("custom tls json missing %s in %s", token, raw)
		}
		if idx <= prev {
			t.Fatalf("custom tls extension order moved around token=%s json=%s", token, raw)
		}
		prev = idx
	}

	var decoded struct {
		Suites     []int          `json:"suites"`
		Settings   map[string]int `json:"settings"`
		WindowSize int            `json:"window_size"`
		Weight     int            `json:"weight"`
		Priority   bool           `json:"priority"`
	}
	if err := json.Unmarshal([]byte(raw), &decoded); err != nil {
		t.Fatal(err)
	}
	wantSuites := []int{0x0a0a, 0x1301, 0x1302, 0x1303, 0xc02b, 0xc02f, 0xc02c, 0xc030, 0xcca9, 0xcca8, 0xc013, 0xc014, 0x009c, 0x009d, 0x002f, 0x0035}
	if len(decoded.Suites) != len(wantSuites) {
		t.Fatalf("suite len=%d want=%d suites=%v", len(decoded.Suites), len(wantSuites), decoded.Suites)
	}
	for i := range wantSuites {
		if decoded.Suites[i] != wantSuites[i] {
			t.Fatalf("suite[%d]=%d want=%d all=%v", i, decoded.Suites[i], wantSuites[i], decoded.Suites)
		}
	}
	if decoded.Settings["HeaderTableSize"] != 65536 ||
		decoded.Settings["EnablePush"] != 0 ||
		decoded.Settings["InitialWindowSize"] != 6291456 ||
		decoded.Settings["MaxHeaderListSize"] != 242144 {
		t.Fatalf("settings changed: %#v", decoded.Settings)
	}
	if decoded.WindowSize != 2147418112 || decoded.Weight != 147 || !decoded.Priority {
		t.Fatalf("h2 frame params changed: window=%d weight=%d priority=%v", decoded.WindowSize, decoded.Weight, decoded.Priority)
	}
}

func TestReqrioCustomTLSJSONMatchesPythonGreaseExtensionCollision(t *testing.T) {
	plan := reqrioFingerprintPlan{
		SNI:           "h5.moutai519.com.cn",
		GreaseCipher:  0x0a0a,
		GreaseGroup:   0x1a1a,
		GreaseExt1:    0x2a2a,
		GreaseVersion: 0x3a3a,
		GreaseExt2:    0x2a2a,
		Padding:       173 + len("h5.moutai519.com.cn"),
	}
	raw := reqrioCustomTLSJSON(plan)
	if strings.Contains(raw, `"10794":null`) {
		t.Fatalf("python dict collision should overwrite first grease extension: %s", raw)
	}
	if strings.Count(raw, `"10794":[0]`) != 1 {
		t.Fatalf("python dict collision should keep second grease extension value once: %s", raw)
	}
}

func TestReqrioRequestKeepsCustomTLSAndClientHello(t *testing.T) {
	att := Attempt{
		Method:     "POST",
		URL:        "https://h5.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
		Host:       "h5.moutai519.com.cn",
		AccountKey: "account-a",
	}
	plan, err := reqrioFingerprintPlanFromEntropy(att, []byte("request-entropy-a"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(plan.CustomTLSJSON) == "" {
		t.Fatal("custom tls json missing")
	}
	if len(plan.ClientHello) == 0 {
		t.Fatal("client hello missing")
	}
}

func TestReqrioClientHelloUsesTLS10RecordAndTLS12LegacyVersion(t *testing.T) {
	att := Attempt{
		Method:     "POST",
		URL:        "https://h5.moutai519.com.cn/xhr/front/trade/priority/rushPurchase",
		Host:       "h5.moutai519.com.cn",
		AccountKey: "account-a",
	}
	plan, err := reqrioFingerprintPlanFromEntropy(att, []byte("request-entropy-a"))
	if err != nil {
		t.Fatal(err)
	}
	ch := plan.ClientHello
	if len(ch) < 11 {
		t.Fatalf("client_hello too short: %d", len(ch))
	}
	if ch[0] != 0x16 {
		t.Fatalf("record type=%02x want 16", ch[0])
	}
	if binary.BigEndian.Uint16(ch[1:3]) != 0x0301 {
		t.Fatalf("record version=%04x want 0301", binary.BigEndian.Uint16(ch[1:3]))
	}
	if ch[5] != 0x01 {
		t.Fatalf("handshake type=%02x want 01", ch[5])
	}
	if binary.BigEndian.Uint16(ch[9:11]) != 0x0303 {
		t.Fatalf("legacy version=%04x want 0303", binary.BigEndian.Uint16(ch[9:11]))
	}
	compressCertificate, ok, err := reqrioClientHelloExtension(ch, 0x001b)
	if err != nil {
		t.Fatal(err)
	}
	if !ok {
		t.Fatal("compress_certificate extension missing")
	}
	if !bytes.Equal(compressCertificate, []byte{2, 0, 2}) {
		t.Fatalf("compress_certificate=%x len=%d want 020002 len=3", compressCertificate, len(compressCertificate))
	}
}

func reqrioClientHelloExtension(ch []byte, wantType uint16) ([]byte, bool, error) {
	if len(ch) < 11 || ch[0] != 0x16 || ch[5] != 0x01 {
		return nil, false, io.ErrUnexpectedEOF
	}
	pos := 11 + 32
	if pos >= len(ch) {
		return nil, false, io.ErrUnexpectedEOF
	}
	pos += 1 + int(ch[pos])
	if pos+2 > len(ch) {
		return nil, false, io.ErrUnexpectedEOF
	}
	cipherLen := int(binary.BigEndian.Uint16(ch[pos : pos+2]))
	pos += 2 + cipherLen
	if pos >= len(ch) {
		return nil, false, io.ErrUnexpectedEOF
	}
	pos += 1 + int(ch[pos])
	if pos+2 > len(ch) {
		return nil, false, io.ErrUnexpectedEOF
	}
	extLen := int(binary.BigEndian.Uint16(ch[pos : pos+2]))
	pos += 2
	end := pos + extLen
	if end > len(ch) {
		return nil, false, io.ErrUnexpectedEOF
	}
	for pos < end {
		if pos+4 > end {
			return nil, false, io.ErrUnexpectedEOF
		}
		typ := binary.BigEndian.Uint16(ch[pos : pos+2])
		dataLen := int(binary.BigEndian.Uint16(ch[pos+2 : pos+4]))
		pos += 4
		if pos+dataLen > end {
			return nil, false, io.ErrUnexpectedEOF
		}
		if typ == wantType {
			return ch[pos : pos+dataLen], true, nil
		}
		pos += dataLen
	}
	return nil, false, nil
}

func TestReqrioHeadersJSONFiltersWireHeaders(t *testing.T) {
	att := Attempt{
		HeadersOrdered: []HeaderPair{
			{Name: "Host", Value: "h5.moutai519.com.cn"},
			{Name: "Connection", Value: "keep-alive"},
			{Name: "Content-Length", Value: "2"},
			{Name: "User-Agent", Value: "Mozilla/5.0"},
			{Name: "Content-Type", Value: "application/json;charset=UTF-8"},
			{Name: "MT-K", Value: "k-test"},
			{Name: "Cookie", Value: "MT-Device-ID-Wap=dev-123; MT-Token-Wap=tok"},
		},
	}
	raw, order, headers, err := reqrioHeadersJSON(att)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.ToLower(raw), "host") ||
		strings.Contains(strings.ToLower(raw), "content-length") {
		t.Fatalf("wire-level headers leaked into reqrio header json: %s", raw)
	}
	wantOrder := []string{"connection", "user-agent", "content-type", "mt-k", "cookie"}
	if len(order) != len(wantOrder) {
		t.Fatalf("order len=%d want=%d order=%v", len(order), len(wantOrder), order)
	}
	for i := range wantOrder {
		if order[i] != wantOrder[i] {
			t.Fatalf("order[%d]=%s want=%s all=%v", i, order[i], wantOrder[i], order)
		}
	}
	if headers["Connection"][0] != "keep-alive" || headers["MT-K"][0] != "k-test" || headers["Cookie"][0] != "MT-Device-ID-Wap=dev-123; MT-Token-Wap=tok" {
		t.Fatalf("business headers changed: %#v", headers)
	}
}
