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
    sincronizar_partners = fields.Boolean(
        default=True,
        help="Si está tildado, luego del import sincroniza las FPs de percepción de los partners.",
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

        Import = self.env["baiml.padron.import"]
        Padron = self.env["baiml.padron.iibb"]

        batch = Import.create({
            "jurisdiccion": self.jurisdiccion,
            "archivo_nombre": self.archivo_nombre,
        })

        # Contar diffs antes de reemplazar
        nuevos = actualizados = sin_cambio = 0
        existentes_map = {}
        if not self.reemplazar:
            existentes = Padron.search_read(
                [("jurisdiccion", "=", self.jurisdiccion)],
                ["cuit", "vigencia_desde", "alicuota_percep"],
            )
            existentes_map = {
                (e["cuit"], fields.Date.to_string(e["vigencia_desde"])): e
                for e in existentes
            }
        else:
            Padron.search([("jurisdiccion", "=", self.jurisdiccion)]).unlink()

        for row in rows:
            row["import_id"] = batch.id
            key = (row["cuit"], fields.Date.to_string(row["vigencia_desde"]))
            previo = existentes_map.get(key)
            if previo and abs(previo["alicuota_percep"] - row["alicuota_percep"]) < 1e-6:
                sin_cambio += 1
            elif previo:
                actualizados += 1
            else:
                nuevos += 1

        Padron.create(rows)

        batch.write({
            "registros_importados": len(rows),
            "registros_nuevos": nuevos,
            "registros_actualizados": actualizados,
            "registros_sin_cambio": sin_cambio,
        })
        batch.message_post(body=(
            f"Importados {len(rows)} registros del padrón {self.jurisdiccion}. "
            f"Nuevos: {nuevos}, actualizados: {actualizados}, sin cambio: {sin_cambio}."
        ))

        if self.sincronizar_partners:
            self._sync_partners_de_batch(batch)

        return {
            "type": "ir.actions.act_window",
            "res_model": "baiml.padron.import",
            "res_id": batch.id,
            "view_mode": "form",
            "target": "current",
        }

    def _sync_partners_de_batch(self, batch):
        Partner = self.env["res.partner"]
        Padron = self.env["baiml.padron.iibb"]
        cuits = Padron.search([("import_id", "=", batch.id)]).mapped("cuit")
        partners = Partner.search([
            ("vat", "in", cuits),
            ("customer_rank", ">", 0),
            ("parent_id", "=", False),
        ])
        stats = partners.baiml_sync_percep_desde_padron(import_batch=batch)
        no_encontrados = len(cuits) - len(partners)
        batch.write({
            "partners_asignados": stats.get("asignados", 0),
            "partners_modificados": stats.get("modificados", 0),
            "partners_no_encontrados": no_encontrados,
        })
        batch.message_post(body=(
            f"Sincronización de partners: "
            f"asignados {stats.get('asignados', 0)}, "
            f"modificados {stats.get('modificados', 0)}, "
            f"sin cambio {stats.get('sin_cambio', 0)}, "
            f"fuera de alcance {stats.get('fuera_scope', 0)}, "
            f"CUITs del padrón no encontrados como partner: {no_encontrados}."
        ))

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
        """Parsea CSV API Santa Fe (PARP, RG API 14/2025), separador ';',
        ISO-8859-1, SIN cabecera. Layout posicionalmente igual a ATER."""
        text = data.decode("ISO-8859-1", errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter=";")
        rows = []
        for r in reader:
            if len(r) < 12:
                continue
            try:
                rows.append({
                    "jurisdiccion": "SF",
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
