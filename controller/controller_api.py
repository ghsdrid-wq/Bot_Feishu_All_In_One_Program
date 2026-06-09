from flask import Flask
from flask import request
from flask import jsonify

import threading

app = Flask(__name__)

controller_instance = None


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

    data = request.json

    target_plan = data.get("plan", "").strip()

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

    controller_instance.refresh_status()

    return jsonify({
        "success": True
    })


# =========================================
# RUN API
# =========================================
def start_api():

    app.run(
        host="0.0.0.0",
        port=6100,
        debug=False,
        use_reloader=False
    )