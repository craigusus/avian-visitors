import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime
from unittest.mock import patch

from scripts.utils import db
from scripts.utils import notifications
from scripts.utils.notifications import sendAppriseNotifications

from tests.helpers import Settings


class TestAppriseNotifications(unittest.TestCase):

    def setUp(self):
        db.DB_PATH = self.db_file

    @classmethod
    def setUpClass(cls):
        cls.db_file = "test.db"
        cls.apprise_body_file = "test_apprise_body"
        cls.apprise_config_file = "test_apprise_config"

    def create_test_db(self):
        """ create a database connection to a SQLite database """
        conn = None
        try:
            conn = sqlite3.connect(self.db_file)
            sql_create_detections_table = """ CREATE TABLE IF NOT EXISTS detections (
                                            id integer PRIMARY KEY,
                                            Sci_Name text NOT NULL,
                                            Com_Name text NOT NULL,
                                            Date date NOT NULL,
                                            Time time NULL
                                        ); """
            cur = conn.cursor()
            cur.execute(sql_create_detections_table)
            sql = ''' INSERT INTO detections(Sci_Name, Com_Name, Date)
                  VALUES(?,?,?) '''

            today = datetime.now().strftime("%Y-%m-%d")  # SQLite stores date as YYYY-MM-DD
            cur.execute(sql, ["Myiarchus crinitus", "Great Crested Flycatcher", today])
            conn.commit()

        except Exception as e:
            print(e)
        finally:
            if conn:
                conn.close()

    def create_apprise_config(self):
        with open(self.apprise_body_file, 'w') as f:
            f.write('A $comname ($sciname) was just detected with a confidence of $confidencepct ($reason)')
        with open(self.apprise_config_file, 'w') as f:
            f.write('a dummy config')
        notifications.APPRISE_BODY = self.apprise_body_file
        notifications.APPRISE_CONFIG = self.apprise_config_file

    def tearDown(self):
        if os.path.exists(self.db_file):
            os.remove(self.db_file)
        if os.path.exists(self.apprise_body_file):
            os.remove(self.apprise_body_file)
        if os.path.exists(self.apprise_config_file):
            os.remove(self.apprise_config_file)

    def get_default_params(self):
        return {
            "sci_name": "Myiarchus crinitus",
            "com_name": "Great Crested Flycatcher",
            "confidence": "0.91",
            "confidencepct": "91",
            "path": "filename",
            "date": "1666-06-06",
            "time_of_day": "06:06:06",
            "week": "06",
            "latitude": "-1",
            "longitude": "-1",
            "cutoff": "0.7",
            "sens": "1.25",
            "overlap": "0.0"
        }

    @patch('scripts.utils.helpers._load_settings')
    @patch('scripts.utils.notifications.notify')
    def test_notifications(self, mock_notify, mock_load_settings):
        self.create_test_db()
        self.create_apprise_config()
        notifications.DB_PATH = self.db_file
        settings_dict = Settings.with_defaults()

        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())

        # No active apprise notifications configured. Confirm no notifications.
        self.assertEqual(mock_notify.call_count, 0)

        # Add daily notification.
        mock_notify.reset_mock()
        settings_dict["APPRISE_NOTIFY_NEW_SPECIES_EACH_DAY"] = "1"
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())

        self.assertEqual(mock_notify.call_count, 1)
        self.assertEqual(
            mock_notify.call_args_list[0][0][0],
            "A Great Crested Flycatcher (Myiarchus crinitus) was just detected with a confidence of 91 (first time today)"
        )

        # Add new species notification.
        mock_notify.reset_mock()
        settings_dict["APPRISE_NOTIFY_NEW_SPECIES"] = "1"
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())

        self.assertEqual(mock_notify.call_count, 2)
        self.assertEqual(
            mock_notify.call_args_list[0][0][0],
            "A Great Crested Flycatcher (Myiarchus crinitus) was just detected with a confidence of 91 (first time today)"
        )
        self.assertEqual(
            mock_notify.call_args_list[1][0][0],
            "A Great Crested Flycatcher (Myiarchus crinitus) was just detected with a confidence of 91 (only seen 1 times in last 7d)"
        )

        # Add each species notification.
        mock_notify.reset_mock()
        settings_dict["APPRISE_NOTIFY_EACH_DETECTION"] = "1"
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())

        self.assertEqual(mock_notify.call_count, 3)

    @patch('scripts.utils.helpers._load_settings')
    @patch('scripts.utils.notifications.notify')
    def test_notifications_excluded(self, mock_notify, mock_load_settings):
        self.create_test_db()
        self.create_apprise_config()
        notifications.DB_PATH = self.db_file
        settings_dict = Settings.with_defaults()
        settings_dict["APPRISE_NOTIFY_EACH_DETECTION"] = "1"

        settings_dict['APPRISE_ONLY_NOTIFY_SPECIES_NAMES'] = 'Quailfinch'
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())
        # Not excluded. Confirm notifications.
        self.assertEqual(mock_notify.call_count, 1)

        mock_notify.reset_mock()
        settings_dict['APPRISE_ONLY_NOTIFY_SPECIES_NAMES'] = 'Quailfinch,'
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())
        # Not excluded. Confirm notifications.
        self.assertEqual(mock_notify.call_count, 1)

        mock_notify.reset_mock()
        settings_dict['APPRISE_ONLY_NOTIFY_SPECIES_NAMES'] = 'Quailfinch,Great Crested Flycatcher'
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())

        self.assertEqual(mock_notify.call_count, 0)

    @patch('scripts.utils.helpers._load_settings')
    @patch('scripts.utils.notifications.notify')
    def test_notifications_included(self, mock_notify, mock_load_settings):
        self.create_test_db()
        self.create_apprise_config()
        notifications.DB_PATH = self.db_file
        settings_dict = Settings.with_defaults()
        settings_dict["APPRISE_NOTIFY_EACH_DETECTION"] = "1"

        settings_dict['APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2'] = 'Quailfinch'
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())
        # No wanted species. Confirm no notifications.
        self.assertEqual(mock_notify.call_count, 0)

        settings_dict['APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2'] = 'Quailfinch,'
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())
        # No wanted species. Confirm no notifications.
        self.assertEqual(mock_notify.call_count, 0)

        mock_notify.reset_mock()
        settings_dict['APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2'] = 'Quailfinch,Great Crested Flycatcher'
        mock_load_settings.return_value = settings_dict
        sendAppriseNotifications(**self.get_default_params())
        self.assertEqual(mock_notify.call_count, 1)


