package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/url"
	"strings"
)

func transportTargetAuthority(att Attempt) string {
	for _, raw := range []string{
		strings.TrimSpace(att.Host),
		strings.TrimSpace(firstAttemptHeader(att, "Host")),
	} {
		if raw != "" {
			return strings.ToLower(raw)
		}
	}
	u, err := url.Parse(strings.TrimSpace(att.URL))
	if err == nil && strings.TrimSpace(u.Host) != "" {
		return strings.ToLower(strings.TrimSpace(u.Host))
	}
	if h := reqrioHostForAttempt(att); h != "" {
		return strings.ToLower(h)
	}
	return strings.ToLower(strings.TrimSpace(att.URL))
}

func transportAccountKey(att Attempt) string {
	key, source := reqrioAccountKey(att)
	if strings.TrimSpace(key) == "" {
		return "anonymous"
	}
	if strings.HasPrefix(source, "fallback.") {
		ua := strings.TrimSpace(firstAttemptHeader(att, "User-Agent"))
		fallback := strings.Join([]string{transportTargetAuthority(att), ua}, "|")
		if strings.Trim(fallback, "| ") != "" {
			return fallback
		}
	}
	return key
}

func transportLaneID(att Attempt) int {
	if att.LaneID < 0 {
		return 0
	}
	return att.LaneID
}

func transportProxyKey(att Attempt) string {
	return strings.TrimSpace(att.ProxyURL)
}

func transportTLSProfileKey(att Attempt) string {
	if !att.RuntimeProxy {
		return ""
	}
	return strings.TrimSpace(att.TLSProfile)
}

func transportSessionKey(engine string, att Attempt) string {
	return strings.Join([]string{
		strings.TrimSpace(engine),
		transportAccountKey(att),
		transportTargetAuthority(att),
		"proxy:" + transportProxyKey(att),
		fmt.Sprintf("lane:%d", transportLaneID(att)),
		"tls:" + transportTLSProfileKey(att),
	}, "\x00")
}

func isHTTP429Empty(statusCode int, body []byte) bool {
	return statusCode == 429 && len(bytes.TrimSpace(body)) == 0
}

func http429SessionCloseReason(statusCode int, body []byte) (string, bool) {
	if statusCode != 429 {
		return "", false
	}
	trimmed := bytes.TrimSpace(body)
	if len(trimmed) == 0 {
		return "http429_empty", true
	}
	if bytes.Equal(trimmed, []byte("1")) || bytes.Equal(trimmed, []byte("2")) {
		return "", false
	}
	var biz BizResponse
	if err := json.Unmarshal(trimmed, &biz); err != nil {
		return "http429_non_json", true
	}
	return "", false
}

func transportTraceAccount(att Attempt) string {
	sum := sha256Hex([]byte(transportAccountKey(att)))
	if len(sum) > 12 {
		return sum[:12]
	}
	return sum
}

func transportTrace(format string, args ...interface{}) {
	fmt.Printf("[transport-session] "+format+"\n", args...)
}
