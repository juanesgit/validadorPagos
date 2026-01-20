import os
from dotenv import load_dotenv

try:
    load_dotenv()
except Exception:
    pass


def _mysql_fallback(url: str) -> str:
    # If explicitly using mysqldb driver but module is not installed, fall back to PyMySQL
    if url.startswith("mysql+mysqldb://"):
        try:
            import MySQLdb  # type: ignore
            return url  # driver available, keep as-is
        except ModuleNotFoundError:
            try:
                import pymysql  # type: ignore
                return url.replace("mysql+mysqldb://", "mysql+pymysql://", 1)
            except ModuleNotFoundError:
                raise RuntimeError("Instala mysqlclient o PyMySQL para usar MySQL")
    # If explicitly using PyMySQL, ensure dependency exists
    if url.startswith("mysql+pymysql://"):
        try:
            import pymysql  # type: ignore
        except ModuleNotFoundError:
            raise RuntimeError("Falta PyMySQL: pip install PyMySQL")
        return url
    # Bare mysql:// -> prefer mysqlclient if present, else PyMySQL
    if url.startswith("mysql://"):
        try:
            import MySQLdb  # mysqlclient
            return url.replace("mysql://", "mysql+mysqldb://", 1)
        except ModuleNotFoundError:
            try:
                import pymysql
                pymysql.install_as_MySQLdb()
                return url.replace("mysql://", "mysql+pymysql://", 1)
            except ModuleNotFoundError:
                raise RuntimeError("Instala mysqlclient o PyMySQL para usar MySQL")
    return url


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en .env")

    SQLALCHEMY_DATABASE_URI = _mysql_fallback(
        os.getenv("DATABASE_URL", "sqlite:///payments.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

    # Paths
    BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
    EVID_DIR = os.path.join(BASE_DIR, "evidencias")

    # Telegram API
    BOT_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

    # TTL verificación (minutos). 0 = nunca expira
    VERIF_TTL_MINUTES = int(os.getenv("VERIFICATION_TTL_MINUTES", "480"))
    # Tamaño máximo de evidencia (MB)
    EVID_MAX_MB = int(os.getenv("EVID_MAX_MB", "10"))

    # Dev tunnel
    DEV_TUNNEL = os.getenv("DEV_TUNNEL", "false").lower() == "true"
    NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "").strip()
    NGROK_PATH = os.getenv("NGROK_PATH", "").strip()

    # (Prod) dominio público para setWebhook
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
