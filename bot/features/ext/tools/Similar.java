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
// 调用：cmd=similar, text = "第一句\u0001第二句" → 返回 0.0000 ~ 1.0000

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
        Set<String> A = bigrams(a);
        Set<String> B = bigrams(b);
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
                            + "\"commands\":[{\"name\":\"similar\",\"help\":\"两句话有多像（0-1，Dice 2-gram）\"}]}");
                    continue;
                }
                if ("bye".equals(op)) return;
                String cmd = field(line, "cmd");
                if (!"similar".equals(cmd)) {
                    out.println("{\"id\":" + id + ",\"ok\":false,\"error\":\"没有命令 " + cmd + "\"}");
                    continue;
                }
                String text = field(line, "text");
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
        return v.replace("\\n", "\n").replace("\\t", "\t").replace("\\\"", "\"")
                .replace("\\\\", "\\").replace("\\u0001", "\u0001");
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
