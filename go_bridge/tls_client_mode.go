package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"io"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	ja3client "github.com/smallerqiu/ja3-client"
	jhttp "github.com/smallerqiu/ja3-client/http"
	"github.com/smallerqiu/ja3-client/ja3"
)

const tlsClientRotationName = "tls_client_round_robin_14"

type tlsClientProfileEntry struct {
	Name      string
	JA3NHash  string
	UserAgent string
}

var tlsClientRotationCounter atomic.Uint64

type tlsClientSession struct {
	mu       sync.Mutex
	key      string
	proxyURL string
	entry    tlsClientProfileEntry
	client   ja3client.HttpClient
	created  time.Time
	lastUsed time.Time
}

var (
	tlsClientSessionMu sync.Mutex
	tlsClientSessions  = map[string]*tlsClientSession{}

	tlsClientInsecureSkipVerifyForTest atomic.Bool
)

var tlsClientRotationPool = []tlsClientProfileEntry{
	{Name: "chrome_131", JA3NHash: "3118d34415923e98f69776f47a249f38"},
	{Name: "chrome_129", JA3NHash: "0a69d5a91bf678c328c623e3f803c68f"},
	{Name: "edge_138", JA3NHash: "4cc9d9021e1f6b341f186aa7fa099388"},
	{Name: "firefox_135", JA3NHash: "92ee0eaa115e93f265dfbc1e869601d6"},
	{Name: "safari_18_5", JA3NHash: "6f8d7b13544c93cf73c710a086ae43f7"},
	{Name: "chrome_131_android", JA3NHash: "b9c0aeeee9ccf8a780cb26f4d4b2b5be"},
	{Name: "firefox_132", JA3NHash: "9286584437a8f5a67b0f773c233181f1"},
	{Name: "chrome_117", JA3NHash: "9baf943424eea7cd951b8d90ce495217"},
	{Name: "safari_ios_26_0", JA3NHash: "0d51de88217d5663d2b1b74dbda05158"},
	{Name: "safari_15_3", JA3NHash: "7a8c29eb5a1b7ad9b6869b2f5436b2ac"},
	{Name: "firefox_123", JA3NHash: "87faec9d8efddf2add84582a469ef465"},
	{Name: "safari_26_0", JA3NHash: "ac9ce4feda13b93f7fa3f698571b6ed5"},
	{Name: "firefox_120", JA3NHash: "c740765f09c9636022fb5915550edf22"},
	{Name: "firefox_117", JA3NHash: "fe281b34231900ddc3a502674b54beb5"},
}

func shouldUseCustomTLSTransport(att Attempt) bool {
	if strings.TrimSpace(att.URL) == "" {
		return false
	}
	u, err := url.Parse(strings.TrimSpace(att.URL))
	return err == nil && (strings.EqualFold(u.Scheme, "https") || strings.EqualFold(u.Scheme, "http"))
}

func shouldUseTLSClient(att Attempt) bool {
	return shouldUseCustomTLSTransport(att)
}

func tlsClientProfileName() string {
	return tlsClientRotationName
}

func selectedTLSClientProfile() (ja3.ClientProfile, string, error) {
	entry := tlsClientRotationPool[0]
	profile, err := ja3.BuildClientHelloSpec(entry.Name, nil)
	return profile, entry.Name, err
}

func tlsClientProfileEntryForRequest() tlsClientProfileEntry {
	idx := tlsClientRotationCounter.Add(1) - 1
	entry := tlsClientRotationPool[int(idx%uint64(len(tlsClientRotationPool)))]
	entry.UserAgent = tlsClientProfileUserAgent(entry.Name)
	return entry
}

func tlsClientProfileEntryForPreview() tlsClientProfileEntry {
	idx := tlsClientRotationCounter.Load()
	entry := tlsClientRotationPool[int(idx%uint64(len(tlsClientRotationPool)))]
	entry.UserAgent = tlsClientProfileUserAgent(entry.Name)
	return entry
}

