import os
import sys
import time
import configparser
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

try:
    from Createphoto import get_export_items, migrate_old_export_config, save_config
except Exception:
    get_export_items = None
    migrate_old_export_config = None
    save_config = None

LogFunc = Callable[[str], None]
RunFunc = Callable[[], bool]
token_logger: Optional[LogFunc] = None
send_ui: Optional[LogFunc] = None


def request_with_retry(
    func: Callable[[], Dict[str, Any]],
    retries: int = 3,
    delay: int = 2,
    log: Optional[LogFunc] = None,
    name: str = "",
) -> Dict[str, Any]:
    retries = max(1, retries)
    for i in range(retries):
        try:
            return func()
        except Exception as e:
            if log:
                log(f"{name} failed ({i + 1}/{retries}): {e}")
            else:
                print(f"{name} failed ({i + 1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(delay * (i + 1))
            else:
                raise
    raise RuntimeError(f"{name or 'request'} failed without returning a response")


def response_json(response: requests.Response, name: str) -> Dict[str, Any]:
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise Exception(f"{name} returned invalid JSON") from exc


def resource_path(file: str) -> str:
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), file)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), file)


def load_config() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(resource_path("config.ini"), encoding="utf-8")
    return config


def get_feishu() -> Dict[str, str]:
    config = load_config()
    if "FEISHU" not in config:
        raise Exception("Missing FEISHU config")

    def get_cfg(key: str) -> str:
        return config["FEISHU"].get(key, "").strip().replace('"', "")

    return {
        "APP_ID": get_cfg("APP_ID"),
        "APP_SECRET": get_cfg("APP_SECRET"),
        "CHAT_ID": get_cfg("CHAT_ID"),
    }


def get_token(app_id: str, app_secret: str) -> str:
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal/"

    def do_request() -> Dict[str, Any]:
        response = requests.post(url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
        return response_json(response, "get_token")

    res = request_with_retry(do_request, log=token_logger, name="get_token")
    if "tenant_access_token" in res and token_logger:
        token_logger("TOKEN OK")
    if "tenant_access_token" not in res:
        raise Exception(f"Get token failed: {res}")
    return res["tenant_access_token"]


def upload_image(token: str, path: str, log: Optional[LogFunc] = None) -> str:
    url = "https://open.feishu.cn/open-apis/im/v1/images"
    headers = {"Authorization": f"Bearer {token}"}
    data = {"image_type": "message"}

    def do_request() -> Dict[str, Any]:
        with open(path, "rb") as f:
            files = {"image": f}
            response = requests.post(url, headers=headers, files=files, data=data, timeout=10)
            return response_json(response, "upload_image")

    res = request_with_retry(do_request, log=log, name="upload_image")
    if res.get("code") != 0:
        raise Exception(res)
    return res["data"]["image_key"]


def get_send_file_names() -> List[str]:
    config = load_config()
    if migrate_old_export_config:
        migrate_old_export_config(config)
        if save_config:
            save_config(config)
    if get_export_items:
        return [x.filename for x in get_export_items(config, only_enabled=True) if x.send_enabled]

    files = []
    if "EXPORT" in config:
        for key in ["AUTO_FILE", "DWSREALTIME_FILE", "AUTO_PDA_FILE", "DWS_PDA_FILE", "REALTIME_DB_FILE"]:
            val = config["EXPORT"].get(key)
            if val:
                files.append(val)
    return files

def send_image_chat(token: str, chat_id: str, image_key: str):
    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=chat_id"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "receive_id": chat_id,
        "msg_type": "image",
        "content": f'{{"image_key":"{image_key}"}}'
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=15
    )
    res = response_json(response, "send_image")

    if res.get("code") != 0:
        raise Exception(f"Send image failed: {res}")
    
def run_send(folder: str, log: Optional[LogFunc] = None, is_running: Optional[RunFunc] = None) -> None:
    global token_logger

    def write(msg: str) -> None:
        log(msg) if log else print(msg)

    cfg = get_feishu()
    chat_id = cfg["CHAT_ID"]
    app_id = cfg["APP_ID"]
    app_secret = cfg["APP_SECRET"]

    if not chat_id:
        raise Exception("Missing CHAT_ID")
    if not app_id or not app_secret:
        raise Exception("Missing APP_ID / APP_SECRET")

    token_logger = write
    token = get_token(app_id, app_secret)

    valid_images: List[str] = []
    for filename in get_send_file_names():
        img = os.path.join(folder, filename)
        if os.path.exists(img):
            valid_images.append(img)
        else:
            write(f"Missing file: {img}")

    if not valid_images:
        raise Exception("No images found")

    def process_image(img: str) -> Optional[Tuple[str, str]]:
        if is_running and not is_running():
            return None
        write(f"Uploading: {img}")
        if send_ui:
            try:
                send_ui("upload")
            except Exception:
                pass
        key = upload_image(token, img, log=write)
        return img, key

    results: List[Tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=3) as executor:
        for result in executor.map(process_image, valid_images):
            if result:
                results.append(result)

    for img, key in results:
        if is_running and not is_running():
            return
        write(f"Sending: {img}")
        if send_ui:
            try:
                send_ui("send")
            except Exception:
                pass
        send_image_chat(
            token,
            chat_id,
            key
        )
        for _ in range(10):
            if is_running and not is_running():
                write("Stopped during wait")
                return
            time.sleep(0.1)


if __name__ == "__main__":
    run_send(sys.argv[1])