class TestNotifyResult(unittest.TestCase):

    @patch('scripts.utils.notifications.apprise')
    def test_notify_returns_delivery_result(self, mock_apprise):
        notifications.apobj = None
        mock_apprise.Apprise.return_value.notify.return_value = False
        try:
            self.assertFalse(notifications.notify('body', 'title'))
            mock_apprise.Apprise.return_value.notify.return_value = True
            self.assertTrue(notifications.notify('body', 'title'))
        finally:
            notifications.apobj = None


class TestSendTestNotification(unittest.TestCase):
    """The admin panel's test button trusts this script's exit code."""

    @classmethod
    def setUpClass(cls):
        scripts = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scripts')
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import send_test_notification
        cls.sender = send_test_notification

    def setUp(self):
        self.files = []
        for name, content in (('config', 'json://localhost/x'), ('body', 'A $comname')):
            handle = tempfile.NamedTemporaryFile('w', delete=False, suffix=name)
            handle.write(content)
            handle.close()
            self.files.append(handle.name)
        self.settings = Settings.with_defaults()
        self.settings['APPRISE_ONLY_NOTIFY_SPECIES_NAMES'] = 'King Penguin'
        self.settings['APPRISE_MINIMUM_SECONDS_BETWEEN_NOTIFICATIONS_PER_SPECIES'] = '3600'
        self.sender.notifications.species_last_notified['King Penguin'] = int(time.time())

    def tearDown(self):
        self.sender.notifications.species_last_notified.clear()
        for path in self.files:
            os.remove(path)

    def run_sender(self, delivered):
        args = ['--config', self.files[0], '--body', self.files[1]]
        with patch.object(self.sender, 'get_settings', return_value=self.settings), \
                patch.object(self.sender.notifications, 'get_settings', return_value=self.settings), \
                patch.object(self.sender, 'get_latest', return_value=None), \
                patch.object(self.sender.notifications, 'notify', return_value=delivered) as notify:
            return self.sender.main(args), notify

    def test_delivery_exits_zero_despite_filters_and_cooldown(self):
        code, notify = self.run_sender(True)
        self.assertEqual(code, 0)
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(notify.call_args[0][1], 'New backyard bird!')

    def test_failed_delivery_exits_nonzero(self):
        code, _ = self.run_sender(False)
        self.assertEqual(code, 1)


if __name__ == '__main__':
    unittest.main()
