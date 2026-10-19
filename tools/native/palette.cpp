// 主色提取（C++，零依赖，编成 .so 由 Python ctypes 直调）
//
// 用途：导入新角色时，从立绘自动算出 accent / accent2，生成一套主题配色。
// 以前只能手填色号，或者继承默认的红色 —— 角色换一张图，主题还是别人的颜色。
//
// 做法：5 bit/通道 的直方图（32768 桶，256 KB 栈上分配不行，用静态），
// 按「饱和度 × 明度权重 × 出现次数」打分，再合并相近色，取前 N 个。

#include <cstdint>
#include <cstring>
#include <algorithm>
#include <vector>

namespace {

constexpr int BITS = 5;
constexpr int LEVELS = 1 << BITS;          // 32
constexpr int BUCKETS = LEVELS * LEVELS * LEVELS;

struct Bin {
    uint32_t count;
    uint32_t r, g, b;                      // 累加真实颜色，最后取平均
};

// 静态分配：32768 × 16 B = 512 KB，避免每次调用 malloc
static Bin g_bins[BUCKETS];

inline int idx_of(int r, int g, int b) {
    int qr = r * LEVELS >> 8, qg = g * LEVELS >> 8, qb = b * LEVELS >> 8;
    return (qr * LEVELS + qg) * LEVELS + qb;
}

inline float saturation(int r, int g, int b) {
    int mx = std::max(r, std::max(g, b));
    int mn = std::min(r, std::min(g, b));
    if (mx == 0) return 0.f;
    return float(mx - mn) / float(mx);
}

}  // namespace

extern "C" {

// rgba: 宽高像素，每像素 4 字节；step: 采样步长（1 = 全采，2 = 隔一个）
// out: 输出 RGB 三元组，最多 max_colors 个；返回实际个数
// 颜色按「出现频率 × 饱和度 × 亮度适中性」排序，太黑太白的先扣分
int klee_palette(const unsigned char *rgba, int w, int h, int step,
                 int max_colors, unsigned char *out)
{
    if (!rgba || !out || w <= 0 || h <= 0 || max_colors <= 0) return -1;
    if (step < 1) step = 1;
    std::memset(g_bins, 0, sizeof(g_bins));

    for (int y = 0; y < h; y += step) {
        const unsigned char *row = rgba + (size_t)y * (size_t)w * 4;
        for (int x = 0; x < w; x += step) {
            const unsigned char *p = row + (size_t)x * 4;
            if (p[3] < 8) continue;                    // 全透明跳过
            int r = p[0], g = p[1], b = p[2];
            Bin &bin = g_bins[idx_of(r, g, b)];
            bin.count++;
            bin.r += r;
            bin.g += g;
            bin.b += b;
        }
    }

    struct Cand { float score; int r, g, b; };
    std::vector<Cand> cands;
    cands.reserve(4096);
    for (int i = 0; i < BUCKETS; i++) {
        if (!g_bins[i].count) continue;
        int r = int(g_bins[i].r / g_bins[i].count);
        int g = int(g_bins[i].g / g_bins[i].count);
        int b = int(g_bins[i].b / g_bins[i].count);
        float sat = saturation(r, g, b);
        float lum = (0.2126f * r + 0.7152f * g + 0.0722f * b) / 255.f;
        // 极亮/极暗做背景色可以，做主色发灰 —— 用钟形权重压一下
        float mid = 1.f - std::abs(lum - 0.55f) * 1.1f;
        if (mid < 0.15f) mid = 0.15f;
        float score = float(g_bins[i].count) * (0.25f + sat) * mid;
        cands.push_back({score, r, g, b});
    }
    if (cands.empty()) return 0;

    std::sort(cands.begin(), cands.end(),
              [](const Cand &a, const Cand &b) { return a.score > b.score; });

    // 合并相近色（欧氏距离 < 48 视为同一个），避免输出一堆几乎一样的红
    std::vector<Cand> picked;
    for (const auto &c : cands) {
        bool dup = false;
        for (const auto &p : picked) {
            int dr = c.r - p.r, dg = c.g - p.g, db = c.b - p.b;
            if (dr * dr + dg * dg + db * db < 48 * 48) { dup = true; break; }
        }
        if (dup) continue;
        picked.push_back(c);
        if ((int)picked.size() >= max_colors) break;
    }

    int n = (int)picked.size();
    for (int i = 0; i < n; i++) {
        out[i * 3 + 0] = (unsigned char)picked[i].r;
        out[i * 3 + 1] = (unsigned char)picked[i].g;
        out[i * 3 + 2] = (unsigned char)picked[i].b;
    }
    return n;
}

// 平均亮度（0..255），用来决定覆盖文字用白还是黑
int klee_luma(const unsigned char *rgba, int w, int h, int step)
{
    if (!rgba || w <= 0 || h <= 0) return -1;
    if (step < 1) step = 1;
    double sum = 0;
    long n = 0;
    for (int y = 0; y < h; y += step) {
        const unsigned char *row = rgba + (size_t)y * (size_t)w * 4;
        for (int x = 0; x < w; x += step) {
            const unsigned char *p = row + (size_t)x * 4;
            if (p[3] < 8) continue;
            sum += 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2];
            n++;
        }
    }
    if (!n) return -1;
    return int(sum / n);
}

const char *klee_palette_version(void) { return "1.0"; }

}  // extern "C"
