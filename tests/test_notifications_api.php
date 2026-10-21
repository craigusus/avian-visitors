<?php
declare(strict_types=1);

define('AVIAN_NOTIFICATIONS_LIBRARY_ONLY', true);
require_once dirname(__DIR__) . '/avian/api/notifications.php';

$checks = 0;
$failures = 0;

function check_notifications(bool $condition, string $label): void {
    global $checks, $failures;
    $checks++;
    if ($condition) return;
    $failures++;
    fwrite(STDERR, "FAIL: $label\n");
}

$discord = 'discord://1234567890/secret-webhook-token';
$mqtt = 'birds=mqtt://user:broker-password@homeassistant.local/birdnet';
$apprise = ['urls' => ['# kept comment', $discord, $mqtt], 'body' => 'A $comname was heard'];

// Masking.
$public = notifications_public_status([], $apprise);
$encoded = (string)json_encode($public);
check_notifications(!str_contains($encoded, 'secret-webhook-token'), 'status never contains a webhook token');
check_notifications(!str_contains($encoded, 'broker-password'), 'status never contains a broker password');
check_notifications(count($public['urls']) === 2, 'comments are not listed as services');
check_notifications($public['urls'][0]['scheme'] === 'discord', 'discord scheme is reported');
check_notifications($public['urls'][1]['scheme'] === 'mqtt', 'tagged mqtt scheme is reported');
check_notifications(!array_key_exists('url', $public['urls'][0]), 'masked entries carry no url');
check_notifications(preg_match('/\A[0-9a-f]{12}\z/D', $public['urls'][0]['id']) === 1, 'ids are 12 hex characters');
check_notifications($public['unsupported_lines'] === 0, 'well-formed file has no unsupported lines');
$revealed = notifications_public_status([], $apprise, true);
check_notifications($revealed['urls'][0]['url'] === $discord, 'reveal returns the saved url');

// Config parsing keeps template variables literal and drops unrelated keys.
$conf = tempnam(sys_get_temp_dir(), 'notify');
file_put_contents($conf, implode("\n", [
    'APPRISE_NOTIFICATION_TITLE="New $comname"',
    'APPRISE_NOTIFY_EACH_DETECTION=1',
    'APPRISE_MINIMUM_SECONDS_BETWEEN_NOTIFICATIONS_PER_SPECIES=300',
    'APPRISE_ONLY_NOTIFY_SPECIES_NAMES="House Sparrow"',
    'GEMINI_API_KEY=unrelated-secret',
    '',
]));
$read = notifications_read_conf($conf);
unlink($conf);
check_notifications(($read['APPRISE_NOTIFICATION_TITLE'] ?? '') === 'New $comname', 'title template is read literally');
check_notifications(!array_key_exists('GEMINI_API_KEY', $read), 'unrelated secrets are not retained');
$status = notifications_public_status($read, $apprise);
check_notifications($status['triggers']['notify_each'] === true, 'trigger flag is read');
check_notifications($status['cooldown_seconds'] === 300, 'cooldown is read');
check_notifications($status['exclude_species'] === 'House Sparrow', 'upstream ..._NAMES is the exclude list');

// Titles: template variables only, nothing bash could execute.
check_notifications(notifications_valid_title('New $comname ($confidencepct%)'), 'template variables are allowed');
foreach (['$(reboot)', '${HOME}', '`id`', '$PATH', '$$', 'a "quote"', 'back\\slash', "line\nbreak", str_repeat('a', 121)] as $bad) {
    check_notifications(!notifications_valid_title($bad), 'title rejects ' . json_encode($bad));
}
check_notifications(notifications_valid_species('Amsel, Kohlmeise, Rotkehlchen'), 'species list accepts names');
check_notifications(notifications_valid_species('Mésange charbonnière'), 'species list accepts UTF-8');
foreach (['$x', '`x`', 'a"b', "a\nb"] as $bad) {
    check_notifications(!notifications_valid_species($bad), 'species list rejects ' . json_encode($bad));
}

