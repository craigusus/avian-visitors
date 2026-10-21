import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class NotificationsArchitectureTest(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_text(encoding="utf-8")

    def helper_validators(self):
        """The helper's validation functions, runnable without root."""
        source = self.read("scripts/admin_control.sh")
        pieces = []
        for name in ("APPRISE_TEMPLATE_VARIABLE", "APPRISE_LINE_MAX"):
            match = re.search(rf"^readonly {name}=.*$", source, re.M)
            self.assertIsNotNone(match, name)
            pieces.append(match.group(0))
        for name in ("valid_apprise_title", "valid_apprise_line"):
            match = re.search(rf"^{name}\(\) \{{\n.*?^\}}$", source, re.M | re.S)
            self.assertIsNotNone(match, name)
            pieces.append(match.group(0))
        return "LC_ALL=C\n" + "\n".join(pieces) + "\n"

    def helper_accepts(self, function, value):
        # Script on stdin and value in the environment: Windows argument
        # quoting mangles backslashes and newlines in argv.
        result = subprocess.run(
            ["bash", "-s"],
            input=(self.helper_validators() + f'{function} "$AV_TEST_VALUE"\n').encode(),
            env={**os.environ, "AV_TEST_VALUE": value},
            capture_output=True, timeout=10, check=False,
        )
        return result.returncode == 0

    @unittest.skipUnless(shutil.which("bash"), "bash is unavailable")
    def test_helper_title_rules_match_the_endpoint(self):
        for good in ["New BirdNET-Pi Detection", "New $comname ($confidencepct%)", ""]:
            self.assertTrue(self.helper_accepts("valid_apprise_title", good), good)
        for bad in ["$(reboot)", "${HOME}", "`id`", "$PATH", "$$", 'a "q"', "a\\b", "a\nb", "a" * 121]:
            self.assertFalse(self.helper_accepts("valid_apprise_title", bad), bad)

    @unittest.skipUnless(shutil.which("bash"), "bash is unavailable")
    def test_helper_url_rules_match_the_endpoint(self):
        for good in ["discord://1/token", "birds=mqtt://u:p@ha.local/t", "# comment"]:
            self.assertTrue(self.helper_accepts("valid_apprise_line", good), good)
        for bad in ["include http://example.com/c", "urls:", "discord://a b", "discord://a\tb", "x" * 1025]:
            self.assertFalse(self.helper_accepts("valid_apprise_line", bad), bad)

    def test_helper_writes_as_the_birdnet_user(self):
        source = self.read("scripts/admin_control.sh")
        self.assertIn("CONTROL_VERSION=4", source)
        for function in ("read_user_file", "write_user_file"):
            body = re.search(rf"^{function}\(\) \{{\n(.*?)^\}}$", source, re.M | re.S).group(1)
            self.assertIn('runuser -u "$birdnet_user"', body, function)
        test = re.search(r"^  apprise-test\)\n(.*?)^    ;;$", source, re.M | re.S).group(1)
        self.assertIn('runuser -u "$birdnet_user"', test)
        self.assertIn(">/dev/null 2>&1", test, "Apprise output can echo URLs")

    def test_endpoint_is_published_and_guarded(self):
        caddy = self.read("scripts/update_caddyfile.sh")
        self.assertIn("/avian/api/notifications.php", caddy)
        endpoint = self.read("avian/api/notifications.php")
        main = endpoint[endpoint.index("function notifications_main"):]
        self.assertLess(main.index("avian_require_admin();"), main.index("$_GET"))
        self.assertLess(main.index("avian_require_admin_proof()"), main.index("notifications_public_status("))
        self.assertLess(main.index("avian_require_json_action();"), main.index("php://input"))
        self.assertIn("header('Cache-Control: no-store');", main)

    def test_stock_test_button_does_not_use_a_shell(self):
        config = self.read("scripts/config.php")
        block = config[config.index("$_GET['sendtest']"):]
        block = block[:block.index("die();")]
        self.assertNotIn("shell_exec", block)
        self.assertNotIn("escapeshellcmd", block)
        self.assertIn("proc_open($cmd", block)
        self.assertIn("htmlspecialchars", block)

    def test_frontend_inserts_urls_as_text(self):
        apt = self.read("avian/frontend/apt.js")
        wire = apt[apt.index("function wireNotificationsControl"):apt.index("function wireBirdweatherControl")]
        self.assertNotIn("innerHTML", wire)
        self.assertIn("text.textContent", wire)
        self.assertIn("'X-Avian-Action': '1'", wire)

    @unittest.skipUnless(shutil.which("php"), "PHP CLI is unavailable")
    def test_php_runtime_contract(self):
        result = subprocess.run(
            ["php", "tests/test_notifications_api.php"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertRegex(result.stdout, r"notification api tests passed [(][0-9]+ checks[)]")


if __name__ == "__main__":
    unittest.main()
