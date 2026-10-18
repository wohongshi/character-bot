// 外部插件示例（Rust，单文件、零第三方 crate）：.统计 —— 字符/词/行/字节。
// 编译：rustc -O -o textstat.bin textstat.rs
// 为什么用它：这个命令随消息长度线性变慢，原生二进制没有解释器开销，
// 常驻进程本身只占 ~2MB（Java 是 ~40MB，见 docs/POLYGLOT.md 的实测表）。
use std::io::{self, BufRead, Write};

/// 从一行 JSON 里取顶层字段。只处理本项目协议用到的形状：
/// 字符串值、数字值；嵌套对象（ctx）跳过不管。够用且没有依赖。
fn field(line: &str, key: &str) -> Option<String> {
    let pat = format!("\"{}\"", key);
    let start = line.find(&pat)? + pat.len();
    let rest = &line[start..];
    let colon = rest.find(':')?;
    let mut it = rest[colon + 1..].chars().peekable();

    while matches!(it.peek(), Some(c) if c.is_whitespace()) {
        it.next();
    }
    if it.peek() == Some(&'"') {
        it.next();
        let mut out = String::new();
        while let Some(c) = it.next() {
            match c {
                '"' => return Some(out),
                '\\' => match it.next() {
                    Some('n') => out.push('\n'),
                    Some('t') => out.push('\t'),
                    Some('r') => out.push('\r'),
                    Some('u') => {
                        // \uXXXX：本项目协议里是控制字符，直接跳过四位
                        for _ in 0..4 {
                            it.next();
                        }
                    }
                    Some(c) => out.push(c),
                    None => break,
                },
                c => out.push(c),
            }
        }
        return Some(out);
    }
    let mut num = String::new();
    while let Some(c) = it.peek() {
        if *c == ',' || *c == '}' || c.is_whitespace() {
            break;
        }
        num.push(*c);
        it.next();
    }
    Some(num)
}

fn json_str(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    o.push('"');
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            '\r' => o.push_str("\\r"),
            '\t' => o.push_str("\\t"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
    o
}

fn main() {
    let stdin = io::stdin();
    let mut out = io::stdout();
    let mut ready = false;

    for line in stdin.lock().lines() {
        let line = match line {
            Ok(l) => l,
            Err(_) => break,
        };
        let op = field(&line, "op").unwrap_or_default();
        match op.as_str() {
            "hello" => {
                if !ready {
                    let meta = r#"{"ok":true,"name":"textstat","title":"文本统计","version":"1.0","commands":[{"name":"统计","aliases":["字数","count"],"help":".统计 <文本> —— 字符/词/行/字节（Rust 原生）"}]}"#;
                    let _ = writeln!(out, "{}", meta);
                    let _ = out.flush();
                    ready = true;
                }
            }
            "bye" => break,
            "call" => {
                let id = field(&line, "id").unwrap_or_else(|| "0".into());
                let text = field(&line, "text").unwrap_or_default();
                let chars = text.chars().count();
                let cjk = text
                    .chars()
                    .filter(|c| ('\u{4E00}'..='\u{9FFF}').contains(c))
                    .count();
                let words = text.split_whitespace().count();
                let lines = text.lines().count().max(1);
                let reply = format!(
                    "📐 {} 字符（汉字 {}）· {} 词 · {} 行 · {} 字节",
                    chars,
                    cjk,
                    words,
                    lines,
                    text.len()
                );
                let _ = writeln!(
                    out,
                    "{{\"id\":{},\"ok\":true,\"reply\":{}}}",
                    id,
                    json_str(&reply)
                );
                let _ = out.flush();
            }
            _ => {}
        }
    }
}
