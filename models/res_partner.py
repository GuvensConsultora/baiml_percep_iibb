import re
from datetime import date

from odoo import api, fields, models


def _only_digits(s):
    return re.sub(r"\D", "", s or "")


class ResPartner(models.Model):
    _inherit = "res.partner"

    baiml_percep_ids = fields.One2many(
        "baiml.partner.percep", "partner_id",
        string="Percepciones IIBB vigentes",
    )

    def baiml_sync_percep_desde_padron(self, jurisdicciones=None):
        """Sincroniza baiml_percep_ids a partir de baiml.padron.iibb vigente.

        Para cada partner con CUIT, busca el registro de padrón vigente en
        cada jurisdicción solicitada, obtiene o crea el account.tax con la
        alícuota correspondiente y actualiza (crea/modifica/borra) la línea
        de baiml.partner.percep. Si la alícuota del padrón es 0 la línea se
        elimina (no hay percepción que aplicar).
        """
        Padron = self.env["baiml.padron.iibb"]
        Tax = self.env["account.tax"]
        Line = self.env["baiml.partner.percep"]
        jurs = jurisdicciones or [j[0] for j in Padron._fields["jurisdiccion"].selection]
        hoy = fields.Date.context_today(self)

        for partner in self:
            cuit = _only_digits(partner.vat)
            if len(cuit) != 11:
                continue
            for jur in jurs:
                padron = Padron.search([
                    ("cuit", "=", cuit),
                    ("jurisdiccion", "=", jur),
                    ("vigencia_desde", "<=", hoy),
                    "|",
                    ("vigencia_hasta", ">=", hoy),
                    ("vigencia_hasta", "=", False),
                ], order="vigencia_desde desc", limit=1)

                linea = partner.baiml_percep_ids.filtered(
                    lambda l: l.jurisdiccion == jur
                )

                if not padron or padron.alicuota_percep <= 0:
                    if linea:
                        linea.unlink()
                    continue

                tax = self._baiml_get_or_create_percep_tax(jur, padron.alicuota_percep)
                if linea:
                    linea.write({
                        "tax_id": tax.id,
                        "vigencia_desde": padron.vigencia_desde,
                        "vigencia_hasta": padron.vigencia_hasta,
                        "origen": "padron",
                    })
                else:
                    Line.create({
                        "partner_id": partner.id,
                        "jurisdiccion": jur,
                        "tax_id": tax.id,
                        "vigencia_desde": padron.vigencia_desde,
                        "vigencia_hasta": padron.vigencia_hasta,
                        "origen": "padron",
                    })

    @api.model
    def _baiml_get_or_create_percep_tax(self, jurisdiccion, alicuota):
        """Busca un account.tax de venta con la alícuota exacta para la jur.
        Si no existe, lo crea sobre el tax_group nativo del l10n_ar AR."""
        jur_map = {
            "ER": ("Perc IIBB Entre Ríos", "P. IIBB ER"),
            "SF": ("Perc IIBB Santa Fe",   "P. IIBB SF"),
            "TUC": ("Perc IIBB Tucumán",   "P. IIBB T"),
        }
        group_name, tax_prefix = jur_map[jurisdiccion]
        company = self.env.company
        group = self.env["account.tax.group"].search(
            [("name", "=", group_name), ("country_id.code", "=", "AR")],
            limit=1,
        )
        tax = self.env["account.tax"].search([
            ("type_tax_use", "=", "sale"),
            ("amount", "=", alicuota),
            ("tax_group_id", "=", group.id if group else False),
            ("company_id", "=", company.id),
        ], limit=1)
        if tax:
            return tax
        vals = {
            "name": f"{tax_prefix} {alicuota:.2f}".replace(".", ","),
            "amount": alicuota,
            "amount_type": "percent",
            "type_tax_use": "sale",
            "tax_group_id": group.id if group else False,
            "l10n_ar_tax_type": "iibb_untaxed",
            "company_id": company.id,
            "active": True,
            "description": f"{tax_prefix} {alicuota:.2f}%",
        }
        return self.env["account.tax"].create(vals)

    @api.model
    def _cron_baiml_sync_percep(self):
        """Cron: sincroniza todos los partners con CUIT contra el padrón vigente."""
        partners = self.search([
            ("vat", "!=", False),
            ("customer_rank", ">", 0),
            ("parent_id", "=", False),
        ])
        partners.baiml_sync_percep_desde_padron()
