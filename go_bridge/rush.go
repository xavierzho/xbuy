package main

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"math/rand"
	"net/url"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// ---------- 请求/响应结构 ----------

type Attempt struct {
	Method         string            `json:"method"`
	URL            string            `json:"url"`
	Headers        map[string]string `json:"headers"`
	HeadersOrdered []HeaderPair      `json:"headersOrdered"`
	Body           interface{}       `json:"body"`
	BodyB64        string            `json:"bodyB64"`
	Host           string            `json:"host"`
	ContentLength  int64             `json:"contentLength"`
	ProxyURL       string            `json:"proxyUrl"`
	TimeoutMs      int               `json:"timeoutMs"`
	AccountKey     string            `json:"accountKey"`
	LaneID         int               `json:"laneId"`
	RuntimeProxy   bool              `json:"runtimeProxy,omitempty"`
	TLSProfile     string            `json:"tlsProfile,omitempty"`
}

type HeaderPair struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

type RushLoopRequest struct {
	Attempts             []Attempt `json:"attempts"`
	RushLoopDelayMs      int       `json:"rushLoopDelayMs"`
	RushJitterMsMax      int       `json:"rushJitterMsMax"`
	FireAndForget        bool      `json:"fireAndForget"`
	LaneParallel         bool      `json:"laneParallel,omitempty"`
	RushRounds           int       `json:"rushRounds"`
	RushRoundGapMs       int       `json:"rushRoundGapMs"`
	RushRoundGapJitterMs int       `json:"rushRoundGapJitterMs"`
	RuntimeProxyPool     bool      `json:"runtimeProxyPool,omitempty"`
	ProxyPool            []string  `json:"proxyPool,omitempty"`
	TLSImpersonate       string    `json:"tlsImpersonate"`
	AccountKey           string    `json:"accountKey"`
}

type TraceRow struct {
	Attempt          int    `json:"attempt"`
	HTTPStatus       int    `json:"httpStatus"`
	Code             int    `json:"code"`
	Message          string `json:"message"`
	Ms               int64  `json:"ms"`
	ReceivedAtMs     int64  `json:"receivedAtMs,omitempty"`
	RushRound        int    `json:"rushRound,omitempty"`
	RushRounds       int    `json:"rushRounds,omitempty"`
	LaneID           int    `json:"laneId,omitempty"`
	PriorityRecordID string `json:"priorityRecordId,omitempty"`
	Proxy            string `json:"proxy,omitempty"`
	TLSProfile       string `json:"tlsProfile,omitempty"`
}

type RushLoopResponse struct {
	OK            bool        `json:"ok"`
	RushLoop      bool        `json:"rushLoop"`
	Last          interface{} `json:"last"`
	LastProxyURL  string      `json:"lastProxyUrl"`
	Trace         []TraceRow  `json:"trace"`
	WallMs        int64       `json:"wallMs"`
	FireAndForget bool        `json:"fireAndForget,omitempty"`
	RushRounds    int         `json:"rushRounds,omitempty"`
	Error         string      `json:"error,omitempty"`
}

type RushShotRequest struct {
	Attempt          Attempt  `json:"attempt"`
	AttemptIndex     int      `json:"attemptIndex"`
	RushRound        int      `json:"rushRound,omitempty"`
	RushRounds       int      `json:"rushRounds,omitempty"`
	RuntimeProxyPool bool     `json:"runtimeProxyPool,omitempty"`
	ProxyPool        []string `json:"proxyPool,omitempty"`
	TLSImpersonate   string   `json:"tlsImpersonate"`
	AccountKey       string   `json:"accountKey"`
}

type RushShotResponse struct {
	OK           bool        `json:"ok"`
	RushShot     bool        `json:"rushShot"`
	Row          TraceRow    `json:"row"`
	Last         interface{} `json:"last"`
	LastProxyURL string      `json:"lastProxyUrl"`
	Error        string      `json:"error,omitempty"`
}

type BizResponse struct {
	Code         interface{} `json:"code"`
	Message      string      `json:"message"`
	RushBodyCode interface{} `json:"rushBodyCode,omitempty"`
	Data         interface{} `json:"data,omitempty"`
}

// ---------- 判断逻辑 ----------

var stopRushRe = regexp.MustCompile(`限购|限購|购买已达上限|已达购买上限|不可再购|超出每人|每人仅限|超过限购|已达购买|重复购买|每人每天|请勿重复`)
var stopRushExcludeRe = regexp.MustCompile(`活动信息异常|参数错误|活动未开始|活动已结束`)

func codeStr(v interface{}) string {
	if v == nil {
		return ""
	}
	switch x := v.(type) {
	case float64:
		return fmt.Sprintf("%.0f", x)
	case string:
		return strings.TrimSpace(x)
	default:
		return fmt.Sprintf("%v", x)
	}
}

func toFloat(v interface{}) float64 {
	if v == nil {
		return -1
	}
	switch x := v.(type) {
	case float64:
		return x
	case int:
		return float64(x)
	default:
		return -1
	}
}

func isStopRush(biz *BizResponse) bool {
	if biz == nil {
		return false
	}
	msg := biz.Message
	if stopRushRe.MatchString(msg) {
		return true
	}
	cs := codeStr(biz.Code)
	if cs == "4030" {
		return stopRushRe.MatchString(msg)
	}
	return false
}

func isBizOk(biz *BizResponse) bool {
	if biz == nil {
		return false
	}
	return codeStr(biz.Code) == "2000"
}

func stringifyID(v interface{}) string {
	if v == nil {
		return ""
	}
	switch x := v.(type) {
	case string:
		return strings.TrimSpace(x)
	case float64:
		return fmt.Sprintf("%.0f", x)
	case float32:
		return fmt.Sprintf("%.0f", x)
	case int:
		return fmt.Sprintf("%d", x)
	case int64:
		return fmt.Sprintf("%d", x)
	case int32:
		return fmt.Sprintf("%d", x)
	case uint:
		return fmt.Sprintf("%d", x)
	case uint64:
		return fmt.Sprintf("%d", x)
	case uint32:
		return fmt.Sprintf("%d", x)
	case json.Number:
		return strings.TrimSpace(x.String())
	default:
		s := strings.TrimSpace(fmt.Sprintf("%v", x))
		if s == "<nil>" {
			return ""
		}
		return s
	}
}

func priorityRecordIDFromBiz(biz *BizResponse) string {
	if biz == nil || biz.Data == nil {
		return ""
	}
	if m, ok := biz.Data.(map[string]interface{}); ok {
		for _, k := range []string{"priorityRecordId", "priorityUserRecordId"} {
			if rid := stringifyID(m[k]); rid != "" {
				return rid
			}
		}
	}
	raw, err := json.Marshal(biz.Data)
	if err != nil || len(raw) == 0 {
		return ""
	}
	var m map[string]interface{}
	if err := json.Unmarshal(raw, &m); err != nil {
		return ""
	}
	for _, k := range []string{"priorityRecordId", "priorityUserRecordId"} {
		if rid := stringifyID(m[k]); rid != "" {
			return rid
		}
	}
	return ""
}

