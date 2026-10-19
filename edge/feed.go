// klee-feed：把「消息流」的两个读接口搬进 Go。
//
// 为什么这两个：它们是面板上唯一「成本随数据量线性增长」的接口。Python 侧
// `_read_jsonl` 为了不 O(n) 已经退化成「只读最后 4MB」——文件涨到 15MB 时，
// 每打开一次消息页就要解析 4MB JSON（约 1 秒），而且 `_feed_sources` 的
// 「（N 条）」会悄悄变成只统计尾部，数字是错的。
//
// Go 这边做两件 Python 不好做的事：
//   1. 从文件末尾按 64KB 起步、逐级放大，读够 limit 条就停 —— 常见情况只读 64KB
//   2. 来源统计做增量聚合：记住上次读到的字节偏移，每次只吃新增的那些行，
//      既不重复解析，计数又是全量准确的
//
// 任何一步出错（文件没了 / 解析不了 / 权限）都返回 false，由上层原样反代给
// Python，行为与改动前完全一致 —— 这层永远是「加速器」，不是「唯一实现」。
package main

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// ---------- JSON 容错类型（Python 侧一律 str()/float()，这里对齐） ----------

type flexStr string

func (f *flexStr) UnmarshalJSON(b []byte) error {
	if len(b) == 0 || string(b) == "null" {
		*f = ""
		return nil
	}
	if b[0] == '"' {
		var s string
		if err := json.Unmarshal(b, &s); err != nil {
			return err
		}
		*f = flexStr(s)
		return nil
	}
	*f = flexStr(strings.TrimSpace(string(b)))
	return nil
}

type flexFloat float64

func (f *flexFloat) UnmarshalJSON(b []byte) error {
	s := strings.TrimSpace(string(b))
	if s == "" || s == "null" {
		*f = 0
		return nil
	}
	s = strings.Trim(s, `"`)
	v, err := strconv.ParseFloat(s, 64)
	if err != nil {
		*f = 0
		return nil
	}
	*f = flexFloat(v)
	return nil
}

type feedRec struct {
	Ts     flexFloat `json:"ts"`
	Role   flexStr   `json:"role"`
	Text   flexStr   `json:"text"`
	ChatID flexStr   `json:"chat_id"`
}

// sourceLabel 与 Python `_feed` 的 where 字段逐字一致
func sourceLabel(cid string) string {
	switch {
	case strings.HasPrefix(cid, "group:"):
		parts := strings.Split(cid, ":")
		return "QQ 群 · " + parts[len(parts)-1]
	case strings.HasPrefix(cid, "private:"):
		parts := strings.Split(cid, ":")
		return "QQ 私聊 · " + parts[len(parts)-1]
	case strings.HasPrefix(cid, "web:"):
		return "网页 · " + cid[4:]
	}
	return "其他"
}

func clipRunes(s string, n int) string {
	if n <= 0 {
		return ""
	}
	rs := []rune(s)
	if len(rs) <= n {
		return s
	}
	return string(rs[:n])
}

// ---------- /api/feed ----------

const maxWindow = int64(4) << 20 // 与 Python 的 JSONL_TAIL_BYTES 对齐

// readTail 读文件最后 want 字节（want<=0 表示整份），返回 (数据, 起始偏移)。
func readTail(path string, want int64) ([]byte, int64, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, 0, err
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return nil, 0, err
	}
	size := st.Size()
	start := int64(0)
	if want > 0 && want < size {
		start = size - want
	}
	buf := make([]byte, size-start)
	if _, err := f.ReadAt(buf, start); err != nil && err != io.EOF {
		return nil, 0, err
	}
	return buf, start, nil
}

