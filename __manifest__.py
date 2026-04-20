{
    "name": "BAIML — Percepciones IIBB (ER/SF/Tuc)",
    "version": "19.0.1.0.0",
    "author": "Yagüven Consultora Global",
    "website": "https://yaguven.com",
    "category": "Accounting/Localizations/Argentina",
    "summary": (
        "Ingesta de padrones de IIBB (ATER, API Santa Fe, DGR Tucumán), "
        "asignación automática de alícuota por partner y aplicación "
        "automática de la percepción en facturas de venta."
    ),
    "depends": [
        "base",
        "account",
        "l10n_ar",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/padron_iibb_views.xml",
        "views/res_partner_views.xml",
        "views/import_padron_wizard_views.xml",
        "views/menu_views.xml",
        "data/ir_cron_sync.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