func tlsClientProfileEntryByName(name string) (tlsClientProfileEntry, bool) {
	name = strings.TrimSpace(name)
	if name == "" {
		return tlsClientProfileEntry{}, false
	}
	for _, entry := range tlsClientRotationPool {
		if entry.Name == name {
			entry.UserAgent = tlsClientProfileUserAgent(entry.Name)
			return entry, true
		}
	}
	return tlsClientProfileEntry{}, false
}

func tlsClientProfileUserAgent(profileName string) string {
	if data, ok := ja3.MappedTLSClients[profileName]; ok {
		return strings.TrimSpace(data.UserAgent)
	}
	return ""
}

func tlsClientProfilePoolSummary() []map[string]string {
	out := make([]map[string]string, 0, len(tlsClientRotationPool))
	for i, entry := range tlsClientRotationPool {
		out = append(out, map[string]string{
			"index":     strconv.Itoa(i),
			"profile":   entry.Name,
			"ja3nHash":  entry.JA3NHash,
			"userAgent": tlsClientProfileUserAgent(entry.Name),
		})
	}
	return out
}

func tlsClientActiveSessionCount() int {
	tlsClientSessionMu.Lock()
	defer tlsClientSessionMu.Unlock()
	return len(tlsClientSessions)
}

func resetTLSClientSessionsForTest() {
	tlsClientSessionMu.Lock()
	sessions := make([]*tlsClientSession, 0, len(tlsClientSessions))
	for _, sess := range tlsClientSessions {
		sessions = append(sessions, sess)
	}
	tlsClientSessions = map[string]*tlsClientSession{}
	tlsClientSessionMu.Unlock()
	for _, sess := range sessions {
		sess.close()
	}
}

func getTLSClientSession(att Attempt, timeoutMs int) (*tlsClientSession, error) {
	key := transportSessionKey("tls-client", att)
	now := time.Now()
	tlsClientSessionMu.Lock()
	if sess := tlsClientSessions[key]; sess != nil {
		sess.lastUsed = now
		tlsClientSessionMu.Unlock()
		transportTrace("USE engine=tls-client account=%s target=%s lane=%d proxy=%s profile=%s", transportTraceAccount(att), transportTargetAuthority(att), transportLaneID(att), proxyForTrace(sess.proxyURL), sess.entry.Name)
		return sess, nil
	}

	entry, ok := tlsClientProfileEntryByName(att.TLSProfile)
	if !ok {
		entry = tlsClientProfileEntryForRequest()
		att.TLSProfile = entry.Name
		key = transportSessionKey("tls-client", att)
	}
	profile, err := ja3.BuildClientHelloSpec(entry.Name, nil)
	if err != nil {
		tlsClientSessionMu.Unlock()
		return nil, err
	}
	client, err := buildTLSClient(att.ProxyURL, timeoutMs, profile)
	if err != nil {
		tlsClientSessionMu.Unlock()
		return nil, err
	}
	sess := &tlsClientSession{
		key:      key,
		proxyURL: strings.TrimSpace(att.ProxyURL),
		entry:    entry,
		client:   client,
		created:  now,
		lastUsed: now,
	}
	tlsClientSessions[key] = sess
	tlsClientSessionMu.Unlock()
	transportTrace("NEW engine=tls-client account=%s target=%s lane=%d proxy=%s profile=%s", transportTraceAccount(att), transportTargetAuthority(att), transportLaneID(att), proxyForTrace(sess.proxyURL), sess.entry.Name)
	return sess, nil
}

func invalidateTLSClientSession(key string, sess *tlsClientSession) {
	invalidateTLSClientSessionReason(key, sess, "manual")
}

func invalidateTLSClientSessionReason(key string, sess *tlsClientSession, reason string) {
	tlsClientSessionMu.Lock()
	if cur := tlsClientSessions[key]; cur == sess {
		delete(tlsClientSessions, key)
	}
	tlsClientSessionMu.Unlock()
	if sess != nil {
		transportTrace("CLOSE engine=tls-client reason=%s proxy=%s profile=%s", reason, proxyForTrace(sess.proxyURL), sess.entry.Name)
		sess.close()
	}
}

