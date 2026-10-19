package main

import (
	"os"
	"path/filepath"
	"testing"
)

// 半行必须只算一次。
//
// 真实事故：sourceAgg 把 `off` 回退到「最后一行开头」，而末尾那半行又存进了
// partial，于是下次读会把半行再读一遍、拼成变形行（`par`+`partial…`）。
// 表现是 /api/feed/sources 的条数慢慢漂移（实测私聊少 3 条、网页多 2 条），
// 而 Python 那边的全量口径是对的 —— 只有重启边缘层才对齐。
func TestSourceAggPartialLine(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "messages.jsonl")
	line1 := `{"chat_id":"private:1","role":"user","text":"a"}` + "\n"
	line2 := `{"chat_id":"private:1","role":"user","text":"b"}`
	if err := os.WriteFile(path, []byte(line1+line2[:20]), 0o644); err != nil {
		t.Fatal(err)
	}

	agg := newSourceAgg(path)
	if got := countOf(agg.labels(), "private:1"); got != 1 {
		t.Fatalf("半行还没写完时应当只算 1 条，拿到 %d", got)
	}

	// 把第二行补完，再加一行：总体 3 条
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := f.WriteString(line2[20:] + "\n" + line1); err != nil {
		t.Fatal(err)
	}
	f.Close()

	if got := countOf(agg.labels(), "private:1"); got != 3 {
		t.Fatalf("补完之后应当是 3 条，拿到 %d（半行被重复读/变形）", got)
	}

	// 没有新字节时重复调用必须幂等
	if got := countOf(agg.labels(), "private:1"); got != 3 {
		t.Fatalf("重复调用不该变，拿到 %d", got)
	}
}

// 文件被截断/轮换后要重头算。
func TestSourceAggTruncate(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "messages.jsonl")
	one := `{"chat_id":"private:1","role":"user","text":"a"}` + "\n"
	if err := os.WriteFile(path, []byte(one+one+one), 0o644); err != nil {
		t.Fatal(err)
	}
	agg := newSourceAgg(path)
	if got := countOf(agg.labels(), "private:1"); got != 3 {
		t.Fatalf("初始应当 3 条，拿到 %d", got)
	}
	if err := os.WriteFile(path, []byte(one), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := countOf(agg.labels(), "private:1"); got != 1 {
		t.Fatalf("截断后应当重算成 1 条，拿到 %d", got)
	}
}

// 从「QQ 私聊 · 1（3 条）」里只取括号里那个数（标签里还有 cid 自己的数字）。
func countOf(rows []map[string]string, cid string) int64 {
	for _, r := range rows {
		if r["value"] != cid {
			continue
		}
		label := []rune(r["label"])
		start, end := -1, -1
		for i, c := range label {
			if c == '（' {
				start = i + 1
			}
			if c == ' ' && start > 0 && end < 0 {
				end = i
			}
		}
		if start < 0 || end < 0 {
			return -1
		}
		var n int64
		for _, c := range label[start:end] {
			if c < '0' || c > '9' {
				return -1
			}
			n = n*10 + int64(c-'0')
		}
		return n
	}
	return 0
}
