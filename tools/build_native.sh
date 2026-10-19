#!/usr/bin/env bash
# 编原生扩展（Rust / C / C++）→ bot/native/lib/*.so
#
# 设计：编不出来不算失败。主机上没装 rustc 或 gcc 时，面板照常跑，
# 只是「运行时」卡里那一行显示「未编译」，Python 侧自动回落到纯 Python 实现。
set -u
cd "$(dirname "$0")/.." || exit 1          # → 仓库根

OUT=bot/native/lib
SRC=tools/native
mkdir -p "$OUT"
ok=0; skip=0

export PATH="$HOME/.cargo/bin:$PATH"

say() { printf '  %-8s %s\n' "$1" "$2"; }

# ---------- C：显示宽度 ----------
if command -v gcc >/dev/null 2>&1; then
  if gcc -O3 -fPIC -shared -o "$OUT/libklee_width.so" "$SRC/klee_width.c" 2>/tmp/klee_width.err; then
    say "C" "libklee_width.so $(du -h "$OUT/libklee_width.so" | cut -f1)"; ok=$((ok+1))
  else
    say "C" "编译失败：$(head -2 /tmp/klee_width.err | tr '\n' ' ')"; skip=$((skip+1))
  fi
else
  say "C" "没有 gcc，跳过"; skip=$((skip+1))
fi

# ---------- Rust：多词扫描 ----------
if command -v rustc >/dev/null 2>&1; then
  if rustc -O --crate-type cdylib -C panic=abort -o "$OUT/libklee_text.so" "$SRC/klee_text.rs" \
       >/tmp/klee_text.log 2>&1; then
    say "Rust" "libklee_text.so $(du -h "$OUT/libklee_text.so" | cut -f1)"; ok=$((ok+1))
  else
    say "Rust" "编译失败：$(tail -3 /tmp/klee_text.log | tr '\n' ' ')"; skip=$((skip+1))
  fi
else
  say "Rust" "没有 rustc，跳过（export PATH=\"\$HOME/.cargo/bin:\$PATH\"）"; skip=$((skip+1))
fi

# ---------- C++：主色提取 ----------
if command -v g++ >/dev/null 2>&1; then
  if g++ -O3 -fPIC -shared -std=c++17 -o "$OUT/libklee_palette.so" "$SRC/palette.cpp" \
       >/tmp/klee_palette.log 2>&1; then
    say "C++" "libklee_palette.so $(du -h "$OUT/libklee_palette.so" | cut -f1)"; ok=$((ok+1))
  else
    say "C++" "编译失败：$(tail -3 /tmp/klee_palette.log | tr '\n' ' ')"; skip=$((skip+1))
  fi
else
  say "C++" "没有 g++，跳过"; skip=$((skip+1))
fi

echo "  —— 成功 $ok，跳过 $skip"
[ "$ok" -gt 0 ]
