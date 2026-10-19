// 多关键词扫描 + 文本归一化（Rust，零 crate，编成 cdylib 由 Python ctypes 直调）
//
// 为什么这个要 Rust：屏蔽词检查是**每条入站消息**都要跑的钩子。
//
// 第一版用的是「按首字节分桶 + 逐词比较」，实测比 Python 的 `w in text` 还慢
// （591µs vs 268µs / 一次 200 词 × 600 字）—— 因为中文词的 UTF-8 首字节高度重复，
// 一个桶里挤二三十个词，每个位置都要全比一遍。
// 现在改成 **字节 trie**：只在根节点出现的字节上开始下潜，深度 ≤ 最长词长，
// 实测 20µs 量级（快 30 倍）。
//
// 输入约定（C ABI）：
//   words : 多个词用 \0 连接的一段 UTF-8
//   text  : 待扫描文本
//   out   : 命中词的序号（按 words 里的顺序），最多 out_cap 个
// 返回：命中个数（去重、升序）
//
// 大小写折叠只做 ASCII（中文没有大小写，够用）。

use std::collections::HashMap;
use std::slice;
use std::sync::Mutex;

/// 词表缓存：同一次运行里词表基本不变（插件设置），每次调用重建 trie 是纯粹浪费。
/// 键 = 打包字节日志的 FNV-1a 哈希；最多留 8 份，够所有插件用。
static CACHE: Mutex<Option<HashMap<u64, Trie>>> = Mutex::new(None);

fn fnv1a(bytes: &[u8]) -> u64 {
    let mut h: u64 = 0xcbf29ce484222325;
    for &b in bytes {
        h ^= b as u64;
        h = h.wrapping_mul(0x100000001b3);
    }
    h ^ (bytes.len() as u64)
}

struct Node {
    /// 子节点：(字节, 节点下标)。按字节升序，便于二分。
    children: Vec<(u8, u32)>,
    /// 失配指针（Aho-Corasick）
    fail: u32,
    /// >= 0 表示这个节点是某个词的结尾
    word: i32,
    /// 沿 fail 链最近的一个「词尾」节点（没有就是 -1）
    out_link: i32,
    /// 这个位置有没有任何输出（自己或后缀链上）—— 没有就不用进收集循环
    has_out: bool,
}

struct Trie {
    nodes: Vec<Node>,
}

impl Trie {
    fn new() -> Self {
        Trie {
            nodes: vec![Node { children: Vec::new(), fail: 0, word: -1, out_link: -1, has_out: false }],
        }
    }

    fn insert(&mut self, word: &[u8], idx: i32) {
        let mut cur = 0usize;
        for &b in word {
            let b = fold(b);
            let pos = self.nodes[cur].children.iter().position(|&(c, _)| c == b);
            cur = match pos {
                Some(i) => self.nodes[cur].children[i].1 as usize,
                None => {
                    let next = self.nodes.len();
                    self.nodes.push(Node { children: Vec::new(), fail: 0, word: -1,
                                            out_link: -1, has_out: false });
                    let mut kids = std::mem::take(&mut self.nodes[cur].children);
                    kids.push((b, next as u32));
                    kids.sort_unstable_by_key(|&(c, _)| c);
                    self.nodes[cur].children = kids;
                    next
                }
            };
        }
        self.nodes[cur].word = idx;
    }

    /// BFS 建失配链 —— 建一次，之后扫描是 O(文本长度)，和词表大小无关。
    fn build(&mut self) {
        let mut queue: std::collections::VecDeque<usize> = std::collections::VecDeque::new();
        let roots: Vec<usize> = self.nodes[0].children.iter().map(|&(_, i)| i as usize).collect();
        for r in roots {
            self.nodes[r].fail = 0;
            queue.push_back(r);
        }
        while let Some(u) = queue.pop_front() {
            // 先把 u 的 out_link 定下来
            let f = self.nodes[u].fail as usize;
            self.nodes[u].out_link = if self.nodes[f].word >= 0 {
                f as i32
            } else {
                self.nodes[f].out_link
            };
            self.nodes[u].has_out = self.nodes[u].word >= 0 || self.nodes[u].out_link >= 0;
            let kids: Vec<(u8, usize)> =
                self.nodes[u].children.iter().map(|&(c, i)| (c, i as usize)).collect();
            for (ch, v) in kids {
                let mut f = self.nodes[u].fail as usize;
                loop {
                    if let Some(nx) = self.child(f, ch) {
                        if nx != v {
                            self.nodes[v].fail = nx as u32;
                            break;
                        }
                    }
                    if f == 0 {
                        self.nodes[v].fail = 0;
                        break;
                    }
                    f = self.nodes[f].fail as usize;
                }
                queue.push_back(v);
            }
        }
    }

    #[inline(always)]
    fn child(&self, node: usize, b: u8) -> Option<usize> {
        let kids = &self.nodes[node].children;
        if kids.len() <= 6 {
            for &(c, i) in kids {
                if c == b {
                    return Some(i as usize);
                }
            }
            return None;
        }
        kids.binary_search_by_key(&b, |&(c, _)| c)
            .ok()
            .map(|i| kids[i].1 as usize)
    }

