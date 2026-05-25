# railway_constants.py
# ─────────────────────────────────────────────────────────────────────────────
#  All constants for the Crane Runway module.
#  Change values here — they propagate everywhere without touching business logic.
# ─────────────────────────────────────────────────────────────────────────────


# ═════════════════════════════════════════════════════════════════════════════
#  MATERIAL — Steel S235
# ═════════════════════════════════════════════════════════════════════════════
YOUNG_MODULUS_MPA    = 210_000   # MPa  — Young's modulus
STEEL_FY_MPA         = 235.0     # MPa  — yield strength S235
STEEL_DENSITY_KG_MM3 = 7.85e-6  # kg/mm³  (= 7850 kg/m³)
SAFETY_FACTOR        = 1.5       # safety factor for admissible stress
SIGMA_ADM_MPA        = STEEL_FY_MPA / SAFETY_FACTOR   # ≈ 156.7 MPa


# ═════════════════════════════════════════════════════════════════════════════
#  STRUCTURAL CRITERIA — Runway beams
# ═════════════════════════════════════════════════════════════════════════════
DEFLECTION_RATIO       = 600   # admissible deflection = L / 600  (EN 1993-6)
DEFLECTION_SCAN_POINTS = 300   # numerical scan points for max deflection search
N_APPUIS_MIN           = 4     # minimum support count on the full runway (both beams)

# Cross beam (poutre de suspension du portique Olsen) — critère plus strict
DEFLECTION_CROSS_RATIO = 600  # admissible deflection = L / 1000


# ═════════════════════════════════════════════════════════════════════════════
#  STRUCTURAL CRITERIA — Olsen columns
# ═════════════════════════════════════════════════════════════════════════════
DEFLECTION_COL_RATIO = 400     # admissible deflection = h / 400
FLAMB_FACTOR         = 2.0     # buckling length factor: Lf = FLAMB_FACTOR × h
                               # (fixed-free column, conservative)
FLAMB_IMPERFECTION   = 0.34    # α imperfection factor — EN 1993-1-1 curve b
                               # (HEA/HEB/IPE, tf ≤ 40 mm)
HORIZ_SCAN_POINTS    = 300     # scan points for max column horizontal reaction


# ═════════════════════════════════════════════════════════════════════════════
#  COSTING FACTORS — Runway beams
# ═════════════════════════════════════════════════════════════════════════════
FACT_ADD               = 0.10  # misc. steel mass = 10 % of beam mass
FACT_DECOUPE           = 1.05  # cutting surcharge = +10 % on steel cost
M_DIVERS_RESERVE       = 0.10  # +10 % contingency on misc. mass
M_DIVERS_MIN_KG        = 30.0  # minimum misc. mass [kg]
GOUSSET_AREA_RATIO     = 0.5   # 1 gusset ≈ 0.5 × end plate area

# Thickness caps for laser-cut plates [mm] — practical limits
MAX_TF_ABOUT   = 15.0          # end plate
MAX_TF_ECLISSE = 12.0          # splice plate
MAX_TF_GOUSSET = 12.0          # gusset
MAX_TF_CALAGE  = None          # packing plate — no cap

PRICE_BOULONNERIE_UNIT = 30    # €/support point — runway bolt set


# ═════════════════════════════════════════════════════════════════════════════
#  COSTING FACTORS — Olsen columns
# ═════════════════════════════════════════════════════════════════════════════
PRICE_BOULONNERIE_COL  = 80    # €/column — baseplate bolts + fixings
M_DIVERS_COL_MARGIN    = 0.10  # +10 % contingency on column misc. mass

# Baseplate geometry
EMBASE_OVERHANG  = 100.0       # mm overhang each side  (side = b + 2×100)
EMBASE_TF_THIN   = 20.0        # baseplate thickness [mm] if tf < TF_THIN_LIMIT
EMBASE_TF_THICK  = 25.0        # baseplate thickness [mm] if tf > TF_THICK_LIMIT
TF_THIN_LIMIT    = 15.0        # flange thickness threshold — "thin"
TF_THICK_LIMIT   = 15.0        # flange thickness threshold — "thick"
TF_PLAQUE_SUP_CAP = 15.0       # max thickness of top CR-connection plate [mm]

# Future: MO surcharge for Olsen-fabricated columns (welding, assembly)
FACT_AFAIRE = 1.75             # not yet used — placeholder for MO cost calc


# ═════════════════════════════════════════════════════════════════════════════
#  DEFAULT PRICES  (€/unit) — UI pre-fill, always editable by user
# ═════════════════════════════════════════════════════════════════════════════
DEFAULT_STEEL_PRICE    = 1.15  # €/kg  — structural steel
DEFAULT_RAIL_PRICE     = 1.35  # €/kg  — rail bar
DEFAULT_LASERCUT_PRICE = 2.50  # €/kg  — laser-cut parts
DEFAULT_PAINT_PRICE    = 25.0  # €/L   — paint


