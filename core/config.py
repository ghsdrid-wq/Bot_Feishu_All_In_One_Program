import configparser
import os
import sys


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

CONFIG_FILE = os.path.join(
    BASE_DIR,
    "config.ini"
)

config = configparser.ConfigParser()

# =========================================
# CREATE CONFIG
# =========================================


if not os.path.exists(CONFIG_FILE):

    config["FEISHU"] = {
        "APP_ID": "",
        "APP_SECRET": "",
        "AUTH_TOKEN": ""
    }

    config["NGROK"] = {
        "COMMAND": (
            "ngrok.exe http "
            #"--domain=yourbot.ngrok-free.app "
            "5000"
        )
    }

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)

config.read(CONFIG_FILE, encoding="utf-8")


# =========================================
# GET CONFIG
# =========================================

def get_config():

    return {
        "APP_ID": config["FEISHU"].get(
            "APP_ID",
            ""
        ),

        "APP_SECRET": config["FEISHU"].get(
            "APP_SECRET",
            ""
        ),

        "AUTH_TOKEN": config["FEISHU"].get(
            "AUTH_TOKEN",
            ""
        ),

        "NGROK_COMMAND": config["FEISHU"].get(
            "NGROK_COMMAND",
            "ngrok http 5000"
        ),
    }


# =========================================
# SAVE CONFIG
# =========================================

def save_config(
    app_id,
    app_secret,
    auth_token,
    ngrok_command
):

    config["FEISHU"]["APP_ID"] = app_id
    config["FEISHU"]["APP_SECRET"] = app_secret
    config["FEISHU"]["AUTH_TOKEN"] = auth_token
    config["NGROK"]["COMMAND"] = ngrok_command

    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        config.write(f)
