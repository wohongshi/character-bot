// 外部插件示例（C++）：.排序 —— 把一串数字排序（std::sort）。
// 编译：g++ -O2 -std=c++20 -o sort.bin sort.cpp
#include <algorithm>
#include <charconv>
#include <cstdio>
#include <iostream>
#include <string>
#include <string_view>
#include <vector>

namespace {

// 取顶层字符串/数字字段（形状固定，够用就行）
std::string field(std::string_view line, std::string_view key) {
    std::string pat = "\"" + std::string(key) + "\"";
    auto at = line.find(pat);
    if (at == std::string_view::npos) return {};
    auto p = at + pat.size();
    while (p < line.size() && line[p] != ':') ++p;
    if (p >= line.size()) return {};
    ++p;
    while (p < line.size() && (line[p] == ' ' || line[p] == '\t')) ++p;
    std::string out;
    if (p < line.size() && line[p] == '"') {
        ++p;
        while (p < line.size() && line[p] != '"') {
            if (line[p] == '\\' && p + 1 < line.size()) {
                ++p;
                char c = line[p];
                if (c == 'n') c = '\n';
                else if (c == 't') c = '\t';
                else if (c == 'r') c = '\r';
                out.push_back(c);
                ++p;
                continue;
            }
            out.push_back(line[p++]);
        }
        return out;
    }
    while (p < line.size() && line[p] != ',' && line[p] != '}') out.push_back(line[p++]);
    return out;
}

std::string escape(const std::string& s) {
    std::string o;
    o.reserve(s.size() + 8);
    for (unsigned char c : s) {
        switch (c) {
            case '"': o += "\\\""; break;
            case '\\': o += "\\\\"; break;
            case '\n': o += "\\n"; break;
            case '\r': o += "\\r"; break;
            case '\t': o += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof buf, "\\u%04x", c);
                    o += buf;
                } else {
                    o.push_back(static_cast<char>(c));
                }
        }
    }
    return o;
}

std::vector<double> numbers(const std::string& text) {
    std::vector<double> v;
    std::string cur;
    auto flush = [&] {
        if (cur.empty()) return;
        double d = 0;
        auto res = std::from_chars(cur.data(), cur.data() + cur.size(), d);
        if (res.ec == std::errc()) v.push_back(d);
        cur.clear();
    };
    for (char c : text) {
        if (std::isdigit(static_cast<unsigned char>(c)) || c == '.' || c == '-' || c == '+')
            cur.push_back(c);
        else
            flush();
    }
    flush();
    return v;
}

}  // namespace

int main() {
    std::string line;
    while (std::getline(std::cin, line)) {
        auto op = field(line, "op");
        if (op == "hello") {
            std::cout << R"({"ok":true,"name":"sort","title":"排序","version":"1.0","commands":[{"name":"排序","aliases":["sort"],"help":".排序 3,1,2 —— 数字升序（C++ std::sort）"}]})"
                      << std::endl;
        } else if (op == "bye") {
            break;
        } else if (op == "call") {
            int id = std::atoi(field(line, "id").c_str());
            auto nums = numbers(field(line, "text"));
            std::string reply;
            if (nums.empty()) {
                reply = "给我几个数字呀，比如 .排序 3,1,2";
            } else if (nums.size() > 200) {
                reply = "太多了，一次最多 200 个";
            } else {
                std::sort(nums.begin(), nums.end());
                reply = "🔢 ";
                for (size_t i = 0; i < nums.size(); ++i) {
                    if (i) reply += " ≤ ";
                    char buf[32];
                    std::snprintf(buf, sizeof buf, "%g", nums[i]);
                    reply += buf;
                }
            }
            std::cout << "{\"id\":" << id << ",\"ok\":true,\"reply\":\""
                      << escape(reply) << "\"}" << std::endl;
        }
    }
    return 0;
}
