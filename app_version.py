"""Single source of truth for application and release versioning."""

APP_VERSION = "2.1.6"


def release_tag() -> str:
    return "V" + APP_VERSION.replace(".", "-")


def release_folder() -> str:
    return "AutoReportFeishu" + release_tag()
