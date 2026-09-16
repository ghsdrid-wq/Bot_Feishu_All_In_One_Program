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
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )

BASE_DIR = get_base_path()

CONFIG_FILE = os.path.join(
    BASE_DIR,
    "config.ini"
)

def load_config():

    config = configparser.ConfigParser()
    config.read(CONFIG_FILE, encoding="utf-8")
    return config

# =========================================
# CREATE CONFIG
# =========================================


if not os.path.exists(CONFIG_FILE):

    config = configparser.ConfigParser()

    config["FEISHU"] = {
        "APP_ID": "",
        "APP_SECRET": "",
        "JMS_TOKEN": "",
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


# =========================================
# GET CONFIG
# =========================================

def get_config():

    config = load_config()
    feishu = config["FEISHU"] if "FEISHU" in config else {}
    ngrok = config["NGROK"] if "NGROK" in config else {}
    dws_jms = config["DWS_JMS"] if "DWS_JMS" in config else {}
    jms_token = feishu.get(
        "JMS_TOKEN",
        feishu.get(
            "AUTH_TOKEN",
            dws_jms.get(
                "jms_token",
                ""
            )
        )
    )

    return {
        "APP_ID": feishu.get(
            "APP_ID",
            ""
        ),

        "APP_SECRET": feishu.get(
            "APP_SECRET",
            ""
        ),

        "JMS_TOKEN": jms_token,

        "AUTH_TOKEN": jms_token,

        "NGROK_COMMAND": ngrok.get(
            "COMMAND",
            "ngrok http 5000"
        ),
    }


# =========================================
# SAVE CONFIG
# =========================================

def save_config(
    app_id,
    app_secret,
    jms_token,
    ngrok_command
):

    config = load_config()
    if "FEISHU" not in config:
        config["FEISHU"] = {}
    if "NGROK" not in config:
        config["NGROK"] = {}

    config["FEISHU"]["APP_ID"] = app_id
    config["FEISHU"]["APP_SECRET"] = app_secret
    config["FEISHU"]["JMS_TOKEN"] = jms_token
    config["FEISHU"]["AUTH_TOKEN"] = jms_token
    config["NGROK"]["COMMAND"] = ngrok_command

    tmp_path = f"{CONFIG_FILE}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        config.write(f)
    os.replace(tmp_path, CONFIG_FILE)
