import base64
import csv
import gzip
import io
import re
import zipfile
from datetime import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError


def _only_digits(s):
    return re.sub(r"\D", "", s or "")


def _decompress_if_needed(data):
    """Si el archivo es .gz o .zip, devuelve los bytes del CSV interno.
    Si no, devuelve data tal cual. Permite al usuario subir comprimido
    y esquivar el límite de ~67 MB del upload de Odoo.sh."""
    if not data:
        return data
    # gzip
    if data[:2] == b"\x1f\x8b":
        return gzip.decompress(data)
    # zip
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                raise UserError(_("El ZIP no contiene archivos."))
            if len(names) > 1:
                raise UserError(_(
                    "El ZIP contiene varios archivos (%s). Subí uno solo."
                ) % ", ".join(names))
            return zf.read(names[0])
    return data


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
    archivo = fields.Binary(string="Archivo del padrón")
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
        return self._run_import(data)

    @api.model
    def action_import_from_attachment(self, attachment_id, jurisdiccion,
                                      reemplazar=True,
                                      sincronizar_partners=True,
                                      archivo_nombre=None):
        """Punto de entrada para integraciones: el cliente sube el archivo
        vía HTTP multipart como ir.attachment y luego pasa solo el id por
        XML-RPC, evitando payloads grandes en base64."""
        att = self.env["ir.attachment"].browse(attachment_id)
        if not att.exists():
            raise UserError(_("Attachment %s no existe.") % attachment_id)
        wizard = self.create({
            "jurisdiccion": jurisdiccion,
            "archivo_nombre": archivo_nombre or att.name,
            "reemplazar": reemplazar,
            "sincronizar_partners": sincronizar_partners,
        })
        return wizard._run_import(att.raw)

    def _run_import(self, data):
        self.ensure_one()
        data = _decompress_if_needed(data)
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

        # Create en batches con tracking desactivado y commits intermedios
        # para evitar OOM en padrones grandes (SF: ~588k registros).
        BATCH = 2000
        total = len(rows)
        Padron_fast = Padron.with_context(
            tracking_disable=True,
            mail_notrack=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
        )
        for i in range(0, total, BATCH):
            Padron_fast.create(rows[i:i + BATCH])
            self.env.cr.commit()
            self.env.invalidate_all()

        batch.write({
            "registros_importados": total,
            "registros_nuevos": nuevos,
            "registros_actualizados": actualizados,
            "registros_sin_cambio": sin_cambio,
        })
        batch.message_post(body=(
            f"Importados {total} registros del padrón {self.jurisdiccion}. "
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
