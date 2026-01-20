import datetime
from enum import Enum
from sqlalchemy import Enum as SAEnum
from .extensions import db


class Estado(str, Enum):
    PENDIENTE = "PENDIENTE"
    APROBADO = "APROBADO"
    RECHAZADO = "RECHAZADO"


class Sociedad(str, Enum):
    COANDES = "COANDES"
    MANCHESTER = "MANCHESTER"
    ALMACENES = "ALMACENES"


class PaymentRequest(db.Model):
    __tablename__ = "payment_request"
    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), index=True)
    chat_id_respuesta = db.Column(db.String(50))
    sucursal = db.Column(db.String(120), index=True)
    medio_pago = db.Column(db.String(80), index=True)
    # referencia: aquí guardamos el NOMBRE DEL CLIENTE (compatibilidad)
    cliente = db.Column(db.String(120), index=True)
    valor = db.Column(db.Integer, index=True)
    # fecha de consignación reportada por el usuario
    fecha_consignacion = db.Column(db.Date)
    sociedad = db.Column(SAEnum(Sociedad), index=True)
    estado = db.Column(SAEnum(Estado), default=Estado.PENDIENTE, nullable=False, index=True)
    motivo_rechazo = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, index=True)
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
        db.Integer, db.ForeignKey("payment_request.id"), nullable=False, index=True
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
    sucursal = db.Column(db.String(120))
    ciudad = db.Column(db.String(120))
    sociedad = db.Column(SAEnum(Sociedad), index=True)  # nullable por compatibilidad/migración
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    nombre = db.Column(db.String(120))


class VerifiedUser(db.Model):
    __tablename__ = "verified_user"
    id = db.Column(db.Integer, primary_key=True)
    telegram_user_id = db.Column(db.String(50), unique=True, index=True, nullable=False)
    phone_e164 = db.Column(db.String(20), nullable=False)
    sucursal = db.Column(db.String(120))
    verified_at = db.Column(
        db.DateTime, default=datetime.datetime.utcnow, nullable=False
    )