// ---------- HTTP 发包 ----------

func clamp(v, lo, hi int) int {
	if v < lo {
		return lo
	}
	if v > hi {
		return hi
	}
	return v
}

var rushOutboundLimiter = newRushOutboundLimiter()

func newRushOutboundLimiter() chan struct{} {
	// 代理短连接场景下不能无限同时建 SOCKS5/TLS 隧道。
	// MT_RUSH_MAX_INFLIGHT=0 可关闭；默认 2000，先压住瞬时握手洪峰。
	n := 2000
	raw := strings.TrimSpace(os.Getenv("MT_RUSH_MAX_INFLIGHT"))
	if raw != "" {
		if v, err := strconv.Atoi(raw); err == nil {
			n = v
		}
	}
	if n <= 0 {
		return nil
	}
	if n > 5000 {
		n = 5000
	}
	return make(chan struct{}, n)
}

func acquireRushOutboundSlotCtx(ctx context.Context) (func(), error) {
	if rushOutboundLimiter == nil {
		return func() {}, nil
	}
	select {
	case rushOutboundLimiter <- struct{}{}:
		return func() { <-rushOutboundLimiter }, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func attemptBodyReader(att Attempt) (io.Reader, int64, error) {
	var bodyReader io.Reader
	var bodyLen int64
	if att.BodyB64 != "" {
		decoded, e := base64.StdEncoding.DecodeString(att.BodyB64)
		if e != nil {
			return nil, 0, fmt.Errorf("bodyB64 decode: %w", e)
		}
		bodyLen = int64(len(decoded))
		bodyReader = bytes.NewReader(decoded)
	} else if att.Body != nil {
		switch b := att.Body.(type) {
		case string:
			bodyLen = int64(len([]byte(b)))
			bodyReader = strings.NewReader(b)
		default:
			enc, _ := json.Marshal(b)
			bodyLen = int64(len(enc))
			bodyReader = bytes.NewReader(enc)
		}
	}
	return bodyReader, bodyLen, nil
}

func doRequestCtx(ctx context.Context, att Attempt) (statusCode int, bodyBytes []byte, err error) {
	statusCode, bodyBytes, _, err = doRequestCtxWithProxy(ctx, att)
	return statusCode, bodyBytes, err
}

func doRequestCtxWithProxy(ctx context.Context, att Attempt) (statusCode int, bodyBytes []byte, actualProxyURL string, err error) {
	actualProxyURL = strings.TrimSpace(att.ProxyURL)
	if shouldUseCustomTLSTransport(att) {
		if shouldUseReqrioTransport() {
			statusCode, bodyBytes, actualProxyURL, err = doRequestReqrioCtxWithProxy(ctx, att)
		} else {
			statusCode, bodyBytes, actualProxyURL, err = doRequestTLSClientCtxWithProxy(ctx, att)
		}
		errAtt := att
		errAtt.ProxyURL = actualProxyURL
		return statusCode, bodyBytes, actualProxyURL, annotateRequestErr(errAtt, err)
	}
	return 0, nil, actualProxyURL, annotateRequestErr(att, fmt.Errorf("unsupported request URL for %s transport: %s", tlsTransportModeName(), att.URL))
}

func doRequest(att Attempt) (statusCode int, bodyBytes []byte, err error) {
	return doRequestCtx(context.Background(), att)
}

func parseBizResponse(statusCode int, body []byte) *BizResponse {
	var biz BizResponse
	if err := json.Unmarshal(body, &biz); err != nil {
		preview := string(body)
		if len(preview) > 200 {
			preview = preview[:200]
		}
		return &BizResponse{Code: float64(statusCode), Message: "响应非JSON: " + preview}
	}
	return &biz
}

func sleepMs(ms int) {
	if ms <= 0 {
		return
	}
	time.Sleep(time.Duration(ms) * time.Millisecond)
}

func jitterSleep(maxMs int) {
	if maxMs <= 0 {
		return
	}
	j := rand.Intn(maxMs + 1)
	sleepMs(j)
}

func jitterSleepRange(minMs, maxMs int) {
	if maxMs <= 0 {
		return
	}
	if minMs > maxMs {
		minMs = maxMs
	}
	sleepMs(minMs + rand.Intn(maxMs-minMs+1))
}

func roundGapWithJitter(baseMs, jitterMax int) int {
	if jitterMax <= 0 {
		return baseMs
	}
	lo := baseMs - jitterMax
	if lo < 0 {
		lo = 0
	}
	hi := baseMs + jitterMax
	return lo + rand.Intn(hi-lo+1)
}

func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) > n {
		return string(r[:n])
	}
	return s
}

func proxyForTrace(proxyURL string) string {
	p := strings.TrimSpace(proxyURL)
	if p == "" {
		return "direct"
	}
	u, err := url.Parse(p)
	if err != nil || strings.TrimSpace(u.Host) == "" {
		return truncate(p, 120)
	}
	scheme := strings.ToLower(strings.TrimSpace(u.Scheme))
	if scheme == "" {
		scheme = "proxy"
	}
	user := ""
	if u.User != nil && strings.TrimSpace(u.User.Username()) != "" {
		user = strings.TrimSpace(u.User.Username()) + "@"
	}
	return scheme + "://" + user + u.Host
}

func annotateRequestErr(att Attempt, err error) error {
	if err == nil {
		return nil
	}
	msg := err.Error()
	proxyInfo := proxyForTrace(att.ProxyURL)
	if proxyInfo == "direct" {
		return err
	}
	if strings.Contains(msg, "http: server gave HTTP response to HTTPS client") {
		return fmt.Errorf(
			"%w (proxy=%s; HTTPS target received plain HTTP during TLS/proxy handshake; check whether this HTTP proxy supports CONNECT to HTTPS, proxy auth/whitelist, or old rush_success_proxy_url)",
			err,
			proxyInfo,
		)
	}
	lower := strings.ToLower(msg)
	if strings.Contains(lower, "proxy responded with non 200 code") ||
		strings.Contains(lower, "connect tunnel failed") ||
		strings.Contains(lower, "407 proxy authentication") {
		return fmt.Errorf("%w (proxy=%s)", err, proxyInfo)
	}
	return err
}

type runtimeProxyState struct {
	mu      sync.Mutex
	proxies []string
	next    int
}

type runtimeLaneLease struct {
	ProxyURL   string
	TLSProfile string
}

