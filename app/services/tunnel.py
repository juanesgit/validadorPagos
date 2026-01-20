from pyngrok import ngrok, conf
import atexit, requests


def setup_dev_tunnel_and_webhook(app):
    token = app.config.get("NGROK_AUTHTOKEN", "")
    if not token:
        app.logger.warning("NGROK_AUTHTOKEN no definido")
        return None
    # Permitir usar una ruta de ngrok preinstalada (evita bloqueos del antivirus de Windows)
    custom_path = app.config.get("NGROK_PATH", "")
    try:
        if custom_path:
            conf.set_default(conf.PyngrokConfig(auth_token=token, ngrok_path=custom_path))
        else:
            conf.get_default().auth_token = token
    except Exception as e:
        app.logger.error(f"Config pyngrok error: {e}")
    # Intentar cerrar procesos locales previos antes de abrir uno nuevo
    try:
        ngrok.kill()
    except Exception:
        pass
    try:
        public_url = ngrok.connect(addr=5000, proto="http").public_url
        app.logger.info(f"Túnel ngrok: {public_url}")
    except Exception as e:
        # Reutilizar túnel existente si ya hay uno activo (ERR_NGROK_334)
        try:
            tunnels = ngrok.get_tunnels()
        except Exception:
            tunnels = []
        chosen = None
        for t in tunnels:
            # Preferimos https si está disponible
            if str(t.public_url).startswith("https://"):
                chosen = t
                break
        if not chosen and tunnels:
            chosen = tunnels[0]
        if chosen:
            public_url = chosen.public_url
            app.logger.warning(
                f"Usando túnel existente: {public_url} (no se creó uno nuevo: {e})"
            )
        else:
            raise
    webhook_url = f"{public_url}/telegram/webhook"
    r = requests.post(
        f"{app.config['BOT_API']}/setWebhook", data={"url": webhook_url}, timeout=15
    )
    if not r.ok or not r.json().get("ok"):
        app.logger.error(f"setWebhook error: {r.text}")

    @atexit.register
    def _kill():
        try:
            ngrok.kill()
        except Exception:
            pass

    return public_url
