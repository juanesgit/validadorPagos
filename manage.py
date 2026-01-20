from app import create_app

app = create_app()

if __name__ == "__main__":
    # Túnel dev opcional (usa NGROK_AUTHTOKEN y DEV_TUNNEL=true en .env)
    if app.config.get("DEV_TUNNEL", False):
        try:
            from app.services.tunnel import setup_dev_tunnel_and_webhook

            setup_dev_tunnel_and_webhook(app)
        except Exception as e:
            app.logger.error(f"No se pudo iniciar túnel dev: {e}")
    app.run(host="0.0.0.0", port=5000, debug=True)
