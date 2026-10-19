#!/usr/bin/env bash
# 编译外部插件示例。没装的工具链会自动跳过（并打印说明），不影响其他语言。
set -u
cd "$(dirname "$0")" || exit 1

say() { printf '%-10s %s\n' "$1" "$2"; }

if command -v node >/dev/null 2>&1; then
  say node "ok（解释执行，无需编译）"
else
  say node "未安装 —— uuid 插件会启动失败"
fi

if [ -x "$HOME/.cargo/bin/rustc" ] || command -v rustc >/dev/null 2>&1; then
  RUSTC="$(command -v rustc || echo "$HOME/.cargo/bin/rustc")"
  if "$RUSTC" -O -o textstat.bin textstat.rs; then say rust "已编译 textstat.bin"; fi
else
  say rust "没有 rustc —— 跳过（.统计 会不可用）"
fi

if command -v gcc >/dev/null 2>&1; then
  gcc -O2 -o crc.bin crc.c && say c "已编译 crc.bin"
else
  say c "没有 gcc —— 跳过"
fi

if command -v g++ >/dev/null 2>&1; then
  g++ -O2 -std=c++20 -o sort.bin sort.cpp && say cpp "已编译 sort.bin"
else
  say cpp "没有 g++ —— 跳过"
fi

if command -v javac >/dev/null 2>&1; then
  # 每个 .java 都要编：以前只编了 Hash.java，改了 Similar.java 之后
  # classes/Similar.class 一直是旧的 —— 测试绿着，线上跑的是上个版本。
  #
  # -encoding UTF-8 不能省：JDK < 18 默认用平台编码（LANG=C 的机器上是 ASCII），
  # Similar.java 的字符串字面量里有中文 → 编成「?」且 javac 只 warn、rc 仍是 0，
  # 插件标题和错误信息会变成乱码。
  mkdir -p classes
  if javac -encoding UTF-8 -d classes ./*.java; then
    say java "已编译 $(ls classes/*.class | wc -l) 个 class"
  else
    # 失败就把旧 class 清掉并返回非零：留着上一次的 class 是最坏的一档
    # —— 源码改了、单元测试调的还是旧字节码，看起来一切正常。
    rm -f classes/*.class
    say java "编译失败（已清掉旧 class）"
    exit 1
  fi
else
  say java "没有 javac —— 跳过"
fi

chmod +x ./*.bin 2>/dev/null
