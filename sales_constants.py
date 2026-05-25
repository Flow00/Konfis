# sales_constants.py
# ─────────────────────────────────────────────────────────────────────────────
#  Reference tables for the Sales Automation / Quote Calculator tab.
# ─────────────────────────────────────────────────────────────────────────────


# ═════════════════════════════════════════════════════════════════════════════
#  TEAMS
# ═════════════════════════════════════════════════════════════════════════════
TEAMS = ["LIG", "CRL", "LUX"]


# ═════════════════════════════════════════════════════════════════════════════
#  BUILDING TYPE  →  ratio on installation hours
# ═════════════════════════════════════════════════════════════════════════════
BUILDING_TYPES = {
    "Nouveau":           1.0,
    "Existant spacieux": 1.2,
    "Existant bordel":   1.4,
}


# ═════════════════════════════════════════════════════════════════════════════
#  FIXATION TYPE  →  ratio on installation hours
# ═════════════════════════════════════════════════════════════════════════════
FIXATION_TYPES = {
    "Posé":      1.00,
    "Murale":    1.25,
    "Plaque":    1.25,
    "Plafond":   1.50,
    "Fondation": 1.00,
}


# ═════════════════════════════════════════════════════════════════════════════
#  CLIENT TYPE  →  ratio on installation hours
# ═════════════════════════════════════════════════════════════════════════════
CLIENT_TYPES = {
    "Indépendant": 1.00,
    "PME":         1.25,
    "Multi":       1.50,
}


# ═════════════════════════════════════════════════════════════════════════════
#  NACELLE
# ═════════════════════════════════════════════════════════════════════════════
NACELLE_TYPES            = ["Olsen", "Louée", "Client"]
COUT_NACELLE_LOUEE_EUR_J = 250    # €/day — rented nacelle


# ═════════════════════════════════════════════════════════════════════════════
#  SCENARIO  →  ratio on services total
# ═════════════════════════════════════════════════════════════════════════════
SCENARIOS = {
    "Pessimiste": 0.75,
    "Normal":     1.00,
    "Optimiste":  1.25,
}


# ═════════════════════════════════════════════════════════════════════════════
#  SECURITY  →  ratio on material sale price
# ═════════════════════════════════════════════════════════════════════════════
SECURITE_RENFORCEE_OPTIONS = {"Non": 1.0, "Oui": 1.15}


# ═════════════════════════════════════════════════════════════════════════════
#  INSTALLATION TYPES  →  base install hours per unit
#  Price comes directly from Odoo (already net sale price).
# ═════════════════════════════════════════════════════════════════════════════
INSTALLATION_TYPES = {
    # type : hours per unit
    "DLVM":  16,
    "EDK":   16,
    "EDL":   16,
    "EHPK":  16,
    "ELK":   16,
    "ELS":   16,
    "ELV":   16,
    "ZLK":   20,
    "ZLV":   20,
    "GMC":   10,
    "LPK":   10,
    "LS":    10,
    "LSX":   16,
    "VS":    20,
    "LW":    10,
    "LWX":   16,
    "VW":    20,
    "EHB":   16,
    "EHB-I": 16,
    "EHB-X": 16,
    "ESB":   16,
    "ESK":   16,
    "ZHB":   20,
    "ZHB-3": 20,
    "ZHB-I": 20,
    "ZHB-X": 20,
    "ZSB":   20,
    "GM2":   10,
    "GM4":   10,
    "GM6":   16,
    "GM8":   16,
    "GM 800":   16,
    "GM 1000":   16,
    "GM 2000":   16,
    "GM 3000":   16,
    "GM 4000":   16,
    "GM 5000":   16,
    "GM 6000":   16,
}


# ═════════════════════════════════════════════════════════════════════════════
#  GAINE TYPES  →  install hours per metre
#  Price comes directly from Odoo (already net sale price).
# ═════════════════════════════════════════════════════════════════════════════
GAINE_TYPES = {
    "KBH 63":  0.32,
    "KBH 80":  0.32,
    "KBH 100": 0.32,
    "KBH 125": 0.40,
    "KBH 160": 0.40,
    "KBH 200": 0.40,
}


# ═════════════════════════════════════════════════════════════════════════════
#  PRESTATION — base hours per event
# ═════════════════════════════════════════════════════════════════════════════
H_RELEVE_PAR_CONFIG   = 4    # survey hours per config
H_TRAJET_RELEVE       = 1    # travel hours per survey
H_REMISE_DOSSIER      = 1    # handover hours per config
H_RECEPTION           = 5    # reception hours per config
H_TRAJET_RECEPTION    = 1    # travel hours per reception
H_TRAJET_MONTAGE_BASE = 1    # travel hours per install (config 3+)
H_TRAJET_MONTAGE_C1   = 6    # travel hours config 1 (round trip)
H_TRAJET_MONTAGE_C2   = 2    # travel hours config 2


# ═════════════════════════════════════════════════════════════════════════════
#  TARIF & DÉFRAIEMENT
# ═════════════════════════════════════════════════════════════════════════════
DEFAULT_TARIF_H    = 85.0   # €/h — technician hourly rate
DEFAULT_KM_AR      = 50     # km round trip Olsen ↔ client
DEFRAIMENT_EUR_KM  = 0.75   # €/km


# ═════════════════════════════════════════════════════════════════════════════
#  MISC
# ═════════════════════════════════════════════════════════════════════════════
MAX_CONFIGS = 6