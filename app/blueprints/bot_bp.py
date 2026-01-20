import os, re
from flask import Blueprint, request, current_app
from ..extensions import db
from ..models import (
    PaymentRequest,
    Evidence,
    ConvState,
    VerifiedUser,
    ReporterWhitelist,
    Estado,
)
from ..services.telegram import (
    send_message,
    edit_message_text,
    edit_message_reply_markup,
    answer_callback_query,
    reply_kb,
    MAIN_KB,
    CANCEL_KB,
    MEDIOS_SET,
    medio_keyboard_rows,
    CAPTION_REGEX,
    CAPTION_KEYS,
    parse_amount,
    get_file_path,
    download_file,
)
from ..services.verification import (
    normalize_phone,
    send_request_contact,
    check_verification,
    get_verified,
)

bot_bp = Blueprint("bot_bp", __name__)


def set_state(uid, step, data=None):
    st = ConvState.query.filter_by(telegram_user_id=str(uid)).first()
    if not st:
        st = ConvState(telegram_user_id=str(uid))
        db.session.add(st)
    import json

    st.step = step
    st.data = json.dumps(data or {})
    db.session.commit()


def get_state(uid):
    st = ConvState.query.filter_by(telegram_user_id=str(uid)).first()
    if not st:
        return None, {}
    import json

    try:
        return st.step, json.loads(st.data or "{}")
    except Exception:
        return st.step, {}


def clear_state(uid):
    st = ConvState.query.filter_by(telegram_user_id=str(uid)).first()
    if st:
        db.session.delete(st)
        db.session.commit()


