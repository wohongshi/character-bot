/* 外部插件示例（C）：.crc —— 算 CRC32，顺便演示「30 行就够写一个插件」。
 * 编译：gcc -O2 -o crc.bin crc.c
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned int crc_table[256];
static int table_ready = 0;

static void init_table(void) {
    for (unsigned int i = 0; i < 256; i++) {
        unsigned int c = i;
        for (int k = 0; k < 8; k++)
            c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
        crc_table[i] = c;
    }
    table_ready = 1;
}

/* 从一行 JSON 里取顶层字符串/数字字段；返回写进 out 的字节数，没找到返回 -1 */
static int field(const char *line, const char *key, char *out, size_t cap) {
    char pat[64];
    snprintf(pat, sizeof pat, "\"%s\"", key);
    const char *p = strstr(line, pat);
    if (!p) return -1;
    p += strlen(pat);
    while (*p && *p != ':') p++;
    if (!*p) return -1;
    p++;
    while (*p == ' ' || *p == '\t') p++;
    if (*p == '"') {
        p++;
        size_t n = 0;
        while (*p && *p != '"') {
            if (*p == '\\' && p[1]) {
                p++;
                char c = *p;
                if (c == 'n') c = '\n';
                else if (c == 't') c = '\t';
                else if (c == 'r') c = '\r';
                if (n + 1 < cap) out[n++] = c;
                p++;
                continue;
            }
            if (n + 1 < cap) out[n++] = *p;
            p++;
        }
        out[n] = 0;
        return (int)n;
    }
    size_t n = 0;
    while (*p && *p != ',' && *p != '}' && n + 1 < cap) out[n++] = *p++;
    out[n] = 0;
    return (int)n;
}

/* JSON 字符串转义（只处理我们可能输出的字符） */
static void emit_reply(int id, const char *s) {
    printf("{\"id\":%d,\"ok\":true,\"reply\":\"", id);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        switch (*p) {
            case '"':  fputs("\\\"", stdout); break;
            case '\\': fputs("\\\\", stdout); break;
            case '\n': fputs("\\n", stdout);  break;
            case '\r': fputs("\\r", stdout);  break;
            case '\t': fputs("\\t", stdout);  break;
            default:
                if (*p < 0x20) printf("\\u%04x", *p);
                else putchar(*p);
        }
    }
    fputs("\"}\n", stdout);
}

int main(void) {
    char line[65536], op[32], text[32768], idbuf[32];
    init_table();

    while (fgets(line, sizeof line, stdin)) {
        if (field(line, "op", op, sizeof op) < 0) continue;

        if (strcmp(op, "hello") == 0) {
            printf("{\"ok\":true,\"name\":\"crc\",\"title\":\"CRC32\",\"version\":\"1.0\","
                   "\"commands\":[{\"name\":\"crc\",\"aliases\":[\"校验\"],"
                   "\"help\":\".crc <文本> —— CRC32 校验值（C 原生）\"}]}\n");
            fflush(stdout);
        } else if (strcmp(op, "bye") == 0) {
            break;
        } else if (strcmp(op, "call") == 0) {
            int id = 0;
            if (field(line, "id", idbuf, sizeof idbuf) >= 0) id = atoi(idbuf);
            text[0] = 0;
            field(line, "text", text, sizeof text);

            unsigned int c = 0xFFFFFFFFu;
            for (const unsigned char *p = (const unsigned char *)text; *p; p++)
                c = crc_table[(c ^ *p) & 0xFF] ^ (c >> 8);
            c ^= 0xFFFFFFFFu;

            char reply[256];
            snprintf(reply, sizeof reply,
                     "🧮 CRC32: %08X（%zu 字节）", c, strlen(text));
            emit_reply(id, reply);
            fflush(stdout);
        }
    }
    return 0;
}