    /// 从当前节点出发吃一个字节，返回新节点（带失配回退）
    fn step(&self, mut node: usize, b: u8) -> usize {
        loop {
            if let Some(nx) = self.child(node, b) {
                return nx;
            }
            if node == 0 {
                return 0;
            }
            node = self.nodes[node].fail as usize;
        }
    }
}

fn fold(b: u8) -> u8 {
    if b.is_ascii_uppercase() { b + 32 } else { b }
}

#[no_mangle]
pub extern "C" fn klee_scan(
    text_ptr: *const u8, text_len: usize,
    words_ptr: *const u8, words_len: usize,
    out_ptr: *mut i32, out_cap: usize,
) -> i32 {
    if text_ptr.is_null() || words_ptr.is_null() || out_ptr.is_null() {
        return -1;
    }
    let text = unsafe { slice::from_raw_parts(text_ptr, text_len) };
    let packed = unsafe { slice::from_raw_parts(words_ptr, words_len) };
    let out = unsafe { slice::from_raw_parts_mut(out_ptr, out_cap) };
    if text.is_empty() || packed.is_empty() || out.is_empty() {
        return 0;
    }

    let key = fnv1a(packed);
    let mut guard = match CACHE.lock() {
        Ok(g) => g,
        Err(p) => p.into_inner(),          // 锁中毒也继续用（这里没有不变量会被破坏）
    };
    let cache = guard.get_or_insert_with(HashMap::new);
    if !cache.contains_key(&key) {
        let mut trie = Trie::new();
        let mut idx = 0i32;
        let mut start = 0usize;
        for i in 0..=packed.len() {
            if i == packed.len() || packed[i] == 0 {
                let w = &packed[start..i];
                if !w.is_empty() {
                    trie.insert(w, idx);
                    idx += 1;
                }
                start = i + 1;
            }
        }
        if idx == 0 {
            return 0;
        }
        trie.build();                       // 建一次失配链，之后扫描与词表大小无关
        if cache.len() >= 8 {
            cache.clear();                  // 简单粗暴：插件换词表就重建，代价可忽略
        }
        cache.insert(key, trie);
    }
    let trie = &cache[&key];
    let mut hits: Vec<i32> = Vec::new();
    let mut node = 0usize;
    let guard_max = trie.nodes.len() + 4;
    for &raw in text {
        node = trie.step(node, fold(raw));
        // 绝大多数位置没有任何输出 —— 一次布尔判断就走人
        if !trie.nodes[node].has_out {
            continue;
        }
        // 当前节点 + 沿 out_link 的所有词尾（一个位置可能同时结束多个词）
        let mut cur = node;
        let mut guard = 0usize;
        loop {
            let idx = trie.nodes[cur].word;
            if idx >= 0 && !hits.contains(&idx) {
                hits.push(idx);
            }
            let next = trie.nodes[cur].out_link;
            if next < 0 {
                break;
            }
            cur = next as usize;
            guard += 1;
            if guard > guard_max {
                break;                      // 理论上不会发生，防死循环
            }
        }
    }

    hits.sort_unstable();
    let take = hits.len().min(out.len());
    for k in 0..take {
        out[k] = hits[k];
    }
    take as i32
}

/// 全角 ASCII → 半角、去零宽、连续空白折叠成一个空格。
/// 群聊里复制来的消息经常夹全角标点和零宽字符，比对前先归一化。
#[no_mangle]
pub extern "C" fn klee_normalize(
    src_ptr: *const u8, src_len: usize,
    dst_ptr: *mut u8, dst_cap: usize,
) -> i32 {
    if src_ptr.is_null() || dst_ptr.is_null() {
        return -1;
    }
    let src = unsafe { slice::from_raw_parts(src_ptr, src_len) };
    let dst = unsafe { slice::from_raw_parts_mut(dst_ptr, dst_cap) };
    let text = match std::str::from_utf8(src) {
        Ok(t) => t,
        Err(_) => return -2,          // 坏 UTF-8 交给调用方回落
    };
    let mut tmp = String::with_capacity(src.len());
    let mut prev_space = false;
    for ch in text.chars() {
        let cp = ch as u32;
        // 全角 ASCII（！..～）→ 半角
        let ch = if (0xFF01..=0xFF5E).contains(&cp) {
            char::from_u32(cp - 0xFEE0).unwrap_or(ch)
        } else {
            ch
        };
        if matches!(cp, 0x200B | 0x200C | 0x200D | 0x200F | 0xFEFF) {
            continue;
        }
        if ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n' {
            if !prev_space {
                tmp.push(' ');
                prev_space = true;
            }
            continue;
        }
        prev_space = false;
        tmp.push(ch);
    }
    let trimmed = tmp.trim_end_matches(' ');
    let b = trimmed.as_bytes();
    let n = b.len().min(dst.len());
    dst[..n].copy_from_slice(&b[..n]);
    n as i32
}

#[no_mangle]
pub extern "C" fn klee_text_version() -> u32 {
    110 // 1.1（trie 版）
}
