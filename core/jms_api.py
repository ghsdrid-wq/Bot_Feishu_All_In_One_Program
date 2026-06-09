import requests
import configparser

from core.config import get_config

BASE_URL = "https://jmsgw.jtexpress.co.th"

def get_auth_token():

    config = configparser.ConfigParser()

    config.read("config.ini")

    return config["FEISHU"]["AUTH_TOKEN"]

# =========================================
# HEADERS
# =========================================

def get_headers():

    return {

        "accept": "application/json, text/plain, */*",

        "authtoken": get_auth_token(),

        "content-type": "application/json;charset=UTF-8",

        "lang": "TH",

        "langtype": "TH",

        "origin": "https://jms.jtexpress.co.th",

        "referer": "https://jms.jtexpress.co.th/",

        "routename": "userList|permissionIndex",

        "timezone": "GMT+0700",

        "user-agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/148.0.0.0 Safari/537.36"
        )
    }


# =========================================
# SEARCH USER
# =========================================

def search_user(staff_no):

    url = (
        "https://jmsgw.jtexpress.co.th/"
        "oauth/sysUser/staffNosPage"
    )

    payload = {
        "current": 1,
        "size": 20,
        "staffNo": str(staff_no).strip(),
        "countryId": "1"
    }

    response = requests.post(
        url,
        headers=get_headers(),
        json=payload,
        timeout=30
    )

    data = response.json()

    records = (
        data
        .get("data", {})
        .get("records", [])
    )

    if not records:
        return None

    return records[0]


# =========================================
# RESET APP PASSWORD
# =========================================

def reset_app_password(user_id):

    url = (
        f"{BASE_URL}"
        f"/oauth/sysUser/resetPasswordByApp?id={user_id}"
    )

    payload = {
        "countryId": "1"
    }

    response = requests.post(
        url,
        headers=get_headers(),
        json=payload,
        timeout=30
    )

    data = response.json()

    if not data.get("succ"):

        raise Exception(
            data.get("msg")
        )

    return data["data"]


# =========================================
# RESET JMS PASSWORD
# =========================================

def reset_jms_password(user_id):

    url = (
        f"{BASE_URL}"
        f"/oauth/sysUser/resetPasswordByJms?id={user_id}"
    )

    payload = {
        "countryId": "1"
    }

    response = requests.post(
        url,
        headers=get_headers(),
        json=payload,
        timeout=30
    )

    data = response.json()

    if not data.get("succ"):

        raise Exception(
            data.get("msg")
        )

    return data["data"]


# =========================================
# ENABLE USER
# =========================================

def enable_user(user):

    url = (
        f"{BASE_URL}"
        "/oauth/sysUser/enable"
    )

    payload = [
        {
            "newData": {
                "id": user["id"],
                "name": user["name"],
                "staffNo": user["staffNo"],
                "isEnable": 2
            },

            "oldData": {
                "id": user["id"],
                "name": user["name"],
                "staffNo": user["staffNo"],
                "isEnable": user.get(
                    "isEnable",
                    1
                )
            }
        }
    ]

    response = requests.post(
        url,
        headers=get_headers(),
        json=payload,
        timeout=30
    )

    data = response.json()

    if not data.get("succ"):

        raise Exception(
            data.get(
                "msg",
                "ENABLE FAILED"
            )
        )

    return True