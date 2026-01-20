import os, re, json, csv, io, datetime, requests
from enum import Enum
from functools import wraps
import sys

# Archivo legacy deprecado: no ejecutar directamente
if __name__ == "__main__":
    print("Este archivo (app.py) está deprecado. Ejecuta: python manage.py", file=sys.stderr)
    sys.exit(1)

from flask import (
    Flask,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
    render_template,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Enum as SAEnum, or_

# =======================
# Carga entorno
# =======================
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en .env")

BOT_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
DB_URL = os.getenv("DATABASE_URL", "sqlite:///payments.db")
VERIF_TTL_MIN = int(os.getenv("VERIFICATION_TTL_MINUTES", "0"))  # 0 = sin expiración

# Driver MySQL automático
if DB_URL.startswith("mysql://"):
    try:
        import MySQLdb  # mysqlclient

        DB_URL = DB_URL.replace("mysql://", "mysql+mysqldb://", 1)
    except ModuleNotFoundError:
        try:
            import pymysql

            pymysql.install_as_MySQLdb()
            DB_URL = DB_URL.replace("mysql://", "mysql+pymysql://", 1)
        except ModuleNotFoundError:
            raise RuntimeError("Instala mysqlclient o PyMySQL para usar MySQL")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
EVID_DIR = os.path.join(BASE_DIR, "evidencias")
os.makedirs(EVID_DIR, exist_ok=True)

app = Flask(__name__, template_folder="templates")
app.config["SQLALCHEMY_DATABASE_URI"] = DB_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = SECRET_KEY
db = SQLAlchemy(app)


# =======================
# Modelos
# =======================
class Estado(str, Enum):
    PENDIENTE = "PENDIENTE"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"


class PaymentRequest(db.Model):
    __tablename__ = "payment_request"
    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50))
    chat_id_respuesta = db.Column(db.String(50))
    sucursal = db.Column(db.String(120))
    medio_pago = db.Column(db.String(80))
    referencia = db.Column(db.String(120))
    valor = db.Column(db.Integer)
    estado = db.Column(SAEnum(Estado), default=Estado.PENDIENTE, nullable=False)
    motivo_rechazo = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )
    evidences = db.relationship(
        "Evidence", backref="payment", lazy=True, cascade="all, delete-orphan"
    )


class Evidence(db.Model):
    __tablename__ = "evidence"
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(
        db.Integer, db.ForeignKey("payment_request.id"), nullable=False
    )
    telegram_file_id = db.Column(db.String(200))
    filename = db.Column(db.String(200))
    tipo = db.Column(db.String(30))
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class ConvState(db.Model):
    __tablename__ = "conv_state"
    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), index=True, unique=True)
    step = db.Column(db.String(40))
    data = db.Column(db.Text)
    updated_at = db.Column(
        db.DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )


class ReporterWhitelist(db.Model):
    __tablename__ = "reporter_whitelist"
    id = db.Column(db.Integer, primary_key=True)
    phone_e164 = db.Column(db.String(20), unique=True, index=True, nullable=False)
    sucursal = db.Column(db.String(120), nullable=True)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    nombre = db.Column(db.String(120), nullable=True)


class VerifiedUser(db.Model):
    __tablename__ = "verified_user"
    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), unique=True, index=True, nullable=False)
    phone_e164 = db.Column(db.String(20), nullable=False)
    sucursal = db.Column(db.String(120), nullable=True)
    verified_at = db.Column(
        db.DateTime, default=datetime.datetime.utcnow, nullable=False
    )


with app.app_context():
    db.create_all()


# =======================
# Helpers generales
# =======================
def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("is_admin"):
            return f(*args, **kwargs)
        return redirect(url_for("login"))

    return wrapper


