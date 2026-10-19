// 插件包校验（C# / .NET 10，零 NuGet 依赖）
//
// 为什么是 C#：这个活集中在 System.IO.Compression 与 System.Security.Cryptography ——
// .NET 的这两个库最完整（zip 条目校验、SHA-256、路径穿越防护都是一等公民），
// 手写一遍容易漏边界。而且它是个**离线 CLI**，不在请求路径上，启动几十毫秒无所谓。
//
// 用法：
//   pkgcheck <zip>             → 角色包报告（要 character.json）
//   pkgcheck <zip> --plugin    → 插件包报告（只收 .py，不要求 character.json）
//   pkgcheck --selftest        → 自检（造一个坏包，确认能拦住）
//
// 退出码：0 = 通过，1 = 有问题，2 = 用法错误

using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace PkgCheck;

internal static class Program
{
    // 安装器接受的上限：单文件 2 MB，整包 8 MB（插件是单文件脚本，不需要更大）
    private const long MaxEntryBytes = 2 * 1024 * 1024;
    private const long MaxTotalBytes = 8 * 1024 * 1024;
    private const int MaxEntries = 200;
    // 与 server.py 的 PLUGIN_MAX_FILES / PLUGIN_NAME_RE 保持一致
    private const int PluginMaxFiles = 20;
    private static readonly Regex PluginNameRe = new(@"^[a-z][a-z0-9_]{1,40}\.py$", RegexOptions.Compiled);

    private static int Main(string[] argv)
    {
        if (argv.Length == 1 && argv[0] == "--selftest") return SelfTest();
        bool pluginMode = argv.Length == 2 && argv[1] == "--plugin";
        if (argv.Length != 1 && !pluginMode)
        {
            Console.Error.WriteLine("usage: pkgcheck <file.zip> [--plugin] | pkgcheck --selftest");
            return 2;
        }
        var path = argv[0];
        if (!File.Exists(path))
        {
            Console.WriteLine(Json(new { ok = false, error = $"找不到 {path}" }));
            return 1;
        }
        try
        {
            var report = Inspect(path, pluginMode);
            Console.WriteLine(Json(report));
            return report.Ok ? 0 : 1;
        }
        catch (Exception e)
        {
            Console.WriteLine(Json(new { ok = false, error = $"{e.GetType().Name}: {e.Message}" }));
            return 1;
        }
    }

    private sealed record Entry(string Name, string FullName, long Bytes, string Sha256);

    private sealed class Report
    {
        public bool Ok { get; set; }
        public string File { get; set; } = "";
        public long Bytes { get; set; }
        public int Entries { get; set; }
        public List<string> Problems { get; } = new();
        public List<Entry> Files { get; } = new();
        public string? CharacterId { get; set; }
        public List<string> Plugins { get; } = new();
        public string By => "csharp";
    }

    /// <summary>与 server.py::_zip_rel 同口径：`\`→`/`、丢弃空段与 `.` 段。</summary>
    private static string Normalize(string raw)
    {
        var parts = raw.Replace('\\', '/').Split('/')
            .Where(x => x.Length > 0 && x != ".")
            .ToList();
        return string.Join("/", parts);
    }

    /// <summary>与 server.py 相同的「单个顶层目录」归一化。</summary>
    private static (string Prefix, Func<string, string> Rel) CommonPrefix(IReadOnlyList<string> names)
    {
        if (names.Count == 0) return ("", n => n);
        var tops = names.Select(n => n.Split('/')[0]).Distinct().ToList();
        var prefix = (tops.Count == 1 && names.All(n => n.Contains('/'))) ? tops[0] + "/" : "";
        string Rel(string n) =>
            prefix.Length > 0 && n.StartsWith(prefix, StringComparison.Ordinal) ? n[prefix.Length..] : n;
        return (prefix, Rel);
    }

