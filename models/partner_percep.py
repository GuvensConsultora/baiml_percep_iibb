from odoo import api, fields, models

from .padron_iibb import JURISDICCIONES


class BaimlPartnerPercep(models.Model):
    _name = "baiml.partner.percep"
    _description = "Alícuota de percepción IIBB vigente por partner y jurisdicción"
    _order = "partner_id, jurisdiccion"

    partner_id = fields.Many2one(
        "res.partner", required=True, ondelete="cascade", index=True,
    )
    jurisdiccion = fields.Selection(JURISDICCIONES, required=True)
    tax_id = fields.Many2one(
        "account.tax",
        required=True,
        domain=[("type_tax_use", "=", "sale")],
        string="Impuesto de percepción",
    )
    alicuota = fields.Float(related="tax_id.amount", readonly=True)
    vigencia_desde = fields.Date()
    vigencia_hasta = fields.Date()
    origen = fields.Selection(
        [("padron", "Padrón"), ("manual", "Manual")],
        default="manual",
    )

    _sql_constraints = [
        ("uniq_partner_jur",
         "unique(partner_id, jurisdiccion)",
         "Un partner solo puede tener una percepción vigente por jurisdicción."),
    ]