def send_message(chat_id, text, reply_to=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    try:
        requests.post(f"{BOT_API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        app.logger.error(f"send_message error: {e}")


def send_message_kb(chat_id, text, keyboard_rows=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard_rows:
        payload["reply_markup"] = {
            "keyboard": keyboard_rows,
            "resize_keyboard": True,
            "one_time_keyboard": False,
        }
    try:
        requests.post(f"{BOT_API}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        app.logger.error(f"send_message_kb error: {e}")


def get_file_path(file_id):
    r = requests.get(
        f"{BOT_API}/getFile", params={"file_id": file_id}, timeout=15
    ).json()
    if not r.get("ok"):
        raise RuntimeError(f"No se pudo obtener file_path: {r}")
    return r["result"]["file_path"]


def download_file(file_path):
    resp = requests.get(f"{FILE_API}/{file_path}", timeout=30)
    resp.raise_for_status()
    basename = os.path.basename(file_path)
    dest = os.path.join(EVID_DIR, basename)
    with open(dest, "wb") as f:
        f.write(resp.content)
    return basename


def parse_amount(txt):
    try:
        return int(re.sub(r"[^\d]", "", txt or ""))
    except Exception:
        return None


def set_state(user_id, step, data=None):
    st = ConvState.query.filter_by(telegram_user_id=str(user_id)).first()
    if not st:
        st = ConvState(telegram_user_id=str(user_id))
        db.session.add(st)
    st.step = step
    st.data = json.dumps(data or {})
    db.session.commit()


def get_state(user_id):
    st = ConvState.query.filter_by(telegram_user_id=str(user_id)).first()
    if not st:
        return None, {}
    try:
        return st.step, json.loads(st.data or "{}")
    except Exception:
        return st.step, {}


def clear_state(user_id):
    st = ConvState.query.filter_by(telegram_user_id=str(user_id)).first()
    if st:
        db.session.delete(st)
        db.session.commit()


def normalize_phone(raw: str, default_cc="57"):
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if raw and raw.strip().startswith("+"):
        return f"+{digits}"
    if len(digits) == 10:  # celular co
        return f"+{default_cc}{digits}"
    return f"+{digits}"


def send_request_contact(
    chat_id, text="Para continuar, comparte tu número de celular."
):
    kb = [
        [{"text": "Compartir mi número 📲", "request_contact": True}],
        [{"text": "Ayuda"}, {"text": "Menú principal"}],
    ]
    try:
        requests.post(
            f"{BOT_API}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {
                    "keyboard": kb,
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            },
            timeout=15,
        )
    except Exception as e:
        app.logger.error(f"send_request_contact error: {e}")


def get_verified(telegram_user_id: int):
    return VerifiedUser.query.filter_by(telegram_user_id=str(telegram_user_id)).first()


def check_verification(from_user):
    vu = get_verified(from_user)
    if not vu:
        return False, "no_session"
    # TTL
    if VERIF_TTL_MIN > 0:
        delta = datetime.datetime.utcnow() - vu.verified_at
        if delta.total_seconds() > VERIF_TTL_MIN * 60:
            try:
                db.session.delete(vu)
                db.session.commit()
            except Exception:
                db.session.rollback()
            return False, "expired"
    # Whitelist vigente
    wl = ReporterWhitelist.query.filter_by(phone_e164=vu.phone_e164).first()
    if not wl:
        try:
            db.session.delete(vu)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return False, "not_found"
    if not wl.enabled:
        try:
            db.session.delete(vu)
            db.session.commit()
        except Exception:
            db.session.rollback()
        return False, "disabled"
    # Sync sucursal
    if wl.sucursal and wl.sucursal != vu.sucursal:
        try:
            vu.sucursal = wl.sucursal
            db.session.commit()
        except Exception:
            db.session.rollback()
    return True, vu


# Teclados
MAIN_KB = [
    [{"text": "Reportar pago"}],
    [{"text": "Ver estado"}],
    [{"text": "Ayuda"}],
    [{"text": "Cerrar sesión"}],
]
CANCEL_KB = [[{"text": "Cancelar"}, {"text": "Menú principal"}]]

# Medios de pago (botones)
MEDIOS_PAGO = [
    "Bancolombia",
    "Davivienda",
    "Banco de Bogota",
    "Banco BBVA",
    "Corresponsal Bancario",
    "Nequi",
    "Daviplata",
]
MEDIOS_SET = {m.lower() for m in MEDIOS_PAGO}


def medio_keyboard_rows():
    rows, row = [], []
    for i, m in enumerate(MEDIOS_PAGO, start=1):
        row.append({"text": m})
        if i % 2 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "Cancelar"}, {"text": "Menú principal"}])
    return rows


