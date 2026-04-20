import base64
import csv
import io
import re
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError


def _only_digits(s):
    return re.sub(r"\D", "", s or "")


def _ddmmaaaa_to_date(s):
    s = (s or "").strip()
    if len(s) != 8 or not s.isdigit():
        return False
    return datetime.strptime(s, "%d%m%Y").date()


class BaimlImportPadronWizard(models.TransientModel):
    _name = "baiml.import.padron.wizard"
    _description = "Wizard para importar padrones IIBB"

    jurisdiccion = fields.Selection(
        [("ER", "Entre Ríos (ATER)"),
         ("SF", "Santa Fe (API)"),
         ("TUC", "Tucumán (DGR)")],
        required=True,
    )
    archivo = fields.Binary(required=True, string="Archivo del padrón")
    archivo_nombre = fields.Char(string="Nombre del archivo")
    reemplazar = fields.Boolean(
        default=True,
        help="Si está tildado, borra los registros existentes de la jurisdicción antes de importar.",
    )

    def action_import(self):
        self.ensure_one()
        if not self.archivo:
            raise UserError(_("Subí un archivo antes de importar."))

        data = base64.b64decode(self.archivo)
        if self.jurisdiccion == "ER":
            rows = self._parse_ater(data)
        elif self.jurisdiccion == "SF":
            rows = self._parse_api_sf(data)
        else:
            raise UserError(_("Parser para %s todavía no implementado.") % self.jurisdiccion)

        Padron = self.env["baiml.padron.iibb"]
        if self.reemplazar:
            Padron.search([("jurisdiccion", "=", self.jurisdiccion)]).unlink()

        Padron.create(rows)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Padrón importado"),
                "message": _("Se cargaron %d registros para %s.") % (len(rows), self.jurisdiccion),
                "sticky": False,
            },
        }

    def _parse_ater(self, data):
        """Parsea CSV ATER (RG 208/24 + 280/24), separador ';', ISO-8859-1."""
        text = data.decode("ISO-8859-1", errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter=";")
        next(reader, None)  # cabecera
        rows = []
        for r in reader:
            if len(r) < 12:
                continue
            try:
                rows.append({
                    "jurisdiccion": "ER",
                    "fecha_publicacion": _ddmmaaaa_to_date(r[0]),
                    "vigencia_desde": _ddmmaaaa_to_date(r[1]),
                    "vigencia_hasta": _ddmmaaaa_to_date(r[2]),
                    "cuit": _only_digits(r[3]),
                    "tipo_contrib": r[4].strip()[:1] or False,
                    "alicuota_percep": float((r[7] or "0").replace(",", ".")),
                    "alicuota_retenc": float((r[8] or "0").replace(",", ".")),
                    "razon_social": (r[11] or "").strip()[:70],
                })
            except (ValueError, IndexError):
                continue
        return rows

    def _parse_api_sf(self, data):
        """Parser placeholder para API Santa Fe — a completar cuando tengamos
        el archivo real y su layout documentado."""
        raise UserError(_(
            "Parser de API Santa Fe pendiente. Subí un archivo de muestra "
            "y completamos el layout."
        ))
