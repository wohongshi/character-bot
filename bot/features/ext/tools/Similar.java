// 相似度（Java，零依赖，走外部插件协议常驻 —— JVM 只付一次启动成本）
//
// 为什么是 Java：一段「按字符 n-gram 切、取集合交并」的算法，用 Java 的
// HashSet / 位运算写起来最直白；而且它给这个项目补上了一块原来没有的能力：
// 判断两句话是不是「同一个问题」。
//
// 先说踩过的坑：第一版用的是 64 位 SimHash + 汉明距离。**短句上完全不可用** ——
// 实测「今天天气怎么样 / .sha 你好」能得 0.77，而「今天天气怎么样 / 今天天气如何」
// 只有 0.73，不相关的比相关的还高。原因是短句只有几个 shingle，位投票全是噪声。
// SimHash 要的是长文档（新闻、论文），不是一句话。
//
// 现在用 **Dice 系数**（2-gram 集合）：短句上区分度好得多，且天然惩罚长度差。
//   实测：今天天气怎么样 / 今天天气如何 → 0.545
//         今天天气怎么样 / .sha 你好     → 0.000
//

import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.*;

public class Similar {

    static Set<String> bigrams(String text) {
        String s = text.replaceAll("\\s+", " ").trim().toLowerCase();
        Set<String> out = new LinkedHashSet<>();
        if (s.length() <= 2) {
            if (!s.isEmpty()) out.add(s);
            return out;
        }
        for (int i = 0; i + 1 < s.length(); i++) {
            out.add(s.substring(i, i + 2));
        }
        return out;
    }

    static double dice(String a, String b) {
        return diceSets(bigrams(a), bigrams(b));
    }

    static double diceSets(Set<String> A, Set<String> B) {
        if (A.isEmpty() || B.isEmpty()) return 0.0;
        int inter = 0;
        for (String x : A) if (B.contains(x)) inter++;
        return 2.0 * inter / (A.size() + B.size());
    }

    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        PrintWriter out = new PrintWriter(new OutputStreamWriter(System.out, StandardCharsets.UTF_8), true);
        String line;
        while ((line = in.readLine()) != null) {
            line = line.trim();
            if (line.isEmpty()) continue;
            String id = "0";
            try {
                id = field(line, "id");
                String op = field(line, "op");
                if ("hello".equals(op)) {
                    out.println("{\"ok\":true,\"name\":\"similar\",\"title\":\"相似度\",\"version\":\"1.1\","
                            + "\"commands\":[{\"name\":\"similar\",\"help\":\"两句话有多像（0-1，Dice 2-gram）\"},"
                            + "{\"name\":\"similar_batch\",\"help\":\"一次比一批\"}]}");
                    continue;
                }
                if ("bye".equals(op)) return;
                String cmd = field(line, "cmd");
                String text = field(line, "text");
                if ("similar_batch".equals(cmd)) {
                    // 单个候选走一次进程往返要 2 ms，40 个候选就是 80 ms，
                    // 而浏览器每条消息都会问一遍 —— 必须批量。
                    String[] parts = text.split("\u0002", -1);
                    // 只有一个候选时 payload 是 "q\u0001cand"，split 后只有 1 段 ——
                    // 这里以前直接报「至少一个候选」，于是**历史很短时批量路径永远不可用**。
                    if (parts.length < 2 && parts[0].indexOf('\u0001') < 0) {
                        out.println("{\"id\":" + id + ",\"ok\":false,\"error\":\"批量需要查询 + 至少一个候选\"}");
                        continue;
                    }
                    int sep = parts[0].indexOf('\u0001');
                    String q = sep >= 0 ? parts[0].substring(0, sep) : parts[0];
                    // parts[0] 的尾巴就是**第一个候选** —— 第一版从下标 1 开始循环，
                    // 把它跳过了，于是所有分数整体错位一格（问「你好」，答案却是下一条的分数）。
                    java.util.List<String> cands = new java.util.ArrayList<>();
                    if (sep >= 0) cands.add(parts[0].substring(sep + 1));
                    for (int i = 1; i < parts.length; i++) cands.add(parts[i]);
                    Set<String> qs = bigrams(q);
                    StringBuilder sb = new StringBuilder("[");
                    for (int i = 0; i < cands.size(); i++) {
                        double d = diceSets(qs, bigrams(cands.get(i)));
                        if (i > 0) sb.append(',');
                        sb.append(String.format(Locale.ROOT, "%.4f", d));
                    }
                    sb.append(']');
                    out.println("{\"id\":" + id + ",\"ok\":true,\"reply\":\"" + sb + "\"}");
                    continue;
                }
                if (!"similar".equals(cmd)) {
                    out.println("{\"id\":" + id + ",\"ok\":false,\"error\":\"没有命令 " + cmd + "\"}");
                    continue;
                }
                int sep = text.indexOf('\u0001');
                if (sep < 0) {
                    out.println("{\"id\":" + id + ",\"ok\":false,\"error\":\"需要两段文本，用 \\\\u0001 分隔\"}");
                    continue;
                }
                double d = dice(text.substring(0, sep), text.substring(sep + 1));
                out.println("{\"id\":" + id + ",\"ok\":true,\"reply\":\""
                        + String.format(Locale.ROOT, "%.4f", d) + "\"}");
            } catch (Exception e) {
                out.println("{\"id\":" + id + ",\"ok\":false,\"error\":\"" + e.getMessage() + "\"}");
            }
        }
    }

    static String field(String json, String key) {
        String v = raw(json, key);
        // 反转义 n / t / r / 引号 / 反斜杠 / 四位十六进制（分隔符 0002 也要能还原）
        StringBuilder sb = new StringBuilder(v.length());
        for (int i = 0; i < v.length(); i++) {
            char c = v.charAt(i);
            if (c != '\\' || i + 1 >= v.length()) { sb.append(c); continue; }
            char n = v.charAt(++i);
            switch (n) {
                case 'n': sb.append('\n'); break;
                case 't': sb.append('\t'); break;
                case 'r': sb.append('\r'); break;
                case 'b': sb.append('\b'); break;      // JSON 合法转义；漏了会变成字面 b
                case 'f': sb.append('\f'); break;      // 同上，字面 f
                case '"': sb.append('"'); break;
                case '\\': sb.append('\\'); break;
                case 'u':
                    if (i + 4 < v.length()) {
                        sb.append((char) Integer.parseInt(v.substring(i + 1, i + 5), 16));
                        i += 4;
                    }
                    break;
                default: sb.append(n);
            }
        }
        return sb.toString();
    }

    static String raw(String json, String key) {
        String pat = "\"" + key + "\"";
        int i = json.indexOf(pat);
        if (i < 0) return "";
        int c = json.indexOf(':', i + pat.length());
        if (c < 0) return "";
        int p = c + 1;
        while (p < json.length() && Character.isWhitespace(json.charAt(p))) p++;
        if (p >= json.length()) return "";
        if (json.charAt(p) == '"') {
            StringBuilder sb = new StringBuilder();
            for (int q = p + 1; q < json.length(); q++) {
                char ch = json.charAt(q);
                if (ch == '\\') {
                    sb.append(ch);
                    if (q + 1 < json.length()) sb.append(json.charAt(++q));
                    continue;
                }
                if (ch == '"') break;
                sb.append(ch);
            }
            return sb.toString();
        }
        int q = p;
        while (q < json.length() && ",}]\n\r".indexOf(json.charAt(q)) < 0) q++;
        return json.substring(p, q).trim();
    }
}