# ═════════════════════════════════════════════════════════════════════════════
#  PAINT — developed surface area coefficients
#  surface [m²/m] = (a + b×k) × h   where h = section height [m]
#  Based on approximate cross-section perimeter.
# ═════════════════════════════════════════════════════════════════════════════
PAINT_LITERS_PER_M2 = 1/3      # L/m²  ≈ 0.333  (1 primer coat ~3 m²/L)
PAINT_COEFF_HEA = (2.0, 4.0, 0.95)   # → 2h + 4×0.95h ≈ 5.8h m²/m
PAINT_COEFF_HEB = (2.0, 4.0, 1.00)   # → 2h + 4h      = 6.0h m²/m
PAINT_COEFF_IPE = (1.0, 4.0, 0.55)   # → h  + 4×0.55h ≈ 3.2h m²/m


# ═════════════════════════════════════════════════════════════════════════════
#  UI — selectbox presets  (free-entry always allowed)
# ═════════════════════════════════════════════════════════════════════════════
SPACING_PRESETS  = [4000, 5000, 6000, 7000]   # mm — support spacing
CARRIAGE_PRESETS = [1400, 1700, 1900, 2200, 2500, 2900, 3300]        # mm


# ═════════════════════════════════════════════════════════════════════════════
#  UI — SVG diagram colors  (one color per bridge)
# ═════════════════════════════════════════════════════════════════════════════
SVG_COLORS_PONT = ["#e05c5c", "#FDAE1B"]   # bridge 1 = red, bridge 2 = amber


# ═════════════════════════════════════════════════════════════════════════════
#  BEAM SECTION CATALOGUE  —  (h_mm, Iy_cm4, mass_kg/m, Wel_cm3, b_mm, tf_mm)
#
#  h    = section height [mm]
#  Iy   = second moment of area, strong axis [cm⁴]
#  mass = linear mass [kg/m]
#  Wel  = elastic section modulus, strong axis [cm³]
#  b    = flange width [mm]
#  tf   = flange thickness [mm]
#
#  Sources: EN 10025 / EN 10365
# ═════════════════════════════════════════════════════════════════════════════
DATA_HEA = [
    ( 100,   349.2,  17.00,   72.76, 100,  8.0),
    ( 120,   606.2,  20.27,  106.30, 120,  8.0),
    ( 140,  1033.0,  25.13,  155.40, 140,  8.5),
    ( 160,  1673.0,  31.02,  220.10, 160,  9.0),
    ( 180,  2510.0,  36.20,  293.60, 180,  9.5),
    ( 200,  3692.0,  43.06,  388.60, 200, 10.0),
    ( 220,  5410.0,  51.47,  515.20, 220, 11.0),
    ( 240,  7763.0,  61.47,  675.10, 240, 12.0),
    ( 260, 10450.0,  69.46,  836.40, 260, 12.5),
    ( 280, 13670.0,  77.81, 1013.00, 280, 13.0),
    ( 300, 18260.0,  90.02, 1259.00, 300, 14.0),
    ( 320, 22930.0,  99.49, 1479.00, 300, 15.5),
    ( 340, 27690.0, 106.78, 1678.00, 300, 16.5),
    ( 360, 33090.0, 114.21, 1891.00, 300, 17.5),
    ( 400, 45070.0, 127.18, 2311.00, 300, 19.0),
    ( 450, 63720.0, 142.42, 2896.00, 300, 21.0),
    ( 500, 86970.0, 158.03, 3550.00, 300, 23.0),
    ( 600,141200.0, 181.17, 4787.00, 300, 25.0),
    ( 650,175200.0, 190.10, 5474.00, 300, 26.0),
    ( 700,215300.0, 204.50, 6241.00, 300, 27.0),
    ( 800,303400.0, 224.40, 7682.00, 300, 28.0),
    ( 900,422100.0, 252.20, 9484.00, 300, 30.0),
    (1000,553800.0, 272.30,11189.00, 300, 31.0),
]

DATA_HEB = [
    ( 100,   350.0,  18.00,   89.91, 100, 10.0),
    ( 120,   449.5,  20.83,  144.10, 120, 11.0),
    ( 140,  1509.0,  34.36,  215.60, 140, 12.0),
    ( 160,  2492.0,  43.40,  311.50, 160, 13.0),
    ( 180,  3831.0,  52.20,  425.70, 180, 14.0),
    ( 200,  5696.0,  62.46,  569.60, 200, 15.0),
    ( 220,  8091.0,  72.83,  735.50, 220, 16.0),
    ( 240, 11260.0,  84.79,  938.30, 240, 17.0),
    ( 260, 14920.0,  94.76, 1148.00, 260, 17.5),
    ( 280, 19270.0, 105.90, 1376.00, 280, 18.0),
    ( 300, 25170.0, 119.26, 1678.00, 300, 19.0),
    ( 320, 30820.0, 129.07, 1926.00, 300, 20.5),
    ( 340, 36600.0, 136.70, 2156.00, 300, 21.5),
    ( 360, 43190.0, 144.51, 2400.00, 300, 22.5),
    ( 400, 57680.0, 158.22, 2884.00, 300, 24.0),
    ( 450, 79890.0, 174.38, 3551.00, 300, 26.0),
    ( 500,107200.0, 190.91, 4287.00, 300, 28.0),
    ( 600,171000.0, 215.97, 5701.00, 300, 30.0),
    ( 650,210600.0, 225.10, 6480.00, 300, 31.0),
    ( 700,256900.0, 241.30, 7340.00, 300, 32.0),
    ( 800,359100.0, 262.10, 8977.00, 300, 33.0),
    ( 900,494100.0, 291.30,10980.00, 300, 35.0),
    (1000,644700.0, 314.40,12890.00, 300, 36.0),
]

