from odoo import api, fields, models

from .padron_iibb import JURISDICCIONES


class BaimlPadronImport(models.Model):
    _name = "baiml.padron.import"
    _description = "Lote de importación de padrón IIBB"
    _order = "fecha_import desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(compute="_compute_name", store=True)
    jurisdiccion = fields.Selection(JURISDICCIONES, required=True, tracking=True)
    archivo_nombre = fields.Char(string="Archivo", tracking=True)
    fecha_import = fields.Datetime(default=fields.Datetime.now, tracking=True)
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user, tracking=True)

    registros_importados = fields.Integer(tracking=True)
    registros_nuevos = fields.Integer(tracking=True)
    registros_actualizados = fields.Integer(tracking=True)
    registros_sin_cambio = fields.Integer(tracking=True)

    partners_asignados = fields.Integer(tracking=True)
    partners_modificados = fields.Integer(tracking=True)
    partners_no_encontrados = fields.Integer(tracking=True)

    padron_ids = fields.One2many("baiml.padron.iibb", "import_id")

    @api.depends("jurisdiccion", "fecha_import", "archivo_nombre")
    def _compute_name(self):
        for r in self:
            jur = dict(JURISDICCIONES).get(r.jurisdiccion, r.jurisdiccion or "—")
            fecha = fields.Datetime.to_string(r.fecha_import)[:10] if r.fecha_import else ""
            r.name = f"{jur} · {fecha}" if fecha else jur