    private static Report Inspect(string path, bool pluginMode = false)
    {
        var r = new Report { File = Path.GetFileName(path), Bytes = new FileInfo(path).Length };
        using var zip = ZipFile.OpenRead(path);
        long total = 0;
        int n = 0;

        foreach (var e in zip.Entries)
        {
            n++;
            if (n > MaxEntries)
            {
                r.Problems.Add($"条目太多（>{MaxEntries}）—— 可能是 zip 炸弹");
                break;
            }
            // 路径穿越：绝对路径、盘符、越界 ..
            // 判的是**原始名**：Normalize() 会丢掉空段，`/etc/passwd` 归一化后
            // 变成 `etc/passwd`，前导 / 就没了 —— 拿归一化的名字判绝对路径是死代码。
            var raw = e.FullName.Replace('\\', '/');
            var isDir = raw.EndsWith('/');
            if (raw.Length == 0)
            {
                r.Problems.Add("空路径");
                continue;
            }
            var name = Normalize(raw);
            if (name.Length == 0)
            {
                // 只有目录条目（`./`、`a/`）可以没有名字；一个**文件**叫 `.`
                // 会被安装器按「空路径」拒掉，这里也得报，不能静默跳过。
                if (!isDir) r.Problems.Add($"空路径：{raw}");
                continue;
            }
            if (raw.StartsWith('/') || name.Contains(":") || name.Split('/').Any(s => s == ".."))
            {
                r.Problems.Add($"路径穿越：{name}");
                continue;
            }
            if (e.Length > MaxEntryBytes)
            {
                r.Problems.Add($"单个文件过大：{name} {e.Length / 1024} KB");
            }
            total += e.Length;
            string hash = "";
            if (!isDir)                  // 目录条目不算
            {
                using var s = e.Open();
                using var sha = SHA256.Create();
                hash = Convert.ToHexString(sha.ComputeHash(s)).ToLowerInvariant()[..16];
                r.Files.Add(new Entry(name, e.FullName, e.Length, hash));
            }
        }

        if (total > MaxTotalBytes) r.Problems.Add($"整包过大：{total / 1024} KB");

        // 安装器（server.py::_plugin_zip_files / _import_character_zip）允许整包放在
        // **一个**顶层目录里，判定看的是去掉前缀之后的相对名。这里必须同一口径，
        // 否则 `demo/plugin.json` 这种能正常安装的包会被报「只能放 .py」——
        // 一份和安装器打架的审计报告，比没有报告更糟。
        var names = r.Files.Select(f => f.Name).ToList();
        var (_, rel) = CommonPrefix(names);

        // 必填结构：character.json（角色包才要；插件包只看 .py）
        var charJson = r.Files.FirstOrDefault(f => rel(f.Name) == "character.json");
        if (pluginMode)
        {
            if (r.Files.Count == 0) r.Problems.Add("zip 是空的");
            else if (r.Files.Count > PluginMaxFiles)
                r.Problems.Add($"文件太多（>{PluginMaxFiles}）");
            var pys = r.Files.Count(f => rel(f.Name).EndsWith(".py"));
            if (r.Files.Count > 0 && pys == 0) r.Problems.Add("插件包里没有 .py");
            foreach (var f in r.Files)
            {
                var rn = rel(f.Name);
                // 子目录必须最先判：放在 plugin.json/README/_ 前缀的 continue 之后，
                // `_shared/util.py` 会被「共享模块」那条静默跳过，安装器却会拒。
                if (rn.Contains('/')) { r.Problems.Add($"插件文件不能放在子目录：{rn}"); continue; }
                if (rn is "plugin.json" or "README.md") continue;
                if (rn.StartsWith('_') && rn.EndsWith(".py"))
                    continue;                              // 共享模块：留给下面那条循环报
                if (!rn.EndsWith(".py")) { r.Problems.Add($"插件包只能放 .py（发现 {rn}）"); continue; }
                if (!PluginNameRe.IsMatch(rn))
                    r.Problems.Add($"文件名不合法：{rn}（小写字母开头，字母/数字/_）");
            }
        }
        else if (charJson is null)
        {
            r.Problems.Add("缺少 character.json");
        }
        else if (charJson is not null)
        {
            // 必须用原始 FullName：归一化过的名字（反斜杠已换成 /）在 zip 里取不到。
            var entry = zip.GetEntry(charJson.FullName) ?? zip.GetEntry(charJson.Name);
            if (entry is null)
            {
                // 以前这里是静默跳过：能找到 character.json、却读不到，
                // 于是 id/name 一条都不校验，报告还写 Ok=true。
                r.Problems.Add("character.json 读不到（zip 条目名和归一化名对不上）");
            }
            else
            {
                using var s = entry.Open();
                using var reader = new StreamReader(s, Encoding.UTF8);
                var text = reader.ReadToEnd();
                try
                {
                    using var doc = JsonDocument.Parse(text);
                    if (doc.RootElement.TryGetProperty("id", out var id))
                    {
                        r.CharacterId = id.GetString();
                        if (string.IsNullOrWhiteSpace(r.CharacterId))
                            r.Problems.Add("character.json 的 id 是空的");
                    }
                    else r.Problems.Add("character.json 缺少 id");
                    if (doc.RootElement.TryGetProperty("name", out var nm) &&
                        string.IsNullOrWhiteSpace(nm.GetString()))
                        r.Problems.Add("character.json 的 name 是空的");
                }
                catch (JsonException e)
                {
                    r.Problems.Add($"character.json 不是合法 JSON：{e.Message}");
                }
            }
        }

        // 插件文件：*.py 不能以 _ 开头（那是共享模块，不该单独装）
        foreach (var f in r.Files.Where(f => rel(f.Name).EndsWith(".py")))
        {
            var stem = Path.GetFileNameWithoutExtension(rel(f.Name));
            if (stem.StartsWith('_')) r.Problems.Add($"插件名以 _ 开头（那是共享模块）：{f.Name}");
            else r.Plugins.Add(stem);
        }

        r.Entries = n;
        r.Ok = r.Problems.Count == 0;
        return r;
    }

