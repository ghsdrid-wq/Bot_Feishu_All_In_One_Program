from flask import Flask
from flask import request
from flask import jsonify

import hmac
import threading

try:
    from waitress import serve
except Exception:
    serve = None

app = Flask(__name__)

controller_instance = None


def _authorized(data=None):
    if controller_instance is None:
        return False
    expected = str(
        controller_instance.get_feishu_config_value("VERIFY_TOKEN", "") or ""
    ).strip()
    if not expected:
        return False
    data = data if isinstance(data, dict) else {}
    authorization = str(request.headers.get("Authorization", "")).strip()
    bearer = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    candidates = (
        data.get("token"),
        request.headers.get("X-Controller-Token"),
        bearer,
    )
    return any(
        bool(candidate)
        and hmac.compare_digest(str(candidate).strip(), expected)
        for candidate in candidates
    )


# =========================================
# REGISTER CONTROLLER
# =========================================
def register_controller(controller):

    global controller_instance

    controller_instance = controller


# =========================================
# STATUS
# =========================================
@app.route("/status", methods=["GET"])
def status():

    return jsonify({
        "success": True,
        "message": "Controller Online"
    })


# =========================================
# SWITCH PLAN
# =========================================
@app.route("/switch_plan", methods=["POST"])
def switch_plan():

    global controller_instance

    if controller_instance is None:

        return jsonify({
            "success": False,
            "message": "Controller not ready"
        })

    data = request.get_json(silent=True) or {}

    if not _authorized(data):

        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    target_plan = str(data.get("plan", "")).strip()

    if not target_plan:

        return jsonify({
            "success": False,
            "message": "Plan empty"
        })

    threading.Thread(
        target=controller_instance.switch_plan,
        args=(target_plan,),
        daemon=True
    ).start()

    return jsonify({
        "success": True,
        "message": f"Switching to {target_plan}"
    })


# =========================================
# REFRESH
# =========================================
@app.route("/refresh", methods=["POST"])
def refresh():

    global controller_instance

    if controller_instance is None:

        return jsonify({
            "success": False
        })

    data = request.get_json(silent=True) or {}

    if not _authorized(data):

        return jsonify({
            "success": False,
            "message": "Unauthorized"
        }), 401

    controller_instance.refresh_status()

    return jsonify({
        "success": True
    })


# =========================================
# RUN API
# =========================================
def start_api():

    if serve:
        serve(app, host="0.0.0.0", port=6100, threads=8)
    else:
        app.run(
            host="0.0.0.0",
            port=6100,
            debug=False,
            use_reloader=False
        )
