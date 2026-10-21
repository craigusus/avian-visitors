import apprise
import os
import socket
import tempfile
import requests
import html
import time
from urllib.parse import urlencode

from .db import get_todays_count_for, get_this_weeks_count_for
from .helpers import get_settings

userDir = os.path.expanduser('~')
APPRISE_CONFIG = userDir + '/BirdNET-Pi/apprise.txt'
APPRISE_BODY = userDir + '/BirdNET-Pi/body.txt'
COLLAGE_IMAGE_MAX_BYTES = 8 * 1024 * 1024
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'

apobj = None
species_last_notified = {}


def notify(body, title, attached=""):
    global apobj
    if apobj is None:
        asset = apprise.AppriseAsset(
            plugin_paths=[
                userDir + "/.apprise/plugins",
                userDir + "/.config/apprise/plugins",
            ]
        )
        apobj = apprise.Apprise(asset=asset)
        config = apprise.AppriseConfig()
        config.add(APPRISE_CONFIG)
        apobj.add(config)

    if attached != "":
        return apobj.notify(
            body=body,
            title=title,
            attach=attached,
        )
    else:
        return apobj.notify(
            body=body,
            title=title,
        )


def collage_image_path(sci_name):
    # The collage loads each bird from here; without a pose or bundle it
    # serves the active bundle's perched illustration.
    return '/avian/api/cutout.php?' + urlencode({'sci': sci_name})


def fetch_collage_image(sci_name):
    """Save the bird's collage illustration to a temporary PNG and return its
    path, or None when it is unavailable. The caller deletes the file."""
    try:
        resp = requests.get('http://localhost' + collage_image_path(sci_name),
                            timeout=10, allow_redirects=False)
        content_type = resp.headers.get('Content-Type', '').split(';')[0].strip()
        data = resp.content
    except Exception as e:
        print("COLLAGE IMAGE ERROR:", e)
        return None
    if resp.status_code != 200 or content_type != 'image/png' \
            or len(data) > COLLAGE_IMAGE_MAX_BYTES or not data.startswith(PNG_SIGNATURE):
        print("COLLAGE IMAGE UNAVAILABLE:", resp.status_code, content_type)
        return None
    with tempfile.NamedTemporaryFile(prefix='birdnet-notify-', suffix='.png', delete=False) as f:
        f.write(data)
        return f.name


def sendAppriseNotifications(sci_name, com_name, confidence, confidencepct, path, date, time_of_day, week, latitude, longitude, cutoff, sens, overlap):
    def render_template(template, reason=""):
        ret = template.replace("$sciname", sci_name) \
            .replace("$comname", com_name) \
            .replace("$confidencepct", str(confidencepct)) \
            .replace("$confidence", str(confidence)) \
            .replace("$listenurl", listenurl) \
            .replace("$friendlyurl", friendlyurl) \
            .replace("$date", str(date)) \
            .replace("$time", str(time_of_day)) \
            .replace("$week", str(week)) \
            .replace("$latitude", str(latitude)) \
            .replace("$longitude", str(longitude)) \
            .replace("$cutoff", str(cutoff)) \
            .replace("$sens", str(sens)) \
            .replace("$flickrimage", image_url) \
            .replace("$image", image_url) \
            .replace("$overlap", str(overlap)) \
            .replace("$reason", reason)
        return ret

    if not should_notify(com_name):
        return

    settings_dict = get_settings()
    title = html.unescape(settings_dict.get('APPRISE_NOTIFICATION_TITLE'))
    with open(APPRISE_BODY, 'r') as f:
        body = f.read()

    websiteurl = settings_dict.get('BIRDNETPI_URL')
    if websiteurl is None or len(websiteurl) == 0:
        websiteurl = f"http://{socket.gethostname()}.local"

    listenurl = f"{websiteurl}?filename={path}"
    friendlyurl = f"[Listen here]({listenurl})"

    # $image (and the older $flickrimage) link to the collage illustration,
    # which every notification also carries as an attachment.
    image_url = websiteurl.rstrip('/') + collage_image_path(sci_name)
    attachment = None

    def send(reason):
        nonlocal attachment
        if attachment is None:
            attachment = fetch_collage_image(sci_name) or ""
        notify(render_template(body, reason), render_template(title, reason), attachment)
        species_last_notified[com_name] = int(time.time())

    try:
        if settings_dict.get('APPRISE_NOTIFY_EACH_DETECTION') == "1":
            send("detection")

        APPRISE_NOTIFICATION_NEW_SPECIES_DAILY_COUNT_LIMIT = 1  # Notifies the first N per day.
        if settings_dict.get('APPRISE_NOTIFY_NEW_SPECIES_EACH_DAY') == "1":
            numberDetections = get_todays_count_for(sci_name)
            if 0 < numberDetections <= APPRISE_NOTIFICATION_NEW_SPECIES_DAILY_COUNT_LIMIT:
                send("first time today")

        if settings_dict.get('APPRISE_NOTIFY_NEW_SPECIES') == "1":
            numberDetections = get_this_weeks_count_for(sci_name)
            if 0 < numberDetections <= 5:
                send(f"only seen {numberDetections} times in last 7d")
    finally:
        if attachment:
            try:
                os.remove(attachment)
            except OSError:
                pass


def should_notify(com_name):
    settings_dict = get_settings()
    if not (os.path.exists(APPRISE_CONFIG) and os.path.getsize(APPRISE_CONFIG) > 0):
        return False

    # check if this is an excluded species
    APPRISE_ONLY_NOTIFY_SPECIES_NAMES = settings_dict.get('APPRISE_ONLY_NOTIFY_SPECIES_NAMES')
    if APPRISE_ONLY_NOTIFY_SPECIES_NAMES is not None and APPRISE_ONLY_NOTIFY_SPECIES_NAMES.strip() != "":
        excluded_species = [bird.lower().replace(" ", "") for bird in APPRISE_ONLY_NOTIFY_SPECIES_NAMES.split(",")]
        if com_name.lower().replace(" ", "") in excluded_species:
            return False

    # check if this is an included species
    APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2 = settings_dict.get('APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2')
    if APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2 is not None and APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2.strip() != "":
        included_species = [bird.lower().replace(" ", "") for bird in APPRISE_ONLY_NOTIFY_SPECIES_NAMES_2.split(",")]
        if com_name.lower().replace(" ", "") not in included_species:
            return False

    # is it still too soon?
    APPRISE_MINIMUM_SECONDS_BETWEEN_NOTIFICATIONS_PER_SPECIES = settings_dict.get('APPRISE_MINIMUM_SECONDS_BETWEEN_NOTIFICATIONS_PER_SPECIES')
    if APPRISE_MINIMUM_SECONDS_BETWEEN_NOTIFICATIONS_PER_SPECIES != "0":
        if species_last_notified.get(com_name) is not None:
            try:
                if int(time.time()) - species_last_notified[com_name] < int(APPRISE_MINIMUM_SECONDS_BETWEEN_NOTIFICATIONS_PER_SPECIES):
                    return False
            except Exception as e:
                print("APPRISE NOTIFICATION EXCEPTION: " + str(e))
                return False

    return True


if __name__ == "__main__":
    print("notfications")