    private static int SelfTest()
    {
        var tmp = Path.Combine(Path.GetTempPath(), $"pkgcheck-{Guid.NewGuid():N}");
        Directory.CreateDirectory(tmp);
        string Make(string name, Action<ZipArchive> fill)
        {
            var fp = Path.Combine(tmp, name);
            using var fs = File.Create(fp);
            using var z = new ZipArchive(fs, ZipArchiveMode.Create);
            fill(z);
            return fp;
        }
        static void Put(ZipArchive z, string name, string body)
        {
            using var w = new StreamWriter(z.CreateEntry(name).Open());
            w.Write(body);
        }
        try
        {
            // 1) 路径穿越必须被拦下（任一模式）
            var bad = Make("traversal.zip", z => Put(z, "../../etc/passwd", "x"));
            var r1 = Inspect(bad);
            var trav = r1.Problems.Any(x => x.Contains("路径穿越"));
            // 每例的明细走 stderr，stdout 只留**一行**总结 ——
            // 调用方（tests/test_polyglot.py）是对整段 stdout 做 json.loads 的。
            Console.Error.WriteLine(Json(new { ok = trav, self = "路径穿越必须被拦下",
                                               problems = r1.Problems, by = "csharp" }));

            var cases = new (string Name, bool Plugin, Func<Report, bool> Assert)[]
            {
                // 2) 整包放在一个顶层目录里：安装器接受，这里也不能报错。
                //    以前按原始路径判 == "character.json"，这种**合法**包会被报
                //    「缺少 character.json」（server.py 会先剥掉前缀）。
                ("wrap.zip", false, r => r.CharacterId == "demo" && r.Problems.Count == 0),
                // 3) 插件包：一个顶层目录 + plugin.json/README.md，安装器接受
                ("plug-wrap.zip", true, r => r.Problems.Count == 0),
                // 4) 插件包只有 README.md：安装器会拒（zip 里没有 .py）
                ("plug-no-py.zip", true, r => r.Problems.Any(x => x.Contains("没有 .py"))),
                // 5) 插件包混进 .txt：必须报
                ("plug-txt.zip", true, r => r.Problems.Any(x => x.Contains("只能放 .py"))),
                // 6) 混进子目录：安装器会拒，必须报（以前被 _ 前缀那条静默跳过）
                ("plug-sub.zip", true, r => r.Problems.Any(x => x.Contains("子目录"))),
                // 7) `zip -r x.zip .` 的产物（带 ./ 前缀）：安装器接受，不能误报
                ("dot-prefix.zip", true, r => r.Problems.Count == 0),
                // 8) 反斜杠路径的角色包：能读到 character.json，不能「假通过」
                ("backslash.zip", false, r => r.CharacterId == "demo" && r.Problems.Count == 0),
                // 9) 绝对路径：Normalize 会吃掉前导 /，必须在原始名上判
                ("abs.zip", true, r => r.Problems.Any(x => x.Contains("路径穿越"))),
            };
            var okAll = true;
            foreach (var c in cases)
            {
                var path = Make(c.Name, z =>
                {
                    switch (c.Name)
                    {
                        case "wrap.zip":
                            Put(z, "demo/character.json", "{\"id\":\"demo\",\"name\":\"演示\"}");
                            Put(z, "demo/SOUL.md", new string('人', 120));
                            break;
                        case "plug-wrap.zip":
                            Put(z, "demo/demo.py", "x = 1\n");
                            Put(z, "demo/README.md", "# demo\n");
                            Put(z, "demo/plugin.json", "{}");
                            break;
                        case "plug-no-py.zip":
                            Put(z, "README.md", "# nothing\n");
                            break;
                        case "plug-sub.zip":
                            Put(z, "top.py", "x = 1\n");
                            Put(z, "_shared/util.py", "y = 2\n");   // 有 _ 前缀，但也在子目录里
                            break;
                        case "dot-prefix.zip":
                            Put(z, "./demo.py", "x = 1\n");
                            break;
                        case "abs.zip":
                            Put(z, "/etc/passwd", "x");
                            Put(z, "ok.py", "x = 1\n");
                            break;
                        case "backslash.zip":
                            Put(z, @"demo\character.json", "{\"id\":\"demo\",\"name\":\"演示\"}");
                            Put(z, @"demo\SOUL.md", new string('人', 120));
                            break;
                        default:
                            Put(z, "demo.py", "x = 1\n");
                            Put(z, "notes.txt", "x");
                            break;
                    }
                });
                var rep = Inspect(path, c.Plugin);
                var ok = c.Assert(rep);
                okAll &= ok;
                Console.Error.WriteLine(Json(new { ok, self = c.Name, problems = rep.Problems,
                                                   character = rep.CharacterId, by = "csharp" }));
            }
            var allOk = trav && okAll;
            Console.WriteLine(Json(new { ok = allOk, self = "自检（含插件模式）",
                                         cases = cases.Length + 1, by = "csharp" }));
            return allOk ? 0 : 1;
        }
        finally
        {
            try { Directory.Delete(tmp, true); } catch { /* 忽略 */ }
        }
    }

    private static string Json(object o) =>
        JsonSerializer.Serialize(o, new JsonSerializerOptions
        {
            WriteIndented = false,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        });
}