CAPTION_KEYS = ["valor", "sucursal", "medio_pago", "ref"]
CAPTION_REGEX = re.compile(r"(?mi)^(valor|sucursal|medio_pago|ref)\s*:\s*(.+)$")


# =======================
# Rutas Web (login/admin)
# =======================
@app.get("/")
def index():
    return redirect(url_for("admin") if session.get("is_admin") else url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin"))
        flash("Contraseña incorrecta", "danger")
    return render_template("login.html")


@app.post("/logout")
@require_admin
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/admin")
@require_admin
def admin():
    estado = request.args.get("estado")
    q = PaymentRequest.query
    if estado in [e.value for e in Estado]:
        q = q.filter(PaymentRequest.estado == Estado(estado))
    pagos = q.order_by(PaymentRequest.created_at.desc()).limit(300).all()
    return render_template("admin.html", pagos=pagos, Estado=Estado)


@app.get("/evidence/<int:evid_id>")
@require_admin
def evidence_view(evid_id):
    ev = Evidence.query.get_or_404(evid_id)
    return send_from_directory(EVID_DIR, ev.filename, as_attachment=False)


@app.post("/payments/<int:pid>/approve")
@require_admin
def approve(pid):
    p = PaymentRequest.query.get_or_404(pid)
    if p.estado != Estado.PENDIENTE:
        return redirect(url_for("admin"))
    p.estado = Estado.APROBADO
    db.session.commit()
    send_message(
        p.chat_id_respuesta,
        f"✅ Tu pago <b>{p.referencia}</b> fue <b>APROBADO</b> por tesorería.\nID: <b>{p.id}</b> | Valor: ${p.valor:,}",
    )
    return redirect(url_for("admin"))


@app.post("/payments/<int:pid>/reject")
@require_admin
def reject(pid):
    p = PaymentRequest.query.get_or_404(pid)
    if p.estado != Estado.PENDIENTE:
        return redirect(url_for("admin"))
    motivo = (request.form.get("motivo") or "No cumple validación").strip()
    p.estado = Estado.RECHAZADO
    p.motivo_rechazo = motivo
    db.session.commit()
    send_message(
        p.chat_id_respuesta,
        f"❌ Tu pago <b>{p.referencia}</b> fue <b>RECHAZADO</b>.\nMotivo: {motivo}\nID: <b>{p.id}</b>",
    )
    return redirect(url_for("admin"))


# =======================
# Panel Whitelist (CRUD + CSV)
# =======================
@app.get("/whitelist")
@require_admin
def whitelist():
    q = request.args.get("q", "").strip()
    estado = request.args.get("estado", "").strip()
    page = int(request.args.get("page", 1))
    per_page = 50
    query = ReporterWhitelist.query
    if q:
        qq = f"%{q}%"
        query = query.filter(
            or_(
                ReporterWhitelist.phone_e164.like(qq),
                ReporterWhitelist.sucursal.like(qq),
                ReporterWhitelist.nombre.like(qq),
            )
        )
    if estado == "activos":
        query = query.filter(ReporterWhitelist.enabled.is_(True))
    elif estado == "inactivos":
        query = query.filter(ReporterWhitelist.enabled.is_(False))
    total = query.count()
    registros = (
        query.order_by(
            ReporterWhitelist.enabled.desc(),
            ReporterWhitelist.sucursal.asc(),
            ReporterWhitelist.phone_e164.asc(),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )
    return render_template(
        "whitelist.html",
        registros=registros,
        q=q,
        estado=estado,
        page=page,
        per_page=per_page,
        total=total,
    )


@app.post("/whitelist/create")
@require_admin
def whitelist_create():
    phone_raw = request.form.get("phone_e164", "")
    sucursal = (request.form.get("sucursal") or "").strip()
    nombre = (request.form.get("nombre") or "").strip()
    enabled = request.form.get("enabled") == "on"
    phone = normalize_phone(phone_raw)
    if not phone:
        flash("Número inválido. Usa +57xxxxxxxxxx o 10 dígitos.", "danger")
        return redirect(url_for("whitelist"))
    wl = ReporterWhitelist.query.filter_by(phone_e164=phone).first()
    if wl:
        wl.sucursal = sucursal or wl.sucursal
        wl.nombre = nombre or wl.nombre
        wl.enabled = enabled
        msg = f"Actualizado: {phone}"
    else:
        wl = ReporterWhitelist(
            phone_e164=phone, sucursal=sucursal, nombre=nombre, enabled=enabled
        )
        db.session.add(wl)
        msg = f"Creado: {phone}"
    db.session.commit()
    flash(msg, "success")
    return redirect(url_for("whitelist"))


@app.post("/whitelist/<int:rid>/update")
@require_admin
def whitelist_update(rid):
    wl = ReporterWhitelist.query.get_or_404(rid)
    phone_raw = request.form.get("phone_e164", wl.phone_e164)
    phone = normalize_phone(phone_raw)
    if not phone:
        flash("Número inválido.", "danger")
        return redirect(url_for("whitelist"))
    existing = ReporterWhitelist.query.filter(
        ReporterWhitelist.phone_e164 == phone, ReporterWhitelist.id != rid
    ).first()
    if existing:
        flash("Ese número ya existe en la whitelist.", "danger")
        return redirect(url_for("whitelist"))
    wl.phone_e164 = phone
    wl.sucursal = (request.form.get("sucursal") or "").strip()
    wl.nombre = (request.form.get("nombre") or "").strip()
    wl.enabled = request.form.get("enabled") == "on"
    db.session.commit()
    flash("Registro actualizado.", "success")
    return redirect(url_for("whitelist"))


@app.post("/whitelist/<int:rid>/toggle")
@require_admin
def whitelist_toggle(rid):
    wl = ReporterWhitelist.query.get_or_404(rid)
    wl.enabled = not wl.enabled
    db.session.commit()
    flash(
        ("Activado" if wl.enabled else "Desactivado") + f": {wl.phone_e164}", "success"
    )
    return redirect(url_for("whitelist"))


@app.post("/whitelist/<int:rid>/delete")
@require_admin
def whitelist_delete(rid):
    wl = ReporterWhitelist.query.get_or_404(rid)
    db.session.delete(wl)
    db.session.commit()
    flash(f"Eliminado: {wl.phone_e164}", "success")
    return redirect(url_for("whitelist"))


@app.post("/whitelist/import")
@require_admin
def whitelist_import():
    f = request.files.get("file")
    if not f:
        flash("Adjunta un archivo CSV.", "danger")
        return redirect(url_for("whitelist"))
    try:
        content = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        count_new, count_upd = 0, 0
        for row in reader:
            raw = (row.get("phone") or row.get("phone_e164") or "").strip()
            phone = normalize_phone(raw)
            if not phone:
                continue
            sucursal = (row.get("sucursal") or "").strip()
            nombre = (row.get("nombre") or "").strip()
            enabled_str = (
                (row.get("enabled") or row.get("habilitado") or "1").strip().lower()
            )
            enabled = enabled_str in ["1", "true", "t", "yes", "si", "sí"]
            wl = ReporterWhitelist.query.filter_by(phone_e164=phone).first()
            if wl:
                wl.sucursal = sucursal or wl.sucursal
                wl.nombre = nombre or wl.nombre
                wl.enabled = enabled
                count_upd += 1
            else:
                db.session.add(
                    ReporterWhitelist(
                        phone_e164=phone,
                        sucursal=sucursal,
                        nombre=nombre,
                        enabled=enabled,
                    )
                )
                count_new += 1
        db.session.commit()
        flash(
            f"Importación OK. Nuevos: {count_new}, Actualizados: {count_upd}.",
            "success",
        )
    except Exception as e:
        app.logger.exception("Import CSV error")
        flash(f"Error importando CSV: {e}", "danger")
    return redirect(url_for("whitelist"))


@app.get("/whitelist/export")
@require_admin
def whitelist_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["phone_e164", "sucursal", "enabled", "nombre"])
    for wl in ReporterWhitelist.query.order_by(
        ReporterWhitelist.phone_e164.asc()
    ).all():
        writer.writerow(
            [wl.phone_e164, wl.sucursal or "", 1 if wl.enabled else 0, wl.nombre or ""]
        )
    output.seek(0)
    return app.response_class(
        output.read(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=whitelist.csv"},
    )


@app.post("/whitelist/<int:rid>/revoke")
@require_admin
def whitelist_revoke(rid):
    """Revoca cualquier sesión (VerifiedUser) asociada al número de esta fila."""
    wl = ReporterWhitelist.query.get_or_404(rid)
    phone = wl.phone_e164
    try:
        count = VerifiedUser.query.filter_by(phone_e164=phone).delete(
            synchronize_session=False
        )
        db.session.commit()
        flash(f"Sesiones revocadas para {phone}: {count}", "success")
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Error revocando sesiones")
        flash(f"Error revocando sesiones: {e}", "danger")
    return redirect(url_for("whitelist"))


@app.post("/whitelist/revoke_all")
@require_admin
def whitelist_revoke_all():
    """(Opcional) Revoca TODAS las sesiones activas (para casos de emergencia)."""
    try:
        count = VerifiedUser.query.delete(synchronize_session=False)
        db.session.commit()
        flash(f"Todas las sesiones revocadas: {count}", "success")
    except Exception as e:
        db.session.rollback()
        app.logger.exception("Error revocando todas las sesiones")
        flash(f"Error revocando todas las sesiones: {e}", "danger")
    return redirect(url_for("whitelist"))


# =======================
# Webhook Telegram
# =======================
@app.post("/telegram/webhook")
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = msg["chat"]["id"]
    from_user = msg["from"]["id"]

    # Entradas posibles
    text = (msg.get("text") or "").strip()
    caption = (msg.get("caption") or "").strip()
    photos = msg.get("photo")
    document = msg.get("document")
    contact = msg.get("contact")

    # 0) Gateo: validar/solicitar contacto
    if contact:
        if str(contact.get("user_id")) != str(from_user):
            send_message(chat_id, "⚠️ Comparte tu <b>propio</b> número con el botón.")
            send_request_contact(chat_id)
            return {"ok": True}
        phone_e164 = normalize_phone(contact.get("phone_number"))
        if not phone_e164:
            send_message(chat_id, "⚠️ No pude leer tu número. Intenta de nuevo.")
            send_request_contact(chat_id)
            return {"ok": True}
        wl = ReporterWhitelist.query.filter_by(
            phone_e164=phone_e164, enabled=True
        ).first()
        if not wl:
            send_message(
                chat_id,
                "🚫 Tu número no está habilitado para reportar pagos. Contacta a tesorería.",
            )
            return {"ok": True}
        vu = VerifiedUser.query.filter_by(telegram_user_id=str(from_user)).first()
        if not vu:
            vu = VerifiedUser(
                telegram_user_id=str(from_user),
                phone_e164=phone_e164,
                sucursal=wl.sucursal,
            )
            db.session.add(vu)
        else:
            vu.phone_e164 = phone_e164
            vu.sucursal = wl.sucursal
            vu.verified_at = datetime.datetime.utcnow()
        db.session.commit()
        send_message_kb(
            chat_id,
            f"✅ Número verificado: <b>{phone_e164}</b>"
            + (f"\n🏬 Sucursal asignada: <b>{wl.sucursal}</b>" if wl.sucursal else ""),
            keyboard_rows=MAIN_KB,
        )
        return {"ok": True}

    ok, res = check_verification(from_user)
    if not ok:
        if res == "expired":
            send_request_contact(
                chat_id,
                "⏳ Tu verificación expiró. Comparte tu <b>número de celular</b> para continuar.",
            )
        elif res in ["disabled", "not_found"]:
            send_request_contact(
                chat_id,
                "🚫 Tu número ya no está habilitado. Comparte tu número para revalidar o contacta a tesorería.",
            )
        else:
            send_request_contact(
                chat_id, "🔒 Para usar el bot, comparte tu <b>número de celular</b>."
            )
        return {"ok": True}

    # 1) Router de texto
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
            send_message_kb(chat_id, "👋 ¿Qué deseas hacer?", keyboard_rows=MAIN_KB)
            return {"ok": True}

        if lower == "ayuda":
            send_message_kb(
                chat_id,
                "🆘 <b>Ayuda</b>\n• <b>Reportar pago</b>: te guío paso a paso.\n• <b>Ver estado</b>: consulta por referencia.\n\nEn cualquier momento escribe <b>Menú principal</b>.",
                keyboard_rows=MAIN_KB,
            )
            return {"ok": True}

        if lower in ["cerrar sesión", "cerrar sesion", "logout"]:
            clear_state(from_user)
            vu = VerifiedUser.query.filter_by(telegram_user_id=str(from_user)).first()
            if vu:
                try:
                    db.session.delete(vu)
                    db.session.commit()
                except Exception:
                    db.session.rollback()
            send_request_contact(
                chat_id,
                "🔒 Sesión cerrada. Comparte tu <b>número de celular</b> para continuar.",
            )
            return {"ok": True}

        if lower == "cancelar":
            clear_state(from_user)
            send_message_kb(
                chat_id, "❌ Flujo cancelado. ¿Qué deseas hacer?", keyboard_rows=MAIN_KB
            )
            return {"ok": True}

        if lower == "reportar pago":
            clear_state(from_user)
            set_state(from_user, "ASK_VALOR", {})
            send_message_kb(
                chat_id,
                "💰 ¿Cuál es el <b>valor</b> del pago? (solo números)",
                keyboard_rows=CANCEL_KB,
            )
            return {"ok": True}

        if lower == "ver estado":
            clear_state(from_user)
            set_state(from_user, "ASK_REF_STATUS", {})
            send_message_kb(
                chat_id,
                "🔎 Escribe la <b>referencia</b> del pago que deseas consultar.",
                keyboard_rows=CANCEL_KB,
            )
            return {"ok": True}

        # Procesar pasos
        step, data = get_state(from_user)
        if step:
            if step == "AWAIT_EVIDENCE":
                send_message_kb(
                    chat_id,
                    "📸 Envía la <b>foto del comprobante</b>.",
                    keyboard_rows=CANCEL_KB,
                )
                return {"ok": True}

            if step == "ASK_VALOR":
                val = parse_amount(text)
                if not val:
                    send_message_kb(
                        chat_id,
                        "⚠️ Valor no válido. Ingresa solo números (ej: 150000).",
                        keyboard_rows=CANCEL_KB,
                    )
                    return {"ok": True}
                data["valor"] = val
                vu_ok, vu_res = check_verification(from_user)
                vu = vu_res if vu_ok else None
                if vu and getattr(vu, "sucursal", None):
                    data["sucursal"] = vu.sucursal
                    set_state(from_user, "ASK_MEDIO", data)
                    send_message_kb(
                        chat_id,
                        f"🏬 Sucursal asignada: <b>{vu.sucursal}</b>\n💳 Selecciona el <b>medio de pago</b>:",
                        keyboard_rows=medio_keyboard_rows(),
                    )
                    return {"ok": True}
                set_state(from_user, "ASK_SUCURSAL", data)
                send_message_kb(
                    chat_id,
                    "🏬 Ingresa la <b>sucursal</b> (ej: BUCARAMANGA-CENTRO).",
                    keyboard_rows=CANCEL_KB,
                )
                return {"ok": True}

            if step == "ASK_SUCURSAL":
                if len(text) < 2:
                    send_message_kb(
                        chat_id,
                        "⚠️ Sucursal no válida. Intenta de nuevo.",
                        keyboard_rows=CANCEL_KB,
                    )
                    return {"ok": True}
                data["sucursal"] = text
                set_state(from_user, "ASK_MEDIO", data)
                send_message_kb(
                    chat_id,
                    "💳 Selecciona el <b>medio de pago</b>:",
                    keyboard_rows=medio_keyboard_rows(),
                )
                return {"ok": True}

            if step == "ASK_MEDIO":
                choice = text.strip()
                if choice.lower() not in MEDIOS_SET:
                    send_message_kb(
                        chat_id,
                        "⚠️ Por favor <b>elige una opción del teclado</b>.",
                        keyboard_rows=medio_keyboard_rows(),
                    )
                    return {"ok": True}
                if choice.lower() == "otro medio":
                    set_state(from_user, "ASK_MEDIO_OTRO", data)
                    send_message_kb(
                        chat_id,
                        "✍️ Escribe el nombre del <b>otro medio de pago</b>.",
                        keyboard_rows=CANCEL_KB,
                    )
                    return {"ok": True}
                data["medio_pago"] = choice
                set_state(from_user, "ASK_REF", data)
                send_message_kb(
                    chat_id,
                    "🧾 Escribe la <b>referencia</b> de la operación.",
                    keyboard_rows=CANCEL_KB,
                )
                return {"ok": True}

            if step == "ASK_MEDIO_OTRO":
                if len(text) < 3:
                    send_message_kb(
                        chat_id,
                        "⚠️ Texto muy corto. Indica el <b>otro medio de pago</b>.",
                        keyboard_rows=CANCEL_KB,
                    )
                    return {"ok": True}
                data["medio_pago"] = text
                set_state(from_user, "ASK_REF", data)
                send_message_kb(
                    chat_id,
                    "🧾 Escribe la <b>referencia</b> de la operación.",
                    keyboard_rows=CANCEL_KB,
                )
                return {"ok": True}

            if step == "ASK_REF":
                if len(text) < 3:
                    send_message_kb(
                        chat_id,
                        "⚠️ Referencia muy corta. Intenta de nuevo.",
                        keyboard_rows=CANCEL_KB,
                    )
                    return {"ok": True}
                data["ref"] = text
                set_state(from_user, "AWAIT_EVIDENCE", data)
                send_message_kb(
                    chat_id,
                    "📸 Ahora envía la <b>foto del comprobante</b>.",
                    keyboard_rows=CANCEL_KB,
                )
                return {"ok": True}

            if step == "ASK_REF_STATUS":
                ref = text
                pr = (
                    PaymentRequest.query.filter(
                        PaymentRequest.telegram_user_id == str(from_user),
                        PaymentRequest.referencia == ref,
                    )
                    .order_by(PaymentRequest.created_at.desc())
                    .first()
                )
                if not pr:
                    send_message_kb(
                        chat_id,
                        f"ℹ️ No encuentro pagos con ref <b>{ref}</b> reportados por ti.",
                        keyboard_rows=MAIN_KB,
                    )
                else:
                    estado = pr.estado.value
                    linea = (
                        f"🧾 Ref: <b>{pr.referencia}</b>\n"
                        f"💰 Valor: ${pr.valor:,}\n"
                        f"📍 Sucursal: {pr.sucursal}\n"
                        f"📌 Estado: <b>{estado}</b>"
                    )
                    if pr.motivo_rechazo:
                        linea += f"\n❗ Motivo rechazo: {pr.motivo_rechazo}"
                    send_message_kb(chat_id, linea, keyboard_rows=MAIN_KB)
                clear_state(from_user)
                return {"ok": True}

        # Sin estado: mostrar menú
        send_message_kb(
            chat_id,
            "👋 Te puedo ayudar a <b>Reportar pago</b> o <b>Ver estado</b>.",
            keyboard_rows=MAIN_KB,
        )
        return {"ok": True}

    # 2) Si no hay foto/doc
    if not photos and not document:
        step, _ = get_state(from_user)
        if step == "AWAIT_EVIDENCE":
            send_message_kb(
                chat_id,
                "📸 Envía la <b>foto del comprobante</b> para continuar.",
                keyboard_rows=CANCEL_KB,
            )
        else:
            send_message(
                chat_id,
                "Envía una <b>foto del comprobante</b> con el caption:\n\n"
                "valor: 150000\nsucursal: BUCARAMANGA-CENTRO\nmedio_pago: Efectivo\nref: 2025-08-29-OP-1234\n\n"
                "O usa el menú: <b>Reportar pago</b> para hacerlo paso a paso.",
            )
        return {"ok": True}

    # 3) Llega foto/doc → crear solicitud (con flujo guiado o caption)
    step, data = get_state(from_user)
    if step == "AWAIT_EVIDENCE" and data:
        parsed = {
            "valor": str(data.get("valor")),
            "sucursal": data.get("sucursal"),
            "medio_pago": data.get("medio_pago"),
            "ref": data.get("ref"),
        }
    else:
        parsed = {}
        for k, v in CAPTION_REGEX.findall(caption):
            parsed[k.lower()] = v.strip()
        missing = [k for k in CAPTION_KEYS if k not in parsed]
        if missing:
            send_message(
                chat_id,
                "Faltan campos en el caption. También puedes usar el flujo guiado con <b>Reportar pago</b>.",
            )
            return {"ok": True}

    ok, vu = check_verification(from_user)
    if not ok:
        send_request_contact(
            chat_id, "🔒 Para reportar, comparte tu <b>número de celular</b>."
        )
        return {"ok": True}
    if isinstance(vu, VerifiedUser) and vu.sucursal:
        parsed["sucursal"] = vu.sucursal  # fuerza sucursal whitelist

    file_id, tipo = None, None
    if photos:
        best = sorted(photos, key=lambda p: p.get("file_size", 0))[-1]
        file_id = best["file_id"]
        tipo = "photo"
    elif document:
        file_id = document["file_id"]
        tipo = "document"

    try:
        file_path = get_file_path(file_id)
        filename = download_file(file_path)
    except Exception as e:
        app.logger.error(f"Fallo descarga evidencia: {e}")
        send_message(
            chat_id, "⚠️ Ocurrió un error descargando la evidencia. Intenta de nuevo."
        )
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
        referencia=parsed["ref"],
        valor=valor_int,
        estado=Estado.PENDIENTE,
    )
    db.session.add(p)
    db.session.flush()
    ev = Evidence(
        payment_id=p.id, telegram_file_id=file_id, filename=filename, tipo=tipo
    )
    db.session.add(ev)
    db.session.commit()

    send_message(
        chat_id,
        f"✅ Recibido. ID solicitud: <b>{p.id}</b>\nEstado: <b>PENDIENTE</b>.\nTesorería revisará y te notificaremos aquí.",
    )
    if step == "AWAIT_EVIDENCE":
        clear_state(from_user)
    return {"ok": True}


# =======================
# (Opcional) Túnel dev con pyngrok + setWebhook
# =======================
def setup_dev_tunnel_and_webhook():
    from pyngrok import ngrok, conf

    conf.get_default().auth_token = os.getenv("NGROK_AUTHTOKEN", "").strip()
    if not conf.get_default().auth_token:
        app.logger.warning("NGROK_AUTHTOKEN no definido; no se abrirá túnel.")
        return None
    public_url = ngrok.connect(addr=5000, proto="http").public_url
    app.logger.info(f"Túnel ngrok activo: {public_url}")
    webhook_url = f"{public_url}/telegram/webhook"
    r = requests.post(f"{BOT_API}/setWebhook", data={"url": webhook_url}, timeout=15)
    if not r.ok or not r.json().get("ok"):
        app.logger.error(f"Error setWebhook: {r.text}")
    else:
        app.logger.info(f"Webhook configurado en: {webhook_url}")
    import atexit

    @atexit.register
    def _shutdown_ngrok():
        try:
            ngrok.kill()
        except Exception:
            pass

    return public_url


if __name__ == "__main__":
    if os.getenv("DEV_TUNNEL", "false").lower() == "true":
        try:
            setup_dev_tunnel_and_webhook()
        except Exception as e:
            app.logger.error(f"No se pudo iniciar túnel dev: {e}")
    app.run(host="0.0.0.0", port=5000, debug=True)
