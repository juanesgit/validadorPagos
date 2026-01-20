# app/commands.py
import os
import shutil
import datetime
import click
from sqlalchemy import text
from .extensions import db
import requests


def register_cli(app):
    @app.cli.command("reset-db")
    @click.option("--yes", is_flag=True, help="No pedir confirmación.")
    @click.option(
        "--keep-evidencias", is_flag=True, help="No borrar archivos de evidencias."
    )
    @click.option(
        "--seed-demo", is_flag=True, help="Cargar un registro de demo en whitelist."
    )
    def reset_db(yes, keep_evidencias, seed_demo):
        """
        Borra TODAS las tablas y las recrea. Opcionalmente limpia evidencias y carga demo.
        """
        if not yes:
            click.confirm(
                "⚠️ Esto BORRARÁ definitivamente todos los datos. ¿Continuar?",
                abort=True,
            )

        with app.app_context():
            engine = db.engine
            backend = engine.url.get_backend_name()
            click.echo(f"Conectado a: {engine.url}")

            # Desactivar FKs en MySQL para poder dropear en cualquier orden
            if backend.startswith("mysql"):
                db.session.execute(text("SET FOREIGN_KEY_CHECKS=0;"))
                db.session.commit()

            # Drop & create
            db.drop_all()
            if backend.startswith("mysql"):
                db.session.execute(text("SET FOREIGN_KEY_CHECKS=1;"))
                db.session.commit()
            db.create_all()
            click.echo("✅ Tablas recreadas.")

            # Limpiar evidencias
            if not keep_evidencias:
                evid_dir = app.config["EVID_DIR"]
                os.makedirs(evid_dir, exist_ok=True)
                borrados = 0
                for name in os.listdir(evid_dir):
                    path = os.path.join(evid_dir, name)
                    try:
                        if os.path.isfile(path):
                            os.remove(path)
                            borrados += 1
                        elif os.path.isdir(path):
                            shutil.rmtree(path)
                            borrados += 1
                    except Exception as e:
                        click.echo(f"  ! No se pudo borrar {name}: {e}")
                click.echo(f"🧹 Evidencias eliminadas: {borrados}")

            # Seed demo (opcional)
            if seed_demo:
                from .models import ReporterWhitelist
                from .services.verification import normalize_phone

                demo = ReporterWhitelist(
                    phone_e164=normalize_phone("+573001112233"),
                    sucursal="BUCARAMANGA-CENTRO",
                    ciudad="Bucaramanga",
                    enabled=True,
                    nombre="Demo",
                )
                db.session.add(demo)
                db.session.commit()
                click.echo("🌱 Seed: agregado +573001112233 (Demo).")

    @app.cli.command("set-webhook")
    @click.option("--url", "url_override", default="", help="URL pública explícita (opcional)")
    def set_webhook(url_override):
        """Configura el webhook de Telegram usando PUBLIC_BASE_URL o la URL indicada."""
        base = (url_override or app.config.get("PUBLIC_BASE_URL", "")).rstrip("/")
        if not base:
            click.echo("❗ Define PUBLIC_BASE_URL en .env o usa --url")
            return
        webhook_url = f"{base}/telegram/webhook"
        r = requests.post(f"{app.config['BOT_API']}/setWebhook", data={"url": webhook_url}, timeout=15)
        ok = False
        try:
            ok = r.ok and r.json().get("ok")
        except Exception:
            ok = False
        click.echo(f"setWebhook -> {webhook_url} | status={r.status_code} ok={ok}")

    @app.cli.command("delete-webhook")
    def delete_webhook():
        """Elimina el webhook configurado en Telegram."""
        r = requests.post(f"{app.config['BOT_API']}/deleteWebhook", timeout=15)
        ok = False
        try:
            ok = r.ok and r.json().get("ok")
        except Exception:
            ok = False
        click.echo(f"deleteWebhook | status={r.status_code} ok={ok}")

    @app.cli.command("get-webhook")
    def get_webhook():
        """Muestra información del webhook actual en Telegram."""
        r = requests.get(f"{app.config['BOT_API']}/getWebhookInfo", timeout=15)
        try:
            click.echo(r.json())
        except Exception:
            click.echo(f"status={r.status_code} body={r.text[:300]}")

    @app.cli.command("revoke-expired")
    def revoke_expired():
        """Revoca sesiones verificadas expiradas según VERIF_TTL_MINUTES."""
        from .models import VerifiedUser
        ttl = int(app.config.get("VERIF_TTL_MINUTES", 0) or 0)
        if ttl <= 0:
            click.echo("TTL=0 (no expira). Nada por hacer.")
            return
        threshold = datetime.datetime.utcnow() - datetime.timedelta(minutes=ttl)
        count = (
            db.session.query(VerifiedUser)
            .filter(VerifiedUser.verified_at < threshold)
            .delete(synchronize_session=False)
        )
        db.session.commit()
        click.echo(f"Sesiones expiradas revocadas: {count}")
