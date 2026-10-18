// 外部插件示例（Node）：.uuid 生成、.json 格式化。
// 协议：一行一个 JSON，见 bot/ext/host.py 顶部。
import { createInterface } from "node:readline";
import { randomUUID } from "node:crypto";

const rl = createInterface({ input: process.stdin });

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function pretty(text) {
  if (!text.trim()) return "给我一段 JSON 呀，比如 .json {\"a\":1}";
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch (e) {
    return "✗ 不是合法 JSON：" + String(e.message).slice(0, 120);
  }
}

rl.on("line", (line) => {
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    return; // 坏行直接忽略
  }
  if (msg.op === "hello") {
    send({
      ok: true,
      name: "uuid",
      title: "UUID / JSON",
      version: "1.0",
      commands: [
        { name: "uuid", aliases: ["uuid4"], help: ".uuid —— 生成一个 v4 UUID（Node crypto）" },
        { name: "json", help: ".json <内容> —— 把压成一行的 JSON 格式化" },
      ],
    });
    return;
  }
  if (msg.op === "bye") process.exit(0);
  if (msg.op !== "call") return;

  const text = msg.text || "";
  let reply;
  if (msg.cmd === "uuid") {
    const n = Math.min(Math.max(parseInt(msg.args?.[0] || "1", 10) || 1, 1), 5);
    reply = Array.from({ length: n }, () => randomUUID()).join("\n");
  } else if (msg.cmd === "json") {
    reply = pretty(text);
  } else {
    send({ id: msg.id, ok: false, error: "没有这个命令：" + msg.cmd });
    return;
  }
  send({ id: msg.id, ok: true, reply });
});