func (s *tlsClientSession) close() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.client != nil {
		s.client.CloseIdleConnections()
		s.client = nil
	}
}

func buildTLSClient(proxyURL string, timeoutMs int, profile ja3.ClientProfile) (ja3client.HttpClient, error) {
	timeoutMs = clamp(timeoutMs, 1000, 600000)
	idle := 5 * time.Minute
	withoutHTTP3 := false
	opts := []ja3client.HttpClientOption{
		ja3client.WithTimeoutMilliseconds(timeoutMs),
		ja3client.WithClientProfile(profile),
		ja3client.WithHttp3(&withoutHTTP3),
		ja3client.WithNotFollowRedirects(),
		ja3client.WithTransportOptions(&ja3client.TransportOptions{
			DisableKeepAlives:      false,
			DisableCompression:     true,
			IdleConnTimeout:        &idle,
			MaxIdleConns:           64,
			MaxIdleConnsPerHost:    1,
			MaxConnsPerHost:        1,
			MaxResponseHeaderBytes: 262144,
		}),
	}
	if tlsClientInsecureSkipVerifyForTest.Load() {
		opts = append(opts, ja3client.WithInsecureSkipVerify())
	}
	if strings.TrimSpace(proxyURL) != "" {
		opts = append(opts, ja3client.WithProxyUrl(strings.TrimSpace(proxyURL)))
	}
	return ja3client.NewHttpClient(ja3client.NewNoopLogger(), opts...)
}

func attemptBodyBytes(att Attempt) ([]byte, int64, error) {
	bodyReader, bodyLen, err := attemptBodyReader(att)
	if err != nil {
		return nil, 0, err
	}
	if bodyReader == nil {
		return nil, bodyLen, nil
	}
	body, err := io.ReadAll(bodyReader)
	if err != nil {
		return nil, 0, err
	}
	return body, int64(len(body)), nil
}

func ja3SetHeaderExact(headers jhttp.Header, key, value string) {
	for existing := range headers {
		if existing != key && strings.EqualFold(existing, key) {
			delete(headers, existing)
		}
	}
	headers[key] = []string{value}
}

func tlsClientWireUserAgent(att Attempt, entry tlsClientProfileEntry) (string, string) {
	attemptUA := strings.TrimSpace(firstAttemptHeader(att, "User-Agent"))
	if tlsClientShouldPreserveAttemptUserAgent(attemptUA) {
		return attemptUA, "attempt_business"
	}
	profileUA := strings.TrimSpace(entry.UserAgent)
	if profileUA != "" {
		return profileUA, "profile"
	}
	if attemptUA != "" {
		return attemptUA, "attempt"
	}
	return "", ""
}

func tlsClientShouldPreserveAttemptUserAgent(ua string) bool {
	lower := strings.ToLower(strings.TrimSpace(ua))
	return strings.Contains(lower, "bs-dvid/") || strings.Contains(lower, "moutaiapp/")
}

func applyTLSClientHeaderPair(req *jhttp.Request, order *[]string, name, value string) {
	k := strings.TrimSpace(name)
	if k == "" {
		return
	}
	lk := strings.ToLower(k)
	switch lk {
	case "host":
		if strings.TrimSpace(value) != "" {
			req.Host = strings.TrimSpace(value)
		}
	case "content-length":
		// ja3-client derives the wire Content-Length from req.ContentLength.
	case "connection", "proxy-connection", "keep-alive", "transfer-encoding", "upgrade":
		// HTTP/2 forbids connection-specific headers; do not leak them in JA3 client mode.
	default:
		ja3SetHeaderExact(req.Header, k, value)
	}
	if order != nil && lk != "content-length" && lk != "connection" && lk != "proxy-connection" && lk != "keep-alive" && lk != "transfer-encoding" && lk != "upgrade" {
		*order = append(*order, strings.ToLower(k))
	}
}

