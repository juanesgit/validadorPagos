import csv, io
from flask import Blueprint, render_template, request, redirect, url_for, flash
from sqlalchemy import or_
from ..extensions import db
from ..models import ReporterWhitelist, VerifiedUser, ConvState, Sociedad
from .admin_bp import require_admin
from ..services.verification import normalize_phone

whitelist_bp = Blueprint("whitelist_bp", __name__, url_prefix="")


@whitelist_bp.get("/whitelist")
@require_admin
def whitelist():
    q = request.args.get("q", "").strip()
    estado = request.args.get("estado", "").strip()
    sociedad = request.args.get("sociedad", "").strip().upper()
    page = int(request.args.get("page", 1))
    per_page = 50
    query = ReporterWhitelist.query
    if q:
        qq = f"%{q}%"
        query = query.filter(
            or_(
                ReporterWhitelist.phone_e164.like(qq),
                ReporterWhitelist.sucursal.like(qq),
                ReporterWhitelist.ciudad.like(qq),
                ReporterWhitelist.nombre.like(qq),
            )
        )
    if estado == "activos":
        query = query.filter(ReporterWhitelist.enabled.is_(True))
    elif estado == "inactivos":
        query = query.filter(ReporterWhitelist.enabled.is_(False))
    if sociedad in [s.value for s in Sociedad]:
        query = query.filter(ReporterWhitelist.sociedad == Sociedad(sociedad))
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
        sociedad=sociedad,
        page=page,
        per_page=per_page,
        total=total,
    )


@whitelist_bp.post("/whitelist/create")
@require_admin
def whitelist_create():
    phone = normalize_phone(request.form.get("phone_e164", ""))
    if not phone:
        flash("Número inválido.", "danger")
        return redirect(url_for("whitelist_bp.whitelist"))
    wl = ReporterWhitelist.query.filter_by(phone_e164=phone).first()
    sucursal = (request.form.get("sucursal") or "").strip()
    ciudad = (request.form.get("ciudad") or "").strip()
    sociedad_str = (request.form.get("sociedad") or "").strip().upper()
    sociedad_val = Sociedad(sociedad_str) if sociedad_str in [s.value for s in Sociedad] else None
    nombre = (request.form.get("nombre") or "").strip()
    enabled = request.form.get("enabled") == "on"
    if wl:
        wl.sucursal = sucursal or wl.sucursal
        wl.ciudad = ciudad or wl.ciudad
        wl.sociedad = sociedad_val or wl.sociedad
        wl.nombre = nombre or wl.nombre
        wl.enabled = enabled
        msg = f"Actualizado: {phone}"
    else:
        db.session.add(
            ReporterWhitelist(
                phone_e164=phone,
                sucursal=sucursal,
                ciudad=ciudad,
                sociedad=sociedad_val,
                nombre=nombre,
                enabled=enabled,
            )
        )
        msg = f"Creado: {phone}"
    db.session.commit()
    flash(msg, "success")
    return redirect(url_for("whitelist_bp.whitelist"))


@whitelist_bp.post("/whitelist/<int:rid>/update")
@require_admin
def whitelist_update(rid):
    wl = ReporterWhitelist.query.get_or_404(rid)
    phone = normalize_phone(request.form.get("phone_e164", wl.phone_e164))
    if not phone:
        flash("Número inválido.", "danger")
        return redirect(url_for("whitelist_bp.whitelist"))
    exists = ReporterWhitelist.query.filter(
        ReporterWhitelist.phone_e164 == phone, ReporterWhitelist.id != rid
    ).first()
    if exists:
        flash("Ese número ya existe.", "danger")
        return redirect(url_for("whitelist_bp.whitelist"))
    wl.phone_e164 = phone
    wl.sucursal = (request.form.get("sucursal") or "").strip()
    wl.ciudad = (request.form.get("ciudad") or "").strip()
    sociedad_str = (request.form.get("sociedad") or "").strip().upper()
    wl.sociedad = Sociedad(sociedad_str) if sociedad_str in [s.value for s in Sociedad] else wl.sociedad
    wl.nombre = (request.form.get("nombre") or "").strip()
    wl.enabled = request.form.get("enabled") == "on"
    db.session.commit()
    flash("Registro actualizado.", "success")
    return redirect(url_for("whitelist_bp.whitelist"))


