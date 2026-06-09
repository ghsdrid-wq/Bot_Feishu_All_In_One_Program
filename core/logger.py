import os
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
        os.path.abspath(__file__)
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

    # LOG TEXT
    text = (
        "\n"
        f"{time_now}\n"
        f"STATUS : {status}\n"
        f"NAME   : {name}\n"
        f"USER   : {user}\n"
        f"ACTION : {action}\n"
        f"DETAIL : {detail}\n"
    )

    # WRITE FILE
    with open(
        file_path,
        "a",
        encoding="utf-8"
    ) as f:

        f.write(text)