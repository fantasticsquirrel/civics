from __future__ import annotations

from civics_app.db import decode_json_field


def delivery_channels(preference: object | None) -> list[str]:
    if preference is None:
        return ["in_app"]
    channels = decode_json_field(preference["channels"], ["in_app"])
    if preference["digest_frequency"] == "off":
        channels = [channel for channel in channels if channel == "in_app"]
    return [channel for channel in channels if channel in {"in_app", "email", "telegram"}]
