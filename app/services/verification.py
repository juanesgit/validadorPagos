import re, datetime
from flask import current_app
from ..extensions import db
from ..models import ReporterWhitelist, VerifiedUser
from .telegram import send_message, reply_kb


def normalize_phone(raw: str, default_cc="57"):
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if raw and raw.strip().startswith("+"):
        return f"+{digits}"
    if len(digits) == 10:
        return f"+{default_cc}{digits}"
    return f"+{digits}"


def send_request_contact(
    chat_id, text="Para continuar, comparte tu número de celular."
):
    kb = reply_kb(
        [
            [{"text": "Compartir mi número 📲", "request_contact": True}],
            [{"text": "Ayuda"}, {"text": "Menú principal"}],
        ],
        one_time=True,
    )
    send_message(chat_id, text, kb=kb)


def get_verified(telegram_user_id: int):
    return VerifiedUser.query.filter_by(telegram_user_id=str(telegram_user_id)).first()


def check_verification(from_user):
    vu = get_verified(from_user)
    if not vu:
        return False, "no_session"
    ttl = int(current_app.config["VERIF_TTL_MINUTES"])
    if ttl > 0:
        delta = datetime.datetime.utcnow() - vu.verified_at
        if delta.total_seconds() > ttl * 60:
            db.session.delete(vu)
            db.session.commit()
            return False, "expired"
    wl = ReporterWhitelist.query.filter_by(phone_e164=vu.phone_e164).first()
    if not wl:
        db.session.delete(vu)
        db.session.commit()
        return False, "not_found"
    if not wl.enabled:
        db.session.delete(vu)
        db.session.commit()
        return False, "disabled"
    if wl.sucursal and wl.sucursal != vu.sucursal:
        vu.sucursal = wl.sucursal
        db.session.commit()
    return True, vu
