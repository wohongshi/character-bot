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

namespace PkgCheck;

internal static class Program
{
    // 安装器接受的上限：单文件 2 MB，整包 8 MB（插件是单文件脚本，不需要更大）
    private const long MaxEntryBytes = 2 * 1024 * 1024;
    private const long MaxTotalBytes = 8 * 1024 * 1024;
    private const int MaxEntries = 200;

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

    private sealed record Entry(string Name, long Bytes, string Sha256);

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
            var name = e.FullName.Replace('\\', '/');
            if (name.StartsWith('/') || name.Contains(":") || name.Split('/').Any(s => s == ".."))
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
            if (!name.EndsWith('/'))     // 目录条目不算
            {
                using var s = e.Open();
                using var sha = SHA256.Create();
                hash = Convert.ToHexString(sha.ComputeHash(s)).ToLowerInvariant()[..16];
                r.Files.Add(new Entry(name, e.Length, hash));
            }
        }

        if (total > MaxTotalBytes) r.Problems.Add($"整包过大：{total / 1024} KB");

        // 必填结构：character.json（角色包才要；插件包只看 .py）
        var charJson = r.Files.FirstOrDefault(f => f.Name == "character.json");
        if (pluginMode)
        {
            if (r.Files.Count == 0) r.Problems.Add("zip 是空的");
            foreach (var f in r.Files.Where(f => !f.Name.EndsWith(".py")))
                if (f.Name is not ("plugin.json" or "README.md"))
                    r.Problems.Add($"插件包只能放 .py（发现 {f.Name}）");
        }
        else if (charJson is null)
        {
            r.Problems.Add("缺少 character.json");
        }
        else if (charJson is not null)
        {
            var entry = zip.GetEntry(charJson.Name);
            if (entry is not null)
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
        foreach (var f in r.Files.Where(f => f.Name.EndsWith(".py")))
        {
            var stem = Path.GetFileNameWithoutExtension(f.Name);
            if (stem.StartsWith('_')) r.Problems.Add($"插件名以 _ 开头（那是共享模块）：{f.Name}");
            else r.Plugins.Add(stem);
        }

        r.Entries = n;
        r.Ok = r.Problems.Count == 0;
        return r;
    }

    private static int SelfTest()
    {
        var tmp = Path.Combine(Path.GetTempPath(), $"pkgcheck-{Guid.NewGuid():N}.zip");
        try
        {
            using (var fs = File.Create(tmp))
            using (var zip = new ZipArchive(fs, ZipArchiveMode.Create))
            {
                // 故意塞一个路径穿越条目 + 缺 character.json
                var bad = zip.CreateEntry("../../etc/passwd");
                using (var w = new StreamWriter(bad.Open())) w.Write("x");
            }
            var rep = Inspect(tmp);
            var caught = rep.Problems.Any(p => p.Contains("路径穿越"));
            Console.WriteLine(Json(new
            {
                ok = caught,
                self = "路径穿越必须被拦下",
                problems = rep.Problems,
                by = "csharp",
            }));
            return caught ? 0 : 1;
        }
        finally
        {
            try { File.Delete(tmp); } catch { /* 忽略 */ }
        }
    }

    private static string Json(object o) =>
        JsonSerializer.Serialize(o, new JsonSerializerOptions
        {
            WriteIndented = false,
            Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
        });
}
