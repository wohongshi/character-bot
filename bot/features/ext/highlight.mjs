// 代码高亮（Node，零依赖，走外部插件协议常驻）
//
// 为什么是 Node：正则 + 字符串拼装这类活，JS 的 RegExp 是原生实现，
// 写起来最短；而且它已经在仓库里（面板构建、截图脚本都用它）。
//
// 调用：cmd=highlight, args=[lang], text=代码
// 返回：带 <span class="t-k"> 这类 class 的 HTML 片段（**已转义**，可直接 innerHTML）

const LANGS = {
  js: {
    kw: /\b(const|let|var|function|return|if|else|for|while|class|new|await|async|import|from|export|default|try|catch|finally|throw|typeof|instanceof|of|in|this|null|undefined|true|false)\b/g,
    str: /(["'`])(?:\\.|(?!\1)[^\\\n])*\1/g,
    num: /\b\d+(?:\.\d+)?\b/g,
    com: /\/\/[^\n]*|\/\*[\s\S]*?\*\//g,
  },
  py: {
    kw: /\b(def|class|return|if|elif|else|for|while|import|from|as|with|try|except|finally|raise|pass|lambda|yield|None|True|False|and|or|not|in|is|global|nonlocal|assert|del|async|await)\b/g,
    str: /("""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')/g,
    num: /\b\d+(?:\.\d+)?\b/g,
    com: /#[^\n]*/g,
  },
  rust: {
    kw: /\b(fn|let|mut|pub|use|mod|struct|enum|impl|trait|match|if|else|for|while|loop|return|self|Self|const|static|unsafe|extern|as|in|move|ref|where|async|await|dyn|box)\b/g,
    str: /"(?:\\.|[^"\\])*"/g,
    num: /\b\d+(?:\.\d+)?\b/g,
    com: /\/\/[^\n]*|\/\*[\s\S]*?\*\//g,
  },
  c: {
    kw: /\b(int|char|void|long|short|unsigned|signed|float|double|struct|enum|union|typedef|static|const|return|if|else|for|while|do|switch|case|break|continue|sizeof|extern|inline|volatile|goto)\b/g,
    str: /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'/g,
    num: /\b\d+(?:\.\d+)?[uUlLfF]*\b/g,
    com: /\/\/[^\n]*|\/\*[\s\S]*?\*\//g,
  },
  json: {
    kw: /\b(true|false|null)\b/g,
    str: /"(?:\\.|[^"\\])*"(?=\s*:)/g,      // key
    str2: /"(?:\\.|[^"\\])*"/g,             // value
    num: /-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b/g,
    com: null,
  },
  sh: {
    kw: /\b(if|then|else|fi|for|in|do|done|while|case|esac|function|return|export|local|source|set|unset|shift|trap)\b/g,
    str: /"(?:\\.|[^"\\])*"|'[^']*'/g,
    num: /\b\d+\b/g,
    com: /#[^\n]*/g,
  },
  sql: {
    kw: /\b(SELECT|FROM|WHERE|JOIN|LEFT|RIGHT|INNER|OUTER|ON|GROUP|BY|ORDER|HAVING|LIMIT|OFFSET|INSERT|INTO|VALUES|UPDATE|SET|DELETE|CREATE|TABLE|INDEX|VIEW|DROP|ALTER|AND|OR|NOT|NULL|AS|DISTINCT|COUNT|SUM|AVG|MIN|MAX|CASE|WHEN|THEN|ELSE|END|WITH|PRAGMA|EXPLAIN)\b/gi,
    str: /'(?:''|[^'])*'/g,
    num: /\b\d+(?:\.\d+)?\b/g,
    com: /--[^\n]*/g,
  },
};
// 别名：调用方写 "python" / "bash" / "c++" 也要能对上（面板里语言名是人写的）
LANGS.cpp = LANGS.c;
LANGS["c++"] = LANGS.c;
LANGS.cc = LANGS.c;
LANGS.java = LANGS.js;
LANGS.ts = LANGS.js;
LANGS.typescript = LANGS.js;
LANGS.javascript = LANGS.js;
LANGS.python = LANGS.py;
LANGS.rs = LANGS.rust;
LANGS.bash = LANGS.sh;
LANGS.shell = LANGS.sh;
LANGS.zsh = LANGS.sh;
LANGS.sqlite = LANGS.sql;

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
}[c]));

function highlight(code, lang) {
  const L = LANGS[String(lang || "").toLowerCase()];
  if (!L) return `<pre class="code">${esc(code)}</pre>`;
  // 先把「字符串 / 注释」挖出来占位，剩下的部分再做关键词与数字，
  // 这样字符串里的 return 不会被当成关键字（正则替换顺序的经典坑）。
  const holes = [];
  // 占位符要带非数字标记：第一版写成 `\u0000<序号>\u0000`，
  // 结果数字高亮把占位符里的序号也染成了 <span>，还原时就再也匹配不上了。
  const stash = (m) => `\u0000x${holes.push(m) - 1}x\u0000`;
  let s = String(code);
  const strRe = L.str2 || null;
  s = s.replace(L.com || /$^/, (m) => stash(`<span class="t-c">${esc(m)}</span>`));
  s = s.replace(L.str, (m) => stash(`<span class="t-s">${esc(m)}</span>`));
  if (strRe) {
    s = s.replace(strRe, (m) => stash(`<span class="t-a">${esc(m)}</span>`));
  }
  s = esc(s);
  s = s.replace(L.kw, (m) => `<span class="t-k">${m}</span>`);
  s = s.replace(L.num, (m) => `<span class="t-n">${m}</span>`);
  s = s.replace(/\u0000x(\d+)x\u0000/g, (_, i) => holes[Number(i)]);
  return `<pre class="code">${s}</pre>`;
}

// ---- 外部插件协议：NDJSON over stdio ----
let buf = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  buf += chunk;
  let i;
  while ((i = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, i).trim();
    buf = buf.slice(i + 1);
    if (!line) continue;
    let msg;
    try { msg = JSON.parse(line); } catch { continue; }
    handle(msg);
  }
});

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function handle(msg) {
  if (msg.op === "hello") {
    send({
      ok: true,
      name: "highlight",
      title: "代码高亮",
      version: "1.0",
      commands: [
        { name: "highlight", help: "把代码片段染成 HTML（给面板聊天里的代码块用）" },
        { name: "langs", help: "支持哪些语言" },
      ],
    });
    return;
  }
  if (msg.op === "bye") { process.exit(0); }
  if (msg.op !== "call") return;
  try {
    if (msg.cmd === "langs") {
      send({ id: msg.id, ok: true, reply: Object.keys(LANGS).join(",") });
      return;
    }
    const lang = (msg.args && msg.args[0]) || "js";
    const html = highlight(msg.text || "", lang);
    send({ id: msg.id, ok: true, reply: html });
  } catch (e) {
    send({ id: msg.id, ok: false, error: String(e && e.message || e) });
  }
}