type runtimeProxyPicker struct {
	state       *runtimeProxyState
	laneMu      sync.Mutex
	laneCurrent map[int]runtimeLaneLease
	laneBanned  map[int]string
}

var runtimeProxyStates sync.Map

func cleanRuntimeProxyPool(raw []string) []string {
	var out []string
	for _, item := range raw {
		u := strings.TrimSpace(item)
		if u == "" {
			continue
		}
		out = append(out, u)
	}
	return out
}

func runtimeProxyPoolKey(proxies []string) string {
	return strings.Join(proxies, "\x00")
}

func getRuntimeProxyState(proxies []string) *runtimeProxyState {
	key := runtimeProxyPoolKey(proxies)
	if v, ok := runtimeProxyStates.Load(key); ok {
		if st, ok := v.(*runtimeProxyState); ok {
			return st
		}
	}
	st := &runtimeProxyState{proxies: append([]string(nil), proxies...)}
	actual, _ := runtimeProxyStates.LoadOrStore(key, st)
	if cur, ok := actual.(*runtimeProxyState); ok {
		return cur
	}
	return st
}

func (s *runtimeProxyState) update(proxies []string) {
	if s == nil || len(proxies) == 0 {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if runtimeProxyPoolKey(s.proxies) == runtimeProxyPoolKey(proxies) {
		return
	}
	s.proxies = append([]string(nil), proxies...)
	if len(s.proxies) > 0 {
		s.next = s.next % len(s.proxies)
	} else {
		s.next = 0
	}
}

func newRuntimeProxyPicker(enabled bool, raw []string) *runtimeProxyPicker {
	if !enabled {
		return nil
	}
	proxies := cleanRuntimeProxyPool(raw)
	if len(proxies) == 0 {
		return nil
	}
	return &runtimeProxyPicker{
		state:       getRuntimeProxyState(proxies),
		laneCurrent: make(map[int]runtimeLaneLease),
		laneBanned:  make(map[int]string),
	}
}

func (p *runtimeProxyPicker) updatePool(raw []string) {
	if p == nil {
		return
	}
	proxies := cleanRuntimeProxyPool(raw)
	if len(proxies) == 0 {
		return
	}
	if p.state == nil {
		p.state = getRuntimeProxyState(proxies)
		return
	}
	p.state.update(proxies)
}

func (p *runtimeProxyPicker) laneBannedProxy(lane int) string {
	if p == nil {
		return ""
	}
	p.laneMu.Lock()
	defer p.laneMu.Unlock()
	return p.laneBanned[lane]
}

func (p *runtimeProxyPicker) markLaneHTTP429(lane int, proxyURL string) {
	if p == nil || strings.TrimSpace(proxyURL) == "" {
		return
	}
	proxyURL = strings.TrimSpace(proxyURL)
	p.laneMu.Lock()
	p.laneBanned[lane] = proxyURL
	if strings.TrimSpace(p.laneCurrent[lane].ProxyURL) == proxyURL {
		delete(p.laneCurrent, lane)
	}
	p.laneMu.Unlock()
}

func (p *runtimeProxyPicker) pick(lane int) runtimeLaneLease {
	if p == nil || p.state == nil {
		return runtimeLaneLease{}
	}
	p.laneMu.Lock()
	defer p.laneMu.Unlock()
	if cur := p.laneCurrent[lane]; strings.TrimSpace(cur.ProxyURL) != "" {
		return cur
	}
	banned := strings.TrimSpace(p.laneBanned[lane])
	p.state.mu.Lock()
	defer p.state.mu.Unlock()
	proxies := p.state.proxies
	if len(proxies) == 0 {
		return runtimeLaneLease{}
	}
	for i := 0; i < len(proxies); i++ {
		idx := p.state.next % len(proxies)
		p.state.next = (idx + 1) % len(proxies)
		u := proxies[idx]
		if len(proxies) == 1 || strings.TrimSpace(u) != banned {
			lease := runtimeLaneLease{
				ProxyURL:   u,
				TLSProfile: tlsClientProfileEntryForRequest().Name,
			}
			p.laneCurrent[lane] = lease
			if strings.TrimSpace(u) != banned {
				delete(p.laneBanned, lane)
			}
			return lease
		}
	}
	u := proxies[p.state.next%len(proxies)]
	lease := runtimeLaneLease{
		ProxyURL:   u,
		TLSProfile: tlsClientProfileEntryForRequest().Name,
	}
	p.laneCurrent[lane] = lease
	return lease
}

func (p *runtimeProxyPicker) apply(att Attempt) Attempt {
	if p == nil {
		return att
	}
	lease := p.pick(transportLaneID(att))
	att.ProxyURL = lease.ProxyURL
	att.TLSProfile = lease.TLSProfile
	att.RuntimeProxy = true
	return att
}

func runtimeProxyShouldSwitch(statusCode int, body []byte) bool {
	if statusCode != 429 {
		return false
	}
	trimmed := bytes.TrimSpace(body)
	if len(trimmed) == 0 {
		return true
	}
	if bytes.Equal(trimmed, []byte("1")) || bytes.Equal(trimmed, []byte("2")) {
		return false
	}
	var obj map[string]interface{}
	if err := json.Unmarshal(trimmed, &obj); err == nil {
		if _, ok := obj["code"]; ok {
			return false
		}
	}
	return true
}

func doRushAttempt(ctx context.Context, att Attempt, picker *runtimeProxyPicker) (int, []byte, string, Attempt, error) {
	att = picker.apply(att)
	statusCode, body, actualProxyURL, err := doRequestCtxWithProxy(ctx, att)
	if err == nil && runtimeProxyShouldSwitch(statusCode, body) {
		picker.markLaneHTTP429(transportLaneID(att), actualProxyURL)
	}
	return statusCode, body, actualProxyURL, att, err
}

// ---------- MT 串行模式 ----------

func execRushLoopSerialWithCallback(req RushLoopRequest, onTrace func(TraceRow)) RushLoopResponse {
	t0 := time.Now()
	attempts := req.Attempts
	gapMs := clamp(req.RushLoopDelayMs, 0, 300000)
	jitMax := clamp(req.RushJitterMsMax, 0, 100)
	proxyPicker := newRuntimeProxyPicker(req.RuntimeProxyPool, req.ProxyPool)

	var trace []TraceRow
	var last interface{}
	lastProxyURL := ""

	for i, att := range attempts {
		att = withAttemptAccountKey(att, req.AccountKey)
		jitterSleep(jitMax)

		ts := time.Now()
		statusCode, body, actualProxyURL, appliedAtt, err := doRushAttempt(context.Background(), att, proxyPicker)
		ms := time.Since(ts).Milliseconds()
		lastProxyURL = actualProxyURL

		var biz *BizResponse
		if err != nil {
			biz = &BizResponse{Code: float64(-1), Message: "请求异常: " + err.Error()}
			statusCode = 0
		} else {
			biz = parseBizResponse(statusCode, body)
		}
		last = biz

		row := TraceRow{
			Attempt:          i + 1,
			HTTPStatus:       statusCode,
			Code:             int(toFloat(biz.Code)),
			Message:          truncate(biz.Message, 400),
			Ms:               ms,
			ReceivedAtMs:     time.Now().UnixMilli(),
			LaneID:           transportLaneID(appliedAtt),
			PriorityRecordID: priorityRecordIDFromBiz(biz),
			Proxy:            proxyForTrace(actualProxyURL),
			TLSProfile:       appliedAtt.TLSProfile,
		}
		trace = append(trace, row)
		if onTrace != nil {
			onTrace(row)
		}

		if isStopRush(biz) {
			break
		}
		if i < len(attempts)-1 && gapMs > 0 {
			sleepMs(gapMs)
		}
	}

	return RushLoopResponse{
		OK:           true,
		RushLoop:     true,
		Last:         last,
		LastProxyURL: lastProxyURL,
		Trace:        trace,
		WallMs:       time.Since(t0).Milliseconds(),
	}
}

func execRushLoopSerial(req RushLoopRequest) RushLoopResponse {
	return execRushLoopSerialWithCallback(req, nil)
}

func execRushLoopLaneParallelWithCallback(req RushLoopRequest, onTrace func(TraceRow)) RushLoopResponse {
	t0 := time.Now()
	attempts := req.Attempts
	gapMs := clamp(req.RushLoopDelayMs, 0, 300000)
	jitMax := clamp(req.RushJitterMsMax, 0, 100)
	proxyPicker := newRuntimeProxyPicker(req.RuntimeProxyPool, req.ProxyPool)

	type laneAttempt struct {
		idx int
		att Attempt
	}
	lanes := make(map[int][]laneAttempt)
	for i, att := range attempts {
		lane := transportLaneID(att)
		lanes[lane] = append(lanes[lane], laneAttempt{idx: i, att: att})
	}
	if len(lanes) == 0 {
		return RushLoopResponse{OK: false, Error: "rush_loop: no lane attempts"}
	}

	traceSlots := make([]*TraceRow, len(attempts))
	var mu sync.Mutex
	var stop atomic.Bool
	var last interface{}
	lastProxyURL := ""
	okIdx := -1
	lastIdx := -1

	var wg sync.WaitGroup
	for _, laneAttempts := range lanes {
		laneAttempts := laneAttempts
		wg.Add(1)
		go func() {
			defer wg.Done()
			for laneShot, laneAtt := range laneAttempts {
				if stop.Load() {
					return
				}
				att := withAttemptAccountKey(laneAtt.att, req.AccountKey)
				jitterSleep(jitMax)

				ts := time.Now()
				statusCode, body, actualProxyURL, appliedAtt, err := doRushAttempt(context.Background(), att, proxyPicker)
				ms := time.Since(ts).Milliseconds()

				var biz *BizResponse
				if err != nil {
					biz = &BizResponse{Code: float64(-1), Message: "请求异常: " + err.Error()}
					statusCode = 0
				} else {
					biz = parseBizResponse(statusCode, body)
				}

				attemptNo := laneAtt.idx + 1
				row := TraceRow{
					Attempt:          attemptNo,
					HTTPStatus:       statusCode,
					Code:             int(toFloat(biz.Code)),
					Message:          truncate(biz.Message, 400),
					Ms:               ms,
					ReceivedAtMs:     time.Now().UnixMilli(),
					LaneID:           transportLaneID(appliedAtt),
					PriorityRecordID: priorityRecordIDFromBiz(biz),
					Proxy:            proxyForTrace(actualProxyURL),
					TLSProfile:       appliedAtt.TLSProfile,
				}
				slotIdx := attemptNo - 1

				mu.Lock()
				if slotIdx >= 0 && slotIdx < len(traceSlots) {
					cp := row
					traceSlots[slotIdx] = &cp
				}
				if slotIdx > lastIdx {
					lastIdx = slotIdx
					last = biz
					lastProxyURL = actualProxyURL
				}
				if okIdx < 0 && isBizOk(biz) {
					okIdx = slotIdx
					last = biz
					lastProxyURL = actualProxyURL
				}
				if isStopRush(biz) || isBizOk(biz) {
					stop.Store(true)
				}
				mu.Unlock()

				if onTrace != nil {
					onTrace(row)
				}

				if stop.Load() {
					return
				}
				if laneShot < len(laneAttempts)-1 && gapMs > 0 {
					sleepMs(gapMs)
				}
			}
		}()
	}
	wg.Wait()

	var trace []TraceRow
	for _, row := range traceSlots {
		if row != nil {
			trace = append(trace, *row)
		}
	}

	return RushLoopResponse{
		OK:           true,
		RushLoop:     true,
		Last:         last,
		LastProxyURL: lastProxyURL,
		Trace:        trace,
		WallMs:       time.Since(t0).Milliseconds(),
	}
}

func execRushLoopLaneParallel(req RushLoopRequest) RushLoopResponse {
	return execRushLoopLaneParallelWithCallback(req, nil)
}

func execRushShot(req RushShotRequest) RushShotResponse {
	return execRushShotCtx(context.Background(), req)
}

func execRushShotCtx(ctx context.Context, req RushShotRequest) RushShotResponse {
	return execRushShotCtxWithPicker(ctx, req, newRuntimeProxyPicker(req.RuntimeProxyPool, req.ProxyPool))
}

func execRushShotCtxWithPicker(ctx context.Context, req RushShotRequest, proxyPicker *runtimeProxyPicker) RushShotResponse {
	t0 := time.Now()
	att := withAttemptAccountKey(req.Attempt, req.AccountKey)
	attemptIndex := req.AttemptIndex
	if attemptIndex < 1 {
		attemptIndex = 1
	}
	statusCode, body, actualProxyURL, appliedAtt, err := doRushAttempt(ctx, att, proxyPicker)
	ms := time.Since(t0).Milliseconds()
	var biz *BizResponse
	if err != nil {
		biz = &BizResponse{Code: float64(-1), Message: "请求异常: " + err.Error()}
		statusCode = 0
	} else {
		biz = parseBizResponse(statusCode, body)
	}
	row := TraceRow{
		Attempt:          attemptIndex,
		HTTPStatus:       statusCode,
		Code:             int(toFloat(biz.Code)),
		Message:          truncate(biz.Message, 400),
		Ms:               ms,
		ReceivedAtMs:     time.Now().UnixMilli(),
		RushRound:        req.RushRound,
		RushRounds:       req.RushRounds,
		LaneID:           transportLaneID(appliedAtt),
		PriorityRecordID: priorityRecordIDFromBiz(biz),
		Proxy:            proxyForTrace(actualProxyURL),
		TLSProfile:       appliedAtt.TLSProfile,
	}
	return RushShotResponse{
		OK:           true,
		RushShot:     true,
		Row:          row,
		Last:         biz,
		LastProxyURL: actualProxyURL,
	}
}

type AsyncRushStartRequest struct {
	JobID string `json:"jobId"`
}

type AsyncRushShotRequest struct {
	JobID            string   `json:"jobId"`
	AccountKey       string   `json:"accountKey"`
	Attempt          Attempt  `json:"attempt"`
	AttemptIndex     int      `json:"attemptIndex"`
	RushRound        int      `json:"rushRound,omitempty"`
	RushRounds       int      `json:"rushRounds,omitempty"`
	RuntimeProxyPool bool     `json:"runtimeProxyPool,omitempty"`
	ProxyPool        []string `json:"proxyPool,omitempty"`
	TLSImpersonate   string   `json:"tlsImpersonate"`
	CancelJobOnStop  bool     `json:"cancelJobOnStop,omitempty"`
}

type AsyncRushStopRequest struct {
	JobID      string `json:"jobId"`
	AccountKey string `json:"accountKey,omitempty"`
}

type asyncRushEvent struct {
	Type       string            `json:"type"`
	OK         bool              `json:"ok,omitempty"`
	JobID      string            `json:"jobId,omitempty"`
	AccountKey string            `json:"accountKey,omitempty"`
	Row        *TraceRow         `json:"row,omitempty"`
	Result     *RushShotResponse `json:"result,omitempty"`
	Error      string            `json:"error,omitempty"`
	Accepted   int64             `json:"accepted,omitempty"`
	InFlight   int64             `json:"inFlight,omitempty"`
}

type asyncRushJob struct {
	id           string
	ctx          context.Context
	cancel       context.CancelFunc
	events       chan asyncRushEvent
	accepted     int64
	inFlight     int64
	doneOnce     sync.Once
	accountMu    sync.Mutex
	stopped      map[string]bool
	proxyMu      sync.Mutex
	proxyPickers map[string]*runtimeProxyPicker
}

var asyncRushJobs sync.Map

func asyncJobID(raw string) string {
	s := strings.TrimSpace(raw)
	if s == "" {
		s = fmt.Sprintf("job-%d", time.Now().UnixNano())
	}
	return s
}

func startAsyncRushJob(jobID string) *asyncRushJob {
	jobID = asyncJobID(jobID)
	if old, ok := asyncRushJobs.Load(jobID); ok {
		if j, ok := old.(*asyncRushJob); ok {
			j.cancel()
			asyncRushJobs.Delete(jobID)
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	j := &asyncRushJob{
		id:           jobID,
		ctx:          ctx,
		cancel:       cancel,
		events:       make(chan asyncRushEvent, 4096),
		stopped:      make(map[string]bool),
		proxyPickers: make(map[string]*runtimeProxyPicker),
	}
	asyncRushJobs.Store(jobID, j)
	j.emit(asyncRushEvent{Type: "start", OK: true, JobID: jobID})
	go func() {
		<-ctx.Done()
		deadline := time.Now().Add(30 * time.Second)
		for atomic.LoadInt64(&j.inFlight) > 0 && time.Now().Before(deadline) {
			time.Sleep(20 * time.Millisecond)
		}
		j.doneOnce.Do(func() { close(j.events) })
		asyncRushJobs.Delete(jobID)
	}()
	return j
}

func getAsyncRushJob(jobID string) (*asyncRushJob, bool) {
	v, ok := asyncRushJobs.Load(strings.TrimSpace(jobID))
	if !ok {
		return nil, false
	}
	j, ok := v.(*asyncRushJob)
	return j, ok
}

func (j *asyncRushJob) emit(ev asyncRushEvent) {
	defer func() {
		_ = recover()
	}()
	if ev.JobID == "" {
		ev.JobID = j.id
	}
	select {
	case j.events <- ev:
	default:
	}
}

func (j *asyncRushJob) accountStopped(accountKey string) bool {
	accountKey = strings.TrimSpace(accountKey)
	if accountKey == "" {
		return false
	}
	j.accountMu.Lock()
	defer j.accountMu.Unlock()
	return j.stopped[accountKey]
}

func (j *asyncRushJob) stopAccount(accountKey string) {
	accountKey = strings.TrimSpace(accountKey)
	if accountKey == "" {
		return
	}
	j.accountMu.Lock()
	if !j.stopped[accountKey] {
		j.stopped[accountKey] = true
	}
	j.accountMu.Unlock()
}

func (j *asyncRushJob) stopAll() {
	j.cancel()
}

func (j *asyncRushJob) runtimeProxyPicker(accountKey string, enabled bool, raw []string) *runtimeProxyPicker {
	if !enabled {
		return nil
	}
	proxies := cleanRuntimeProxyPool(raw)
	if len(proxies) == 0 {
		return nil
	}
	key := strings.TrimSpace(accountKey)
	if key == "" {
		key = "default"
	}
	j.proxyMu.Lock()
	defer j.proxyMu.Unlock()
	if j.proxyPickers == nil {
		j.proxyPickers = make(map[string]*runtimeProxyPicker)
	}
	if p := j.proxyPickers[key]; p != nil {
		p.updatePool(proxies)
		return p
	}
	p := &runtimeProxyPicker{
		state:       getRuntimeProxyState(proxies),
		laneCurrent: make(map[int]runtimeLaneLease),
		laneBanned:  make(map[int]string),
	}
	j.proxyPickers[key] = p
	return p
}

func submitAsyncRushShot(req AsyncRushShotRequest) (int64, int64, error) {
	j, ok := getAsyncRushJob(req.JobID)
	if !ok {
		return 0, 0, fmt.Errorf("async rush job not found: %s", req.JobID)
	}
	if strings.TrimSpace(req.AccountKey) == "" {
		req.AccountKey = strings.TrimSpace(req.Attempt.AccountKey)
	}
	if strings.TrimSpace(req.Attempt.URL) == "" {
		return 0, 0, fmt.Errorf("attempt.url required")
	}
	if j.accountStopped(req.AccountKey) {
		return atomic.LoadInt64(&j.accepted), atomic.LoadInt64(&j.inFlight), nil
	}
	accepted := atomic.AddInt64(&j.accepted, 1)
	inFlight := atomic.AddInt64(&j.inFlight, 1)
	j.emit(asyncRushEvent{Type: "accepted", OK: true, AccountKey: req.AccountKey, Accepted: accepted, InFlight: inFlight})
	go func() {
		defer atomic.AddInt64(&j.inFlight, -1)
		select {
		case <-j.ctx.Done():
			return
		default:
		}
		shotReq := RushShotRequest{
			Attempt:          req.Attempt,
			AttemptIndex:     req.AttemptIndex,
			RushRound:        req.RushRound,
			RushRounds:       req.RushRounds,
			RuntimeProxyPool: req.RuntimeProxyPool,
			ProxyPool:        req.ProxyPool,
			TLSImpersonate:   req.TLSImpersonate,
			AccountKey:       req.AccountKey,
		}
		res := execRushShotCtxWithPicker(j.ctx, shotReq, j.runtimeProxyPicker(req.AccountKey, req.RuntimeProxyPool, req.ProxyPool))
		row := res.Row
		j.emit(asyncRushEvent{
			Type:       "shot",
			OK:         true,
			AccountKey: req.AccountKey,
			Row:        &row,
			Result:     &res,
			InFlight:   atomic.LoadInt64(&j.inFlight),
		})
		biz, _ := res.Last.(*BizResponse)
		if biz == nil {
			if m, ok := res.Last.(map[string]interface{}); ok {
				biz = &BizResponse{Code: m["code"], Message: fmt.Sprintf("%v", m["message"]), Data: m["data"]}
			}
		}
		if row.Code == 2000 || isBizOk(biz) {
			j.stopAccount(req.AccountKey)
			j.emit(asyncRushEvent{
				Type:       "account_stop",
				OK:         true,
				AccountKey: req.AccountKey,
				Row:        &row,
				Result:     &res,
			})
			if req.CancelJobOnStop {
				j.cancel()
			}
			return
		}
		if isStopRush(biz) {
			j.stopAccount(req.AccountKey)
			j.emit(asyncRushEvent{
				Type:       "account_stop",
				OK:         true,
				AccountKey: req.AccountKey,
				Row:        &row,
				Result:     &res,
			})
			if req.CancelJobOnStop {
				j.cancel()
			}
		}
	}()
	return accepted, inFlight, nil
}

// ---------- 高频 fire-and-forget 模式 ----------

type shotResult struct {
	idx        int
	round      int
	statusCode int
	biz        *BizResponse
	ms         int64
}

func execRushLoopFireAndForgetWithCallback(req RushLoopRequest, onTrace func(TraceRow)) RushLoopResponse {
	t0 := time.Now()
	attempts := req.Attempts
	total := len(attempts)
	jitMin := 10
	jitMax := clamp(req.RushJitterMsMax, 0, 100)
	if jitMax == 0 {
		jitMax = 20
	}
	// fire-and-forget 模式下的「枪间隔」：每发出一枪 goroutine 后主线程 sleep(delayMs)
	// 之前忽略此值导致 N 枪在 ~jitter 内全部 dispatch（密度过高 → 4293）
	shotGapMs := clamp(req.RushLoopDelayMs, 0, 300000)
	rounds := req.RushRounds
	if rounds < 1 {
		rounds = 1
	}
	if rounds > 100 {
		rounds = 100
	}
	roundGapBase := clamp(req.RushRoundGapMs, 0, 300000)
	roundJit := clamp(req.RushRoundGapJitterMs, 0, 60000)
	proxyPicker := newRuntimeProxyPicker(req.RuntimeProxyPool, req.ProxyPool)

	if total%rounds != 0 {
		return RushLoopResponse{
			OK:    false,
			Error: fmt.Sprintf("rush_loop: attempts=%d 不能整除 rushRounds=%d", total, rounds),
		}
	}
	shotsPerRound := total / rounds

	// trace 按全局 idx 存储
	trace := make([]*TraceRow, total)
	fullBiz := make([]*BizResponse, total)
	actualProxyURLs := make([]string, total)
	var mu sync.Mutex
	stopped := false // 触发停枪后不再发新枪

	globalIdx := 0
	var wg sync.WaitGroup
	for round := 0; round < rounds; round++ {
		// 轮前检测：已有返回触发停枪则不再发新轮
		mu.Lock()
		s := stopped
		mu.Unlock()
		if s {
			break
		}

		for i := 0; i < shotsPerRound; i++ {
			mu.Lock()
			if stopped {
				mu.Unlock()
				break
			}
			mu.Unlock()

			idx := globalIdx
			globalIdx++
			att := withAttemptAccountKey(attempts[idx], req.AccountKey)

			wg.Add(1)
			go func(idx, round int, att Attempt, jMin, jMax int) {
				defer wg.Done()
				jitterSleepRange(jMin, jMax)
				ts := time.Now()
				var statusCode int
				var body []byte
				var actualProxyURL string
				var err error
				var appliedAtt Attempt
				statusCode, body, actualProxyURL, appliedAtt, err = doRushAttempt(context.Background(), att, proxyPicker)
				ms := time.Since(ts).Milliseconds()

				var biz *BizResponse
				if err != nil {
					biz = &BizResponse{Code: float64(-1), Message: "请求异常: " + err.Error()}
					statusCode = 0
				} else {
					biz = parseBizResponse(statusCode, body)
				}

				row := &TraceRow{
					Attempt:          idx + 1,
					HTTPStatus:       statusCode,
					Code:             int(toFloat(biz.Code)),
					Message:          truncate(biz.Message, 400),
					Ms:               ms,
					ReceivedAtMs:     time.Now().UnixMilli(),
					RushRound:        round + 1,
					RushRounds:       rounds,
					LaneID:           transportLaneID(appliedAtt),
					PriorityRecordID: priorityRecordIDFromBiz(biz),
					Proxy:            proxyForTrace(actualProxyURL),
					TLSProfile:       appliedAtt.TLSProfile,
				}
				if onTrace != nil {
					onTrace(*row)
				}

				mu.Lock()
				trace[idx] = row
				fullBiz[idx] = biz
				actualProxyURLs[idx] = actualProxyURL
				if isStopRush(biz) || isBizOk(biz) {
					stopped = true
				}
				mu.Unlock()
			}(idx, round, att, jitMin, jitMax)

			// 枪间隔（fire-and-forget 模式下严格按 RushLoopDelayMs sleep，不等响应）
			// 等价于"主线程节奏控制"：发出 goroutine 后立刻 sleep(N ms) 再发下一枪
			// 不是最后一枪 + 设置了间隔时才 sleep，避免无谓延迟
			if i < shotsPerRound-1 && shotGapMs > 0 {
				sleepMs(shotGapMs)
			}
		}

		// 轮间间隔（不等本轮返回，直接等间隔后发下一轮）
		if round < rounds-1 {
			mu.Lock()
			s := stopped
			mu.Unlock()
			if s {
				break
			}
			rg := roundGapWithJitter(roundGapBase, roundJit)
			sleepMs(rg)
		}
	}
	wg.Wait()

	// 收集非 nil trace
	var filled []TraceRow
	for _, row := range trace {
		if row != nil {
			filled = append(filled, *row)
		}
	}

	// last：优先取业务成功的那枪，否则取最后一枪
	var last interface{}
	lastProxyURL := ""
	okIdx := -1
	for i, b := range fullBiz {
		if isBizOk(b) {
			okIdx = i
			break
		}
	}
	if okIdx >= 0 {
		last = fullBiz[okIdx]
		lastProxyURL = actualProxyURLs[okIdx]
	} else {
		for i := total - 1; i >= 0; i-- {
			if fullBiz[i] != nil {
				last = fullBiz[i]
				lastProxyURL = actualProxyURLs[i]
				break
			}
		}
	}

	return RushLoopResponse{
		OK:            true,
		RushLoop:      true,
		FireAndForget: true,
		RushRounds:    rounds,
		Last:          last,
		LastProxyURL:  lastProxyURL,
		Trace:         filled,
		WallMs:        time.Since(t0).Milliseconds(),
	}
}

func execRushLoopFireAndForget(req RushLoopRequest) RushLoopResponse {
	return execRushLoopFireAndForgetWithCallback(req, nil)
}

// ---------- 统一入口 ----------

func execRushLoop(req RushLoopRequest) RushLoopResponse {
	if len(req.Attempts) == 0 {
		return RushLoopResponse{OK: false, Error: "rushLoop: attempts required"}
	}
	if req.FireAndForget {
		return execRushLoopFireAndForget(req)
	}
	if req.LaneParallel {
		return execRushLoopLaneParallel(req)
	}
	return execRushLoopSerial(req)
}

func execRushLoopWithCallback(req RushLoopRequest, onTrace func(TraceRow)) RushLoopResponse {
	if len(req.Attempts) == 0 {
		return RushLoopResponse{OK: false, Error: "rushLoop: attempts required"}
	}
	if req.FireAndForget {
		return execRushLoopFireAndForgetWithCallback(req, onTrace)
	}
	if req.LaneParallel {
		return execRushLoopLaneParallelWithCallback(req, onTrace)
	}
	return execRushLoopSerialWithCallback(req, onTrace)
}

// ---------- 单条请求（/req 接口） ----------

type SingleReqRequest struct {
	Method         string            `json:"method"`
	URL            string            `json:"url"`
	Headers        map[string]string `json:"headers"`
	HeadersOrdered []HeaderPair      `json:"headersOrdered"`
	Body           interface{}       `json:"body"`
	BodyB64        string            `json:"bodyB64"`
	Host           string            `json:"host"`
	ContentLength  int64             `json:"contentLength"`
	ProxyURL       string            `json:"proxyUrl"`
	TimeoutMs      int               `json:"timeoutMs"`
	AccountKey     string            `json:"accountKey"`
	LaneID         int               `json:"laneId"`
}

type SingleReqResponse struct {
	OK      bool   `json:"ok"`
	Status  int    `json:"status,omitempty"`
	BodyB64 string `json:"bodyB64,omitempty"`
	Error   string `json:"error,omitempty"`
}

func execSingleReq(r SingleReqRequest) SingleReqResponse {
	att := Attempt{
		Method:         r.Method,
		URL:            r.URL,
		Headers:        r.Headers,
		HeadersOrdered: r.HeadersOrdered,
		Body:           r.Body,
		BodyB64:        r.BodyB64,
		Host:           r.Host,
		ContentLength:  r.ContentLength,
		ProxyURL:       r.ProxyURL,
		TimeoutMs:      r.TimeoutMs,
		AccountKey:     r.AccountKey,
		LaneID:         r.LaneID,
	}
	statusCode, body, err := doRequest(att)
	if err != nil {
		return SingleReqResponse{OK: false, Error: err.Error()}
	}
	return SingleReqResponse{
		OK:      true,
		Status:  statusCode,
		BodyB64: base64.StdEncoding.EncodeToString(body),
	}
}

// ---------- 批量并发（/batch 接口） ----------

type BatchTask struct {
	// rushLoop 任务字段
	RushLoop             bool      `json:"rushLoop"`
	Attempts             []Attempt `json:"attempts"`
	RushLoopDelayMs      int       `json:"rushLoopDelayMs"`
	RushJitterMsMax      int       `json:"rushJitterMsMax"`
	FireAndForget        bool      `json:"fireAndForget"`
	LaneParallel         bool      `json:"laneParallel,omitempty"`
	RushRounds           int       `json:"rushRounds"`
	RushRoundGapMs       int       `json:"rushRoundGapMs"`
	RushRoundGapJitterMs int       `json:"rushRoundGapJitterMs"`
	RuntimeProxyPool     bool      `json:"runtimeProxyPool,omitempty"`
	ProxyPool            []string  `json:"proxyPool,omitempty"`
	// 普通 req 任务字段
	Method         string            `json:"method"`
	URL            string            `json:"url"`
	Headers        map[string]string `json:"headers"`
	HeadersOrdered []HeaderPair      `json:"headersOrdered"`
	Body           interface{}       `json:"body"`
	BodyB64        string            `json:"bodyB64"`
	Host           string            `json:"host"`
	ContentLength  int64             `json:"contentLength"`
	ProxyURL       string            `json:"proxyUrl"`
	TimeoutMs      int               `json:"timeoutMs"`
	// 公共
	TLSImpersonate string `json:"tlsImpersonate"`
	AccountKey     string `json:"accountKey"`
	LaneID         int    `json:"laneId"`
}

type BatchRequest struct {
	Tasks          []BatchTask `json:"tasks"`
	Concurrency    int         `json:"concurrency"`
	TLSImpersonate string      `json:"tlsImpersonate"`
}

type BatchResponse struct {
	OK      bool          `json:"ok"`
	Results []interface{} `json:"results"`
	Error   string        `json:"error,omitempty"`
}

func execBatch(req BatchRequest) BatchResponse {
	n := len(req.Tasks)
	results := make([]interface{}, n)

	conc := req.Concurrency
	if conc < 1 {
		conc = 64
	}
	if conc > 512 {
		conc = 512
	}
	if conc > n {
		conc = n
	}

	topTLS := req.TLSImpersonate

	// 检查是否所有任务都是高频模式
	allFireAndForget := true
	for _, t := range req.Tasks {
		if t.RushLoop && !t.FireAndForget {
			allFireAndForget = false
			break
		}
	}

	// 使用原子索引实现账号级并行；高频模式的“每号多枪并发”由 execRushLoopFireAndForget 内部完成。
	idx := int64(-1)
	var wg sync.WaitGroup

	if allFireAndForget {
		// 高频模式：账号任务并行启动，但仍等待本批请求完成并返回 trace/last，避免 Python/UI 收到空结果。
		for w := 0; w < conc; w++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for {
					i := int(atomic.AddInt64(&idx, 1))
					if i >= n {
						return
					}
					t := req.Tasks[i]
					tls := t.TLSImpersonate
					if tls == "" {
						tls = topTLS
					}
					if t.RushLoop {
						rl := RushLoopRequest{
							Attempts:             t.Attempts,
							RushLoopDelayMs:      t.RushLoopDelayMs,
							RushJitterMsMax:      t.RushJitterMsMax,
							FireAndForget:        t.FireAndForget,
							LaneParallel:         t.LaneParallel,
							RushRounds:           t.RushRounds,
							RushRoundGapMs:       t.RushRoundGapMs,
							RushRoundGapJitterMs: t.RushRoundGapJitterMs,
							RuntimeProxyPool:     t.RuntimeProxyPool,
							ProxyPool:            t.ProxyPool,
							TLSImpersonate:       tls,
							AccountKey:           t.AccountKey,
						}
						if rl.RushRounds < 1 {
							rl.RushRounds = 1
						}
						results[i] = execRushLoop(rl)
					} else {
						results[i] = execSingleReq(SingleReqRequest{
							Method:         t.Method,
							URL:            t.URL,
							Headers:        t.Headers,
							HeadersOrdered: t.HeadersOrdered,
							Body:           t.Body,
							BodyB64:        t.BodyB64,
							Host:           t.Host,
							ContentLength:  t.ContentLength,
							ProxyURL:       t.ProxyURL,
							TimeoutMs:      t.TimeoutMs,
							AccountKey:     t.AccountKey,
							LaneID:         t.LaneID,
						})
					}
				}
			}()
		}
	} else {
		// 普通模式：等待完成
		for w := 0; w < conc; w++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				for {
					i := int(atomic.AddInt64(&idx, 1))
					if i >= n {
						return
					}
					t := req.Tasks[i]
					tls := t.TLSImpersonate
					if tls == "" {
						tls = topTLS
					}
					if t.RushLoop {
						rl := RushLoopRequest{
							Attempts:             t.Attempts,
							RushLoopDelayMs:      t.RushLoopDelayMs,
							RushJitterMsMax:      t.RushJitterMsMax,
							FireAndForget:        t.FireAndForget,
							LaneParallel:         t.LaneParallel,
							RushRounds:           t.RushRounds,
							RushRoundGapMs:       t.RushRoundGapMs,
							RushRoundGapJitterMs: t.RushRoundGapJitterMs,
							RuntimeProxyPool:     t.RuntimeProxyPool,
							ProxyPool:            t.ProxyPool,
							TLSImpersonate:       tls,
							AccountKey:           t.AccountKey,
						}
						if rl.RushRounds < 1 {
							rl.RushRounds = 1
						}
						results[i] = execRushLoop(rl)
					} else {
						results[i] = execSingleReq(SingleReqRequest{
							Method:         t.Method,
							URL:            t.URL,
							Headers:        t.Headers,
							HeadersOrdered: t.HeadersOrdered,
							Body:           t.Body,
							BodyB64:        t.BodyB64,
							Host:           t.Host,
							ContentLength:  t.ContentLength,
							ProxyURL:       t.ProxyURL,
							TimeoutMs:      t.TimeoutMs,
							AccountKey:     t.AccountKey,
							LaneID:         t.LaneID,
						})
					}
				}
			}()
		}
	}
	wg.Wait()
	return BatchResponse{OK: true, Results: results}
}

