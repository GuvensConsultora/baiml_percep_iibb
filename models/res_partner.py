import logging
import re

from odoo import api, fields, models


_logger = logging.getLogger(__name__)


FP_PREFIX = "BAIML Percep"           # prefijo FPs generadas por este módulo
GROUP_PREFIX = "BAIML Combo"          # prefijo tax groups combinados


STATE_TO_JUR = {
    "Entre Ríos": "ER",
    "Entre Rios": "ER",
    "Santa Fe": "SF",
    "Tucumán": "TUC",
    "Tucuman": "TUC",
}


def _only_digits(s):
    return re.sub(r"\D", "", s or "")


def _alic_label(alicuota):
    return f"{alicuota:.2f}".replace(".", ",")


class ResPartner(models.Model):
    _inherit = "res.partner"

    def baiml_sync_percep_desde_padron(self, import_batch=None):
        """Sincroniza la FP de percepción IIBB del partner contra el padrón vigente.

        La jurisdicción se deduce del ``state_id`` del partner (ER/SF/TUC). Si
        existe alícuota > 0 para ese CUIT y jurisdicción en el padrón vigente,
        se asegura el combo nativo (tax simple + tax group + FP) y se asigna
        ``property_account_position_id`` al partner.

        Si la FP actual del partner no es una generada por este módulo, no se
        pisa (se registra en chatter).

        Devuelve un dict con contadores para auditoría.
        """
        Padron = self.env["baiml.padron.iibb"]
        hoy = fields.Date.context_today(self)
        stats = {"asignados": 0, "modificados": 0, "sin_cambio": 0,
                 "fuera_scope": 0, "sin_padron": 0}

        for partner in self:
            cuit = _only_digits(partner.vat)
            if len(cuit) != 11 or not partner.state_id:
                stats["fuera_scope"] += 1
                continue

            jur = STATE_TO_JUR.get(partner.state_id.name)
            if not jur:
                stats["fuera_scope"] += 1
                continue

            padron = Padron.search([
                ("cuit", "=", cuit),
                ("jurisdiccion", "=", jur),
                ("vigencia_desde", "<=", hoy),
                "|",
                ("vigencia_hasta", ">=", hoy),
                ("vigencia_hasta", "=", False),
            ], order="vigencia_desde desc", limit=1)

            fp_actual = partner.property_account_position_id
            fp_actual_es_baiml = fp_actual and fp_actual.name.startswith(FP_PREFIX)

            if not padron or padron.alicuota_percep <= 0:
                # Sin percepción vigente: si tenía FP de este módulo, la quitamos.
                if fp_actual_es_baiml:
                    partner.message_post(body=(
                        f"Percepción IIBB {jur}: sin alícuota vigente en padrón. "
                        f"Posición fiscal <b>{fp_actual.name}</b> removida."
                    ))
                    partner.property_account_position_id = False
                    stats["modificados"] += 1
                else:
                    stats["sin_padron"] += 1
                continue

            fp = self._baiml_ensure_fp_percep(jur, padron.alicuota_percep, partner.company_id)
            if not fp:
                continue

            if fp_actual and not fp_actual_es_baiml:
                # Respetamos FP manual (Exenta, Exterior, Monotributo, etc.)
                partner.message_post(body=(
                    f"Percepción IIBB {jur} {_alic_label(padron.alicuota_percep)}% "
                    f"detectada en padrón, pero el partner tiene FP manual "
                    f"<b>{fp_actual.name}</b>. No se modifica automáticamente."
                ))
                stats["fuera_scope"] += 1
                continue

            if fp_actual and fp_actual.id == fp.id:
                stats["sin_cambio"] += 1
                continue

            if fp_actual_es_baiml:
                partner.message_post(body=(
                    f"Percepción IIBB {jur} actualizada: "
                    f"<b>{fp_actual.name}</b> → <b>{fp.name}</b>. "
                    f"Vigencia padrón {padron.vigencia_desde}."
                ))
                stats["modificados"] += 1
            else:
                partner.message_post(body=(
                    f"Percepción IIBB {jur} asignada: <b>{fp.name}</b> "
                    f"({_alic_label(padron.alicuota_percep)}%). "
                    f"Vigencia padrón {padron.vigencia_desde}."
                ))
                stats["asignados"] += 1

            partner.property_account_position_id = fp.id

        return stats

    @api.model
    def _baiml_ensure_fp_percep(self, jurisdiccion, alicuota, company):
        """Asegura tax simple + tax group + FP nativos para esta combinación.

        Devuelve el ``account.fiscal.position`` listo para asignar al partner.
        """
        if not company:
            company = self.env.company

        jur_map = {
            "ER": ("Perc IIBB Entre Ríos", "P. IIBB ER"),
            "SF": ("Perc IIBB Santa Fe",   "P. IIBB SF"),
            "TUC": ("Perc IIBB Tucumán",   "P. IIBB T"),
        }
        group_name_ar, tax_prefix = jur_map[jurisdiccion]
        alic_lbl = _alic_label(alicuota)

        Tax = self.env["account.tax"]
        FP = self.env["account.fiscal.position"]
        TaxGroup = self.env["account.tax.group"]

        # IVA 21% de venta de la compañía
        iva21 = Tax.search([
            ("type_tax_use", "=", "sale"),
            ("amount", "=", 21.0),
            ("amount_type", "=", "percent"),
            ("company_id", "=", company.id),
            ("active", "=", True),
        ], limit=1)
        if not iva21:
            _logger.warning("baiml_percep_iibb: no se encontró IVA 21%% de venta en %s", company.name)
            return False

        # Tax simple de percepción
        tax_group_ar = TaxGroup.search(
            [("name", "=", group_name_ar), ("country_id.code", "=", "AR")], limit=1,
        )
        percep_name = f"{tax_prefix} {alic_lbl}"
        percep = Tax.search([
            ("name", "=", percep_name),
            ("company_id", "=", company.id),
        ], limit=1)
        if not percep:
            percep = Tax.create({
                "name": percep_name,
                "amount": alicuota,
                "amount_type": "percent",
                "type_tax_use": "sale",
                "tax_group_id": tax_group_ar.id if tax_group_ar else False,
                "l10n_ar_tax_type": "iibb_untaxed",
                "company_id": company.id,
                "active": True,
                "description": f"{percep_name}%",
            })

        # Tax group combinado IVA21 + Percep
        combo_name = f"{GROUP_PREFIX} IVA 21 + {tax_prefix} {alic_lbl}"
        combo = Tax.search([
            ("name", "=", combo_name),
            ("company_id", "=", company.id),
        ], limit=1)
        if not combo:
            combo = Tax.create({
                "name": combo_name,
                "amount_type": "group",
                "type_tax_use": "sale",
                "company_id": company.id,
                "children_tax_ids": [(6, 0, [iva21.id, percep.id])],
                "description": f"IVA 21% + {percep_name}%",
                "active": True,
            })

        # FP
        fp_name = f"{FP_PREFIX} {jurisdiccion} {alic_lbl}"
        fp = FP.search([
            ("name", "=", fp_name),
            ("company_id", "=", company.id),
        ], limit=1)
        if not fp:
            fp = FP.create({
                "name": fp_name,
                "company_id": company.id,
                "country_id": self.env.ref("base.ar").id,
                "auto_apply": False,
                "sequence": 20,
                "tax_ids": [(6, 0, [combo.id])],
            })
        else:
            if combo.id not in fp.tax_ids.ids:
                fp.write({"tax_ids": [(4, combo.id)]})

        # Linkear combo ↔ FP + marcar que reemplaza IVA21
        updates = {}
        if fp.id not in combo.fiscal_position_ids.ids:
            updates["fiscal_position_ids"] = [(4, fp.id)]
        if iva21.id not in combo.original_tax_ids.ids:
            updates["original_tax_ids"] = [(4, iva21.id)]
        if updates:
            combo.write(updates)

        return fp

    @api.model
    def _cron_baiml_sync_percep(self):
        """Cron diario: sincroniza todos los partners activos con CUIT."""
        partners = self.search([
            ("vat", "!=", False),
            ("customer_rank", ">", 0),
            ("parent_id", "=", False),
        ])
        partners.baiml_sync_percep_desde_padron()
