import os, re, requests, uuid
from flask import current_app
from werkzeug.utils import secure_filename

# Acepta 'cliente', 'nombre' y 'ref' (alias compat)
CAPTION_KEYS = ["valor", "sucursal", "medio_pago", "cliente"]
CAPTION_REGEX = re.compile(
    r"(?mi)^(valor|sucursal|medio_pago|cliente|nombre|ref)\s*:\s*(.+)$"
)


def parse_amount(txt):
    try:
        return int(re.sub(r"[^\d]", "", txt or ""))
    except Exception:
        return None


def send_message(chat_id, text, reply_to=None, kb=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    if kb:
        payload["reply_markup"] = kb
    try:
        requests.post(
            f"{current_app.config['BOT_API']}/sendMessage", json=payload, timeout=15
        )
    except Exception as e:
        try:
            current_app.logger.error(f"sendMessage error: {e}")
        except Exception:
            pass


def edit_message_text(chat_id, message_id, text, kb=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if kb:
        payload["reply_markup"] = kb
    try:
        requests.post(
            f"{current_app.config['BOT_API']}/editMessageText", json=payload, timeout=15
        )
    except Exception as e:
        try:
            current_app.logger.error(f"editMessageText error: {e}")
        except Exception:
            pass


def edit_message_reply_markup(chat_id, message_id, kb):
    payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": kb}
    try:
        requests.post(
            f"{current_app.config['BOT_API']}/editMessageReplyMarkup", json=payload, timeout=15
        )
    except Exception as e:
        try:
            current_app.logger.error(f"editMessageReplyMarkup error: {e}")
        except Exception:
            pass


def answer_callback_query(callback_id, text=None):
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text
    try:
        requests.post(
            f"{current_app.config['BOT_API']}/answerCallbackQuery", json=payload, timeout=15
        )
    except Exception as e:
        try:
            current_app.logger.error(f"answerCallbackQuery error: {e}")
        except Exception:
            pass


def reply_kb(rows, resize=True, one_time=False):
    return {"keyboard": rows, "resize_keyboard": resize, "one_time_keyboard": one_time}


def get_file_path(file_id):
    try:
        r = requests.get(
            f"{current_app.config['BOT_API']}/getFile",
            params={"file_id": file_id},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"getFile network error: {e}")
    if not data.get("ok"):
        raise RuntimeError(f"getFile error: {data}")
    return data["result"]["file_path"]


def download_file(file_path):
    resp = requests.get(f"{current_app.config['FILE_API']}/{file_path}", timeout=30)
    resp.raise_for_status()
    name = secure_filename(os.path.basename(file_path))
    base, ext = os.path.splitext(name)
    dest = os.path.join(current_app.config["EVID_DIR"], name)
    if os.path.exists(dest):
        name = f"{base}_{uuid.uuid4().hex[:8]}{ext}"
        dest = os.path.join(current_app.config["EVID_DIR"], name)
    with open(dest, "wb") as f:
        f.write(resp.content)
    return name


# Teclados
MAIN_KB = reply_kb(
    [
        [{"text": "Reportar pago"}],
        [{"text": "Ver estado"}],
        [{"text": "Ayuda"}],
        [{"text": "Cerrar sesión"}],
    ]
)
CANCEL_KB = reply_kb([[{"text": "Cancelar"}, {"text": "Menú principal"}]])

MEDIOS_PAGO = [
    "Bancolombia",
    "Davivienda",
    "Banco de Bogota",
    "Banco BBVA",
    "Corresponsal Bancario",
    "Nequi",
    "Daviplata",
    "Otro medio",
]
MEDIOS_SET = {m.lower() for m in MEDIOS_PAGO}


def medio_keyboard_rows():
    rows, row = [], []
    for i, m in enumerate(MEDIOS_PAGO, 1):
        row.append({"text": m})
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "Cancelar"}, {"text": "Menú principal"}])
    return reply_kb(rows)


def set_webhook(app, url: str):
    requests.post(f"{app.config['BOT_API']}/setWebhook", data={"url": url}, timeout=15)
