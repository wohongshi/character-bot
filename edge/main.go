// klee-edge：面板的静态资源边缘层。
//
// Python 那套 ThreadingHTTPServer 每发一个静态文件都要读盘 + 算 sha1 + 写 socket，
// 在 RK3399 上约 2ms/请求（20 个并发请求首屏就排成一条队）。这里把静态资源
// 常驻内存（原始 + 预压缩 + ETag），其余路径原样反代给 Python。
//
// 只依赖标准库，`go build` 即可。
package main

import (
	"compress/gzip"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

type asset struct {
	mtime  time.Time
	size   int64
	raw    []byte
	gz     []byte
	tag    string
	ctype  string
	maxAge int
}

var (
	root    string
	index   string
	cacheMu sync.RWMutex
	cache   = map[string]*asset{}
)

func mimeOf(p string) (string, int) {
	switch strings.ToLower(filepath.Ext(p)) {
	case ".html":
		return "text/html; charset=utf-8", 0
	case ".css":
		return "text/css; charset=utf-8", 86400
	case ".js":
		return "text/javascript; charset=utf-8", 86400
	case ".json", ".webmanifest":
		return "application/manifest+json", 86400
	case ".webp":
		return "image/webp", 604800
	case ".jpg", ".jpeg":
		return "image/jpeg", 604800
	case ".png":
		return "image/png", 604800
	case ".svg":
		return "image/svg+xml", 86400
	case ".ttf":
		return "font/ttf", 604800
	case ".woff2":
		return "font/woff2", 604800
	case ".ico":
		return "image/x-icon", 604800
	}
	return "application/octet-stream", 86400
}

func compressible(ctype string) bool {
	return strings.HasPrefix(ctype, "text/") ||
		strings.Contains(ctype, "javascript") ||
		strings.Contains(ctype, "json") ||
		strings.Contains(ctype, "svg")
}

// load 读取并缓存一个文件；mtime/size 没变就直接用内存里的。
func load(rel string) (*asset, error) {
	full := filepath.Join(root, filepath.FromSlash(rel))
	st, err := os.Stat(full)
	if err != nil || st.IsDir() {
		if err == nil {
			err = os.ErrNotExist
		}
		return nil, err
	}
	cacheMu.RLock()
	hit := cache[rel]
	cacheMu.RUnlock()
	if hit != nil && hit.size == st.Size() && hit.mtime.Equal(st.ModTime()) {
		return hit, nil
	}
	raw, err := os.ReadFile(full)
	if err != nil {
		return nil, err
	}
	ctype, maxAge := mimeOf(full)
	a := &asset{mtime: st.ModTime(), size: st.Size(), raw: raw, ctype: ctype, maxAge: maxAge}
	a.tag = fmt.Sprintf(`"%x-%x"`, st.ModTime().UnixNano(), len(raw))
	if compressible(ctype) && len(raw) > 1024 {
		var b strings.Builder
		zw, _ := gzip.NewWriterLevel(&b, 5)
		if _, err := zw.Write(raw); err == nil && zw.Close() == nil {
			a.gz = []byte(b.String())
		}
	}
	cacheMu.Lock()
	if len(cache) > 512 { // 角色图多了也别无限长
		for k := range cache {
			delete(cache, k)
			if len(cache) < 384 {
				break
			}
		}
	}
	cache[rel] = a
	cacheMu.Unlock()
	return a, nil
}

func serveAsset(w http.ResponseWriter, r *http.Request, rel string) bool {
	a, err := load(rel)
	if err != nil {
		return false
	}
	h := w.Header()
	h.Set("ETag", a.tag)
	h.Set("Content-Type", a.ctype)
	h.Set("Vary", "Accept-Encoding")
	if a.maxAge > 0 {
		h.Set("Cache-Control", fmt.Sprintf("public, max-age=%d", a.maxAge))
	} else {
		h.Set("Cache-Control", "no-cache, must-revalidate")
	}
	h.Set("Last-Modified", a.mtime.UTC().Format(http.TimeFormat))
	h.Set("X-Edge", "go")
	if r.Header.Get("If-None-Match") == a.tag {
		w.WriteHeader(http.StatusNotModified)
		return true
	}
	body := a.raw
	if a.gz != nil && strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		body = a.gz
		h.Set("Content-Encoding", "gzip")
	}
	h.Set("Content-Length", fmt.Sprint(len(body)))
	if r.Method == http.MethodHead {
		w.WriteHeader(http.StatusOK)
		return true
	}
	w.WriteHeader(http.StatusOK)
	w.Write(body)
	return true
}

func main() {
	listen := flag.String("listen", ":8796", "listen address")
	upstream := flag.String("upstream", "http://127.0.0.1:8798", "python backend")
	staticDir := flag.String("root", ".", "repo root (serves <root>/static/*)")
	flag.Parse()

	root = *staticDir
	index = "static/index.html"

	u, err := url.Parse(*upstream)
	if err != nil {
		log.Fatalf("bad upstream: %v", err)
	}
	proxy := httputil.NewSingleHostReverseProxy(u)
	proxy.Transport = &http.Transport{
		MaxIdleConns:        64,
		MaxIdleConnsPerHost: 32,
		IdleConnTimeout:     60 * time.Second,
		DialContext:         (&net.Dialer{Timeout: 3 * time.Second}).DialContext,
		// 让客户端的 Accept-Encoding 原样透传，避免 Go 自己加 gzip 又解压
		DisableCompression: true,
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, e error) {
		w.Header().Set("X-Edge", "go")
		http.Error(w, "backend unavailable: "+e.Error(), http.StatusBadGateway)
	}
	proxy.ModifyResponse = func(resp *http.Response) error {
		resp.Header.Set("X-Edge", "go")
		return nil
	}

	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet || r.Method == http.MethodHead {
			switch {
			case r.URL.Path == "/" || r.URL.Path == "/index.html":
				if serveAsset(w, r, index) {
					return
				}
			case strings.HasPrefix(r.URL.Path, "/static/"):
				rel := path.Clean(strings.TrimPrefix(r.URL.Path, "/static/"))
				if rel != "." && !strings.HasPrefix(rel, "..") {
					if serveAsset(w, r, "static/"+rel) {
						return
					}
				}
				http.NotFound(w, r)
				return
			}
		}
		proxy.ServeHTTP(w, r)
	})

	srv := &http.Server{
		Addr:              *listen,
		Handler:           handler,
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       65 * time.Second,
	}
	log.Printf("[klee-edge] %s -> %s (static=%s)", *listen, *upstream, root)
	log.Fatal(srv.ListenAndServe())
}
