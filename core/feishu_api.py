import requests
import json

from core.config import get_config


# =========================================
# GET CONFIG
# =========================================

def get_tenant_access_token():

    config_data = get_config()
    app_id = config_data["APP_ID"]
    app_secret = config_data["APP_SECRET"]
    if not app_id or not app_secret:
        raise Exception("Missing APP_ID / APP_SECRET")

    url = (
        "https://open.feishu.cn/open-apis/"
        "auth/v3/tenant_access_token/internal"
    )

    payload = {
        "app_id": app_id,
        "app_secret": app_secret
    }

    response = requests.post(
        url,
        json=payload,
        timeout=30
    )
    response.raise_for_status()

    data = response.json()

    if data.get("code") != 0:

        raise Exception(
            data.get("msg")
        )

    return data["tenant_access_token"]


# =========================================
# SEND MESSAGE
# =========================================

def send_message(message_id, text):

    token = get_tenant_access_token()

    url = (
        "https://open.feishu.cn/open-apis/"
        f"im/v1/messages/{message_id}/reply"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "msg_type": "text",
        "content": json.dumps({
            "text": text
        })
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )
    response.raise_for_status()

    return response.json()
