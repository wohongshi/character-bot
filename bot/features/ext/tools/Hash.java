// 外部插件示例（Java）：.sha —— SHA-256（java.security.MessageDigest）。
// 编译：javac -d . Hash.java && 运行：java -XX:+UseSerialGC -Xss256k -cp . Hash
// 注意：JVM 启动约 360ms、首次调用 150ms（JIT 未热），从第 2 次起约 3ms；
// 常驻 43MB。实测数据见 docs/POLYGLOT.md。
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

public class Hash {
    static String field(String line, String key) {
        String pat = "\"" + key + "\"";
        int at = line.indexOf(pat);
        if (at < 0) return null;
        int p = at + pat.length();
        while (p < line.length() && line.charAt(p) != ':') p++;
        if (p >= line.length()) return null;
        p++;
        while (p < line.length() && (line.charAt(p) == ' ' || line.charAt(p) == '\t')) p++;
        if (p < line.length() && line.charAt(p) == '"') {
            p++;
            StringBuilder sb = new StringBuilder();
            while (p < line.length() && line.charAt(p) != '"') {
                char c = line.charAt(p);
                if (c == '\\' && p + 1 < line.length()) {
                    p++;
                    char e = line.charAt(p);
                    if (e == 'n') sb.append('\n');
                    else if (e == 't') sb.append('\t');
                    else if (e == 'r') sb.append('\r');
                    else sb.append(e);
                    p++;
                    continue;
                }
                sb.append(c);
                p++;
            }
            return sb.toString();
        }
        int start = p;
        while (p < line.length() && line.charAt(p) != ','
                && line.charAt(p) != '}') p++;
        return line.substring(start, p);
    }

    static String esc(String s) {
        StringBuilder o = new StringBuilder(s.length() + 8);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': o.append("\\\""); break;
                case '\\': o.append("\\\\"); break;
                case '\n': o.append("\\n"); break;
                case '\r': o.append("\\r"); break;
                case '\t': o.append("\\t"); break;
                default:
                    if (c < 0x20) o.append(String.format("\\u%04x", (int) c));
                    else o.append(c);
            }
        }
        return o.toString();
    }

    public static void main(String[] args) throws Exception {
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        PrintStream out = new PrintStream(System.out, true, StandardCharsets.UTF_8);
        String line;
        while ((line = in.readLine()) != null) {
            String op = field(line, "op");
            if (op == null) continue;
            if (op.equals("hello")) {
                out.println("{\"ok\":true,\"name\":\"hash\",\"title\":\"SHA-256\",\"version\":\"1.0\","
                        + "\"commands\":[{\"name\":\"sha\",\"aliases\":[\"哈希\"],"
                        + "\"help\":\".sha <文本> —— SHA-256（Java 原生 MessageDigest）\"}]}");
            } else if (op.equals("bye")) {
                break;
            } else if (op.equals("call")) {
                String id = field(line, "id");
                String text = field(line, "text");
                if (text == null) text = "";
                MessageDigest md = MessageDigest.getInstance("SHA-256");
                byte[] d = md.digest(text.getBytes(StandardCharsets.UTF_8));
                StringBuilder hex = new StringBuilder();
                for (byte b : d) hex.append(String.format("%02x", b));
                String reply = "🔐 " + hex.substring(0, 32) + "…（" + text.length() + " 字符）";
                out.println("{\"id\":" + (id == null ? "0" : id) + ",\"ok\":true,\"reply\":\""
                        + esc(reply) + "\"}");
            }
        }
    }
}