func execBatchStream(req BatchRequest, emit func(map[string]interface{})) BatchResponse {
	n := len(req.Tasks)
	results := make([]interface{}, n)

	conc := req.Concurrency
	if conc < 1 {
		conc = 64
	}
	if conc > 512 {
		conc = 512
	}
	if conc > n {
		conc = n
	}
	if conc < 1 {
		conc = 1
	}

	topTLS := req.TLSImpersonate
	emit(map[string]interface{}{"type": "start", "ok": true, "total": n, "concurrency": conc})

	idx := int64(-1)
	var wg sync.WaitGroup
	for w := 0; w < conc; w++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				i := int(atomic.AddInt64(&idx, 1))
				if i >= n {
					return
				}
				t := req.Tasks[i]
				tls := t.TLSImpersonate
				if tls == "" {
					tls = topTLS
				}
				emit(map[string]interface{}{"type": "task_start", "taskIndex": i, "total": n})
				if t.RushLoop {
					rl := RushLoopRequest{
						Attempts:             t.Attempts,
						RushLoopDelayMs:      t.RushLoopDelayMs,
						RushJitterMsMax:      t.RushJitterMsMax,
						FireAndForget:        t.FireAndForget,
						LaneParallel:         t.LaneParallel,
						RushRounds:           t.RushRounds,
						RushRoundGapMs:       t.RushRoundGapMs,
						RushRoundGapJitterMs: t.RushRoundGapJitterMs,
						RuntimeProxyPool:     t.RuntimeProxyPool,
						ProxyPool:            t.ProxyPool,
						TLSImpersonate:       tls,
						AccountKey:           t.AccountKey,
					}
					if rl.RushRounds < 1 {
						rl.RushRounds = 1
					}
					res := execRushLoopWithCallback(rl, func(row TraceRow) {
						emit(map[string]interface{}{
							"type":             "shot",
							"taskIndex":        i,
							"attempt":          row.Attempt,
							"httpStatus":       row.HTTPStatus,
							"code":             row.Code,
							"message":          row.Message,
							"ms":               row.Ms,
							"rushRound":        row.RushRound,
							"rushRounds":       row.RushRounds,
							"laneId":           row.LaneID,
							"tlsProfile":       row.TLSProfile,
							"priorityRecordId": row.PriorityRecordID,
							"row":              row,
						})
					})
					results[i] = res
					emit(map[string]interface{}{"type": "result", "taskIndex": i, "result": res})
				} else {
					res := execSingleReq(SingleReqRequest{
						Method:         t.Method,
						URL:            t.URL,
						Headers:        t.Headers,
						HeadersOrdered: t.HeadersOrdered,
						Body:           t.Body,
						BodyB64:        t.BodyB64,
						Host:           t.Host,
						ContentLength:  t.ContentLength,
						ProxyURL:       t.ProxyURL,
						TimeoutMs:      t.TimeoutMs,
						AccountKey:     t.AccountKey,
						LaneID:         t.LaneID,
					})
					results[i] = res
					emit(map[string]interface{}{"type": "result", "taskIndex": i, "result": res})
				}
			}
		}()
	}
	wg.Wait()
	emit(map[string]interface{}{"type": "done", "ok": true, "total": n})
	return BatchResponse{OK: true, Results: results}
}