// collectNewestFirst 从尾部往前解析，凑够 limit 条就停。
func collectNewestFirst(data []byte, dropFirst bool, limit int, src string) []map[string]any {
	lines := bytes.Split(data, []byte("\n"))
	if dropFirst && len(lines) > 0 {
		lines = lines[1:] // 第一条大概率被截断
	}
	out := make([]map[string]any, 0, limit)
	for i := len(lines) - 1; i >= 0 && len(out) < limit; i-- {
		line := bytes.TrimSpace(lines[i])
		if len(line) == 0 || line[0] != '{' {
			continue
		}
		var r feedRec
		if err := json.Unmarshal(line, &r); err != nil {
			continue
		}
		role := string(r.Role)
		if role != "user" && role != "assistant" {
			continue
		}
		text := string(r.Text)
		if text == "" {
			continue
		}
		cid := string(r.ChatID)
		if src == "web" {
			if !strings.HasPrefix(cid, "web:") {
				continue
			}
		} else if src != "" && cid != src {
			continue
		}
		out = append(out, map[string]any{
			"ts":    float64(r.Ts),
			"where": sourceLabel(cid),
			"role":  role,
			"text":  clipRunes(text, 800),
		})
	}
	return out
}

func feedMessages(path string, limit int, src string) ([]map[string]any, bool) {
	if _, err := os.Stat(path); err != nil {
		return nil, false
	}
	// 64KB → 256KB → 1MB → 4MB → 整份；够用就停
	for _, w := range []int64{64 << 10, 256 << 10, 1 << 20, maxWindow, 0} {
		data, start, err := readTail(path, w)
		if err != nil {
			return nil, false
		}
		if start == 0 && len(data) == 0 { // 空文件
			return []map[string]any{}, true
		}
		got := collectNewestFirst(data, start > 0, limit, src)
		// 够数、或者已经读到文件头、或者是最后一档（整份）→ 收工
		if len(got) >= limit || start == 0 || w == 0 {
			return got, true
		}
	}
	return nil, false
}

func serveFeed(w http.ResponseWriter, r *http.Request, path string) bool {
	q := r.URL.Query()
	limit := 80
	if v := q.Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			limit = n
		}
	}
	if limit < 10 {
		limit = 10
	}
	if limit > 200 {
		limit = 200
	}
	src := q.Get("src")
	msgs, ok := feedMessages(path, limit, src)
	if !ok {
		return false
	}
	writeJSON(w, map[string]any{"messages": msgs, "src": src})
	return true
}

// ---------- /api/feed/sources ----------

// sourceAgg 增量聚合：只吃新增字节，计数是全量的。
type sourceAgg struct {
	mu      sync.Mutex
	path    string
	off     int64
	partial []byte
	counts  map[string]int64
	order   []string
}

func newSourceAgg(path string) *sourceAgg {
	return &sourceAgg{path: path, counts: map[string]int64{}}
}

func (a *sourceAgg) reset() {
	a.off = 0
	a.partial = nil
	a.counts = map[string]int64{}
	a.order = nil
}

func (a *sourceAgg) update() bool {
	st, err := os.Stat(a.path)
	if err != nil {
		return false
	}
	size := st.Size()
	if size < a.off { // 文件被清空或轮换
		a.reset()
	}
	if size == a.off {
		return true
	}
	f, err := os.Open(a.path)
	if err != nil {
		return false
	}
	defer f.Close()
	if _, err := f.Seek(a.off, io.SeekStart); err != nil {
		return false
	}
	fresh := make([]byte, size-a.off)
	if _, err := io.ReadFull(f, fresh); err != nil && err != io.ErrUnexpectedEOF {
		return false
	}
	buf := append(append([]byte{}, a.partial...), fresh...)
	lines := bytes.Split(buf, []byte("\n"))
	var rest []byte
	if len(lines) > 0 {
		rest = lines[len(lines)-1] // 末尾没换行的那半行留到下次
		lines = lines[:len(lines)-1]
	}
	for _, ln := range lines {
		ln = bytes.TrimSpace(ln)
		if len(ln) == 0 || ln[0] != '{' {
			continue
		}
		var r feedRec
		if err := json.Unmarshal(ln, &r); err != nil {
			continue
		}
		role := string(r.Role)
		if role != "user" && role != "assistant" || string(r.Text) == "" {
			continue
		}
		cid := string(r.ChatID)
		if _, seen := a.counts[cid]; !seen {
			a.order = append(a.order, cid)
		}
		a.counts[cid]++
	}
	// off 是「读到文件的哪个字节了」，**不是** 「最后一行从哪开始」。
	// 末尾那半行已经存进 a.partial，下次会和新增字节拼起来再解析；
	// 如果把 off 回退到 size-len(rest)，下次就会把这半行再读一遍，
	// 拼成 `par` + `partial…` 这种变形的行 —— JSON 解析失败被丢掉，
	// 或者被算两次，来源统计于是慢慢漂移（实测差 3 条私聊 / 多 2 条网页）。
	a.partial = rest
	a.off = size
	return true
}