@whitelist_bp.post("/whitelist/<int:rid>/toggle")
@require_admin
def whitelist_toggle(rid):
    wl = ReporterWhitelist.query.get_or_404(rid)
    wl.enabled = not wl.enabled
    db.session.commit()
    flash(
        ("Activado" if wl.enabled else "Desactivado") + f": {wl.phone_e164}", "success"
    )
    return redirect(url_for("whitelist_bp.whitelist"))


@whitelist_bp.post("/whitelist/<int:rid>/delete")
@require_admin
def whitelist_delete(rid):
    wl = ReporterWhitelist.query.get_or_404(rid)
    db.session.delete(wl)
    db.session.commit()
    flash(f"Eliminado: {wl.phone_e164}", "success")
    return redirect(url_for("whitelist_bp.whitelist"))


@whitelist_bp.post("/whitelist/import")
@require_admin
def whitelist_import():
    f = request.files.get("file")
    if not f:
        flash("Adjunta un CSV.", "danger")
        return redirect(url_for("whitelist_bp.whitelist"))
    try:
        content = f.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        new, upd = 0, 0
        for row in reader:
            phone = normalize_phone(
                (row.get("phone") or row.get("phone_e164") or "").strip()
            )
            if not phone:
                continue
            sucursal = (row.get("sucursal") or "").strip()
            ciudad = (row.get("ciudad") or "").strip()
            sociedad_str = (row.get("sociedad") or "").strip().upper()
            sociedad_val = Sociedad(sociedad_str) if sociedad_str in [s.value for s in Sociedad] else None
            nombre = (row.get("nombre") or "").strip()
            enabled = str(
                row.get("enabled") or row.get("habilitado") or "1"
            ).strip().lower() in ["1", "true", "t", "yes", "si", "sí"]
            wl = ReporterWhitelist.query.filter_by(phone_e164=phone).first()
            if wl:
                wl.sucursal = sucursal or wl.sucursal
                wl.ciudad = ciudad or wl.ciudad
                wl.sociedad = sociedad_val or wl.sociedad
                wl.nombre = nombre or wl.nombre
                wl.enabled = enabled
                upd += 1
            else:
                db.session.add(
                    ReporterWhitelist(
                        phone_e164=phone,
                        sucursal=sucursal,
                        ciudad=ciudad,
                        sociedad=sociedad_val,
                        nombre=nombre,
                        enabled=enabled,
                    )
                )
                new += 1
        db.session.commit()
        flash(f"Importación OK. Nuevos: {new}, Actualizados: {upd}.", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Error importando: {e}", "danger")
    return redirect(url_for("whitelist_bp.whitelist"))


@whitelist_bp.get("/whitelist/export")
@require_admin
def whitelist_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["phone_e164", "sucursal", "ciudad", "sociedad", "enabled", "nombre"])
    for wl in ReporterWhitelist.query.order_by(
        ReporterWhitelist.phone_e164.asc()
    ).all():
        writer.writerow(
            [
                wl.phone_e164,
                wl.sucursal or "",
                wl.ciudad or "",
                (wl.sociedad.value if wl.sociedad else ""),
                1 if wl.enabled else 0,
                wl.nombre or "",
            ]
        )
    output.seek(0)
    from flask import current_app

    return current_app.response_class(
        output.read(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=whitelist.csv"},
    )


@whitelist_bp.post("/whitelist/<int:rid>/revoke")
@require_admin
def whitelist_revoke(rid):
    wl = ReporterWhitelist.query.get_or_404(rid)
    phone = wl.phone_e164
    count = VerifiedUser.query.filter_by(phone_e164=phone).delete(
        synchronize_session=False
    )
    db.session.commit()
    flash(f"Sesiones revocadas para {phone}: {count}", "success")
    return redirect(url_for("whitelist_bp.whitelist"))


@whitelist_bp.post("/whitelist/revoke_all")
@require_admin
def whitelist_revoke_all():
    count = VerifiedUser.query.delete(synchronize_session=False)
    db.session.commit()
    flash(f"Todas las sesiones revocadas: {count}", "success")
    return redirect(url_for("whitelist_bp.whitelist"))
