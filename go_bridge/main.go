package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
)

func getPort() int {
	// MT_GO_BRIDGE_PORT 专用端口，与 node_bridge 的 MT_NODE_HTTP_BRIDGE_PORT(19090) 隔离
	v := strings.TrimSpace(os.Getenv("MT_GO_BRIDGE_PORT"))
	if v == "" {
		v = "19091"
	}
	n, err := strconv.Atoi(v)
	if err != nil || n < 1 || n > 65535 {
		return 19091
	}
	return n
}

func readBody(r *http.Request) ([]byte, error) {
	defer r.Body.Close()
	return io.ReadAll(r.Body)
}

func writeJSON(w http.ResponseWriter, v interface{}) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	json.NewEncoder(w).Encode(v)
}

func writeNDJSON(enc *json.Encoder, flusher http.Flusher, mu *sync.Mutex, v interface{}) {
	mu.Lock()
	defer mu.Unlock()
	_ = enc.Encode(v)
	flusher.Flush()
}

func handleBatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "read body: " + err.Error()})
		return
	}
	var req BatchRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
		return
	}
	if len(req.Tasks) == 0 {
		writeJSON(w, BatchResponse{OK: true, Results: []interface{}{}})
		return
	}
	writeJSON(w, execBatch(req))
}

func handleBatchStream(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		w.WriteHeader(http.StatusInternalServerError)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "streaming unsupported"})
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "read body: " + err.Error()})
		return
	}
	var req BatchRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
		return
	}

	w.Header().Set("Content-Type", "application/x-ndjson; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache, no-transform")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	enc := json.NewEncoder(w)
	var mu sync.Mutex
	emit := func(v map[string]interface{}) {
		writeNDJSON(enc, flusher, &mu, v)
	}
	if len(req.Tasks) == 0 {
		emit(map[string]interface{}{"type": "done", "ok": true, "total": 0})
		return
	}
	execBatchStream(req, emit)
}

func handleRushLoop(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "read body: " + err.Error()})
		return
	}
	var req RushLoopRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
		return
	}
	if len(req.Attempts) == 0 {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "attempts required", "rushLoop": true})
		return
	}
	// 默认值
	if req.RushRounds < 1 {
		req.RushRounds = 1
	}
	var resp RushLoopResponse
	if req.FireAndForget {
		resp = execRushLoopFireAndForget(req)
	} else {
		resp = execRushLoopSerial(req)
	}
	writeJSON(w, resp)
}

func handleRushShot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "read body: " + err.Error()})
		return
	}
	var req RushShotRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
		return
	}
	if strings.TrimSpace(req.Attempt.URL) == "" {
		writeJSON(w, map[string]interface{}{"ok": false, "error": "attempt.url required", "rushShot": true})
		return
	}
	writeJSON(w, execRushShot(req))
}

func handleRushAsyncStart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "read body: " + err.Error()})
		return
	}
	var req AsyncRushStartRequest
	if len(body) > 0 {
		if err := json.Unmarshal(body, &req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
			return
		}
	}
	j := startAsyncRushJob(req.JobID)
	writeJSON(w, map[string]interface{}{"ok": true, "jobId": j.id})
}

func handleRushAsyncShot(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "read body: " + err.Error()})
		return
	}
	var req AsyncRushShotRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
		return
	}
	accepted, inFlight, err := submitAsyncRushShot(req)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error(), "jobId": req.JobID})
		return
	}
	writeJSON(w, map[string]interface{}{"ok": true, "accepted": accepted, "inFlight": inFlight, "jobId": req.JobID})
}

func handleRushAsyncStop(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "read body: " + err.Error()})
		return
	}
	var req AsyncRushStopRequest
	if len(body) > 0 {
		if err := json.Unmarshal(body, &req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
			return
		}
	}
	j, ok := getAsyncRushJob(req.JobID)
	if !ok {
		writeJSON(w, map[string]interface{}{"ok": true, "jobId": req.JobID, "stopped": false, "message": "job not found"})
		return
	}
	if strings.TrimSpace(req.AccountKey) != "" {
		j.stopAccount(req.AccountKey)
		j.emit(asyncRushEvent{Type: "account_stop", OK: true, AccountKey: req.AccountKey})
	} else {
		j.emit(asyncRushEvent{Type: "stopping", OK: true, JobID: j.id})
		j.stopAll()
	}
	writeJSON(w, map[string]interface{}{"ok": true, "jobId": j.id, "stopped": true})
}

func handleRushAsyncEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	jobID := strings.TrimSpace(r.URL.Query().Get("jobId"))
	j, ok := getAsyncRushJob(jobID)
	if !ok {
		w.WriteHeader(http.StatusNotFound)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "job not found", "jobId": jobID})
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		w.WriteHeader(http.StatusInternalServerError)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "streaming unsupported"})
		return
	}
	w.Header().Set("Content-Type", "application/x-ndjson; charset=utf-8")
	w.Header().Set("Cache-Control", "no-cache, no-transform")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	enc := json.NewEncoder(w)
	var mu sync.Mutex
	writeNDJSON(enc, flusher, &mu, map[string]interface{}{"type": "events_start", "ok": true, "jobId": j.id})
	for {
		select {
		case ev, ok := <-j.events:
			if !ok {
				writeNDJSON(enc, flusher, &mu, map[string]interface{}{"type": "done", "ok": true, "jobId": j.id})
				return
			}
			writeNDJSON(enc, flusher, &mu, ev)
		case <-r.Context().Done():
			return
		}
	}
}

func handleReq(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	var req SingleReqRequest
	if err := json.Unmarshal(body, &req); err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
		return
	}
	writeJSON(w, execSingleReq(req))
}

func handlePrewarm(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, map[string]interface{}{"ok": true, "message": "go bridge ready"})
}

func handleHealth(w http.ResponseWriter, r *http.Request) {
	resp := map[string]interface{}{"ok": true, "bridge": "go", "mode": tlsTransportModeName(), "tls": tlsClientProfileName(), "asyncRush": true}
	if shouldUseReqrioTransport() {
		resp["tls"] = reqrioRandomProfileName
		resp["reqrio"] = reqrioSummary()
	}
	writeJSON(w, resp)
}

func handleTLSClientProfiles(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, tlsClientProfilesSummary())
}

func handleTLSClientPreview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		w.WriteHeader(http.StatusMethodNotAllowed)
		return
	}
	body, err := readBody(r)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	var att Attempt
	if err := json.Unmarshal(body, &att); err != nil {
		var req SingleReqRequest
		if err2 := json.Unmarshal(body, &req); err2 != nil {
			w.WriteHeader(http.StatusBadRequest)
			writeJSON(w, map[string]interface{}{"ok": false, "error": "invalid JSON: " + err.Error()})
			return
		}
		att = Attempt{
			Method:         req.Method,
			URL:            req.URL,
			Headers:        req.Headers,
			HeadersOrdered: req.HeadersOrdered,
			Body:           req.Body,
			BodyB64:        req.BodyB64,
			Host:           req.Host,
			ContentLength:  req.ContentLength,
			ProxyURL:       req.ProxyURL,
			TimeoutMs:      req.TimeoutMs,
			AccountKey:     req.AccountKey,
			LaneID:         req.LaneID,
		}
	}
	preview, err := tlsClientAttemptPreview(att)
	if err != nil {
		w.WriteHeader(http.StatusBadRequest)
		writeJSON(w, map[string]interface{}{"ok": false, "error": err.Error()})
		return
	}
	preview["ok"] = true
	writeJSON(w, preview)
}

func main() {
	port := getPort()
	mux := http.NewServeMux()
	mux.HandleFunc("/rush_loop", handleRushLoop)
	mux.HandleFunc("/rush_shot", handleRushShot)
	mux.HandleFunc("/rush_async_start", handleRushAsyncStart)
	mux.HandleFunc("/rush_async_shot", handleRushAsyncShot)
	mux.HandleFunc("/rush_async_stop", handleRushAsyncStop)
	mux.HandleFunc("/rush_async_events", handleRushAsyncEvents)
	mux.HandleFunc("/batch", handleBatch)
	mux.HandleFunc("/batch_stream", handleBatchStream)
	mux.HandleFunc("/req", handleReq)
	mux.HandleFunc("/prewarm", handlePrewarm)
	mux.HandleFunc("/health", handleHealth)
	mux.HandleFunc("/tls_client_profiles", handleTLSClientProfiles)
	mux.HandleFunc("/tls_client_preview", handleTLSClientPreview)
	addr := fmt.Sprintf("127.0.0.1:%d", port)
	mode := tlsTransportModeName()
	profile := tlsClientProfileName()
	if shouldUseReqrioTransport() {
		profile = reqrioRandomProfileName
	}
	fmt.Printf("[go-rush-bridge] listening on http://%s/rush_loop /rush_shot /rush_async_* /batch /batch_stream /req /prewarm /health | %s %s\n", addr, profile, mode)
	if err := http.ListenAndServe(addr, mux); err != nil {
		fmt.Fprintf(os.Stderr, "[go-rush-bridge] fatal: %v\n", err)
		os.Exit(1)
	}
}
