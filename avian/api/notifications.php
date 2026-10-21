<?php
// Admin-only Apprise notification settings. Apprise URLs carry credentials
// (webhook tokens, broker passwords), so status responses list each URL only
// by an opaque id and its service scheme. An explicit reveal requires a
// verifier-bound admin session even when ordinary direct-LAN access is trusted.
//
// apprise.txt and body.txt live in the BirdNET-Pi user's home. This endpoint
// never opens them; the root helper reads and writes them as that user.

declare(strict_types=1);

require_once __DIR__ . '/admin-auth.php';

const AVIAN_NOTIFICATIONS_HELPER_VERSION = 4;
const AVIAN_NOTIFICATIONS_URL_MAX = 20;
const AVIAN_NOTIFICATIONS_LINE_MAX = 1024;
const AVIAN_NOTIFICATIONS_CONFIG_MAX = 16384;
const AVIAN_NOTIFICATIONS_BODY_MAX = 4000;
const AVIAN_NOTIFICATIONS_TITLE_MAX = 120;
const AVIAN_NOTIFICATIONS_SPECIES_MAX = 1000;
const AVIAN_NOTIFICATIONS_COOLDOWN_MAX = 86400;
const AVIAN_NOTIFICATIONS_TEST_INTERVAL = 10;
const AVIAN_NOTIFICATIONS_TEMPLATE_VARIABLES = [
    'comname', 'sciname', 'confidencepct', 'confidence', 'listenurl',
    'friendlyurl', 'date', 'time', 'week', 'latitude', 'longitude', 'cutoff',
    'sens', 'overlap', 'image', 'flickrimage', 'reason',
];
// API field => birdnet.conf key. Upstream names the exclude list
// APPRISE_ONLY_NOTIFY_SPECIES_NAMES and the include list ..._2.
const AVIAN_NOTIFICATIONS_TRIGGERS = [
    'notify_each' => 'APPRISE_NOTIFY_EACH_DETECTION',
    'notify_first_today' => 'APPRISE_NOTIFY_NEW_SPECIES_EACH_DAY',
    'notify_new_species' => 'APPRISE_NOTIFY_NEW_SPECIES',
    'weekly_report' => 'APPRISE_WEEKLY_REPORT',
];
const AVIAN_NOTIFICATIONS_TEXT = [
    'title' => 'APPRISE_NOTIFICATION_TITLE',
    'only_species' => 'APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2',
    'exclude_species' => 'APPRISE_ONLY_NOTIFY_SPECIES_NAMES',
];
const AVIAN_NOTIFICATIONS_COOLDOWN_KEY = 'APPRISE_MINIMUM_SECONDS_BETWEEN_NOTIFICATIONS_PER_SPECIES';