DATA_IPE = [
    (  80,    80.1,   6.00,   20.03,  46,  5.2),
    ( 100,   171.0,   8.26,   34.20,  55,  5.7),
    ( 120,   317.8,  10.57,   52.96,  64,  6.3),
    ( 140,   541.2,  13.14,   77.32,  73,  6.9),
    ( 160,   869.3,  16.07,  108.70,  82,  7.4),
    ( 180,  1317.0,  19.16,  146.30,  91,  8.0),
    ( 200,  1943.0,  22.79,  194.30, 100,  8.5),
    ( 220,  2772.0,  26.70,  252.00, 110,  9.2),
    ( 240,  3892.0,  31.29,  324.30, 120,  9.8),
    ( 270,  5790.0,  36.76,  428.90, 135, 10.2),
    ( 300,  8356.0,  43.05,  557.10, 150, 10.7),
    ( 330, 11770.0,  50.08,  713.10, 160, 11.5),
    ( 360, 16270.0,  58.18,  903.60, 170, 12.7),
    ( 400, 23130.0,  67.57, 1156.00, 180, 13.5),
    ( 450, 33740.0,  79.06, 1500.00, 190, 14.6),
    ( 500, 48200.0,  92.42, 1928.00, 200, 16.0),
    ( 550, 67120.0, 105.50, 2440.00, 210, 17.2),
    ( 600, 92080.0, 124.79, 3069.00, 220, 19.0),
    ( 750,205800.0, 173.00, 5489.00, 265, 25.0),
]

TABLES = {"HEA": DATA_HEA, "HEB": DATA_HEB, "IPE": DATA_IPE}


# ═════════════════════════════════════════════════════════════════════════════
#  AVAILABLE RAILS  — solid rectangular/square bars S235
#  Linear mass = cross-section [mm²] × 7.85e-3 [kg/(mm²·m)]
#  First entry = UI default.
# ═════════════════════════════════════════════════════════════════════════════
RAIL_MASS_KGM = {
    "50×30": 11.8,   # 1500 mm²  ← default
    "40×40": 12.6,   # 1600 mm²
    "50×50": 19.6,   # 2500 mm²
    "60×40": 18.8,   # 2400 mm²
    "60×60": 28.3,   # 3600 mm²
}


# ═════════════════════════════════════════════════════════════════════════════
#  COLUMN CATALOGUE — weak-axis second moment of area Iz [cm⁴]
#  Used for buckling check parallel to CR (weak axis = translation direction).
# ═════════════════════════════════════════════════════════════════════════════
IZ_HEA = {
    100:  38.8,  120:  64.9,  140: 103.0,  160: 161.0,  180: 237.0,  200: 336.0,
    220: 470.0,  240: 652.0,  260: 836.0,  280:1050.0,  300:1260.0,  320:1280.0,
    340:1290.0,  360:1300.0,  400:1318.0,  450:1341.0,  500:1356.0,
    600:1380.0,  650:1390.0,  700:1400.0,  800:1418.0,
}
IZ_HEB = {
    100: 167.0,  120: 318.0,  140: 550.0,  160: 889.0,  180:1363.0,  200:2003.0,
    220:2843.0,  240:3923.0,  260:5135.0,  280:6595.0,  300:8563.0,  320:9239.0,
    340:9690.0,  360:10140.0, 400:10820.0, 450:11720.0, 500:12620.0,
    600:13530.0, 650:13980.0, 700:14440.0, 800:15100.0,
}
IZ_IPE = {
     80:   8.49, 100:  15.9,  120:  27.7,  140:  44.9,  160:  68.3,  180: 100.9,
    200: 142.4,  220: 204.6,  240: 284.0,  270: 419.8,  300: 603.8,  330: 788.1,
    360:1043.0,  400:1318.0,  450:1676.0,  500:2142.0,  550:2672.0,  600:3387.0,
    750:6960.0,
}
IZ_TABLES = {"HEA": IZ_HEA, "HEB": IZ_HEB, "IPE": IZ_IPE}
