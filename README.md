# Validador de Pagos (Flask)

Sistema para recibir reportes de pagos vía Telegram (con evidencia) y gestionarlos en un panel administrativo.

## Estructura
- app/__init__.py: app factory y registro de blueprints
- app/blueprints/: admin, whitelist, bot (webhook Telegram)
- app/models.py: modelos SQLAlchemy
- app/services/: telegram, tunnel (ngrok), verification
- manage.py: entrada principal (desarrollo/producción), CLI

## Requisitos
- Python 3.10+
- Base de datos: SQLite (por defecto) o MySQL/MariaDB

## Instalación
1) Crear entorno y dependencias
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

2) Configurar variables de entorno
- Copia `.env.example` a `.env` y completa los valores.
- Variables clave:
  - TELEGRAM_BOT_TOKEN=...
  - ADMIN_PASSWORD=...
  - SECRET_KEY=...
  - DATABASE_URL=sqlite:///payments.db (o mysql+pymysql://user:pass@host/db)
  - DEV_TUNNEL=true|false
  - NGROK_AUTHTOKEN=...
  - NGROK_PATH=C:\\ruta\\a\\ngrok.exe (Windows recomendado)
  - PUBLIC_BASE_URL=https://tu-dominio (prod o túnel manual)
  - VERIFICATION_TTL_MINUTES=480 (0 = nunca expira)
  - EVID_MAX_MB=10 (tamaño máximo de evidencia)

3) Migraciones
- Primera vez (si no existe carpeta migrations):
```
flask --app manage.py db init
```
- Generar/aplicar migraciones:
```
flask --app manage.py db migrate -m "baseline + indexes"
flask --app manage.py db upgrade
```

## Ejecutar
```
python manage.py
```
- Admin: http://localhost:5000/admin (contraseña = ADMIN_PASSWORD)
- Health: http://localhost:5000/health

## Webhook y túnel (ngrok)
- DEV_TUNNEL=true: intenta abrir túnel automáticamente y setear el webhook.
- En Windows:
  - Es MUY recomendable definir `NGROK_PATH` apuntando al ejecutable ngrok (para evitar bloqueos del antivirus/Defender).
  - Si sigues con problemas, pon `DEV_TUNNEL=false` y usa un túnel manual.

### Túnel manual
1) Ejecuta ngrok manualmente:
```
ngrok http http://localhost:5000
```
2) Copia el dominio público (ej. https://xxxx.ngrok-free.app) y define PUBLIC_BASE_URL en `.env`.
3) Sube webhook con CLI:
```
flask --app manage.py set-webhook
```

### CLI útil
```
flask --app manage.py get-webhook
flask --app manage.py delete-webhook
flask --app manage.py set-webhook
flask --app manage.py revoke-expired
```

## Panel Admin (funcionalidades)
- Filtros: texto (`q`), estado, rango de fechas (`desde`/`hasta`)
- Conteo por estado para los filtros aplicados
- Paginación y exportación a Excel (normal y con imágenes)

## Bot Telegram (flujo)
- Validación por número (whitelist)
- Reporte guiado: valor → sucursal (o detectada) → medio → cliente → evidencia
- Ver estado por cliente
- Límite de tamaño de evidencia (`EVID_MAX_MB`)

## Troubleshooting
- ngrok no arranca / ERR_NGROK_334:
  - Cierra procesos previos de ngrok (el sistema lo intenta automáticamente).
  - Define `NGROK_PATH` y usa el ejecutable instalado manualmente.
  - Si persiste, desactiva `DEV_TUNNEL` y usa túnel manual + `set-webhook`.
- 4040 ocupado o UI de ngrok caída: reinicia ngrok o cambia a túnel manual.
- Windows Defender bloquea ngrok: añade exclusión para la ruta del ejecutable.

## Nota sobre app.py (legacy)
Existe un `app.py` monolítico descontinuado. Usa siempre `manage.py` (app factory). En producción, apunta tu WSGI/ASGI al `create_app()` de `app/__init__.py`.