// URL lines.
foreach ([$discord, $mqtt, 'ntfy://ntfy.sh/birds', 'json://192.168.1.10:8123/api/webhook/x'] as $good) {
    check_notifications(notifications_url_scheme($good) !== null, 'url accepted: ' . $good);
}
foreach (['include http://example.com/config', 'urls:', 'version: 1', 'discord://a b', "discord://a\tb",
    'not a url', 'DISCORD://upper', str_repeat('a', 1020) . '://x'] as $bad) {
    check_notifications(notifications_url_scheme($bad) === null, 'url rejected: ' . $bad);
}

// Patches.
$ok = notifications_validate_update(['notify_each' => true, 'cooldown_seconds' => 60], $apprise);
check_notifications(!empty($ok['ok']) && $ok['updates'] === [
    'APPRISE_NOTIFY_EACH_DETECTION' => '1',
    'APPRISE_MINIMUM_SECONDS_BETWEEN_NOTIFICATIONS_PER_SPECIES' => '60',
] && $ok['lines'] === null && $ok['body'] === null, 'trigger and cooldown patch');
check_notifications(empty(notifications_validate_update([], $apprise)['ok']), 'empty patch is rejected');
check_notifications(empty(notifications_validate_update(['urls' => []], $apprise)['ok']), 'unknown key is rejected');
check_notifications(empty(notifications_validate_update(['notify_each' => 1], $apprise)['ok']), 'non-bool trigger is rejected');
check_notifications(empty(notifications_validate_update(['cooldown_seconds' => 86401], $apprise)['ok']), 'cooldown cap');
check_notifications(empty(notifications_validate_update(['title' => '$(id)'], $apprise)['ok']), 'unsafe title is rejected');
check_notifications(empty(notifications_validate_update(['body' => "a\0b"], $apprise)['ok']), 'NUL body is rejected');
check_notifications(empty(notifications_validate_update(['body' => str_repeat('a', 4001)], $apprise)['ok']), 'long body is rejected');

$added = notifications_validate_update(['add_url' => ' ntfy://ntfy.sh/birds '], $apprise);
check_notifications(!empty($added['ok']) && end($added['lines']) === 'ntfy://ntfy.sh/birds', 'url is trimmed and appended');
check_notifications(in_array('# kept comment', $added['lines'], true), 'comments survive a rewrite');
check_notifications(empty(notifications_validate_update(['add_url' => $discord], $apprise)['ok']), 'duplicate url is rejected');
check_notifications(empty(notifications_validate_update(['add_url' => 'include file:///etc/shadow'], $apprise)['ok']), 'include is rejected');

$id = notifications_url_id($discord);
$removed = notifications_validate_update(['remove_url_id' => $id], $apprise);
check_notifications(!empty($removed['ok']) && !in_array($discord, $removed['lines'], true)
    && in_array($mqtt, $removed['lines'], true), 'remove drops only the matching url');
check_notifications(empty(notifications_validate_update(['remove_url_id' => 'ffffffffffff'], $apprise)['ok']), 'unknown id is rejected');
check_notifications(empty(notifications_validate_update(['remove_url_id' => '../etc'], $apprise)['ok']), 'malformed id is rejected');

$full = ['urls' => array_map(static fn(int $n): string => 'ntfy://ntfy.sh/t' . $n, range(1, 20)), 'body' => ''];
check_notifications(empty(notifications_validate_update(['add_url' => 'ntfy://ntfy.sh/t21'], $full)['ok']), 'url limit is enforced');

$unmanaged = ['urls' => [$discord, 'include https://example.com/more.cfg'], 'body' => ''];
check_notifications(notifications_public_status([], $unmanaged)['unsupported_lines'] === 1, 'unsupported line is counted');
$refused = notifications_validate_update(['add_url' => 'ntfy://ntfy.sh/x'], $unmanaged);
check_notifications(empty($refused['ok']) && $refused['status'] === 409, 'files with unsupported lines are not rewritten');

// No error message carries a saved secret.
foreach ([
    notifications_validate_update(['add_url' => $discord], $apprise),
    notifications_validate_update(['remove_url_id' => 'ffffffffffff'], $apprise),
    $refused,
] as $result) {
    $text = (string)json_encode($result);
    check_notifications(!str_contains($text, 'secret-webhook-token') && !str_contains($text, 'broker-password'),
        'error responses never echo saved secrets');
}

if ($failures > 0) {
    fwrite(STDERR, "$failures of $checks notification api checks failed\n");
    exit(1);
}
echo "notification api tests passed ($checks checks)\n";