# Calendario inline
def _spanish_month(m):
    names = ["Enero","Febrero","Marzo","Abril","Mayo","Junio","Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]
    return names[m-1] if 1 <= m <= 12 else str(m)


def _build_calendar_kb(year, month):
    import calendar, datetime as _dt
    y, m = year, month
    cal = calendar.Calendar(firstweekday=0)
    weeks = cal.monthdayscalendar(y, m)
    title = f"{_spanish_month(m)} {y}"
    rows = []
    today = _dt.date.today()
    next_cb = "CAL_NOP" if (y, m) >= (today.year, today.month) else f"CAL_NAV:{y}-{m:02d}:next"
    rows.append([
        {"text": "◀", "callback_data": f"CAL_NAV:{y}-{m:02d}:prev"},
        {"text": title, "callback_data": "CAL_NOP"},
        {"text": "▶", "callback_data": next_cb},
    ])
    rows.append([
        {"text": "Lu", "callback_data": "CAL_NOP"},
        {"text": "Ma", "callback_data": "CAL_NOP"},
        {"text": "Mi", "callback_data": "CAL_NOP"},
        {"text": "Ju", "callback_data": "CAL_NOP"},
        {"text": "Vi", "callback_data": "CAL_NOP"},
        {"text": "Sa", "callback_data": "CAL_NOP"},
        {"text": "Do", "callback_data": "CAL_NOP"},
    ])
    for w in weeks:
        row = []
        for i, d in enumerate(w):
            if d == 0:
                row.append({"text": " ", "callback_data": "CAL_NOP"})
            else:
                date_obj = _dt.date(y, m, d)
                day = f"{y}-{m:02d}-{d:02d}"
                cb = "CAL_NOP" if date_obj > today else f"CAL_SET:{day}"
                row.append({"text": str(d), "callback_data": cb})
        rows.append(row)
    rows.append([
        {"text": "Hoy", "callback_data": "CAL_TODAY"},
        {"text": "Cancelar", "callback_data": "CAL_CANCEL"},
    ])
    return {"inline_keyboard": rows}, title


@bot_bp.post("/telegram/webhook")
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    cb = update.get("callback_query")
    if cb:
        chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
        from_user = (cb.get("from") or {}).get("id")
        message_id = (cb.get("message") or {}).get("message_id")
        data_cb = cb.get("data") or ""
        cb_id = cb.get("id")
        # Only handle our calendar callbacks
        if data_cb.startswith("CAL_"):
            import datetime as _dt
            step, stdata = get_state(from_user)
            if data_cb == "CAL_NOP":
                answer_callback_query(cb_id)
                return {"ok": True}
            if data_cb == "CAL_CANCEL":
                clear_state(from_user)
                try:
                    edit_message_text(chat_id, message_id, "❌ Operación cancelada.")
                except Exception:
                    pass
                answer_callback_query(cb_id, "Cancelado")
                return {"ok": True}
            if data_cb == "CAL_TODAY":
                day = _dt.date.today().isoformat()
                data_cb = f"CAL_SET:{day}"
            if data_cb.startswith("CAL_NAV:"):
                try:
                    _, rest = data_cb.split(":", 1)
                    ym, direction = rest.split(":")
                    y, m = [int(x) for x in ym.split("-")]
                    if direction == "next":
                        if m == 12:
                            y += 1; m = 1
                        else:
                            m += 1
                    else:
                        if m == 1:
                            y -= 1; m = 12
                        else:
                            m -= 1
                except Exception:
                    answer_callback_query(cb_id)
                    return {"ok": True}
                # Clamp navigation so it never goes beyond current month
                today = _dt.date.today()
                if (y, m) > (today.year, today.month):
                    y, m = today.year, today.month
                kb, _ = _build_calendar_kb(y, m)
                try:
                    edit_message_reply_markup(chat_id, message_id, kb)
                except Exception:
                    pass
                answer_callback_query(cb_id)
                return {"ok": True}
            if data_cb.startswith("CAL_SET:"):
                _, day = data_cb.split(":", 1)
                if step == "ASK_FECHA_CONSIG":
                    pid = (stdata or {}).get("pid")
                    if not pid:
                        clear_state(from_user)
                        answer_callback_query(cb_id, "Sin contexto")
                        return {"ok": True}
                    p = PaymentRequest.query.get(pid)
                    if not p:
                        clear_state(from_user)
                        answer_callback_query(cb_id, "No encontrado")
                        return {"ok": True}
                    try:
                        selected = _dt.date.fromisoformat(day)
                        today = _dt.date.today()
                        if selected > today:
                            answer_callback_query(cb_id, "Fecha futura no permitida")
                            return {"ok": True}
                        p.fecha_consignacion = selected
                        db.session.commit()
                        disp = selected.strftime("%d/%m/%Y")
                        try:
                            edit_message_text(chat_id, message_id, f"✅ Fecha seleccionada: <b>{disp}</b>")
                        except Exception:
                            pass
                        send_message(chat_id, f"✅ Fecha registrada: <b>{disp}</b>\nID solicitud: <b>{p.id}</b>\nCliente: <b>{p.cliente}</b>\nEstado: <b>{p.estado.value}</b>.",)
                        clear_state(from_user)
                        answer_callback_query(cb_id, "Fecha aplicada")
                        return {"ok": True}
                    except Exception:
                        answer_callback_query(cb_id, "Fecha inválida")
                        return {"ok": True}
                else:
                    answer_callback_query(cb_id)
                    return {"ok": True}
        # ignore other callbacks
        return {"ok": True}
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = msg["chat"]["id"]
    from_user = msg["from"]["id"]
    text = (msg.get("text") or "").strip()
    caption = (msg.get("caption") or "").strip()
    photos = msg.get("photo")
    document = msg.get("document")
    contact = msg.get("contact")

    # Si manda contacto → validar y crear sesión
    if contact:
        if str(contact.get("user_id")) != str(from_user):
            send_message(chat_id, "⚠️ Comparte tu <b>propio</b> número con el botón.")
            send_request_contact(chat_id)
            return {"ok": True}
        phone = normalize_phone(contact.get("phone_number"))
        if not phone:
            send_message(chat_id, "⚠️ No pude leer tu número. Intenta de nuevo.")
            send_request_contact(chat_id)
            return {"ok": True}
        wl = ReporterWhitelist.query.filter_by(phone_e164=phone, enabled=True).first()
        if not wl:
            send_message(
                chat_id, "🚫 Tu número no está habilitado para reportar pagos."
            )
            return {"ok": True}
        vu = VerifiedUser.query.filter_by(telegram_user_id=str(from_user)).first()
        if not vu:
            vu = VerifiedUser(
                telegram_user_id=str(from_user), phone_e164=phone, sucursal=wl.sucursal
            )
            db.session.add(vu)
        else:
            import datetime

            vu.phone_e164 = phone
            vu.sucursal = wl.sucursal
            vu.verified_at = datetime.datetime.utcnow()
        db.session.commit()
        txt = f"✅ Número verificado: <b>{phone}</b>"
        if wl.sucursal:
            txt += f"\n🏬 Sucursal asignada: <b>{wl.sucursal}</b>"
        send_message(chat_id, txt, kb=MAIN_KB)
        return {"ok": True}

    ok, res = check_verification(from_user)
    if not ok:
        if res == "expired":
            send_request_contact(
                chat_id, "⏳ Tu verificación expiró. Comparte tu <b>número</b>."
            )
        elif res in ["disabled", "not_found"]:
            send_request_contact(
                chat_id,
                "🚫 Tu número ya no está habilitado. Comparte tu número o contacta a tesorería.",
            )
        else:
            send_request_contact(
                chat_id, "🔒 Para usar el bot, comparte tu <b>número</b>."
            )
        return {"ok": True}

    # Router texto
    if text:
        lower = text.lower()

        if lower in [
            "menú principal",
            "menu principal",
            "menu",
            "volver",
            "inicio",
            "hola",
            "buenas",
        ]:
            clear_state(from_user)
            send_message(chat_id, "👋 ¿Qué deseas hacer?", kb=MAIN_KB)
            return {"ok": True}
        if lower == "ayuda":
            send_message(
                chat_id,
                "🆘 <b>Ayuda</b>\n• <b>Reportar pago</b>: te guío paso a paso.\n• <b>Ver estado</b>: consulta por cliente.\n\nEscribe <b>Menú principal</b> para volver.",
                kb=MAIN_KB,
            )
            return {"ok": True}
        if lower in ["cerrar sesión", "cerrar sesion", "logout"]:
            clear_state(from_user)
            vu = VerifiedUser.query.filter_by(telegram_user_id=str(from_user)).first()
            if vu:
                db.session.delete(vu)
                db.session.commit()
            send_request_contact(
                chat_id, "🔒 Sesión cerrada. Comparte tu <b>número</b> para continuar."
            )
            return {"ok": True}
        if lower == "cancelar":
            clear_state(from_user)
            send_message(chat_id, "❌ Flujo cancelado. ¿Qué deseas hacer?", kb=MAIN_KB)
            return {"ok": True}
        if lower == "reportar pago":
            clear_state(from_user)
            set_state(from_user, "ASK_VALOR", {})
            send_message(
                chat_id,
                "💰 ¿Cuál es el <b>valor</b> del pago? (solo números)",
                kb=CANCEL_KB,
            )
            return {"ok": True}
        if lower == "ver estado":
            clear_state(from_user)
            set_state(from_user, "ASK_CLIENTE_STATUS", {})
            send_message(
                chat_id,
                "🔎 Escribe el <b>nombre del cliente</b> a consultar.",
                kb=CANCEL_KB,
            )
            return {"ok": True}

        step, data = get_state(from_user)
        if step:
            if step == "AWAIT_EVIDENCE":
                send_message(
                    chat_id, "📸 Envía la <b>foto del comprobante</b>.", kb=CANCEL_KB
                )
                return {"ok": True}

            if step == "ASK_FECHA_CONSIG":
                # No aceptamos texto: mostramos calendario inline
                import datetime as _dt
                today = _dt.date.today()
                kb, title = _build_calendar_kb(today.year, today.month)
                send_message(chat_id, f"🗓️ Selecciona la <b>fecha de consignación</b> (usa el calendario).", kb=kb)
                return {"ok": True}

            if step == "ASK_VALOR":
                val = parse_amount(text)
                if not val:
                    send_message(chat_id, "⚠️ Valor no válido. Ej: 150000", kb=CANCEL_KB)
                    return {"ok": True}
                data["valor"] = val
                ok2, vu = check_verification(from_user)
                if ok2 and getattr(vu, "sucursal", None):
                    data["sucursal"] = vu.sucursal
                    set_state(from_user, "ASK_MEDIO", data)
                    send_message(
                        chat_id,
                        f"🏬 Sucursal: <b>{vu.sucursal}</b>\n💳 Selecciona el <b>medio de pago</b>:",
                        kb=medio_keyboard_rows(),
                    )
                    return {"ok": True}
                set_state(from_user, "ASK_SUCURSAL", data)
                send_message(
                    chat_id,
                    "🏬 Ingresa la <b>sucursal</b> (ej: BUCARAMANGA-CENTRO).",
                    kb=CANCEL_KB,
                )
                return {"ok": True}

            if step == "ASK_SUCURSAL":
                if len(text) < 2:
                    send_message(chat_id, "⚠️ Sucursal no válida.", kb=CANCEL_KB)
                    return {"ok": True}
                data["sucursal"] = text
                set_state(from_user, "ASK_MEDIO", data)
                send_message(
                    chat_id,
                    "💳 Selecciona el <b>medio de pago</b>:",
                    kb=medio_keyboard_rows(),
                )
                return {"ok": True}

            if step == "ASK_MEDIO":
                choice = text.strip()
                if choice.lower() not in MEDIOS_SET:
                    send_message(
                        chat_id,
                        "⚠️ Elige una opción del teclado.",
                        kb=medio_keyboard_rows(),
                    )
                    return {"ok": True}
                if choice.lower() == "otro medio":
                    set_state(from_user, "ASK_MEDIO_OTRO", data)
                    send_message(
                        chat_id, "✍️ Escribe el <b>otro medio de pago</b>.", kb=CANCEL_KB
                    )
                    return {"ok": True}
                data["medio_pago"] = choice
                set_state(from_user, "ASK_CLIENTE", data)
                send_message(
                    chat_id, "👤 Escribe el <b>nombre del cliente</b>.", kb=CANCEL_KB
                )
                return {"ok": True}

            if step == "ASK_MEDIO_OTRO":
                if len(text) < 3:
                    send_message(chat_id, "⚠️ Texto muy corto.", kb=CANCEL_KB)
                    return {"ok": True}
                data["medio_pago"] = text
                set_state(from_user, "ASK_CLIENTE", data)
                send_message(
                    chat_id, "👤 Escribe el <b>nombre del cliente</b>.", kb=CANCEL_KB
                )
                return {"ok": True}

            if step == "ASK_CLIENTE":
                if len(text) < 2:
                    send_message(
                        chat_id, "⚠️ Nombre muy corto. Intenta de nuevo.", kb=CANCEL_KB
                    )
                    return {"ok": True}
                data["cliente"] = text
                set_state(from_user, "AWAIT_EVIDENCE", data)
                send_message(
                    chat_id, "📸 Envía la <b>foto del comprobante</b>.", kb=CANCEL_KB
                )
                return {"ok": True}

            if step == "ASK_CLIENTE_STATUS":
                cliente = text
                pr = (
                    PaymentRequest.query.filter(
                        PaymentRequest.telegram_user_id == str(from_user),
                        PaymentRequest.cliente == cliente,
                    )
                    .order_by(PaymentRequest.created_at.desc())
                    .first()
                )
                if not pr:
                    send_message(
                        chat_id,
                        f"ℹ️ No encuentro pagos del cliente <b>{cliente}</b> reportados por ti.",
                        kb=MAIN_KB,
                    )
                else:
                    linea = (
                        f"👤 Cliente: <b>{pr.cliente}</b>\n"
                        f"💰 Valor: ${pr.valor:,}\n"
                        f"📍 Sucursal: {pr.sucursal}\n"
                        f"📌 Estado: <b>{pr.estado.value}</b>"
                    )
                    if pr.motivo_rechazo:
                        linea += f"\n❗ Motivo: {pr.motivo_rechazo}"
                    send_message(chat_id, linea, kb=MAIN_KB)
                clear_state(from_user)
                return {"ok": True}

        send_message(
            chat_id,
            "👋 Te ayudo a <b>Reportar pago</b> o <b>Ver estado</b>.",
            kb=MAIN_KB,
        )
        return {"ok": True}

    # Sin foto/doc
    if not photos and not document:
        step, _ = get_state(from_user)
        if step == "AWAIT_EVIDENCE":
            send_message(
                chat_id, "📸 Envía la <b>foto del comprobante</b>.", kb=CANCEL_KB
            )
        else:
            send_message(
                chat_id,
                "Envía una <b>foto</b> con caption:\n\n"
                "valor: 150000\nsucursal: BUCARAMANGA-CENTRO\nmedio_pago: Efectivo\ncliente: Juan Pérez\n\n"
                "O usa el menú: <b>Reportar pago</b>.",
            )
        return {"ok": True}

    # Foto/doc → crear solicitud (flujo guiado o caption)
    step, data = get_state(from_user)
    if step == "AWAIT_EVIDENCE" and data:
        parsed = {
            "valor": str(data.get("valor")),
            "sucursal": data.get("sucursal"),
            "medio_pago": data.get("medio_pago"),
            "cliente": data.get("cliente"),
        }
    else:
        parsed = {}
        for k, v in CAPTION_REGEX.findall(caption):
            kk = k.lower().strip()
            if kk in ("ref", "nombre"):
                kk = "cliente"
            parsed[kk] = v.strip()
        missing = [k for k in CAPTION_KEYS if k not in parsed]
        if missing:
            send_message(
                chat_id,
                "Faltan campos en el caption. También puedes usar <b>Reportar pago</b>.",
            )
            return {"ok": True}

    ok2, vu = check_verification(from_user)
    if not ok2:
        send_request_contact(chat_id, "🔒 Para reportar, comparte tu <b>número</b>.")
        return {"ok": True}
    if getattr(vu, "sucursal", None):
        parsed["sucursal"] = vu.sucursal
    # Obtener sociedad desde whitelist del número verificado
    sociedad_val = None
    try:
        if getattr(vu, "phone_e164", None):
            wl_row = ReporterWhitelist.query.filter_by(phone_e164=vu.phone_e164).first()
            if wl_row and getattr(wl_row, "sociedad", None):
                sociedad_val = wl_row.sociedad
    except Exception:
        sociedad_val = None

    # archivo
    file_id, tipo = None, None
    file_size = 0
    if photos:
        best = sorted(photos, key=lambda p: p.get("file_size", 0))[-1]
        file_id, tipo = best["file_id"], "photo"
        file_size = int(best.get("file_size", 0) or 0)
    else:
        file_id, tipo = document["file_id"], "document"
        file_size = int((document or {}).get("file_size", 0) or 0)

    # Validación de tamaño máximo
    try:
        max_mb = int(current_app.config.get("EVID_MAX_MB", 10))
    except Exception:
        max_mb = 10
    max_bytes = max_mb * 1024 * 1024
    if file_size and file_size > max_bytes:
        send_message(
            chat_id,
            f"⚠️ El archivo es muy grande (>{max_mb} MB). Envía una imagen o documento más liviano.",
        )
        return {"ok": True}

    # Validación de tipo de archivo permitido (documentos)
    if tipo == "document":
        allowed_mimes = {"image/jpeg", "image/png", "application/pdf"}
        allowed_exts = {".jpg", ".jpeg", ".png", ".pdf"}
        mime = ((document or {}).get("mime_type") or "").lower()
        fname = (document or {}).get("file_name") or ""
        ext = os.path.splitext(fname)[1].lower()
        if (mime and mime not in allowed_mimes) or (not mime and ext and ext not in allowed_exts):
            send_message(
                chat_id,
                "⚠️ Tipo de archivo no soportado. Envía JPG/PNG o PDF.",
            )
            return {"ok": True}

    try:
        file_path = get_file_path(file_id)
        filename = download_file(file_path)
    except Exception:
        send_message(chat_id, "⚠️ Error descargando la evidencia. Intenta de nuevo.")
        return {"ok": True}

    try:
        valor_int = int(re.sub(r"[^\d]", "", parsed["valor"]))
    except Exception:
        valor_int = None

    p = PaymentRequest(
        telegram_user_id=str(from_user),
        chat_id_respuesta=str(chat_id),
        sucursal=parsed["sucursal"],
        medio_pago=parsed["medio_pago"],
        cliente=parsed["cliente"],  # nombre del cliente
        valor=valor_int,
        sociedad=sociedad_val,
        estado=Estado.PENDIENTE,
    )
    db.session.add(p)
    db.session.flush()
    db.session.add(
        Evidence(
            payment_id=p.id, telegram_file_id=file_id, filename=filename, tipo=tipo
        )
    )
    db.session.commit()

    # Solicitar fecha de consignación con calendario inline
    set_state(from_user, "ASK_FECHA_CONSIG", {"pid": p.id})
    import datetime as _dt
    today = _dt.date.today()
    kb, _ = _build_calendar_kb(today.year, today.month)
    send_message(
        chat_id,
        f"✅ Comprobante recibido. ID solicitud: <b>{p.id}</b>\n🗓️ Selecciona la <b>fecha de consignación</b> en el calendario.",
        kb=kb,
    )
    return {"ok": True}