function notifications_response(int $status, array $body): void {
    http_response_code($status);
    echo json_encode($body, JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
    exit;
}

function notifications_conf_path(): string {
    // CLI-only override keeps runtime tests away from the real station config.
    if (PHP_SAPI === 'cli') {
        $override = getenv('AV_NOTIFICATIONS_CONF');
        if (is_string($override) && $override !== '') return $override;
    }
    return '/etc/birdnet/birdnet.conf';
}

function notifications_conf_keys(): array {
    return array_merge(
        array_values(AVIAN_NOTIFICATIONS_TRIGGERS),
        array_values(AVIAN_NOTIFICATIONS_TEXT),
        [AVIAN_NOTIFICATIONS_COOLDOWN_KEY]
    );
}

function notifications_parse_conf_value(string $raw): ?string {
    if (strlen($raw) > 2048) return null;
    $raw = trim($raw);
    if ($raw === '') return '';
    $first = $raw[0];
    if ($first === "'" || $first === '"') {
        // Values are read literally, as BirdNET-Pi's Python reader does. A
        // title such as "New $comname" is a template, not a shell expansion.
        $value = '';
        $length = strlen($raw);
        for ($index = 1; $index < $length; $index++) {
            $character = $raw[$index];
            if ($character === $first) {
                return preg_match('/\A\s*(?:#.*)?\z/D', substr($raw, $index + 1)) === 1
                    ? $value
                    : null;
            }
            if ($first === '"' && $character === '\\' && $index + 1 < $length
                && in_array($raw[$index + 1], ['\\', '"'], true)) {
                $index++;
                $character = $raw[$index];
            }
            $value .= $character;
        }
        return null;
    }
    if (preg_match('/\A([^\s#]*)(?:\s+#.*)?\z/D', $raw, $match) !== 1) return null;
    return $match[1];
}

function notifications_read_conf(string $path): array {
    if (!is_readable($path) || is_dir($path)) return [];
    $lines = @file($path, FILE_IGNORE_NEW_LINES);
    if (!is_array($lines)) return [];
    $keys = notifications_conf_keys();
    $values = [];
    foreach ($lines as $line) {
        if ($line === '' || $line[0] === '#') continue;
        if (preg_match('/^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$/', $line, $match) !== 1) {
            continue;
        }
        // Only notification keys are retained, keeping unrelated secrets out
        // of memory and out of any accidental response serialization.
        if (!in_array($match[1], $keys, true)) continue;
        $value = notifications_parse_conf_value($match[2]);
        if ($value === null) unset($values[$match[1]]);
        else $values[$match[1]] = $value;
    }
    return $values;
}

function notifications_valid_title(string $title): bool {
    if (strlen($title) > AVIAN_NOTIFICATIONS_TITLE_MAX) return false;
    if (preg_match('/["\\\\`\x00-\x1F\x7F]/', $title) === 1) return false;
    if (preg_match('//u', $title) !== 1) return false;
    // birdnet.conf is sourced by bash: only plain template variables may
    // follow a dollar sign, never a command or parameter substitution.
    $pattern = '/\$(?:' . implode('|', AVIAN_NOTIFICATIONS_TEMPLATE_VARIABLES) . ')/';
    return !str_contains((string)preg_replace($pattern, '', $title), '$');
}

function notifications_valid_species(string $value): bool {
    return strlen($value) <= AVIAN_NOTIFICATIONS_SPECIES_MAX
        && preg_match('/["\\\\`$\x00-\x1F\x7F]/', $value) !== 1
        && preg_match('//u', $value) === 1;
}

function notifications_url_scheme(string $line): ?string {
    $pattern = '/\A(?:[a-z0-9_-]+(?:,[a-z0-9_-]+)*=)?([a-z][a-z0-9+.-]{1,31}):\/\/[^\s\x00-\x1F\x7F]+\z/D';
    if (strlen($line) > AVIAN_NOTIFICATIONS_LINE_MAX) return null;
    if (preg_match('//u', $line) !== 1) return null;
    return preg_match($pattern, $line, $match) === 1 ? $match[1] : null;
}

function notifications_is_comment(string $line): bool {
    return strlen($line) <= AVIAN_NOTIFICATIONS_LINE_MAX
        && preg_match('/\A#[^\x00-\x1F\x7F]*\z/D', $line) === 1
        && preg_match('//u', $line) === 1;
}

function notifications_url_id(string $line): string {
    return substr(hash('sha256', $line), 0, 12);
}

// Splits saved lines into URLs this panel manages, comments it preserves,
// and anything else (include directives, YAML, hand edits) it refuses to
// rewrite.
function notifications_classify_lines(array $lines): array {
    $urls = [];
    $unsupported = 0;
    foreach ($lines as $line) {
        if (!is_string($line)) {
            $unsupported++;
            continue;
        }
        if (notifications_is_comment($line)) continue;
        $scheme = notifications_url_scheme($line);
        if ($scheme === null) {
            $unsupported++;
            continue;
        }
        $urls[] = ['id' => notifications_url_id($line), 'scheme' => $scheme, 'line' => $line];
    }
    return ['urls' => $urls, 'unsupported' => $unsupported];
}

function notifications_flag(array $conf, string $key): bool {
    return (string)($conf[$key] ?? '0') === '1';
}

function notifications_public_status(array $conf, array $apprise, bool $reveal = false): array {
    $classified = notifications_classify_lines($apprise['urls'] ?? []);
    $urls = [];
    foreach ($classified['urls'] as $entry) {
        $public = ['id' => $entry['id'], 'scheme' => $entry['scheme']];
        if ($reveal) $public['url'] = $entry['line'];
        $urls[] = $public;
    }
    $triggers = [];
    foreach (AVIAN_NOTIFICATIONS_TRIGGERS as $field => $key) {
        $triggers[$field] = notifications_flag($conf, $key);
    }
    $text = [];
    foreach (AVIAN_NOTIFICATIONS_TEXT as $field => $key) {
        $text[$field] = (string)($conf[$key] ?? '');
    }
    $cooldown = filter_var(
        $conf[AVIAN_NOTIFICATIONS_COOLDOWN_KEY] ?? 0,
        FILTER_VALIDATE_INT,
        ['options' => ['min_range' => 0, 'max_range' => AVIAN_NOTIFICATIONS_COOLDOWN_MAX]]
    );
    return [
        'ok' => true,
        'triggers' => $triggers,
        'cooldown_seconds' => $cooldown === false ? 0 : (int)$cooldown,
        'title' => $text['title'],
        'only_species' => $text['only_species'],
        'exclude_species' => $text['exclude_species'],
        'body' => (string)($apprise['body'] ?? ''),
        'urls' => $urls,
        'url_limit' => AVIAN_NOTIFICATIONS_URL_MAX,
        'unsupported_lines' => $classified['unsupported'],
        'template_variables' => AVIAN_NOTIFICATIONS_TEMPLATE_VARIABLES,
    ];
}

function notifications_validate_update(array $body, array $apprise): array {
    $allowed = array_merge(
        array_keys(AVIAN_NOTIFICATIONS_TRIGGERS),
        array_keys(AVIAN_NOTIFICATIONS_TEXT),
        ['cooldown_seconds', 'body', 'add_url', 'remove_url_id']
    );
    if ($body === []) return ['ok' => false, 'status' => 400, 'error' => 'no setting supplied'];
    foreach (array_keys($body) as $key) {
        if (!is_string($key) || !in_array($key, $allowed, true)) {
            return ['ok' => false, 'status' => 400, 'error' => 'unknown setting'];
        }
    }

    // Each request is a patch, so independently saved controls never restore
    // stale values for their neighbours.
    $updates = [];
    foreach (AVIAN_NOTIFICATIONS_TRIGGERS as $field => $key) {
        if (!array_key_exists($field, $body)) continue;
        if (!is_bool($body[$field])) {
            return ['ok' => false, 'status' => 400, 'error' => $field . ' must be true or false'];
        }
        $updates[$key] = $body[$field] ? '1' : '0';
    }
    if (array_key_exists('cooldown_seconds', $body)) {
        $cooldown = $body['cooldown_seconds'];
        if (!is_int($cooldown) || $cooldown < 0 || $cooldown > AVIAN_NOTIFICATIONS_COOLDOWN_MAX) {
            return ['ok' => false, 'status' => 400, 'error' => 'cooldown must be 0 to 86400 seconds'];
        }
        $updates[AVIAN_NOTIFICATIONS_COOLDOWN_KEY] = (string)$cooldown;
    }
    if (array_key_exists('title', $body)) {
        $title = is_string($body['title']) ? trim($body['title'], ' ') : null;
        if ($title === null || !notifications_valid_title($title)) {
            return ['ok' => false, 'status' => 400, 'error' => 'title may use up to 120 characters, '
                . 'no quotes, backslashes or backticks, and $ only before a template variable'];
        }
        $updates[AVIAN_NOTIFICATIONS_TEXT['title']] = $title;
    }
    foreach (['only_species', 'exclude_species'] as $field) {
        if (!array_key_exists($field, $body)) continue;
        $value = is_string($body[$field]) ? trim($body[$field], ' ') : null;
        if ($value === null || !notifications_valid_species($value)) {
            return ['ok' => false, 'status' => 400, 'error' => 'species lists may use up to 1000 characters, '
                . 'without quotes, backslashes, backticks or $'];
        }
        $updates[AVIAN_NOTIFICATIONS_TEXT[$field]] = $value;
    }

    $newBody = null;
    if (array_key_exists('body', $body)) {
        $newBody = $body['body'];
        if (!is_string($newBody)
            || strlen($newBody) > AVIAN_NOTIFICATIONS_BODY_MAX
            || str_contains($newBody, "\0")
            || preg_match('//u', $newBody) !== 1) {
            return ['ok' => false, 'status' => 400, 'error' => 'message template must be text up to 4000 bytes'];
        }
    }

    $newLines = null;
    if (array_key_exists('add_url', $body) || array_key_exists('remove_url_id', $body)) {
        $lines = $apprise['urls'] ?? [];
        $classified = notifications_classify_lines($lines);
        if ($classified['unsupported'] > 0) {
            return ['ok' => false, 'status' => 409, 'error' => 'apprise.txt has lines this panel cannot '
                . 'manage, such as include directives; edit it over SSH'];
        }
        if (array_key_exists('remove_url_id', $body)) {
            $id = $body['remove_url_id'];
            if (!is_string($id) || preg_match('/\A[0-9a-f]{12}\z/D', $id) !== 1) {
                return ['ok' => false, 'status' => 400, 'error' => 'unknown notification URL'];
            }
            $kept = [];
            $removed = false;
            foreach ($lines as $line) {
                if (!$removed && notifications_url_scheme($line) !== null
                    && hash_equals(notifications_url_id($line), $id)) {
                    $removed = true;
                    continue;
                }
                $kept[] = $line;
            }
            if (!$removed) return ['ok' => false, 'status' => 409, 'error' => 'notification URL was already removed'];
            $lines = $kept;
        }
        if (array_key_exists('add_url', $body)) {
            $url = is_string($body['add_url']) ? trim($body['add_url'], ' ') : '';
            if (notifications_url_scheme($url) === null) {
                return ['ok' => false, 'status' => 400, 'error' => 'enter one Apprise URL, such as discord://… or mqtt://…'];
            }
            if (in_array($url, $lines, true)) {
                return ['ok' => false, 'status' => 409, 'error' => 'that URL is already saved'];
            }
            $lines[] = $url;
        }
        $count = count(notifications_classify_lines($lines)['urls']);
        $total = array_sum(array_map(static fn(string $line): int => strlen($line) + 1, $lines));
        if ($count > AVIAN_NOTIFICATIONS_URL_MAX || $total > AVIAN_NOTIFICATIONS_CONFIG_MAX) {
            return ['ok' => false, 'status' => 409, 'error' => 'up to 20 notification URLs can be saved'];
        }
        $newLines = array_values($lines);
    }

    return ['ok' => true, 'updates' => $updates, 'lines' => $newLines, 'body' => $newBody];
}

function notifications_run_admin_control(string $control, array $arguments, string $input = ''): array {
    if (!is_executable($control)) {
        return ['ok' => false, 'error' => 'admin control is not installed'];
    }
    $command = ['/usr/bin/sudo', '-n', $control];
    foreach ($arguments as $argument) $command[] = (string)$argument;
    $pipes = [];
    $process = proc_open($command, [
        0 => ['pipe', 'r'],
        1 => ['pipe', 'w'],
        2 => ['pipe', 'w'],
    ], $pipes, null, null, ['bypass_shell' => true]);
    if (!is_resource($process)) {
        return ['ok' => false, 'error' => 'could not start admin control'];
    }
    $offset = 0;
    $inputLength = strlen($input);
    $writeOk = true;
    while ($offset < $inputLength) {
        $written = @fwrite($pipes[0], substr($input, $offset));
        if (!is_int($written) || $written < 1) {
            $writeOk = false;
            break;
        }
        $offset += $written;
    }
    fclose($pipes[0]);
    $stdout = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    // Never surface stderr: it can contain a credential-bearing URL.
    stream_get_contents($pipes[2]);
    fclose($pipes[2]);
    $status = proc_close($process);
    if (!$writeOk) return ['ok' => false, 'error' => 'could not send settings to admin control'];
    $decoded = json_decode(is_string($stdout) ? $stdout : '', true);
    if ($status !== 0 || !is_array($decoded) || empty($decoded['ok'])) {
        $error = is_array($decoded) && is_string($decoded['error'] ?? null)
            && str_starts_with($decoded['error'], 'notification')
            ? $decoded['error']
            : 'admin control failed';
        return ['ok' => false, 'error' => $error];
    }
    return ['ok' => true, 'data' => $decoded];
}

function notifications_read_apprise(string $control): ?array {
    $read = notifications_run_admin_control($control, ['apprise-read']);
    $data = $read['data'] ?? null;
    if (empty($read['ok']) || !is_array($data)
        || !is_array($data['urls'] ?? null) || !is_string($data['body'] ?? null)) {
        return null;
    }
    return ['urls' => array_values($data['urls']), 'body' => $data['body']];
}

function notifications_helper_current(string $control): bool {
    $version = notifications_run_admin_control($control, ['version']);
    return !empty($version['ok'])
        && (int)($version['data']['version'] ?? 0) >= AVIAN_NOTIFICATIONS_HELPER_VERSION;
}

function notifications_test_allowed(): bool {
    $path = sys_get_temp_dir() . '/avian-notification-test';
    $handle = @fopen($path, 'c+');
    if ($handle === false) return false;
    try {
        if (!flock($handle, LOCK_EX | LOCK_NB)) return false;
        $last = (int)stream_get_contents($handle);
        if ($last > time() - AVIAN_NOTIFICATIONS_TEST_INTERVAL) return false;
        ftruncate($handle, 0);
        rewind($handle);
        fwrite($handle, (string)time());
        return true;
    } finally {
        fclose($handle);
    }
}

function notifications_canonical_status(string $confPath, string $control): ?array {
    $apprise = notifications_read_apprise($control);
    if ($apprise === null || !is_readable($confPath)) return null;
    return notifications_public_status(notifications_read_conf($confPath), $apprise);
}

function notifications_main(): void {
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('Referrer-Policy: no-referrer');
    avian_require_admin();

    $method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
    $confPath = notifications_conf_path();
    $control = getenv('AV_ADMIN_CONTROL') ?: '/usr/local/sbin/avian-admin-control';
    if (!is_readable($confPath)) {
        notifications_response(503, ['ok' => false, 'error' => 'station config is unavailable']);
    }
    if (!notifications_helper_current($control)) {
        notifications_response(503, [
            'ok' => false,
            'update_required' => true,
            'error' => 'update the station to manage notifications here',
        ]);
    }

    if ($method === 'GET') {
        $revealValue = $_GET['reveal'] ?? '0';
        if (!is_string($revealValue) || !in_array($revealValue, ['0', '1'], true)) {
            notifications_response(400, ['ok' => false, 'error' => 'invalid reveal setting']);
        }
        if ($revealValue === '1') avian_require_admin_proof();
        $apprise = notifications_read_apprise($control);
        if ($apprise === null) {
            notifications_response(503, ['ok' => false, 'error' => 'notification settings could not be read']);
        }
        $status = notifications_public_status(notifications_read_conf($confPath), $apprise, $revealValue === '1');
        $state = avian_admin_state();
        $status['reveal_available'] = !empty($state['valid']) && !empty($state['configured']);
        notifications_response(200, $status);
    }

    avian_require_json_action();
    $body = json_decode((string)file_get_contents('php://input'), true);
    if (!is_array($body) || array_is_list($body)) {
        notifications_response(400, ['ok' => false, 'error' => 'expected a JSON object']);
    }

    if (array_key_exists('action', $body)) {
        if ($body !== ['action' => 'test']) {
            notifications_response(400, ['ok' => false, 'error' => 'unknown action']);
        }
        if (!notifications_test_allowed()) {
            header('Retry-After: ' . AVIAN_NOTIFICATIONS_TEST_INTERVAL);
            notifications_response(429, ['ok' => false, 'error' => 'wait a few seconds before sending another test']);
        }
        $test = notifications_run_admin_control($control, ['apprise-test']);
        if (empty($test['ok'])) {
            notifications_response(502, [
                'ok' => false,
                'error' => 'the test notification was not delivered; check the URLs and the station logs',
            ]);
        }
        notifications_response(200, ['ok' => true, 'sent' => true]);
    }

    $apprise = notifications_read_apprise($control);
    if ($apprise === null) {
        notifications_response(503, ['ok' => false, 'error' => 'notification settings could not be read']);
    }
    $validation = notifications_validate_update($body, $apprise);
    if (empty($validation['ok'])) {
        notifications_response((int)$validation['status'], [
            'ok' => false,
            'error' => (string)$validation['error'],
        ]);
    }

    $restart = false;
    if ($validation['updates'] !== []) {
        $payload = '';
        foreach ($validation['updates'] as $key => $value) $payload .= $key . "\0" . $value . "\0";
        $write = notifications_run_admin_control(
            $control,
            ['config-set-stdin', (string)count($validation['updates'])],
            $payload
        );
        if (empty($write['ok'])) notifications_response(503, ['ok' => false, 'error' => $write['error']]);
        $restart = true;
    }
    if (is_array($validation['lines'])) {
        $payload = '';
        foreach ($validation['lines'] as $line) $payload .= $line . "\0";
        $write = notifications_run_admin_control(
            $control,
            ['apprise-urls-set-stdin', (string)count($validation['lines'])],
            $payload
        );
        $payload = '';
        if (empty($write['ok'])) notifications_response(503, ['ok' => false, 'error' => $write['error']]);
        $restart = true;
    }
    if (is_string($validation['body'])) {
        // The analyzer reads the template for every notification: no restart.
        $write = notifications_run_admin_control($control, ['apprise-body-set-stdin'], $validation['body']);
        if (empty($write['ok'])) notifications_response(503, ['ok' => false, 'error' => $write['error']]);
    }
    $validation = [];

    // The analyzer caches birdnet.conf and the Apprise service list.
    $restarted = !$restart
        || !empty(notifications_run_admin_control($control, ['restart', 'birdnet_analysis'])['ok']);
    $response = notifications_canonical_status($confPath, $control);
    if (!$restarted) {
        $failure = [
            'ok' => false,
            'saved' => true,
            'error' => 'settings saved, but birdnet_analysis did not restart',
        ];
        if (is_array($response)) $failure['settings'] = $response;
        notifications_response(500, $failure);
    }
    if (!is_array($response)) {
        notifications_response(500, [
            'ok' => false,
            'saved' => true,
            'error' => 'settings saved, but could not be re-read',
        ]);
    }
    notifications_response(200, $response);
}

if (!defined('AVIAN_NOTIFICATIONS_LIBRARY_ONLY')) notifications_main();
