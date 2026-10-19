<?php
/**
 * 插件市场索引（PHP，零依赖，走外部插件协议常驻）
 *
 * 为什么是 PHP：这个活是「扫目录 + 读文件头 + 拼 JSON」，纯文本处理 + 数组排序，
 * PHP 在这类脚本任务上是原生强项，写出来最短（而且宿主上已经有 php-cli）。
 *
 * 干的事：
 *   1. 扫 ../*.py 的插件头（NAME / DESCRIPTION / CHARACTERS / SYSTEM）
 *   2. 扫 ext/*.ext.json（别的语言写的插件）
 *   3. 统计命令数、按分类分组、算出「可安装 / 已启用」
 * 面板「插件 → 市场」直接吃这份清单。
 *
 * 调用：cmd=manifest（无参）→ 返回 JSON 字符串
 */

function read_head(string $path): array
{
    $fh = @fopen($path, 'r');
    if (!$fh) return [];
    $head = fread($fh, 8192);
    fclose($fh);
    $out = [];
    foreach (['NAME', 'DESCRIPTION', 'VERSION'] as $key) {
        if (preg_match('/^' . $key . '\s*=\s*"([^"]*)"/m', $head, $m)) {
            $out[strtolower($key)] = $m[1];
        }
    }
    if (preg_match('/^SYSTEM\s*=\s*True/m', $head)) $out['system'] = true;
    if (preg_match('/^CHARACTERS\s*=\s*\(([^)]*)\)/m', $head, $m)) {
        preg_match_all('/"([a-z0-9_\-]+)"/', $m[1], $ids);
        $out['characters'] = $ids[1];
    }
    if (preg_match_all('/\.on_command\(\s*"([^"]+)"/', file_get_contents($path), $cmds)) {
        $out['commands'] = array_values(array_unique($cmds[1]));
    }
    return $out;
}

function manifest(string $base): array
{
    $items = [];
    foreach (glob($base . '/*.py') as $path) {
        $name = basename($path, '.py');
        if (str_starts_with($name, '_')) continue;
        $h = read_head($path);
        $items[] = [
            'id'          => $name,
            'title'       => $h['name'] ?? $name,
            'description' => $h['description'] ?? '',
            'version'     => $h['version'] ?? '1.0',
            'kind'        => 'python',
            'system'      => (bool)($h['system'] ?? false),
            'characters'  => $h['characters'] ?? [],
            'commands'    => $h['commands'] ?? [],
            'bytes'       => filesize($path),
        ];
    }
    foreach (glob($base . '/ext/*.ext.json') as $path) {
        $spec = json_decode(file_get_contents($path), true) ?: [];
        $items[] = [
            'id'          => $spec['id'] ?? basename($path, '.ext.json'),
            'title'       => $spec['title'] ?? '',
            'description' => $spec['description'] ?? '',
            'version'     => $spec['version'] ?? '1.0',
            'kind'        => $spec['language'] ?? 'external',
            'system'      => false,
            'characters'  => [],
            'commands'    => [],
            'bytes'       => filesize($path),
        ];
    }
    usort($items, function ($a, $b) {
        if ($a['system'] !== $b['system']) return $a['system'] ? -1 : 1;
        return strcmp($a['title'] ?: $a['id'], $b['title'] ?: $b['id']);
    });
    $byKind = [];
    $byChar = [];
    foreach ($items as $it) {
        $byKind[$it['kind']] = ($byKind[$it['kind']] ?? 0) + 1;
        foreach ($it['characters'] as $c) {
            $byChar[$c] = ($byChar[$c] ?? 0) + 1;
        }
    }
    arsort($byKind);
    ksort($byChar);
    return [
        'ok'       => true,
        'total'    => count($items),
        'by_kind'  => $byKind,
        'by_char'  => $byChar,
        'commands' => array_sum(array_map(fn($i) => count($i['commands']), $items)),
        'items'    => $items,
        'by'       => 'php ' . PHP_VERSION,
    ];
}

function handle(array $msg, string $base): array
{
    $op = $msg['op'] ?? '';
    if ($op === 'hello') {
        return ['ok' => true, 'name' => 'market', 'title' => '插件市场索引', 'version' => '1.0',
                'commands' => [['name' => 'manifest', 'help' => '扫一遍插件目录，出清单']]];
    }
    if ($op === 'bye') { exit(0); }
    if ($op === 'call') {
        $id = $msg['id'] ?? 0;
        try {
            if (($msg['cmd'] ?? '') !== 'manifest') {
                return ['id' => $id, 'ok' => false, 'error' => '没有命令 ' . ($msg['cmd'] ?? '')];
            }
            return ['id' => $id, 'ok' => true,
                    'reply' => json_encode(manifest($base), JSON_UNESCAPED_UNICODE)];
        } catch (Throwable $e) {
            return ['id' => $id, 'ok' => false, 'error' => $e->getMessage()];
        }
    }
    return ['ok' => false, 'error' => 'unknown op'];
}

// NDJSON over stdio —— 和其它语言的插件完全一致的协议
// __DIR__ = features/ext/tools；插件本体在 features/ 与 features/ext/
$base = realpath(__DIR__ . '/../..') ?: __DIR__;
$stdin = fopen('php://stdin', 'r');
while (($line = fgets($stdin)) !== false) {
    $line = trim($line);
    if ($line === '') continue;
    $msg = json_decode($line, true);
    if (!is_array($msg)) continue;
    $res = handle($msg, $base);
    fwrite(STDOUT, json_encode($res, JSON_UNESCAPED_UNICODE) . "\n");
    fflush(STDOUT);
}
