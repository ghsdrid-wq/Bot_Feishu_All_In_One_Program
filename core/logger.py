import os
import re
import sys
from datetime import datetime


# =====================================
# CREATE LOG FOLDER
# =====================================

def get_base_path():

    if getattr(
        sys,
        "frozen",
        False
    ):

        return os.path.dirname(
            sys.executable
        )

    return os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )

BASE_DIR = get_base_path()

LOG_DIR = os.path.join(
    BASE_DIR,
    "logs"
)

os.makedirs(
    LOG_DIR,
    exist_ok=True
)


# =====================================
# WRITE LOG
# =====================================

def mask_sensitive(value):

    text = str(value or "")
    patterns = [
        r"(?i)(password\s*[:=]\s*)(\S+)",
        r"(?i)(app_secret\s*[:=]\s*)(\S+)",
        r"(?i)(jms_token\s*[:=]\s*)(\S+)",
        r"(?i)(auth_token\s*[:=]\s*)(\S+)",
        r"(?i)(token\s*[:=]\s*)(\S+)",
    ]
    for pattern in patterns:
        text = re.sub(pattern, r"\1***", text)
    return text

def write_log(
    status,
    user="",
    name="",
    action="",
    detail=""
    ):

    # DATE
    date_now = datetime.now().strftime(
        "%Y-%m-%d"
    )

    # FILE NAME
    file_path = (
        os.path.join(
            LOG_DIR,
            f"{date_now}.log"
        )
    )

    # TIME
    time_now = datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    safe_detail = mask_sensitive(detail)

    # LOG TEXT
    text = (
        "\n"
        f"{time_now}\n"
        f"STATUS : {status}\n"
        f"NAME   : {name}\n"
        f"USER   : {user}\n"
        f"ACTION : {action}\n"
        f"DETAIL : {safe_detail}\n"
    )

    # WRITE FILE
    with open(
        file_path,
        "a",
        encoding="utf-8"
    ) as f:

        f.write(text)