func (a *sourceAgg) labels() []map[string]string {
	a.mu.Lock()
	defer a.mu.Unlock()
	if !a.update() {
		return nil
	}
	out := []map[string]string{{"value": "", "label": "全部来源"}}
	// 按条数降序；同条数保持首次出现顺序（Python 的 dict 顺序）
	type row struct {
		cid string
		n   int64
	}
	rows := make([]row, 0, len(a.order))
	for _, cid := range a.order {
		rows = append(rows, row{cid, a.counts[cid]})
	}
	sort.SliceStable(rows, func(i, j int) bool { return rows[i].n > rows[j].n })
	var web int64
	for _, x := range rows {
		switch {
		case strings.HasPrefix(x.cid, "group:"):
			parts := strings.Split(x.cid, ":")
			out = append(out, map[string]string{
				"value": x.cid,
				"label": "QQ 群 · " + parts[len(parts)-1] + "（" + strconv.FormatInt(x.n, 10) + " 条）"})
		case strings.HasPrefix(x.cid, "private:"):
			parts := strings.Split(x.cid, ":")
			out = append(out, map[string]string{
				"value": x.cid,
				"label": "QQ 私聊 · " + parts[len(parts)-1] + "（" + strconv.FormatInt(x.n, 10) + " 条）"})
		case strings.HasPrefix(x.cid, "web:"):
			web += x.n
		}
	}
	if web > 0 {
		out = append(out, map[string]string{
			"value": "web", "label": "网页 / 其他（" + strconv.FormatInt(web, 10) + " 条）"})
	}
	return out
}

func serveSources(w http.ResponseWriter, r *http.Request, agg *sourceAgg) bool {
	labels := agg.labels()
	if labels == nil {
		return false
	}
	writeJSON(w, map[string]any{"sources": labels})
	return true
}

// ---------- 公共 ----------

func writeJSON(w http.ResponseWriter, v any) {
	body, err := json.Marshal(v)
	if err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	h := w.Header()
	h.Set("Content-Type", "application/json")
	h.Set("Cache-Control", "no-store") // 与 Python 的敏感 GET 一致
	h.Set("X-Edge", "feed")
	h.Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}

// edgeGuardOK 与 Python `_guard` 同规则；不通过就返回 false，让请求落到 Python
// （那边会给出规范的 403 JSON，不在这里分叉）。
func edgeGuardOK(r *http.Request, token string) bool {
	if r.Header.Get("X-Klee-Studio") != "1" {
		return false
	}
	if token != "" {
		if r.Header.Get("X-Klee-Studio-Token") != token {
			return false
		}
	}
	return true
}

// messagesPath 定位 state/messages.jsonl（跟随 KLEE_STATE_DIR，和 Python 一致）。
func messagesPath(root string) string {
	if d := os.Getenv("KLEE_STATE_DIR"); d != "" {
		return filepath.Join(d, "messages.jsonl")
	}
	return filepath.Join(root, "state", "messages.jsonl")
}
