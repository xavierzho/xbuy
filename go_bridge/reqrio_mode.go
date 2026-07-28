package main

import (
	"bytes"
	"context"
	"crypto/ecdh"
	crand "crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

const reqrioRandomProfileName = "reqrio_mt_random"
const reqrioWireALPN = "http/1.1"

type reqrioRequest struct {
	Method        string
	URL           string
	HeadersJSON   string
	Body          []byte
	ContentType   string
	ProxyURL      string
	TimeoutMs     int
	SNI           string
	CustomTLSJSON string
	ClientHello   []byte
	Token         string
}

type reqrioFingerprintPlan struct {
	ProfileID       string
	RandomID        string
	AccountKey      string
	AccountSource   string
	AccountHash     string
	SNI             string
	GreaseCipher    int
	GreaseGroup     int
	GreaseExt1      int
	GreaseVersion   int
	GreaseExt2      int
	Padding         int
	CustomTLSJSON   string
	CustomTLSSHA256 string
	ClientHello     []byte
	ClientHelloSHA  string
}

var reqrioTokenRE = regexp.MustCompile(`token\s*=\s*["']([^"']+)["']`)

func tlsTransportModeName() string {
	raw := strings.ToLower(strings.TrimSpace(os.Getenv("MT_TLS_ENGINE")))
	switch raw {
	case "reqrio", "reqrio_custom", "dynamic", "dynamic_tls":
		return "reqrio"
	default:
		return "ja3_client"
	}
}

func shouldUseReqrioTransport() bool {
	return tlsTransportModeName() == "reqrio"
}

func withAttemptAccountKey(att Attempt, accountKey string) Attempt {
	if strings.TrimSpace(att.AccountKey) == "" {
		att.AccountKey = strings.TrimSpace(accountKey)
	}
	return att
}

func doRequestReqrioCtx(ctx context.Context, att Attempt) (statusCode int, bodyBytes []byte, err error) {
	statusCode, bodyBytes, _, err = doRequestReqrioCtxWithProxy(ctx, att)
	return statusCode, bodyBytes, err
}

func doRequestReqrioCtxWithProxy(ctx context.Context, att Attempt) (statusCode int, bodyBytes []byte, actualProxyURL string, err error) {
	body, _, err := attemptBodyBytes(att)
	if err != nil {
		return 0, nil, strings.TrimSpace(att.ProxyURL), err
	}
	headersJSON, _, _, err := reqrioHeadersJSON(att)
	if err != nil {
		return 0, nil, strings.TrimSpace(att.ProxyURL), err
	}
	if ctx == nil {
		ctx = context.Background()
	}
	select {
	case <-ctx.Done():
		return 0, nil, strings.TrimSpace(att.ProxyURL), ctx.Err()
	default:
	}
	release, err := acquireRushOutboundSlotCtx(ctx)
	if err != nil {
		return 0, nil, strings.TrimSpace(att.ProxyURL), err
	}
	defer release()
	select {
	case <-ctx.Done():
		return 0, nil, strings.TrimSpace(att.ProxyURL), ctx.Err()
	default:
	}
	tms := att.TimeoutMs
	if tms <= 0 {
		tms = 30000
	}
	return reqrioSendSession(ctx, att, reqrioRequest{
		Method:      normalizeReqrioMethod(att.Method),
		URL:         strings.TrimSpace(att.URL),
		HeadersJSON: headersJSON,
		Body:        body,
		ContentType: reqrioContentType(att),
		ProxyURL:    strings.TrimSpace(att.ProxyURL),
		TimeoutMs:   tms,
		SNI:         reqrioSNIForAttempt(att),
	})
}

func reqrioAttemptPreview(att Attempt) (map[string]interface{}, error) {
	body, bodyLen, err := attemptBodyBytes(att)
	if err != nil {
		return nil, err
	}
	plan, err := reqrioFingerprintPlanForAttempt(att)
	if err != nil {
		return nil, err
	}
	headersJSON, headerOrder, headers, err := reqrioHeadersJSON(att)
	if err != nil {
		return nil, err
	}
	method := normalizeReqrioMethod(att.Method)
	contentLength := att.ContentLength
	if contentLength <= 0 && bodyLen > 0 {
		contentLength = bodyLen
	}
	return map[string]interface{}{
		"mode":                "reqrio",
		"profile":             reqrioRandomProfileName,
		"engine":              "reqrio",
		"stablePerAccount":    false,
		"randomPerRequest":    false,
		"randomPerConnection": true,
		"method":              method,
		"url":                 strings.TrimSpace(att.URL),
		"host":                reqrioHostForAttempt(att),
		"sni":                 plan.SNI,
		"alpn":                reqrioWireALPN,
		"contentLength":       contentLength,
		"bodyLen":             bodyLen,
		"headerOrder":         headerOrder,
		"headers":             headers,
		"headersJSON":         headersJSON,
		"bodySha256":          sha256Hex(body),
		"accountKeySource":    plan.AccountSource,
		"accountKeyHash":      plan.AccountHash,
		"connectionReuse":     true,
		"fingerprint": map[string]interface{}{
			"profileId":       plan.ProfileID,
			"randomId":        plan.RandomID,
			"customTLSSha256": plan.CustomTLSSHA256,
			"clientHelloSha":  plan.ClientHelloSHA,
			"recordVersion":   "0301",
			"legacyVersion":   "0303",
			"greaseCipher":    hex16(plan.GreaseCipher),
			"greaseGroup":     hex16(plan.GreaseGroup),
			"greaseExt1":      hex16(plan.GreaseExt1),
			"greaseVersion":   hex16(plan.GreaseVersion),
			"greaseExt2":      hex16(plan.GreaseExt2),
			"padding":         plan.Padding,
		},
	}, nil
}

func reqrioFingerprintPlanForAttempt(att Attempt) (reqrioFingerprintPlan, error) {
	entropy, err := reqrioFingerprintEntropy()
	if err != nil {
		return reqrioFingerprintPlan{}, err
	}
	return reqrioFingerprintPlanFromEntropy(att, entropy)
}

func reqrioFingerprintEntropy() ([]byte, error) {
	b := make([]byte, 32)
	if _, err := crand.Read(b); err != nil {
		return nil, fmt.Errorf("reqrio random custom_tls entropy: %w", err)
	}
	return b, nil
}

func reqrioFingerprintPlanFromEntropy(att Attempt, entropy []byte) (reqrioFingerprintPlan, error) {
	sni := reqrioSNIForAttempt(att)
	if sni == "" {
		return reqrioFingerprintPlan{}, errors.New("reqrio: empty SNI")
	}
	if len(entropy) == 0 {
		return reqrioFingerprintPlan{}, errors.New("reqrio: empty custom_tls entropy")
	}
	accountKey, source := reqrioAccountKey(att)
	accountSum := sha256.Sum256([]byte(accountKey))
	seedInput := make([]byte, 0, len(entropy)+len(sni)+16)
	seedInput = append(seedInput, entropy...)
	seedInput = append(seedInput, 0)
	seedInput = append(seedInput, sni...)
	seedInput = append(seedInput, 0)
	seedInput = append(seedInput, []byte(strings.TrimSpace(att.URL))...)
	sum := sha256.Sum256(seedInput)
	grease := reqrioGreaseValues()
	ext1 := grease[int(sum[2])%len(grease)]
	ext2 := grease[int(sum[4])%len(grease)]
	plan := reqrioFingerprintPlan{
		AccountKey:    accountKey,
		AccountSource: source,
		AccountHash:   hex.EncodeToString(accountSum[:8]),
		RandomID:      hex.EncodeToString(sum[:8]),
		SNI:           sni,
		GreaseCipher:  grease[int(sum[0])%len(grease)],
		GreaseGroup:   grease[int(sum[1])%len(grease)],
		GreaseExt1:    ext1,
		GreaseVersion: grease[int(sum[3])%len(grease)],
		GreaseExt2:    ext2,
		Padding:       173 + len(sni),
	}
	plan.ProfileID = fmt.Sprintf("reqrio_mt_rand_%s_%s_%s_%s_%s_%s",
		plan.RandomID,
		hex16(plan.GreaseCipher),
		hex16(plan.GreaseGroup),
		hex16(plan.GreaseExt1),
		hex16(plan.GreaseVersion),
		hex16(plan.GreaseExt2),
	)
	plan.CustomTLSJSON = reqrioCustomTLSJSON(plan)
	plan.CustomTLSSHA256 = sha256Hex([]byte(plan.CustomTLSJSON))
	clientHello, err := reqrioClientHelloBytes(plan, entropy)
	if err != nil {
		return reqrioFingerprintPlan{}, err
	}
	plan.ClientHello = clientHello
	plan.ClientHelloSHA = sha256Hex(clientHello)
	return plan, nil
}

func reqrioGreaseValues() []int {
	return []int{
		0x0a0a, 0x1a1a, 0x2a2a, 0x3a3a, 0x4a4a,
		0x5a5a, 0x6a6a, 0x7a7a, 0x8a8a, 0x9a9a,
		0xaaaa, 0xbaba, 0xcaca, 0xeaea, 0xfafa,
	}
}

func reqrioCustomTLSJSON(plan reqrioFingerprintPlan) string {
	padding := plan.Padding
	if padding < 0 {
		padding = 0
	}
	var b strings.Builder
	b.WriteString(`{"suites":[`)
	writeIntList(&b, []int{
		plan.GreaseCipher,
		0x1301, 0x1302, 0x1303,
		0xc02b, 0xc02f, 0xc02c, 0xc030,
		0xcca9, 0xcca8,
		0xc013, 0xc014,
		0x009c, 0x009d, 0x002f, 0x0035,
	})
	b.WriteString(`],"extensions":{`)
	firstExt := true
	if plan.GreaseExt1 == plan.GreaseExt2 {
		writeJSONField(&b, plan.GreaseExt1, "[0]", firstExt)
	} else {
		writeJSONField(&b, plan.GreaseExt1, "null", firstExt)
	}
	firstExt = false
	writeJSONField(&b, 0x0000, "null", firstExt)
	writeJSONField(&b, 0x0017, "null", false)
	writeJSONField(&b, 0xff01, "null", false)
	writeJSONField(&b, 0x000a, fmt.Sprintf("[%d,%d,%d,%d]", plan.GreaseGroup, 0x001d, 0x0017, 0x0018), false)
	writeJSONField(&b, 0x000b, "[0]", false)
	writeJSONField(&b, 0x0023, "null", false)
	writeJSONField(&b, 0x0010, `["http/1.1"]`, false)
	writeJSONField(&b, 0x0005, "null", false)
	writeJSONField(&b, 0x000d, "[1027,2052,1025,1283,2053,1281,2054,1537]", false)
	writeJSONField(&b, 0x0012, "null", false)
	writeJSONField(&b, 0x0033, fmt.Sprintf("[%d,%d]", plan.GreaseGroup, 0x001d), false)
	writeJSONField(&b, 0x002d, "1", false)
	writeJSONField(&b, 0x002b, fmt.Sprintf("[%d,%d,%d,%d,%d]", plan.GreaseVersion, 0x0304, 0x0303, 0x0302, 0x0301), false)
	writeJSONField(&b, 0x001b, "[2]", false)
	if plan.GreaseExt1 != plan.GreaseExt2 {
		writeJSONField(&b, plan.GreaseExt2, "[0]", false)
	}
	writeJSONField(&b, 0x0015, strconv.Itoa(padding), false)
	b.WriteString(`},"settings":{"HeaderTableSize":65536,"EnablePush":0,"InitialWindowSize":6291456,"MaxHeaderListSize":242144},"window_size":2147418112,"weight":147,"priority":true}`)
	return b.String()
}

func reqrioClientHelloBytes(plan reqrioFingerprintPlan, entropy []byte) ([]byte, error) {
	x25519Share, err := reqrioX25519Share(entropy)
	if err != nil {
		return nil, err
	}
	clientRandom := reqrioDeriveBytes(entropy, "client-random", 32)
	sessionID := reqrioDeriveBytes(entropy, "session-id", 32)

	body := &bytes.Buffer{}
	reqrioWriteU16(body, 0x0303)
	body.Write(clientRandom)
	body.WriteByte(byte(len(sessionID)))
	body.Write(sessionID)

	suites := []int{
		plan.GreaseCipher,
		0x1301, 0x1302, 0x1303,
		0xc02b, 0xc02f, 0xc02c, 0xc030,
		0xcca9, 0xcca8,
		0xc013, 0xc014,
		0x009c, 0x009d, 0x002f, 0x0035,
	}
	reqrioWriteU16(body, len(suites)*2)
	for _, suite := range suites {
		reqrioWriteU16(body, suite)
	}
	body.WriteByte(1)
	body.WriteByte(0)

	exts := &bytes.Buffer{}
	if plan.GreaseExt1 == plan.GreaseExt2 {
		reqrioWriteTLSExt(exts, plan.GreaseExt1, []byte{0})
	} else {
		reqrioWriteTLSExt(exts, plan.GreaseExt1, nil)
	}
	reqrioWriteTLSExt(exts, 0x0000, reqrioEncodeSNI(plan.SNI))
	reqrioWriteTLSExt(exts, 0x0017, nil)
	reqrioWriteTLSExt(exts, 0xff01, []byte{0})
	reqrioWriteTLSExt(exts, 0x000a, reqrioEncodeU16Vector([]int{plan.GreaseGroup, 0x001d, 0x0017, 0x0018}))
	reqrioWriteTLSExt(exts, 0x000b, []byte{1, 0})
	reqrioWriteTLSExt(exts, 0x0023, nil)
	reqrioWriteTLSExt(exts, 0x0010, reqrioEncodeALPNList([]string{reqrioWireALPN}))
	reqrioWriteTLSExt(exts, 0x0005, []byte{1, 0, 0, 0, 0})
	reqrioWriteTLSExt(exts, 0x000d, reqrioEncodeU16Vector([]int{1027, 2052, 1025, 1283, 2053, 1281, 2054, 1537}))
	reqrioWriteTLSExt(exts, 0x0012, nil)
	reqrioWriteTLSExt(exts, 0x0033, reqrioEncodeKeyShare(plan.GreaseGroup, x25519Share))
	reqrioWriteTLSExt(exts, 0x002d, []byte{1, 1})
	reqrioWriteTLSExt(exts, 0x002b, reqrioEncodeSupportedVersions([]int{plan.GreaseVersion, 0x0304, 0x0303, 0x0302, 0x0301}))
	reqrioWriteTLSExt(exts, 0x001b, []byte{2, 0, 2})
	if plan.GreaseExt1 != plan.GreaseExt2 {
		reqrioWriteTLSExt(exts, plan.GreaseExt2, []byte{0})
	}
	if plan.Padding < 0 {
		plan.Padding = 0
	}
	reqrioWriteTLSExt(exts, 0x0015, bytes.Repeat([]byte{0}, plan.Padding))

	reqrioWriteU16(body, exts.Len())
	body.Write(exts.Bytes())

	handshake := &bytes.Buffer{}
	handshake.WriteByte(0x01)
	reqrioWriteU24(handshake, body.Len())
	handshake.Write(body.Bytes())

	record := &bytes.Buffer{}
	record.WriteByte(0x16)
	reqrioWriteU16(record, 0x0301)
	reqrioWriteU16(record, handshake.Len())
	record.Write(handshake.Bytes())
	return record.Bytes(), nil
}

func reqrioDeriveBytes(entropy []byte, label string, n int) []byte {
	if n <= 0 {
		return nil
	}
	out := make([]byte, 0, n)
	for counter := 0; len(out) < n; counter++ {
		h := sha256.New()
		h.Write(entropy)
		h.Write([]byte(label))
		h.Write([]byte{byte(counter)})
		out = append(out, h.Sum(nil)...)
	}
	return out[:n]
}

func reqrioX25519Share(entropy []byte) ([]byte, error) {
	seed := reqrioDeriveBytes(entropy, "x25519-private", 32)
	priv, err := ecdh.X25519().NewPrivateKey(seed)
	if err != nil {
		return nil, fmt.Errorf("reqrio x25519 private key: %w", err)
	}
	return priv.PublicKey().Bytes(), nil
}

func reqrioEncodeSNI(sni string) []byte {
	host := []byte(sni)
	name := &bytes.Buffer{}
	name.WriteByte(0)
	reqrioWriteU16(name, len(host))
	name.Write(host)
	list := name.Bytes()
	out := &bytes.Buffer{}
	reqrioWriteU16(out, len(list))
	out.Write(list)
	return out.Bytes()
}

func reqrioEncodeALPNList(values []string) []byte {
	list := &bytes.Buffer{}
	for _, v := range values {
		list.WriteByte(byte(len(v)))
		list.WriteString(v)
	}
	out := &bytes.Buffer{}
	reqrioWriteU16(out, list.Len())
	out.Write(list.Bytes())
	return out.Bytes()
}

func reqrioEncodeU16Vector(values []int) []byte {
	out := &bytes.Buffer{}
	reqrioWriteU16(out, len(values)*2)
	for _, v := range values {
		reqrioWriteU16(out, v)
	}
	return out.Bytes()
}

func reqrioEncodeSupportedVersions(values []int) []byte {
	out := &bytes.Buffer{}
	out.WriteByte(byte(len(values) * 2))
	for _, v := range values {
		reqrioWriteU16(out, v)
	}
	return out.Bytes()
}

func reqrioEncodeKeyShare(greaseGroup int, x25519Share []byte) []byte {
	list := &bytes.Buffer{}
	reqrioWriteU16(list, greaseGroup)
	reqrioWriteU16(list, 1)
	list.WriteByte(0)
	reqrioWriteU16(list, 0x001d)
	reqrioWriteU16(list, len(x25519Share))
	list.Write(x25519Share)
	out := &bytes.Buffer{}
	reqrioWriteU16(out, list.Len())
	out.Write(list.Bytes())
	return out.Bytes()
}

func reqrioWriteTLSExt(b *bytes.Buffer, typ int, data []byte) {
	reqrioWriteU16(b, typ)
	reqrioWriteU16(b, len(data))
	if len(data) > 0 {
		b.Write(data)
	}
}

func reqrioWriteU16(b *bytes.Buffer, v int) {
	b.WriteByte(byte(v >> 8))
	b.WriteByte(byte(v))
}

func reqrioWriteU24(b *bytes.Buffer, v int) {
	b.WriteByte(byte(v >> 16))
	b.WriteByte(byte(v >> 8))
	b.WriteByte(byte(v))
}

func writeIntList(b *strings.Builder, vals []int) {
	for i, v := range vals {
		if i > 0 {
			b.WriteByte(',')
		}
		b.WriteString(strconv.Itoa(v))
	}
}

func writeJSONField(b *strings.Builder, key int, value string, first bool) {
	if !first {
		b.WriteByte(',')
	}
	b.WriteByte('"')
	b.WriteString(strconv.Itoa(key))
	b.WriteString(`":`)
	b.WriteString(value)
}

func reqrioAccountKey(att Attempt) (string, string) {
	if v := strings.TrimSpace(att.AccountKey); v != "" {
		return v, "attempt.accountKey"
	}
	for _, name := range []string{"X-Account-Key", "X-MT-Account-Key"} {
		if v := strings.TrimSpace(firstAttemptHeader(att, name)); v != "" {
			return v, "header." + name
		}
	}
	if v := strings.TrimSpace(firstAttemptHeader(att, "MT-Device-ID")); v != "" {
		return v, "header.MT-Device-ID"
	}
	cookie := firstAttemptHeader(att, "Cookie")
	for _, name := range []string{"MT-Device-ID-Wap", "_bs_device_id", "_d_u", "MT-Token-Wap", "MT-Token"} {
		if v := cookieValue(cookie, name); v != "" {
			return v, "cookie." + name
		}
	}
	ua := firstAttemptHeader(att, "User-Agent")
	fallback := strings.Join([]string{
		reqrioHostForAttempt(att),
		strings.TrimSpace(att.ProxyURL),
		strings.TrimSpace(ua),
	}, "|")
	if strings.Trim(fallback, "| ") == "" {
		fallback = strings.TrimSpace(att.URL)
	}
	return fallback, "fallback.host_proxy_ua"
}

func firstAttemptHeader(att Attempt, name string) string {
	for _, h := range att.HeadersOrdered {
		if strings.EqualFold(strings.TrimSpace(h.Name), name) {
			return h.Value
		}
	}
	for k, v := range att.Headers {
		if strings.EqualFold(strings.TrimSpace(k), name) {
			return v
		}
	}
	return ""
}

func cookieValue(cookieHeader, name string) string {
	name = strings.ToLower(strings.TrimSpace(name))
	for _, part := range strings.Split(cookieHeader, ";") {
		p := strings.TrimSpace(part)
		if p == "" {
			continue
		}
		k, v, ok := strings.Cut(p, "=")
		if !ok {
			continue
		}
		if strings.ToLower(strings.TrimSpace(k)) == name {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

func reqrioHeadersJSON(att Attempt) (string, []string, map[string][]string, error) {
	pairs := reqrioHeaderPairs(att)
	var b bytes.Buffer
	b.WriteByte('{')
	order := make([]string, 0, len(pairs))
	headers := make(map[string][]string, len(pairs))
	for i, p := range pairs {
		if i > 0 {
			b.WriteByte(',')
		}
		k, err := json.Marshal(p.Name)
		if err != nil {
			return "", nil, nil, err
		}
		v, err := json.Marshal(p.Value)
		if err != nil {
			return "", nil, nil, err
		}
		b.Write(k)
		b.WriteByte(':')
		b.Write(v)
		order = append(order, strings.ToLower(strings.TrimSpace(p.Name)))
		headers[p.Name] = []string{p.Value}
	}
	b.WriteByte('}')
	return b.String(), order, headers, nil
}

func reqrioHeaderPairs(att Attempt) []HeaderPair {
	seen := map[string]bool{}
	add := func(out *[]HeaderPair, name, value string) {
		k := strings.TrimSpace(name)
		if k == "" {
			return
		}
		lk := strings.ToLower(k)
		switch lk {
		case "host", "content-length", "proxy-connection", "keep-alive", "transfer-encoding", "upgrade":
			return
		}
		if seen[lk] {
			return
		}
		seen[lk] = true
		*out = append(*out, HeaderPair{Name: k, Value: value})
	}
	out := make([]HeaderPair, 0, len(att.HeadersOrdered)+len(att.Headers))
	if len(att.HeadersOrdered) > 0 {
		for _, h := range att.HeadersOrdered {
			add(&out, h.Name, h.Value)
		}
		return out
	}
	keys := make([]string, 0, len(att.Headers))
	for k := range att.Headers {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		add(&out, k, att.Headers[k])
	}
	return out
}

func reqrioContentType(att Attempt) string {
	if v := strings.TrimSpace(firstAttemptHeader(att, "Content-Type")); v != "" {
		return v
	}
	if firstAttemptHeader(att, "content-type") != "" {
		return strings.TrimSpace(firstAttemptHeader(att, "content-type"))
	}
	return "application/octet-stream"
}

func normalizeReqrioMethod(method string) string {
	method = strings.ToUpper(strings.TrimSpace(method))
	if method == "" {
		return "POST"
	}
	return method
}

func reqrioMethodValue(method string) int {
	switch normalizeReqrioMethod(method) {
	case "GET":
		return 0
	case "POST":
		return 1
	case "PUT":
		return 2
	case "HEAD":
		return 3
	case "DELETE":
		return 4
	case "OPTIONS":
		return 5
	case "TRACE":
		return 6
	case "CONNECT":
		return 7
	case "PATCH":
		return 8
	default:
		return 1
	}
}

func reqrioSNIForAttempt(att Attempt) string {
	if h := reqrioHostNoPort(att.Host); h != "" {
		return h
	}
	return reqrioURLHostname(att.URL)
}

func reqrioHostForAttempt(att Attempt) string {
	if h := strings.TrimSpace(att.Host); h != "" {
		return h
	}
	if h := firstAttemptHeader(att, "Host"); h != "" {
		return strings.TrimSpace(h)
	}
	return reqrioURLHostname(att.URL)
}

func reqrioURLHostname(rawURL string) string {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil || u == nil {
		return ""
	}
	return strings.TrimSpace(u.Hostname())
}

func reqrioHostNoPort(host string) string {
	host = strings.TrimSpace(host)
	if host == "" {
		return ""
	}
	if strings.Contains(host, ":") {
		if h, _, err := net.SplitHostPort(host); err == nil {
			return strings.Trim(h, "[]")
		}
	}
	return strings.Trim(strings.Split(host, ":")[0], "[]")
}

func reqrioTimeoutJSON(timeoutMs int) string {
	timeoutMs = clamp(timeoutMs, 1000, 600000)
	connectMs := clamp(timeoutMs, 1000, 30000)
	return fmt.Sprintf(`{"connect":%d,"read":%d,"write":%d,"handle":%d,"connect_times":3,"handle_times":3}`,
		connectMs, timeoutMs, timeoutMs, timeoutMs)
}

func reqrioToken() (string, error) {
	if v := strings.TrimSpace(os.Getenv("MT_REQRIO_TOKEN")); v != "" {
		return v, nil
	}
	paths := []string{}
	if p := strings.TrimSpace(os.Getenv("MT_REQRIO_TOKEN_FILE")); p != "" {
		paths = append(paths, p)
	}
	if exe, err := os.Executable(); err == nil {
		paths = append(paths, filepath.Join(filepath.Dir(exe), "reqrio_runtime", "token.txt"))
	}
	if cwd, err := os.Getwd(); err == nil {
		paths = append(paths, filepath.Join(cwd, "reqrio_runtime", "token.txt"))
	}
	paths = append(paths, `C:\Users\Administrator\Desktop\TLS动态指纹\mt专用.py`)
	for _, p := range paths {
		if p == "" {
			continue
		}
		data, err := os.ReadFile(p)
		if err != nil {
			continue
		}
		text := strings.TrimSpace(string(data))
		if m := reqrioTokenRE.FindStringSubmatch(text); len(m) == 2 && strings.TrimSpace(m[1]) != "" {
			return strings.TrimSpace(m[1]), nil
		}
		if text != "" && !strings.Contains(text, "\n") && !strings.Contains(text, " ") {
			return text, nil
		}
	}
	return "", errors.New("reqrio token missing: set MT_REQRIO_TOKEN or MT_REQRIO_TOKEN_FILE")
}

func reqrioDLLPath() string {
	if p := strings.TrimSpace(os.Getenv("MT_REQRIO_DLL")); p != "" {
		return p
	}
	candidates := []string{}
	if exe, err := os.Executable(); err == nil {
		candidates = append(candidates, filepath.Join(filepath.Dir(exe), "reqrio_runtime", "reqrio.dll"))
	}
	if cwd, err := os.Getwd(); err == nil {
		candidates = append(candidates, filepath.Join(cwd, "reqrio_runtime", "reqrio.dll"))
	}
	candidates = append(candidates, `C:\Users\Administrator\Desktop\TLS动态指纹\reqrio-py\reqrio\reqrio.dll`)
	for _, p := range candidates {
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	if len(candidates) > 0 {
		return candidates[0]
	}
	return "reqrio.dll"
}

func reqrioSummary() map[string]interface{} {
	_, tokenErr := reqrioToken()
	return map[string]interface{}{
		"profile":             reqrioRandomProfileName,
		"stablePerAccount":    false,
		"randomPerRequest":    false,
		"randomPerConnection": true,
		"connectionReuse":     true,
		"alpn":                reqrioWireALPN,
		"client":              "reqrio.dll via in-process Go syscall",
		"dllPath":             reqrioDLLPath(),
		"dllAvailable":        reqrioDLLAvailable(),
		"tokenConfigured":     tokenErr == nil,
	}
}

func hex16(v int) string {
	return fmt.Sprintf("0x%04x", v)
}
