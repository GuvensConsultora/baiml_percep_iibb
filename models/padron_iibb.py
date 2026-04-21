from odoo import api, fields, models


JURISDICCIONES = [
    ("ER", "Entre Ríos (ATER)"),
    ("SF", "Santa Fe (API)"),
    ("TUC", "Tucumán (DGR)"),
]


class BaimlPadronIibb(models.Model):
    _name = "baiml.padron.iibb"
    _description = "Registro de padrón IIBB por CUIT y jurisdicción"
    _order = "vigencia_desde desc, jurisdiccion, cuit"
    _rec_name = "cuit"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    jurisdiccion = fields.Selection(JURISDICCIONES, required=True, index=True, tracking=True)
    cuit = fields.Char(required=True, index=True, size=11, tracking=True)
    tipo_contrib = fields.Selection(
        [("D", "Directo"), ("C", "Convenio Multilateral"), ("P", "Profesiones Liberales")],
        tracking=True,
    )
    alicuota_percep = fields.Float(digits=(5, 4), string="Alíc. Percep. (%)", tracking=True)
    alicuota_retenc = fields.Float(digits=(5, 4), string="Alíc. Reten. (%)", tracking=True)
    razon_social = fields.Char(tracking=True)
    fecha_publicacion = fields.Date()
    vigencia_desde = fields.Date(required=True, index=True, tracking=True)
    vigencia_hasta = fields.Date(tracking=True)
    import_id = fields.Many2one(
        "baiml.padron.import", ondelete="set null", index=True,
        string="Lote de importación",
    )
    partner_id = fields.Many2one(
        "res.partner",
        compute="_compute_partner_id",
        store=True,
        index=True,
    )

    _sql_constraints = [
        ("uniq_padron_jur_cuit_vig",
         "unique(jurisdiccion, cuit, vigencia_desde)",
         "Ya existe un registro de padrón para ese CUIT, jurisdicción y vigencia."),
    ]

    @api.depends("cuit")
    def _compute_partner_id(self):
        Partner = self.env["res.partner"]
        for rec in self:
            if not rec.cuit:
                rec.partner_id = False
                continue
            p = Partner.search([("vat", "=", rec.cuit)], limit=1)
            rec.partner_id = p.id if p else False