func applyTLSClientAttemptHeaders(req *jhttp.Request, att Attempt, entry tlsClientProfileEntry) {
	req.Header = jhttp.Header{}
	var order []string
	if len(att.HeadersOrdered) > 0 {
		for _, h := range att.HeadersOrdered {
			applyTLSClientHeaderPair(req, &order, h.Name, h.Value)
		}
	} else {
		keys := make([]string, 0, len(att.Headers))
		for k := range att.Headers {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			applyTLSClientHeaderPair(req, &order, k, att.Headers[k])
		}
	}
	if strings.TrimSpace(att.Host) != "" {
		req.Host = strings.TrimSpace(att.Host)
	}
	if req.Host == "" && req.URL != nil && req.URL.Host != "" {
		req.Host = req.URL.Host
	}
	if ua, _ := tlsClientWireUserAgent(att, entry); ua != "" {
		ja3SetHeaderExact(req.Header, "User-Agent", ua)
		hasUA := false
		for _, item := range order {
			if item == "user-agent" {
				hasUA = true
				break
			}
		}
		if !hasUA {
			order = append([]string{"user-agent"}, order...)
		}
	}
	if len(order) > 0 {
		req.Header[jhttp.HeaderOrderKey] = dedupeLowerOrder(order)
	}
}

func dedupeLowerOrder(order []string) []string {
	seen := make(map[string]bool, len(order))
	out := make([]string, 0, len(order))
	for _, item := range order {
		k := strings.ToLower(strings.TrimSpace(item))
		if k == "" || seen[k] {
			continue
		}
		seen[k] = true
		out = append(out, k)
	}
	return out
}

func newTLSClientRequest(ctx context.Context, att Attempt, body []byte, bodyLen int64, entry tlsClientProfileEntry) (*jhttp.Request, error) {
	method := strings.ToUpper(strings.TrimSpace(att.Method))
	if method == "" {
		method = "POST"
	}
	var bodyReader io.Reader
	if body != nil {
		bodyReader = bytes.NewReader(body)
	}
	req, err := jhttp.NewRequest(method, att.URL, bodyReader)
	if err != nil {
		return nil, err
	}
	if ctx != nil {
		req = req.WithContext(ctx)
	}
	applyTLSClientAttemptHeaders(req, att, entry)
	if att.ContentLength > 0 {
		req.ContentLength = att.ContentLength
	} else if bodyLen > 0 {
		req.ContentLength = bodyLen
	}
	if body != nil {
		b := append([]byte(nil), body...)
		req.GetBody = func() (io.ReadCloser, error) {
			return io.NopCloser(bytes.NewReader(b)), nil
		}
	}
	req.Close = false
	return req, nil
}

func doRequestTLSClientCtx(ctx context.Context, att Attempt) (statusCode int, bodyBytes []byte, err error) {
	statusCode, bodyBytes, _, err = doRequestTLSClientCtxWithProxy(ctx, att)
	return statusCode, bodyBytes, err
}

