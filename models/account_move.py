import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    def _baiml_aplicar_percep_partner(self):
        """Agrega a las líneas de producto los tax de percepción del partner.

        Solo aplica en facturas y notas de venta (out_invoice/out_refund) y
        no pisa tax que ya estén presentes. Es idempotente: volver a llamar
        no duplica.
        """
        for move in self:
            if move.move_type not in ("out_invoice", "out_refund"):
                continue
            if move.state != "draft":
                continue
            partner = move.partner_id
            if not partner:
                continue
            percep_taxes = partner.baiml_percep_ids.mapped("tax_id").filtered(
                lambda t: t.type_tax_use == "sale" and t.company_id == move.company_id
            )
            if not percep_taxes:
                continue
            for line in move.invoice_line_ids:
                if line.display_type and line.display_type != "product":
                    continue
                add = percep_taxes - line.tax_ids
                if add:
                    line.tax_ids = [(4, t.id) for t in add]

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        try:
            moves._baiml_aplicar_percep_partner()
        except Exception:
            _logger.exception(
                "baiml_percep_iibb: fallo al aplicar percep en create "
                "(no bloquea creación de factura)"
            )
        return moves

    @api.onchange("partner_id")
    def _onchange_partner_id_baiml_percep(self):
        """Al cambiar el partner en el form, refresca las percep en las líneas."""
        if self.state == "draft" and self.partner_id:
            try:
                self._baiml_aplicar_percep_partner()
            except Exception:
                _logger.exception(
                    "baiml_percep_iibb: fallo al aplicar percep en onchange"
                )
