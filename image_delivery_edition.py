"""Select edition-specific validation and receipt stores without sending."""

from datetime import date


def bundle_edition(folder):
    day_text, separator, edition = folder.name.rpartition("-")
    if separator != "-" or edition not in {"am", "pm"}:
        raise ValueError("Bundle name must be YYYY-MM-DD-am or YYYY-MM-DD-pm")
    day = date.fromisoformat(day_text)
    if day.isoformat() != day_text:
        raise ValueError("Bundle date must be YYYY-MM-DD")
    return day, edition


def telegram_state_name(folder):
    return ".closing_image_delivery" if bundle_edition(folder)[1] == "pm" else ".morning_image_delivery"


def kakao_state_name(folder):
    return ".closing_kakao_delivery" if bundle_edition(folder)[1] == "pm" else ".morning_kakao_delivery"


def validate_bundle(folder, now):
    if bundle_edition(folder)[1] == "pm":
        from publish_closing_image import validate_bundle as validate
    else:
        from publish_morning_image import validate_bundle as validate
    return validate(folder, now)
