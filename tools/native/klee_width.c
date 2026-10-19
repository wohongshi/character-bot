/* 显示宽度 / 按宽度截断（C，编成 .so 由 Python ctypes 直调）
 *
 * 为什么这个要 C：卡片渲染每行文字都要量一次宽度、按像素宽截断。
 * Python 里逐字符判断 East Asian Wide 要走解释器循环，一张卡 20 行就是上千次；
 * 这里是一次线性扫描，几十纳秒。
 *
 * 口径（够用就好，不追求 unicode 全表）：
 *   - ASCII 0x20..0x7E          宽 1
 *   - 组合附加符号 / 零宽       宽 0
 *   - CJK / 韩文 / 全角 / emoji 宽 2
 *   - 其余（拉丁扩展、西里尔…） 宽 1
 */
#include <stddef.h>
#include <stdint.h>

static int cp_width(uint32_t cp)
{
    if (cp == 0) return 0;
    if (cp < 0x20) return 0;                 /* 控制字符 */
    if (cp < 0x7F) return 1;
    /* 零宽 / 组合符 */
    if ((cp >= 0x0300 && cp <= 0x036F) || (cp >= 0x200B && cp <= 0x200F) ||
        (cp >= 0xFE00 && cp <= 0xFE0F) || cp == 0xFEFF) return 0;
    /* CJK 相关区段（含全角标点、假名、韩文、注音） */
    if ((cp >= 0x1100 && cp <= 0x115F) ||   /* 韩文字母 */
        (cp >= 0x2E80 && cp <= 0x303E) ||   /* CJK 部首 · 标点 */
        (cp >= 0x3041 && cp <= 0x33FF) ||   /* 假名 · 注音 · CJK 兼容 */
        (cp >= 0x3400 && cp <= 0x4DBF) ||   /* CJK 扩展 A */
        (cp >= 0x4E00 && cp <= 0x9FFF) ||   /* CJK 基本区 */
        (cp >= 0xA000 && cp <= 0xA4CF) ||   /* 彝文 */
        (cp >= 0xAC00 && cp <= 0xD7A3) ||   /* 韩文音节 */
        (cp >= 0xF900 && cp <= 0xFAFF) ||   /* CJK 兼容表意 */
        (cp >= 0xFE30 && cp <= 0xFE6F) ||   /* CJK 兼容形式 */
        (cp >= 0xFF00 && cp <= 0xFF60) ||   /* 全角 ASCII */
        (cp >= 0xFFE0 && cp <= 0xFFE6) ||
        (cp >= 0x1F300 && cp <= 0x1F64F) || /* emoji */
        (cp >= 0x1F900 && cp <= 0x1F9FF) ||
        (cp >= 0x20000 && cp <= 0x3FFFD))   /* CJK 扩展 B+ */
        return 2;
    return 1;
}

/* 解一个 UTF-8 码点；坏字节当 1 字节宽 1 处理，绝不越界 */
static uint32_t dec(const unsigned char *s, size_t n, size_t *used)
{
    unsigned char c = s[0];
    if (c < 0x80) { *used = 1; return c; }
    size_t len = 0;
    uint32_t cp = 0;
    if ((c & 0xE0) == 0xC0) { len = 2; cp = c & 0x1F; }
    else if ((c & 0xF0) == 0xE0) { len = 3; cp = c & 0x0F; }
    else if ((c & 0xF8) == 0xF0) { len = 4; cp = c & 0x07; }
    else { *used = 1; return 0xFFFD; }
    if (len > n) { *used = 1; return 0xFFFD; }
    for (size_t i = 1; i < len; i++) {
        if ((s[i] & 0xC0) != 0x80) { *used = i; return 0xFFFD; }
        cp = (cp << 6) | (s[i] & 0x3F);
    }
    *used = len;
    return cp;
}

/* 整段显示宽度（列数） */
int klee_width(const char *text, size_t n)
{
    if (!text || n == 0) return 0;
    const unsigned char *s = (const unsigned char *)text;
    int w = 0;
    size_t i = 0;
    while (i < n) {
        size_t used = 0;
        uint32_t cp = dec(s + i, n - i, &used);
        w += cp_width(cp);
        i += used ? used : 1;
    }
    return w;
}

/* 返回「截到不超过 maxw 列」所需的字节数（不会切在多字节字符中间） */
size_t klee_truncate(const char *text, size_t n, int maxw)
{
    if (!text || n == 0 || maxw <= 0) return 0;
    const unsigned char *s = (const unsigned char *)text;
    int w = 0;
    size_t i = 0;
    while (i < n) {
        size_t used = 0;
        uint32_t cp = dec(s + i, n - i, &used);
        if (!used) used = 1;
        int cw = cp_width(cp);
        if (w + cw > maxw) return i;
        w += cw;
        i += used;
    }
    return n;
}

/* 版本号：面板上显示，确认加载的是这一份 */
const char *klee_width_version(void) { return "1.0"; }
