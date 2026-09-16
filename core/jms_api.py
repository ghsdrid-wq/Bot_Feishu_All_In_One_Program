import requests

from core.config import get_config

BASE_URL = "https://jmsgw.jtexpress.co.th"

def get_auth_token():

    config = get_config()
    token = (
        config.get("JMS_TOKEN", "")
        or config.get("AUTH_TOKEN", "")
    ).strip()
    if not token:
        raise Exception("Missing JMS_TOKEN")
    return token


def post_json(url, payload):

    response = requests.post(
        url,
        headers=get_headers(),
        json=payload,
        timeout=30
    )
    if response.status_code in (401, 403):
        raise Exception("JMS_TOKEN หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า")
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise Exception("Invalid JSON response from JMS") from exc
    if isinstance(data, dict):
        msg = str(data.get("msg") or data.get("message") or "")
        lowered = msg.lower()
        if any(x in lowered for x in ("token", "auth", "unauthorized", "forbidden", "login", "session")):
            raise Exception("JMS_TOKEN หมดอายุหรือไม่ถูกต้อง กรุณาอัปเดต JMS_TOKEN ที่หน้า ตั้งค่า")
    return data

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

    data = post_json(url, payload)

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

    data = post_json(url, payload)

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

    data = post_json(url, payload)

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

    data = post_json(url, payload)

    if not data.get("succ"):

        raise Exception(
            data.get(
                "msg",
                "ENABLE FAILED"
            )
        )

    return True
