import os
import re
import sys
import threading
import traceback
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

_LOG_LOCK = threading.Lock()


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

def write_runtime_log(level, message, source="APPLICATION", traceback_text=""):
    """Persist operational warnings and errors without employee audit data.

    NOTICE is used for scheduler lifecycle events (Auto start/stop/finish) so a
    stopped Auto can be explained afterwards; it never carries employee data.
    """
    normalized_level = str(level or "ERROR").strip().upper()
    if normalized_level not in {"NOTICE", "WARNING", "WARN", "ERROR", "CRITICAL"}:
        return None
    if normalized_level == "WARN":
        normalized_level = "WARNING"

    os.makedirs(LOG_DIR, exist_ok=True)
    date_now = datetime.now().strftime("%Y-%m-%d")
    time_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    file_path = os.path.join(LOG_DIR, f"{date_now}-runtime.log")
    safe_message = mask_sensitive(message).strip()
    safe_traceback = mask_sensitive(traceback_text).strip()
    text = (
        "\n"
        f"{time_now}\n"
        f"LEVEL  : {normalized_level}\n"
        f"SOURCE : {str(source or 'APPLICATION').strip()}\n"
        f"MESSAGE: {safe_message}\n"
    )
    if safe_traceback and safe_traceback != "NoneType: None":
        text += f"TRACEBACK:\n{safe_traceback}\n"

    with _LOG_LOCK:
        with open(file_path, "a", encoding="utf-8") as handle:
            handle.write(text)
    return file_path


def write_runtime_exception(error, source="APPLICATION", message=""):
    detail = message or str(error)
    trace = "".join(traceback.format_exception(
        type(error), error, error.__traceback__))
    return write_runtime_log("ERROR", detail, source, trace)


def write_log(status, user="", name="", action="", detail=""):
    """Compatibility wrapper for old callers.

    Successful, blocked, and exempt employee actions are intentionally not
    persisted. Only a genuine failed operation is retained, without the
    employee number, name, or generated password.
    """
    if str(status or "").strip().upper() not in {"FAILED", "ERROR"}:
        return None
    source = f"JMS {str(action or 'COMMAND').strip()}"
    return write_runtime_log("ERROR", detail, source)