func doRequestTLSClientCtxWithProxy(ctx context.Context, att Attempt) (statusCode int, bodyBytes []byte, actualProxyURL string, err error) {
	body, bodyLen, err := attemptBodyBytes(att)
	if err != nil {
		return 0, nil, strings.TrimSpace(att.ProxyURL), err
	}
	if ctx == nil {
		ctx = context.Background()
	}
	tms := att.TimeoutMs
	if tms < 0 {
		tms = 0
	}
	if tms <= 0 {
		tms = 30000
	}
	sess, err := getTLSClientSession(att, tms)
	if err != nil {
		return 0, nil, strings.TrimSpace(att.ProxyURL), err
	}
	actualProxyURL = sess.proxyURL
	att.ProxyURL = actualProxyURL
	req, err := newTLSClientRequest(ctx, att, body, bodyLen, sess.entry)
	if err != nil {
		return 0, nil, actualProxyURL, err
	}
	release, err := acquireRushOutboundSlotCtx(ctx)
	if err != nil {
		return 0, nil, actualProxyURL, err
	}
	defer release()

	sess.mu.Lock()
	client := sess.client
	if client == nil {
		sess.mu.Unlock()
		return 0, nil, actualProxyURL, context.Canceled
	}
	resp, err := client.Do(req)
	if err != nil {
		sess.mu.Unlock()
		invalidateTLSClientSessionReason(sess.key, sess, "request_error")
		return 0, nil, actualProxyURL, err
	}
	respBody, err := io.ReadAll(resp.Body)
	closeErr := resp.Body.Close()
	statusCode = resp.StatusCode
	sess.mu.Unlock()
	if err != nil {
		invalidateTLSClientSessionReason(sess.key, sess, "read_error")
		return statusCode, respBody, actualProxyURL, err
	}
	if closeErr != nil {
		invalidateTLSClientSessionReason(sess.key, sess, "body_close_error")
		return statusCode, respBody, actualProxyURL, closeErr
	}
	if reason, ok := http429SessionCloseReason(statusCode, respBody); ok {
		invalidateTLSClientSessionReason(sess.key, sess, reason)
	}
	return statusCode, respBody, actualProxyURL, nil
}

func tlsClientAttemptPreview(att Attempt) (map[string]interface{}, error) {
	if shouldUseReqrioTransport() {
		return reqrioAttemptPreview(att)
	}
	body, bodyLen, err := attemptBodyBytes(att)
	if err != nil {
		return nil, err
	}
	entry := tlsClientProfileEntryForPreview()
	req, err := newTLSClientRequest(context.Background(), att, body, bodyLen, entry)
	if err != nil {
		return nil, err
	}
	headers := make(map[string][]string, len(req.Header))
	for k, v := range req.Header {
		if k == jhttp.HeaderOrderKey || k == jhttp.PHeaderOrderKey {
			continue
		}
		headers[k] = append([]string(nil), v...)
	}
	headerOrder := append([]string(nil), req.Header[jhttp.HeaderOrderKey]...)
	wireUA, uaSource := tlsClientWireUserAgent(att, entry)
	return map[string]interface{}{
		"mode":             "ja3_client",
		"profile":          entry.Name,
		"rotation":         tlsClientProfileName(),
		"ja3nHash":         entry.JA3NHash,
		"profileUserAgent": entry.UserAgent,
		"userAgent":        wireUA,
		"userAgentSource":  uaSource,
		"method":           req.Method,
		"url":              req.URL.String(),
		"host":             req.Host,
		"contentLength":    req.ContentLength,
		"bodyLen":          bodyLen,
		"headerOrder":      headerOrder,
		"headers":          headers,
		"bodySha256":       sha256Hex(body),
	}, nil
}

func tlsClientProfilesSummary() map[string]interface{} {
	keys := make([]string, 0, len(ja3.MappedTLSClients))
	for k := range ja3.MappedTLSClients {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	summary := map[string]interface{}{
		"ok":              true,
		"mode":            tlsTransportModeName(),
		"selected":        tlsClientProfileName(),
		"defaultSelected": tlsClientRotationName,
		"fixed":           false,
		"rotationCount":   len(tlsClientRotationPool),
		"nextProfile":     tlsClientProfileEntryForPreview().Name,
		"rotationPool":    tlsClientProfilePoolSummary(),
		"connectionReuse": true,
		"activeSessions":  tlsClientActiveSessionCount(),
		"client":          "github.com/smallerqiu/ja3-client",
		"count":           len(keys),
		"profiles":        keys,
	}
	if shouldUseReqrioTransport() {
		summary["selected"] = reqrioRandomProfileName
		summary["fixed"] = false
		summary["reqrio"] = reqrioSummary()
	}
	return summary
}

func sha256Hex(b []byte) string {
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
