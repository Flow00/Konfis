# tab_railway_sizing.py
import streamlit as st
import re, math, base64, json
from datetime import date

from railway_constants import (
    DEFAULT_STEEL_PRICE, DEFAULT_RAIL_PRICE,
    DEFAULT_LASERCUT_PRICE, DEFAULT_PAINT_PRICE, DEFAULT_MO_PRICE,
    MO_M_PER_HOUR_BEAM_WELD, MO_M_PER_HOUR_BEAM_PAINT,
    MO_M_PER_HOUR_ANGLE_WELD, MO_M_PER_HOUR_ANGLE_PAINT,
    MO_HOURS_PER_SUPPORT_SUSP,
    MO_HOURS_PER_COLUMN, MO_HOURS_BRACING_PER_COL,
    MO_HOURS_DOUBLE_COL, MO_HOURS_PER_CROSS_BEAM,
    PRICE_BOULONNERIE_UNIT,
    FACT_ADD, FACT_DECOUPE,
    DEFLECTION_RATIO, YOUNG_MODULUS_MPA,
    DEFLECTION_CROSS_RATIO,
    STEEL_FY_MPA, SAFETY_FACTOR, SIGMA_ADM_MPA,
    PAINT_LITERS_PER_M2,
    PAINT_COEFF_HEA, PAINT_COEFF_HEB, PAINT_COEFF_IPE,
    RAIL_MASS_KGM, TABLES,
    SVG_COLORS_PONT,
    DEFLECTION_SCAN_POINTS,
    STEEL_DENSITY_KG_MM3, GOUSSET_AREA_RATIO, N_APPUIS_MIN,
    MAX_TF_ABOUT, MAX_TF_ECLISSE, MAX_TF_GOUSSET, MAX_TF_CALAGE,
    M_DIVERS_MIN_KG, M_DIVERS_RESERVE,
    SPACING_PRESETS, CARRIAGE_PRESETS,
    IZ_TABLES, DEFLECTION_COL_RATIO, FLAMB_FACTOR, FLAMB_IMPERFECTION,
    PRICE_BOULONNERIE_COL, M_DIVERS_COL_MARGIN,
    EMBASE_TF_THIN, EMBASE_TF_THICK, TF_THIN_LIMIT, TF_THICK_LIMIT,
    EMBASE_OVERHANG, TF_PLAQUE_SUP_CAP, HORIZ_SCAN_POINTS,
    ANGLE_CATALOGUE, LTB_REINFORCE_THRESHOLD,
    MAX_CRANE_SPAN_MM_POSE, MAX_CRANE_SPAN_MM_SUSPENDU,
    MAX_SUPPORT_SPACING_MM, MAX_RAIL_HEIGHT_MM,
)

ss = st.session_state


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _paint_surface(beam_type, taille):
    """Périmètre développé (m²/m de poutre)."""
    h = taille / 1000.0
    if beam_type == "HEA":
        a, b, k = PAINT_COEFF_HEA
    elif beam_type == "HEB":
        a, b, k = PAINT_COEFF_HEB
    else:
        a, b, k = PAINT_COEFF_IPE
    return a * h + b * k * h


def _short_rail(rail_label):
    """Retourne juste la dimension du rail (ex : '50×50') sans la masse."""
    if not rail_label:
        return ""
    return rail_label.split("(")[0].strip()


def _sanitize_filename(s):
    """Nettoie une chaîne pour usage dans un nom de fichier."""
    s = (s or "").strip()
    s = re.sub(r"[^\w\-\.]+", "_", s, flags=re.UNICODE)
    return s.strip("_") or "NA"


# ─────────────────────────────────────────────────────────────────────────────
#  LOAD CASE ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def _I_req_from_forces(forces_N_mm, L, fmax):
    """
    forces_N_mm = [(F_N, a_mm), ...].
    Returns (I_min_cm4, delta_unit, M_max_Nmm).
      - delta_unit : flèche × I (mm × mm⁴ = mm⁵) — divisée plus tard par
                     I_chosen_mm4 pour obtenir la flèche réelle en mm.
      - M_max_Nmm : moment fléchissant max sur la poutre, en N·mm.

    Poutre simple sur 2 appuis, charges ponctuelles.
      - Réactions : R_A = Σ F·b/L, R_B = Σ F·a/L (avec a,b distances aux appuis).
      - Moment au point x : M(x) = R_A·x - Σ F·max(0, x-a_i)
    """
    # Filtre charges hors portée (sécurité ; appelants déjà filtrés)
    forces = [(F, a) for (F, a) in forces_N_mm if 0 < a < L]

    # Réaction R_A (somme des contributions)
    R_A = sum(F * (L - a) / L for F, a in forces)

    best_delta_unit = 0.0
    M_max = 0.0
    # Critique : on évalue moment et flèche aux mêmes points + sous chaque charge
    # (le moment max sous une charge ponctuelle est exactement sous la charge)
    sample_x = [L * xi / float(DEFLECTION_SCAN_POINTS) for xi in range(1, DEFLECTION_SCAN_POINTS)]
    sample_x += [a for _, a in forces]   # évaluer sous chaque charge
    for x in sample_x:
        # Deflection en x (formule classique Macaulay pour charges ponctuelles)
        delta = 0.0
        for F, a in forces:
            b = L - a
            if x <= a:
                delta += F * b * x / (6 * YOUNG_MODULUS_MPA * L) * (L*L - b*b - x*x)
            else:
                delta += F * a * (L-x) / (6 * YOUNG_MODULUS_MPA * L) * (L*L - a*a - (L-x)**2)
        best_delta_unit = max(best_delta_unit, delta)

        # Moment en x : R_A·x - Σ F·(x - a)+
        M = R_A * x
        for F, a in forces:
            if x > a:
                M -= F * (x - a)
        M_max = max(M_max, abs(M))

    if best_delta_unit <= 0:
        return 0.0, 0.0, M_max
    I_min_cm4 = (best_delta_unit / fmax) / 10000.0
    return I_min_cm4, best_delta_unit, M_max


def _galet_pos(center, support_spacing, L):
    """
    Positions (mm) des galets d'un chariot centré en 'center'.
    On retourne TOUJOURS les 2 galets à center ± support_spacing/2.
    Les galets hors portée seront filtrés en aval (par 0 < p < L)
    dans _I_req_from_forces.
    """
    s = float(support_spacing)
    if s <= 0:
        return [center]
    return [center - s/2.0, center + s/2.0]


# ─────────────────────────────────────────────────────────────────────────────
#  POUTRE CONTINUE 3 APPUIS — 2 spans égales
#
#  Modèle : appuis A (x=0), B (x=a), C (x=2a). Travée 1 = [0,a], span 2 = [a,2a].
#  Méthode :
#    1) On résout le moment M_B à l'appui central via l'équation des 3 moments.
#       Pour 2 spans égales et appuis simples extérieurs (M_A=M_C=0) :
#         4·M_B·a = -Σ_{i∈trav1} P_i·a_i·(a² - a_i²)/a
#                   -Σ_{i∈trav2} P_i·b_i·(a² - b_i²)/a
#       où a_i = distance depuis A pour les charges sur span 1,
#          b_i = distance depuis C pour les charges sur span 2.
#    2) Chaque span est ensuite traitée comme une poutre simple soumise :
#       - aux charges directes (formule classique de la flèche d'une poutre sur 2 appuis)
#       - à un moment de bord M_B à son extrémité côté appui central
#       Les flèches sont additionnées par superposition.
# ─────────────────────────────────────────────────────────────────────────────
def _I_req_from_forces_3_appuis(forces_N_mm, a, fmax):
    """
    forces_N_mm = [(F_N, x_mm), ...] avec x mesuré depuis l'appui gauche A
                   (0 ≤ x ≤ 2a, hors charges sur appuis).
    a = longueur d'UNE span (mm). Portée totale = 2a.
    Returns (I_min_cm4, delta_unit_mm, travee_critique_idx, M_max_Nmm).
    delta_unit_mm est en unités mm × mm⁴ (à diviser par I_chosen_mm4 pour
    obtenir la flèche réelle).
    travee_critique_idx : 1 si flèche max en span 1, 2 si span 2.
    M_max_Nmm : moment fléchissant max sur toute la poutre (incl. pivot M_B).
    """
    L_tot = 2.0 * a

    # Séparer les charges entre span 1 (0 < x < a) et span 2 (a < x < 2a).
    # Une charge pile sur l'appui central (x == a) est ignorée (elle ne fléchit pas).
    loads_1 = [(F, x) for (F, x) in forces_N_mm if 0 < x < a]
    loads_2 = [(F, x - a) for (F, x) in forces_N_mm if a < x < L_tot]
    # Pour span 2, on translate l'origine à l'appui central B.
    # Donc dans le repère span 2 : appui gauche = B (à x'=0), appui droit = C (à x'=a).

    # ── 1) Moment au pivot central M_B
    rhs = 0.0
    for F, x in loads_1:
        rhs += -F * x * (a*a - x*x) / a
    for F, xp in loads_2:
        b = a - xp
        rhs += -F * b * (a*a - b*b) / a
    M_B = rhs / (4.0 * a)                        # N·mm  (négatif si charges descendantes)

    # ── Réactions dans chaque span (isolement)
    # Travée 1 (A→B), moment de bord à B = M_B (du côté de la span 1, le moment
    # à droite vu de la span est M_B).
    # Équilibre des moments autour de B (sur span 1) : R_A·a = Σ P_i·(a−x_i) + M_B
    sum_P_x_1   = sum(F * x         for F, x in loads_1)
    sum_P_amx_1 = sum(F * (a - x)   for F, x in loads_1)
    R_A         = (sum_P_amx_1 + M_B) / a   # réaction à l'appui A
    # Travée 2 (B→C) : équilibre des moments autour de C → R_B2·a = Σ P_i·(a-xp) - M_B
    # (M_B s'applique côté gauche de la span 2, donc avec signe inversé pour l'équilibre)
    sum_P_amx_2 = sum(F * (a - xp)  for F, xp in loads_2)
    R_B2        = (sum_P_amx_2 - M_B) / a   # réaction côté gauche de span 2

    # ── 2) Deflection + moment dans chaque span par superposition

    def _delta_in_span(x, span_len, loads_in_span, M_left, M_right):
        s = span_len
        d = 0.0
        for F, p in loads_in_span:
            if p <= 0 or p >= s:
                continue
            b = s - p
            if x <= p:
                d += F * b * x * (s*s - b*b - x*x) / (6.0 * s)
            else:
                d += F * p * (s - x) * (s*s - p*p - (s - x)**2) / (6.0 * s)
        if M_right != 0:
            d += M_right * x * (s*s - x*x) / (6.0 * s)
        if M_left != 0:
            xp = s - x
            d += M_left * xp * (s*s - xp*xp) / (6.0 * s)
        return d

    # Scan flèche et moment dans chaque span
    best_delta = 0.0
    best_trav  = 1
    M_max = abs(M_B)   # le pivot est toujours un extremum candidat
    # Points d'évaluation : grille uniforme + sous chaque charge
    sample_x = [a * xi / float(DEFLECTION_SCAN_POINTS) for xi in range(1, DEFLECTION_SCAN_POINTS)]
    sample_x_1 = sample_x + [p for _, p in loads_1]
    sample_x_2 = sample_x + [p for _, p in loads_2]

    for xl in sample_x_1:
        if not (0 < xl < a):
            continue
        d1 = _delta_in_span(xl, a, loads_1, 0.0, M_B)
        if d1 > best_delta:
            best_delta = d1
            best_trav  = 1
        # Moment span 1 à position xl : M(xl) = R_A·xl - Σ F·(xl - x_i)+
        M1 = R_A * xl
        for F, x in loads_1:
            if xl > x:
                M1 -= F * (xl - x)
        if abs(M1) > M_max:
            M_max = abs(M1)

    for xl in sample_x_2:
        if not (0 < xl < a):
            continue
        d2 = _delta_in_span(xl, a, loads_2, M_B, 0.0)
        if d2 > best_delta:
            best_delta = d2
            best_trav  = 2
        # Moment span 2 (repère local) : M(xl) = R_B2·xl - Σ F·(xl - xp)+ - M_B·(xl/a)·0 ...
        # En fait : équilibre depuis l'appui gauche (B) :
        # M(xl) = M_B + R_B2·xl - Σ F·(xl - xp)+   (le moment de bord à gauche s'ajoute)
        # NB : signe — un M_B négatif et R_B2 positif donneront un M intérieur correct.
        M2 = M_B + R_B2 * xl
        for F, xp in loads_2:
            if xl > xp:
                M2 -= F * (xl - xp)
        if abs(M2) > M_max:
            M_max = abs(M2)

    if best_delta <= 0:
        return 0.0, 0.0, 1, M_max

    best_delta_unit = best_delta / YOUNG_MODULUS_MPA
    I_min_cm4 = (best_delta_unit / fmax) / 10000.0
    return I_min_cm4, best_delta_unit, best_trav, M_max


def compute_load_cases(rv1, som1, rv2, som2, space_btw, portee, nbre_pont, nbre_appuis=2):
    """
    Returns (inertie_cm4, description, forces_norm, delta_unit_mm, travee_critique)
      - forces_norm = [(pos_norm, F_kN, pont_idx), ...]
        Pour 2 appuis : pos_norm = x / L_span ∈ [0,1]
        Pour 3 appuis : pos_norm = x / (2·L_span) ∈ [0,1]
      - delta_unit_mm : flèche brute (à diviser par I_chosen_mm4 pour obtenir
                        la flèche physique en mm).
      - travee_critique : 1 ou 2 (uniquement pertinent en 3 appuis)

    portee = entraxe entre 2 appuis successifs (= longueur d'UNE span).
    Pour 3 appuis, la portée totale du système est 2 × portee.
    """
    a    = float(portee)          # longueur d'une span
    L_eq = a if nbre_appuis == 2 else 2.0 * a   # portée pour le scan
    fmax = a / float(DEFLECTION_RATIO)   # critère L/600 sur la longueur d'UNE span
    P1   = float(rv1) * 1000
    P2   = float(rv2) * 1000 if nbre_pont == 2 else 0.0
    s1   = float(som1)
    s2   = float(som2) if nbre_pont == 2 else 0.0
    gap  = float(space_btw) if nbre_pont == 2 else 0.0

    # Fonction qui calcule (I_req, delta_unit, travee_critique, M_max) pour un set de forces
    def solve(forces):
        if not forces:
            return 0.0, 0.0, 1, 0.0
        if nbre_appuis == 3:
            return _I_req_from_forces_3_appuis(forces, a, fmax)
        else:
            I, du, Mmx = _I_req_from_forces(forces, a, fmax)
            return I, du, 1, Mmx

    # ── 1 pont ───────────────────────────────────────────────────────────────
    if nbre_pont == 1:
        if s1 >= L_eq:
            if nbre_appuis == 2:
                forces = [(P1, a/2.0)]
                I, du, _, Mmx = solve(forces)
                return (I,
                        f"1 galet au centre (carriage {s1:.0f} mm ≥ span {a:.0f} mm)",
                        [(0.5, P1/1000, 0)], du, 1, Mmx)
            else:
                best = (0.0, 0.0, 1, 0.0, 0.0)   # (I, du, tc, x_pos, Mmx)
                for i in range(1, DEFLECTION_SCAN_POINTS):
                    x_pos = L_eq * i / DEFLECTION_SCAN_POINTS
                    I, du, tc, Mmx = solve([(P1, x_pos)])
                    if I > best[0]:
                        best = (I, du, tc, x_pos, Mmx)
                I, du, tc, x_pos, Mmx = best
                return (I,
                        f"1 galet à x={x_pos:.0f} mm (carriage ≥ portée totale)",
                        [(x_pos / L_eq, P1/1000, 0)], du, tc, Mmx)

        if nbre_appuis == 2:
            # Balaye toutes les positions — ne pas supposer que le centre est le pire cas
            best_I2, best_du2, best_norm2, best_desc2, best_M2 = 0.0, 0.0, [], "", 0.0
            for i in range(1, DEFLECTION_SCAN_POINTS + 1):
                c = a * i / DEFLECTION_SCAN_POINTS
                pos = _galet_pos(c, s1, a)
                forces = [(P1, p) for p in pos if 0 < p < a]
                if not forces:
                    continue
                I2, du2, _, Mmx2 = solve(forces)
                if I2 > best_I2:
                    best_I2, best_du2, best_M2 = I2, du2, Mmx2
                    n_in = len(forces)
                    best_norm2 = [(p/a, P1/1000, 0) for p in pos if 0 < p < a]
                    best_desc2 = f"{n_in} galet(s) — position critique x={c:.0f} mm"
            if best_I2 == 0:
                # Fallback symétrique
                pos = _galet_pos(a/2.0, s1, a)
                forces = [(P1, p) for p in pos if 0 < p < a]
                best_I2, best_du2, _, best_M2 = solve(forces)
                best_norm2 = [(p/a, P1/1000, 0) for p in pos if 0 < p < a]
                best_desc2 = f"2 galets sym. ±{s1/2:.0f} mm du centre"
            return (best_I2, best_desc2, best_norm2, best_du2, 1, best_M2)

        # 3 appuis, 1 pont : balaye la position du chariot
        best_I, best_du, best_norm, best_desc, best_tc, best_M = 0.0, 0.0, [], "", 1, 0.0
        margin = s1
        N_SCAN = 200
        for i in range(N_SCAN + 1):
            c = -margin + (L_eq + 2*margin) * i / N_SCAN
            pos = _galet_pos(c, s1, a)
            forces = [(P1, p) for p in pos if 0 < p < L_eq]
            if not forces:
                continue
            I, du, tc, Mmx = solve(forces)
            if I > best_I:
                best_I, best_du, best_tc, best_M = I, du, tc, Mmx
                best_norm = [(p/L_eq, P1/1000, 0) for p in pos if 0 < p < L_eq]
                n_in = sum(1 for p in pos if 0 < p < L_eq)
                best_desc = f"1 pont — {n_in}/2 galets sur portée, critical span {tc}"
        if best_I == 0:
            best_desc = "Aucune position ne charge la portée"
        return best_I, best_desc, best_norm, best_du, best_tc, best_M

    # ── 2 ponts ──────────────────────────────────────────────────────────────
    d_min = s1/2.0 + gap + s2/2.0
    best_I, best_du, best_norm, best_desc, best_tc, best_M = 0.0, 0.0, [], "", 1, 0.0
    margin = max(s1, s2)
    N_SCAN = 200
    for i in range(N_SCAN + 1):
        c1 = -margin + (L_eq + 2*margin) * i / N_SCAN
        c2 = c1 + d_min
        pos1 = _galet_pos(c1, s1, a)
        pos2 = _galet_pos(c2, s2, a)
        forces = [(P1, p) for p in pos1 if 0 < p < L_eq] + \
                 [(P2, p) for p in pos2 if 0 < p < L_eq]
        if not forces:
            continue
        I, du, tc, Mmx = solve(forces)
        if I > best_I:
            best_I, best_du, best_tc, best_M = I, du, tc, Mmx
            n1_in = sum(1 for p in pos1 if 0 < p < L_eq)
            n2_in = sum(1 for p in pos2 if 0 < p < L_eq)
            if nbre_appuis == 3:
                best_desc = (
                    f"2 ponts — écart {gap:.0f} mm — "
                    f"{n1_in}/2 + {n2_in}/2 galets sur portée, critical span {tc}"
                )
            else:
                best_desc = (
                    f"2 ponts — écart {gap:.0f} mm — "
                    f"{n1_in}/2 galets pont 1 + {n2_in}/2 galets pont 2 sur span"
                )
            best_norm = (
                [(p/L_eq, P1/1000, 0) for p in pos1 if 0 < p < L_eq] +
                [(p/L_eq, P2/1000, 1) for p in pos2 if 0 < p < L_eq]
            )

    if best_I == 0:
        best_desc = "Aucune position ne charge la portée"

    return best_I, best_desc, best_norm, best_du, best_tc, best_M


# ─────────────────────────────────────────────────────────────────────────────
#  BEAM SELECTION & MAIN COMPUTE
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
#  DÉVERSEMENT (Lateral-Torsional Buckling) — EN 1993-1-1 §6.3.2 simplified
#
#  For a rolled I/H section with uniform moment (worst case C1=1):
#    λ_LT = √(Wy·fy / Mcr)
#    Mcr  = C1·(π²EIz/L²)·√(Iz·Iw/Iz² + L²·G·It/(π²EIz))
#
#  Simplified for crane runway beams:
#    - Loaded on top flange (destabilizing) → C1 = 1.0 conservative
#    - Lc = distance between lateral restraints (= entraxe appuis / 2 default, or L)
#    - We use the "simplified" formula from ENV1993 for rolled sections:
#        Mcr ≈ 77.2·(Iz·It)^0.5 / Lc  [rough estimate used in pre-design]
#
#  Actually we use the full formula with Iw = (h-tf)²·Iz/4 (approx for I-section):
#    Mcr = (π/Lc)·√(EIz·GIt + (π/Lc)²·EIz·EIw)
#    G ≈ E/2.6 ≈ 80770 MPa
#
#  Returns (χ_LT, λ_LT, Mcr_kNm, util_LTB) where util = M_max / (χ_LT·Wy·fy)
# ─────────────────────────────────────────────────────────────────────────────
def _check_ltb(size_mm, b_mm, tf_mm, Iy_cm4, Iz_cm4, M_max_Nmm, Lc_mm, load_pos="top"):
    """
    LTB check for rolled I/H section (EN 1993-1-1 annex F, general formula).
    size_mm  : section height h [mm]
    b_mm     : flange width [mm]
    tf_mm    : flange thickness [mm]
    Iy_cm4   : strong-axis inertia [cm⁴]
    Iz_cm4   : weak-axis inertia [cm⁴]  (from IZ_TABLES)
    M_max_Nmm: max bending moment [N·mm]
    Lc_mm    : lateral restraint spacing [mm]  (= entraxe appuis)
    load_pos : point d'application de la charge transversale —
               'top'    : charge sur la semelle SUPÉRIEURE (pont POSÉ sur rail)
                          → effet DÉSTABILISANT (zg > 0) → Mcr réduit
               'bottom' : charge sur la semelle INFÉRIEURE (pont SUSPENDU)
                          → effet STABILISANT (zg < 0) → Mcr augmenté
    Returns dict with λ_LT, χ_LT, Mcr_kNm, util, ok
    """
    import math
    E  = YOUNG_MODULUS_MPA    # 210000 MPa
    G  = 80770.0              # shear modulus [MPa]
    Fy = STEEL_FY_MPA         # 235 MPa

    # Convert units to mm
    h  = float(size_mm)
    b  = float(b_mm)
    tf = float(tf_mm)
    tw = 6.0   # approximate web thickness (conservative)
    Iz = float(Iz_cm4) * 1e4   # mm⁴
    Iy = float(Iy_cm4) * 1e4   # mm⁴
    Wy = Iy / (h / 2)           # elastic section modulus mm³ (strong axis)
    L  = float(Lc_mm)

    # Torsion constant It ≈ (1/3)·(2·b·tf³ + (h-2tf)·tw³)
    It = (1.0/3.0) * (2 * b * tf**3 + (h - 2*tf) * tw**3)

    # Warping constant Iw ≈ (h-tf)²·Iz/4  (symmetric I-section)
    Iw = (h - tf)**2 * Iz / 4.0

    if L <= 0:
        return {"λ_LT": 0.0, "χ_LT": 1.0, "Mcr_kNm": 9999.0, "util": 0.0,
                "ok": True, "load_pos": load_pos}

    # Coefficients EN 1993-1-1 annexe F (charge transversale, appuis fourche) :
    #   C1 ≈ 1.13, C2 ≈ 0.45 (valeurs usuelles, conservatrices).
    C1, C2 = 1.13, 0.45
    # zg = distance du point d'application de charge au centre de cisaillement.
    #   posé (charge sur semelle sup)   → zg = +h/2  (déstabilisant)
    #   suspendu (charge sur semelle inf)→ zg = −h/2  (stabilisant)
    zg = (+h / 2.0) if load_pos == "top" else (-h / 2.0)

    # Mcr = C1·(π²·E·Iz/L²)·[ √( Iw/Iz + L²·G·It/(π²·E·Iz) + (C2·zg)² ) − C2·zg ]
    pi2EIz_L2 = (math.pi**2) * E * Iz / (L**2)
    under = (Iw / Iz) + (L**2 * G * It) / ((math.pi**2) * E * Iz) + (C2 * zg)**2
    Mcr = C1 * pi2EIz_L2 * (math.sqrt(max(under, 0.0)) - C2 * zg)   # N·mm

    # Slenderness λ_LT
    lam  = math.sqrt(Wy * Fy / Mcr) if Mcr > 0 else 99.0

    # Reduction factor χ_LT (EN 1993-1-1 §6.3.2.3, curve b for rolled sections α=0.34)
    alpha = 0.34
    lam0  = 0.4   # plateau limit for rolled sections
    if lam <= lam0:
        chi = 1.0
    else:
        phi = 0.5 * (1 + alpha * (lam - lam0) + lam**2)
        chi = min(1.0, 1.0 / (phi + math.sqrt(max(0.0, phi**2 - lam**2))))

    M_Rd = chi * Wy * Fy   # N·mm  design resistance
    util = M_max_Nmm / M_Rd if M_Rd > 0 else 99.0

    return {
        "λ_LT":    round(lam, 3),
        "χ_LT":    round(chi, 3),
        "Mcr_kNm": round(Mcr / 1e6, 1),   # N·mm → kN·m
        "util":    round(util, 3),
        "ok":      util <= 1.0,
        "load_pos": load_pos,
    }


# ═════════════════════════════════════════════════════════════════════════════
#  RENFORT DÉVERSEMENT — cornières soudées sur la semelle supérieure
#  (catalogue ANGLE_CATALOGUE et seuil LTB_REINFORCE_THRESHOLD : voir
#   railway_constants.py)
# ═════════════════════════════════════════════════════════════════════════════


def _ltb_with_angles(size_mm, b_mm, tf_mm, Iy_cm4, Iz_cm4, M_max_Nmm, Lc_mm, angle, load_pos="top"):
    """Recalcule le déversement en ajoutant 4 cornières (2 par côté de l'âme,
    sur toute la longueur, sur la semelle supérieure).

    Théorème de transfert (Huygens) :
      • Iy (axe fort) : chaque cornière, CdG ≈ au-dessus de la semelle sup,
        à une distance d_y de l'axe neutre fort (h/2).
      • Iz (axe faible) : cornières écartées latéralement de l'âme → grand bras
        de levier d_z, ce qui augmente fortement Iz (donc Mcr).

    angle = tuple (label, A_cm2, I_propre_cm4, e_cm, masse_kg_m).
    Retourne (ltb_dict, Iy_tot_cm4, Iz_tot_cm4).
    """
    _lbl, A_cm2, I0_cm4, e_cm, _m = angle
    h  = float(size_mm)
    b  = float(b_mm)              # largeur de semelle
    A  = A_cm2 * 100.0            # mm²
    I0 = I0_cm4 * 1e4             # mm⁴
    e  = e_cm * 10.0              # mm — CdG cornière depuis le talon (heel)

    # Disposition : la cornière coiffe le BORD de la semelle supérieure —
    # aile horizontale dans la continuité de la semelle (même niveau), aile
    # verticale rabattue vers le bas le long du chant. Le talon est au bord
    # de la semelle ; le CdG de la cornière est donc À ≈ e vers l'EXTÉRIEUR
    # du bord (latéralement) et ≈ e sous le dessus de semelle (verticalement).

    # ── Iy (axe fort) : CdG ≈ au niveau de la semelle supérieure (à h/2) ─────
    # L'aile horizontale est dans la continuité de la semelle → d_y ≈ h/2 − e.
    d_y = max(h / 2.0 - e, 0.0)
    Iy_add = 2.0 * (I0 + A * d_y**2)          # mm⁴ (2 cornières / poutre)

    # ── Iz (axe faible) : cornière DÉBORDANT au-delà du bord de semelle ──────
    # CdG à b/2 + e du plan de l'âme (talon au bord, masse vers l'extérieur).
    d_z = b / 2.0 + e
    Iz_add = 2.0 * (I0 + A * d_z**2)          # mm⁴ (2 cornières / poutre)

    Iy_tot = float(Iy_cm4) * 1e4 + Iy_add     # mm⁴
    Iz_tot = float(Iz_cm4) * 1e4 + Iz_add     # mm⁴

    ltb = _check_ltb(size_mm, b_mm, tf_mm, Iy_tot / 1e4, Iz_tot / 1e4,
                     M_max_Nmm, Lc_mm, load_pos)
    return ltb, Iy_tot / 1e4, Iz_tot / 1e4


def _reinforce_against_ltb(size_mm, b_mm, tf_mm, Iy_cm4, Iz_cm4, M_max_Nmm, Lc_mm, L_beam_mm, load_pos="top"):
    """Si le taux de déversement de base dépasse 85 %, essaie successivement
    les cornières du catalogue (50×50×5 → 70×70×7 → 100×100×10) jusqu'à ramener
    le taux ≤ 1.0. Renforce Iy ET Iz.

    Retourne un dict :
      {needed, base_util, angle, util, Iy_tot, Iz_tot, m_angles_kg, ltb}
      - needed     : bool — un renfort est-il requis (taux base > seuil) ?
      - angle      : label de la cornière retenue (ou None)
      - m_angles_kg: masse des 4 cornières sur toute la longueur du CR [kg]
                     (×2 poutres parallèles → calculé à l'appelant si besoin)
    """
    base = _check_ltb(size_mm, b_mm, tf_mm, Iy_cm4, Iz_cm4, M_max_Nmm, Lc_mm, load_pos)
    out = {
        "needed": base["util"] > LTB_REINFORCE_THRESHOLD,
        "base_util": base["util"],
        "angle": None, "util": base["util"],
        "Iy_tot": Iy_cm4, "Iz_tot": Iz_cm4,
        "m_angles_kg": 0.0, "ltb": base,
    }
    if not out["needed"]:
        return out

    # Essai des cornières par taille croissante
    best = None
    for ang in ANGLE_CATALOGUE:
        ltb_a, Iy_t, Iz_t = _ltb_with_angles(
            size_mm, b_mm, tf_mm, Iy_cm4, Iz_cm4, M_max_Nmm, Lc_mm, ang, load_pos)
        if best is None:
            best = (ang, ltb_a, Iy_t, Iz_t)        # garde au moins la 1ère
        if ltb_a["util"] <= 1.0:
            best = (ang, ltb_a, Iy_t, Iz_t)
            break

    ang, ltb_a, Iy_t, Iz_t = best
    _lbl, _A, _I0, _e, m_lin = ang
    # 2 cornières sur toute la longueur d'UNE poutre (une à chaque bord de
    # semelle). ×2 poutres parallèles → calculé à l'appelant.
    m_angles_one_beam = m_lin * (float(L_beam_mm) / 1000.0) * 2.0
    out.update({
        "angle": _lbl, "util": ltb_a["util"],
        "Iy_tot": round(Iy_t, 1), "Iz_tot": round(Iz_t, 1),
        "m_angles_kg": m_angles_one_beam, "ltb": ltb_a,
    })
    return out


def _select_beam(beam_type, I_req_cm4, M_max_Nmm):
    """
    Sélectionne la première poutre du catalogue qui satisfait :
      1) I_y ≥ I_req (critère de flèche L/600)
      2) σ_max = M_max / Wel ≤ σ_adm = Fy / SF  (critère de contrainte)
    Retourne (taille_h, masse_kg/m, I_cm4, Wel_cm3, b_mm, tf_mm, sigma_MPa, gov)
    où `gov` ∈ {"flèche", "contrainte", "égalité"} indique le critère gouvernant.
    Retourne (None,)*8 si aucune poutre ne convient.
    """
    sigma_adm = SIGMA_ADM_MPA   # MPa = N/mm²
    table = TABLES.get(beam_type, TABLES["HEA"])
    for size, inertie, masse, Wel_cm3, b_mm, tf_mm in table:
        Wel_mm3 = Wel_cm3 * 1000.0
        sigma = M_max_Nmm / Wel_mm3 if Wel_mm3 > 0 else float('inf')
        ok_fleche     = inertie >= I_req_cm4
        ok_contrainte = sigma <= sigma_adm
        if ok_fleche and ok_contrainte:
            ratio_fleche     = I_req_cm4 / inertie if inertie > 0 else 0
            ratio_contrainte = sigma / sigma_adm
            if abs(ratio_fleche - ratio_contrainte) < 0.02:
                gov = "égalité"
            elif ratio_fleche > ratio_contrainte:
                gov = "flèche"
            else:
                gov = "contrainte"
            return size, masse, inertie, Wel_cm3, b_mm, tf_mm, sigma, gov
    return None, None, None, None, None, None, None, None


def _compute_m_divers(h_mm, b_mm, tf_mm, n_appuis_total):
    """
    Masse totale [kg] des pièces d'acier découpées :
      - 4 platines d'about       (surface h × b, ép. tf cap MAX_TF_ABOUT)
      - (n_appuis − 4) × 2 plats d'éclissage
                                  (surface h_âme × h_âme, ép. tf cap MAX_TF_ECLISSE)
      - (n_appuis − 2) × 4 goussets
                                  (surface 0.5 × h × b, ép. tf cap MAX_TF_GOUSSET)
      - n_appuis × 1 plat de calage par appui
                                  (surface b × b, ép. tf sans cap par défaut)

    + marge de réserve M_DIVERS_RESERVE (10% pour aléas/quincaillerie).
    + plancher M_DIVERS_MIN_KG (30 kg minimum).

    n_appuis est plafonné à N_APPUIS_MIN (=4) pour éviter des quantités négatives.
    """
    n_app = max(N_APPUIS_MIN, int(n_appuis_total))

    # Épaisseurs effectives capées par type de pièce
    def _cap(tf, cap):
        return float(tf) if cap is None else min(float(tf), float(cap))

    tf_about    = _cap(tf_mm, MAX_TF_ABOUT)
    tf_eclisse  = _cap(tf_mm, MAX_TF_ECLISSE)
    tf_gousset  = _cap(tf_mm, MAX_TF_GOUSSET)
    tf_calage   = _cap(tf_mm, MAX_TF_CALAGE)   # None → pas de cap → tf brut

    # Hauteur d'âme (entre les semelles) — calculée avec tf réel, pas capé
    h_ame = max(0.0, h_mm - 2.0 * tf_mm)

    # Surfaces unitaires (mm²)
    S_about    = h_mm * b_mm
    S_eclisse  = h_ame * h_ame                    # plat carré côté h_âme
    S_gousset  = GOUSSET_AREA_RATIO * S_about     # ≈ 0.5 × h × b
    S_calage   = b_mm * b_mm                      # plat carré côté b

    # Quantités
    n_about    = 4
    n_eclisse  = (n_app - 4) * 2
    n_gousset  = (n_app - 2) * 4
    n_calage   = n_app

    # Masses (kg) = ρ × Σ(N × t × S)
    rho = STEEL_DENSITY_KG_MM3
    m = rho * (
        n_about   * tf_about   * S_about   +
        n_eclisse * tf_eclisse * S_eclisse +
        n_gousset * tf_gousset * S_gousset +
        n_calage  * tf_calage  * S_calage
    )

    # Marge de réserve (10% par défaut)
    m *= (1.0 + M_DIVERS_RESERVE)

    # Plancher minimum
    return max(M_DIVERS_MIN_KG, m)



# ─────────────────────────────────────────────────────────────────────────────
#  COLONNES OLSEN — Dimensionnement
#
#  Géométrie : colonne encastrée en base, libre en tête (fixée par le CR).
#              h_col = h_rail_mm (hauteur CR) - h_rail_section - h_poutre_CR
#              Mais pour la FLÈCHE on utilise h_total = h_rail_mm.
#
#  Efforts horizontaux (perpendiculaires au CR → axe FORT de la colonne) :
#    HT3 + HS  (accélération chariot + mise en crabe)
#    F_perp = HT3 + HS  [kN], repris par N_cols_perp colonnes
#
#  Efforts horizontaux (parallèles au CR → axe FAIBLE) :
#    HL  [kN], repris par N_cols_long colonnes
#
#  Effort normal (vertical) :
#    N_v = réaction d'appui maximale du CR  [kN], repris par 2 colonnes
#
#  Critères :
#    1) Deflection axe fort : δ = F_perp·h³/(3·E·Iy) ≤ h/400
#    2) Flambement axe faible + compression :
#         λ_barre = Lf/i_z · √(Fy/(π²E))  avec Lf = 2h
#         χ via courbe b (α=0.34)
#         N_Ed/χ·A·Fy + M_Ed/W_pl,z·Fy ≤ 1  (interaction simplifié)
#         M_Ed = HL·h / N_cols_long  (moment à la base)
#
#  twin_column : 2 colonnes en parallèle → Iy_eff = 2·Iy, Iz_eff = 2·Iz
#                N_cols_perp = 2  (twin reprend les 2 plans)
#                N_cols_long = 3  (1 noeud résistant dans chaque plan + central)
#                → on considère que 2 colonnes reprennent les H parallèles aussi
# ─────────────────────────────────────────────────────────────────────────────

def _chi_buckling(lf_mm, Iz_mm4, A_mm2):
    """
    Facteur de réduction au flambement χ (courbe b, EN 1993-1-1).
    lf_mm : longueur de flambement [mm]
    Iz_mm4 : inertie axe faible [mm⁴]
    A_mm2  : aire de la section [mm²]
    Retourne (χ, λ_barre, N_cr_kN).
    """
    if Iz_mm4 <= 0 or A_mm2 <= 0:
        return 0.0, 999.0, 0.0
    E  = YOUNG_MODULUS_MPA
    Fy = STEEL_FY_MPA
    alpha = FLAMB_IMPERFECTION   # 0.34 courbe b

    N_cr  = math.pi**2 * E * Iz_mm4 / lf_mm**2   # N
    lam   = math.sqrt(A_mm2 * Fy / N_cr)           # λ_barre (adimensionnel)
    phi   = 0.5 * (1 + alpha * (lam - 0.2) + lam**2)
    chi   = min(1.0, 1.0 / (phi + math.sqrt(max(0.0, phi**2 - lam**2))))
    return chi, lam, N_cr / 1000.0   # N_cr en kN


# ─────────────────────────────────────────────────────────────────────────────
#  RÉPARTITION DES EFFORTS HORIZONTAUX SUR LES COLONNES
#
#  Modèle : CR = poutre sur 2 appuis (colonnes), portée = entraxe colonnes (support_spacing_mm).
#  Charges horizontales ponctuelles → réaction d'appui = force × (L-a)/L
#  où a = distance du point d'application à la colonne gauche.
#
#  HT3 [kN/galet] : s'applique à chaque galet (2 positions ± sommier_chariot/2)
#  HS  [kN/pont]  : s'applique au centre du pont (a = L/2)
#  HL  [kN/galet] : s'applique à chaque galet moteur (même positions que HT3)
#
#  On balaye la position du chariot sur la portée horizontale et on retient
#  la réaction maximale sur la colonne la plus chargée.
#
#  Pour 2 ponts : on additionne les réactions des 2 ponts (cas défavorable simultané).
# ─────────────────────────────────────────────────────────────────────────────
def _max_horiz_reaction(
    HT3_kN, HS_kN, HL_kN,
    sommier_chariot_mm,
    entraxe_col_mm,
    n_scan=None,
):
    if n_scan is None: n_scan = HORIZ_SCAN_POINTS
    """
    Retourne (F_perp_max_kN, F_long_max_kN) — effort max sur la colonne critique.
    HT3 et HL sont par galet, HS est pour le pont entier.

    Réaction d'appui (colonne gauche) pour charge P à position a :
        R = P × (L - a) / L
    On prend max(R_gauche, R_droite) = max(R, P-R).
    """
    L = float(entraxe_col_mm)
    if L <= 0:
        return float(HT3_kN) + float(HS_kN), float(HL_kN)

    s  = float(sommier_chariot_mm)
    HT = float(HT3_kN)
    HS = float(HS_kN)
    HL = float(HL_kN)

    max_perp = 0.0
    max_long = 0.0

    for i in range(n_scan + 1):
        # Position du centre du chariot sur la portée horizontale
        c = L * i / n_scan

        # Positions des 2 galets
        g1 = c - s / 2.0
        g2 = c + s / 2.0

        # ── Effort ⊥ CR (HT3 par galet + HS au centre pont) ──────────────────
        R_perp = 0.0
        for g in [g1, g2]:
            if 0 <= g <= L:
                R = HT * (L - g) / L
                R_perp += max(R, HT - R)   # colonne la plus chargée
            else:
                # Galet hors portée → réaction sur la colonne la plus proche
                R_perp += HT   # force entière sur la colonne d'extrémité
        # HS au centre du pont (a = c)
        a_hs = max(0.0, min(c, L))
        R_hs = HS * (L - a_hs) / L
        R_perp += max(R_hs, HS - R_hs)

        max_perp = max(max_perp, R_perp)

        # ── Effort ‖ CR (HL par galet) ────────────────────────────────────────
        R_long = 0.0
        for g in [g1, g2]:
            if 0 <= g <= L:
                R = HL * (L - g) / L
                R_long += max(R, HL - R)
            else:
                R_long += HL
        max_long = max(max_long, R_long)

    return round(max_perp, 3), round(max_long, 3)


def compute_columns(
    col_type,           # "HEA"|"HEB"|"IPE"
    h_rail_mm,          # hauteur totale depuis sol jusqu'au dessus du rail [mm]
    h_rail_section_mm,  # hauteur de la section de rail [mm]
    h_poutre_CR_mm,     # hauteur de la poutre du chemin de roulement [mm]
    HT3_kN,             # accélération chariot [kN]
    HS_kN,              # mise en crabe [kN]
    HL_kN,              # accélération pont [kN]
    N_v_kN,             # réaction verticale max du CR (1 colonne) [kN]
    twin_col,           # bool — double colonne (twin)
    contrevente,        # bool — contreventé dans le plan ‖ CR
    n_appuis_total=4,   # nb total d'appuis
    sommier_chariot_mm=2000,  # entraxe galets du chariot [mm]
    entraxe_col_mm=6000,      # entraxe entre colonnes [mm]
    is_suspendu=False,        # bool — pont suspendu
    h_cross_beam_mm=0.0,      # hauteur section cross beam [mm] (suspendu)
    mo_price=DEFAULT_MO_PRICE,
):
    """
    Dimensionne la colonne et retourne un dict complet.
    Utilise h_rail_mm pour la flèche (hauteur totale).

    Hauteur NETTE de colonne (pour la masse acier) :
      • Pont POSÉ     : h_col = h_rail − h_rail_section − h_poutre_CR
                        (la voie repose sur la colonne, sous le niveau rail).
      • Pont SUSPENDU : h_col = h_rail + h_poutre_CR + h_cross_beam
                        (la cross beam est en tête de colonne et la voie est
                        suspendue dessous ; la colonne monte donc plus haut).
    """
    h_total = float(h_rail_mm)        # mm — hauteur pour critère flèche
    if is_suspendu:
        h_col = max(100.0, h_total + float(h_poutre_CR_mm) + float(h_cross_beam_mm))
    else:
        h_col = max(100.0, h_total - float(h_rail_section_mm) - float(h_poutre_CR_mm))

    # Colonnes par appui : 2 si simple, 3 si twin (2 + 1 central)
    # Réaction max sur la colonne critique (distribution par poutre horizontale)
    F_perp_unit, F_long_unit = _max_horiz_reaction(
        HT3_kN, HS_kN, HL_kN,
        sommier_chariot_mm = sommier_chariot_mm,
        entraxe_col_mm     = entraxe_col_mm,
    )
    N_v_unit    = float(N_v_kN)
    n_cols_perp = 1

    E = YOUNG_MODULUS_MPA   # MPa = N/mm²

    table  = TABLES.get(col_type, TABLES["HEA"])
    iz_map = IZ_TABLES.get(col_type, {})

    # ── Critère 1 : flèche axe fort (δ ≤ h_total/400) ────────────────────────
    # δ = F·h³/(3·E·Iy) ≤ h_total / DEFLECTION_COL_RATIO
    # → Iy_min = F·h³·DEFLECTION_COL_RATIO / (3·E·h_total) · 1/10000  [cm⁴]
    f_adm = h_total / float(DEFLECTION_COL_RATIO)   # mm
    F_N   = F_perp_unit * 1000.0                     # N
    # Pour twin : 2 colonnes → Iy_eff = 2·Iy → on cherche Iy tel que 2·Iy ≥ Iy_req
    n_twin_factor = 1  # twin = same calc as single, just +2 columns for counting
    Iy_req_cm4 = F_N * h_total**3 / (3 * E * f_adm * 10000.0)

    results = []
    for size, Iy_cm4, masse, Wel_cm3, b_mm, tf_mm in table:
        if Iy_cm4 < Iy_req_cm4:
            continue

        Iz_cm4  = iz_map.get(size, 0.0)
        Iy_mm4  = Iy_cm4 * 10000.0
        Iz_mm4  = Iz_cm4 * 10000.0
        # Twin : on double les inerties
        Iy_eff  = Iy_mm4
        Iz_eff  = Iz_mm4
        A_unit  = masse / 7.85e-3   # mm² (masse en kg/m, densité 7.85e-3 kg/(mm²·m))
        A_eff   = A_unit

        # ── Vérification flambement + interaction (axe faible) ────────────────
        Lf = FLAMB_FACTOR * h_col   # longueur de flambement [mm]
        chi, lam, N_cr_kN = _chi_buckling(Lf, Iz_eff, A_eff)
        N_pl_kN = A_eff * STEEL_FY_MPA / 1000.0   # N_pl [kN]
        N_Ed    = N_v_unit   # kN — effort axial par système de colonnes

        # Moment à la base dû à HL : M_Ed = HL × h_col / n_long  [kN·mm]
        M_Ed_kNmm = F_long_unit * h_col

        # Module plastique axe faible (approx. = 1.15 × Wel,z pour profil I)
        Wpl_z_cm3  = Wel_cm3 * 0.5 * (b_mm / (size * 0.5))   # approx Wel,z ≈ b²·tf/4
        # Simpler: Wpl_z ~ 1.12 × Iz/( b/2) pour sections I
        Wpl_z_mm3  = (Iz_eff * 2.0 / b_mm) * 10000.0 * 1.12   # mm³

        M_pl_kNmm  = Wpl_z_mm3 * STEEL_FY_MPA / 1e6           # kN·mm → kN·m×1000

        # Taux d'utilisation interaction (NF EN 1993-1-1 eq. 6.61 simplified)
        util_N  = N_Ed / (chi * N_pl_kN) if (chi * N_pl_kN) > 0 else 9.9
        util_M  = M_Ed_kNmm / M_pl_kNmm  if M_pl_kNmm > 0 else 9.9
        util    = util_N + util_M   # doit être ≤ 1.0

        # Deflection réelle axe fort
        delta_mm = F_N * h_total**3 / (3 * E * Iy_eff)

        results.append({
            "size":       size,
            "col_label":  f"{col_type}{size}",
            "masse_lin":  masse,
            "Iy_cm4":     Iy_cm4,
            "Iz_cm4":     Iz_cm4,
            "delta_mm":   round(delta_mm, 2),
            "f_adm_mm":   round(f_adm, 2),
            "fleche_ok":  delta_mm <= f_adm,
            "lam_barre":  round(lam, 3),
            "chi":        round(chi, 3),
            "N_cr_kN":    round(N_cr_kN, 1),
            "N_pl_kN":    round(N_pl_kN, 1),
            "util_N":     round(util_N, 3),
            "util_M":     round(util_M, 3),
            "util_tot":   round(util, 3),
            "flamb_ok":   util <= 1.0,
            "ok":         delta_mm <= f_adm and util <= 1.0,
            "F_perp_unit": round(F_perp_unit, 2),
            "F_long_unit": round(F_long_unit, 2),
            "N_v_unit":    round(N_v_unit, 2),
            "n_cols_perp": n_cols_perp,
            "twin_col":    twin_col,
            "h_col_mm":    round(h_col),
            "h_total_mm":  round(h_total),
            "col_type":    col_type,
            "b_mm":        b_mm,
            "masse_lin_unit": masse,  # kg/m par colonne unitaire
        })

        # Prendre la première section qui satisfait les deux critères
        if delta_mm <= f_adm and util <= 1.0:
            return results[-1], results

    # Aucune section ne satisfait les 2 critères → retourner le moins mauvais
    if results:
        best = min(results, key=lambda x: x["util_tot"] + (0 if x["fleche_ok"] else 2))
        return best, results
    return None, []


# ─────────────────────────────────────────────────────────────────────────────
#  CROSS BEAM — Poutre de suspension (portique Olsen, pont suspendu/posé)
#
#  Une cross beam relie les 2 chemins de roulement au droit d'un portique.
#  Il y en a n_appuis_total / 2 (une par portique).
#
#  Géométrie :
#    L_beam = crane_span + 2 × 500 mm   (les 500 mm = poutres de suspension de
#                                        chaque côté).
#  Chargement :
#    2 charges ponctuelles espacées de crane_span (donc à 500 mm de chaque
#    extrémité), chacune = R_max (réaction d'appui max du CR).
#  Sélection :
#    même catalogue/type que la colonne (col_type), critère flèche L/600.
#  Divers (→ ajouté à "Misc. col.") par cross beam :
#    2 abouts (b×h×tf, cap 12 mm) + 8 goussets (½ about chacun)
#    ⇒ équivalent (2 + 8×0.5) = 6 abouts.
#  Boulonnerie : forfait n_appuis × PRICE_BOULONNERIE_UNIT (visserie CR).
# ─────────────────────────────────────────────────────────────────────────────
CROSS_BEAM_OVERHANG = 500.0   # mm de chaque côté (poutre de suspension)

def compute_cross_beam(col_type, crane_span_mm, R_max_kN, n_appuis_total,
                       steel_price, lasercut_price, paint_price,
                       mo_price=DEFAULT_MO_PRICE):
    """
    Dimensionne la cross beam et retourne un dict complet (masses, coûts),
    ou None si crane_span ≤ 0 ou aucune section ne convient.
    """
    cs = float(crane_span_mm)
    if cs <= 0:
        return None

    L_beam = cs + 2.0 * CROSS_BEAM_OVERHANG     # mm
    n_beams = max(1, int(n_appuis_total) // 2)  # une cross beam par portique

    # ── Chargement : 2 charges = R_max, à 500 mm de chaque extrémité ─────────
    P = float(R_max_kN) * 1000.0   # N
    a1 = CROSS_BEAM_OVERHANG       # mm (charge gauche)
    a2 = L_beam - CROSS_BEAM_OVERHANG  # mm (charge droite)
    forces = [(P, a1), (P, a2)]

    # I requis sur critère flèche L/1000 (poutre sur 2 appuis) + moment max
    fmax = L_beam / float(DEFLECTION_CROSS_RATIO)
    I_req_cm4, delta_unit, M_max_Nmm = _I_req_from_forces(forces, L_beam, fmax)

    # ── Sélection : même type que la colonne, critère flèche (et contrainte) ──
    size, masse_lin, inertie, Wel_cm3, b_mm, tf_mm, sigma_MPa, gov = \
        _select_beam(col_type, I_req_cm4, M_max_Nmm)
    if size is None:
        return {
            "error": (
                f"Aucune poutre {col_type} ne convient pour la cross beam : "
                f"I ≥ {I_req_cm4:,.0f} cm⁴ requis (flèche L/{DEFLECTION_CROSS_RATIO})."
            ),
            "I_req_cm4": round(I_req_cm4),
            "L_beam_mm": round(L_beam),
            "n_beams":   n_beams,
        }

    beam_label = f"{col_type}{size}"

    # Flèche réelle
    I_chosen_mm4 = inertie * 10000.0
    delta_mm = (delta_unit / I_chosen_mm4) if I_chosen_mm4 > 0 else 0.0
    f_adm_mm = fmax

    # ── Contrainte (σ = M_max / Wel) ─────────────────────────────────────────
    Wel_mm3   = float(Wel_cm3) * 1000.0
    sigma_MPa = (M_max_Nmm / Wel_mm3) if Wel_mm3 > 0 else 0.0
    sigma_adm = SIGMA_ADM_MPA

    # ── Déversement (LTB) — maintien latéral sur toute la portée (conservatif) ─
    Iz_cm4 = IZ_TABLES.get(col_type, {}).get(size, inertie * 0.04)
    ltb = _check_ltb(size, b_mm, tf_mm, inertie, Iz_cm4, M_max_Nmm, L_beam)

    # ── Masse acier ──────────────────────────────────────────────────────────
    m_beam = masse_lin * (L_beam / 1000.0) * n_beams   # kg
    a_cost = m_beam * float(steel_price) * FACT_DECOUPE

    # ── Divers (abouts + goussets), à ajouter à Misc. col. ───────────────────
    # 2 abouts (b×h×tf cap 12) + 8 goussets (½ about) = 6 abouts équivalents
    tf_about = min(float(tf_mm), MAX_TF_ABOUT) if MAX_TF_ABOUT else float(tf_mm)
    tf_about = min(tf_about, 12.0)            # cap explicite à 12 mm
    S_about  = float(b_mm) * float(size)      # mm²
    n_about_equiv = (2 + 8 * GOUSSET_AREA_RATIO)   # = 6
    m_div_unit = STEEL_DENSITY_KG_MM3 * n_about_equiv * tf_about * S_about  # kg / cross beam
    m_div = m_div_unit * n_beams
    c_cost = m_div * float(lasercut_price)

    # ── Peinture ─────────────────────────────────────────────────────────────
    surf_m   = _paint_surface(col_type, size)
    peinture = surf_m * PAINT_LITERS_PER_M2 * (L_beam / 1000.0) * n_beams
    d_cost   = peinture * float(paint_price)

    # ── Boulonnerie : forfait n_appuis × visserie CR ─────────────────────────
    boul = int(n_appuis_total) * PRICE_BOULONNERIE_UNIT
    # ── MO cross beam : heures + coût ────────────────────────────────────────
    h_mo_xb   = n_beams * MO_HOURS_PER_CROSS_BEAM
    cout_mo_xb = h_mo_xb * float(mo_price)

    return {
        "beam_label":  beam_label,
        "size":        size,
        "col_type":    col_type,
        "n_beams":     n_beams,
        "L_beam_mm":   round(L_beam),
        "crane_span_mm": round(cs),
        "I_req_cm4":   round(I_req_cm4),
        "I_chosen":    inertie,
        "M_max_Nmm":   M_max_Nmm,
        "R_charge_kN": round(float(R_max_kN), 2),
        "delta_mm":    round(delta_mm, 2),
        "f_adm_mm":    round(f_adm_mm, 2),
        "fleche_ok":   delta_mm <= f_adm_mm,
        "sigma_MPa":   round(sigma_MPa, 1),
        "sigma_adm_MPa": round(sigma_adm, 1),
        "sigma_ok":    sigma_MPa <= sigma_adm,
        "ltb":         ltb,
        "masse_lin":   masse_lin,
        "m_beam":      round(m_beam),
        "a_beam":      round(a_cost),
        "m_div":       round(m_div),
        "c_div":       round(c_cost),
        "peinture":    round(peinture, 1),
        "d_paint":     round(d_cost),
        "boul":        boul,
        "h_mo_xb":     round(h_mo_xb, 1),
        "cout_mo_xb":  round(cout_mo_xb),
        "cout_cross":  round(a_cost + c_cost + d_cost + boul + cout_mo_xb),
    }


def compute_railway(client, project, username, total_length_mm, beam_type, rail_kgm, rail_label,
                    rv_kN, carriage_mm, support_spacing_mm, nbre_pont,
                    rv2_kN, carriage2_mm, space_mm,
                    appui_type, spacing_appuis_mm,
                    steel_price, rail_price, lasercut_price, paint_price,
                    nbre_appuis=2,
                    crane_type="Posé", crane_span_mm=0.0,
                    mo_price=DEFAULT_MO_PRICE):
    L      = float(total_length_mm)
    spa    = float(spacing_appuis_mm)
    is_suspendu = (str(crane_type) == "Suspendu")
    # Pont suspendu → pas de rails : on neutralise la masse linéaire du rail
    # (défense en profondeur ; l'UI passe déjà rail_kgm=0).
    if is_suspendu:
        rail_kgm = 0.0
    # NOTE : Appuis "Olsen" — pas de surcharge masse/prix pour l'instant
    #        (FACT_AFAIRE neutralisé, à réactiver plus tard si besoin).

    I_req, lc_desc, forces_norm, delta_unit, trav_critique, M_max_Nmm = compute_load_cases(
        float(rv_kN), float(carriage_mm),
        float(rv2_kN) if nbre_pont==2 else 0, float(carriage2_mm) if nbre_pont==2 else 0,
        float(space_mm) if nbre_pont==2 else 0,
        float(support_spacing_mm),     # ← longueur d'UNE span = entraxe entre 2 appuis successifs
        nbre_pont,
        nbre_appuis=nbre_appuis,
    )

    (taille, masse_lin, inertie_chosen, Wel_cm3,
     b_mm, tf_mm, sigma_MPa, gov) = _select_beam(beam_type, I_req, M_max_Nmm)

    if taille is None:
        # Aucune poutre du catalogue ne satisfait flèche + contrainte
        max_size, max_I, max_masse, max_Wel, _, _ = TABLES[beam_type][-1]
        sigma_max_largest = M_max_Nmm / (max_Wel * 1000.0)
        return {
            "error": (
                f"Aucune poutre {beam_type} ne convient. "
                f"Requis : I ≥ {I_req:,.0f} cm⁴ ET σ ≤ {SIGMA_ADM_MPA:.1f} MPa "
                f"(Fy/{SAFETY_FACTOR}). "
                f"Plus grande {beam_type}{max_size} : I={max_I:,.0f} cm⁴, "
                f"σ={sigma_max_largest:.1f} MPa. "
                f"Choose a different profile, reduce the span or increase the number of supports."
            ),
            "I_req": I_req,
            "M_max_Nmm": M_max_Nmm,
            "lc_desc": lc_desc,
            "R_max_kN": round(sum(f*(1.0-p) for p,f,_ in forces_norm), 2),
            "forces_norm": forces_norm,
            "beam_type": beam_type,
            "total_length_mm": total_length_mm,
            "support_spacing_mm": support_spacing_mm,
            "nbre_appuis": nbre_appuis,
            "crane_type": crane_type,
            "is_suspendu": is_suspendu,
            "crane_span_mm": crane_span_mm,
        }

    beam_label = f"{beam_type}{taille}"

    # Deflection réelle [mm] = delta_unit / I_chosen_mm4
    I_chosen_mm4 = inertie_chosen * 10000.0
    fleche_mm = (delta_unit / I_chosen_mm4) if I_chosen_mm4 > 0 else 0.0
    fleche_admissible_mm = float(support_spacing_mm) / float(DEFLECTION_RATIO)

    # Nombre d'appuis total (sur 2 poutres parallèles) — utilisé pour
    # la boulonnerie ET la masse divers.
    # n_appuis sur 2 poutres parallèles = 2 × ceil(L/entraxe + 1)
    # (le ceil est à l'INTÉRIEUR pour garantir un nombre pair)
    n_appuis = 2 * math.ceil(L / spa + 1) if spa > 0 else 0
    n_appuis = max(N_APPUIS_MIN, n_appuis)

    # ── Masses ───────────────────────────────────────────────────────────────
    # m1 : masse des poutres (× 2 pour les 2 poutres parallèles)
    m1 = masse_lin * L / 1000 * 2
    # m2 : masse des rails
    # m2 : masse des rails = kg/m × longueur (m, arrondi sup) × 2 poutres
    m2 = float(rail_kgm) * math.ceil(L / 1000) * 2
    # m3 : masse des pièces d'acier découpées (platines d'about, éclisses, goussets)
    #      Formule géométrique fonction de h, b, tf et n_appuis (cf _compute_m_divers).
    m3 = _compute_m_divers(taille, b_mm, tf_mm, n_appuis)

    surf_m = _paint_surface(beam_type, taille)
    peinture = surf_m * PAINT_LITERS_PER_M2 * L / 1000 * 2

    cout_boulonnerie = n_appuis * PRICE_BOULONNERIE_UNIT

    # ── Renfort déversement (cornières) ──────────────────────────────────────
    # Si le taux de déversement de base dépasse 85 %, on ajoute des cornières
    # sur la semelle supérieure (4 par poutre, sur toute la longueur).
    _Iz_base = IZ_TABLES.get(beam_type, {}).get(taille, inertie_chosen * 0.04)
    # Point d'application de la charge pour le déversement :
    #   posé sur rail → charge en HAUT (semelle sup) = déstabilisant
    #   suspendu      → charge en BAS (semelle inf)  = stabilisant
    _load_pos = "bottom" if is_suspendu else "top"
    reinf = _reinforce_against_ltb(
        taille, b_mm, tf_mm, inertie_chosen, _Iz_base,
        M_max_Nmm, float(support_spacing_mm), L, _load_pos,
    )
    # Masse cornières : 2 cornières par poutre × 2 poutres. Elle est INTÉGRÉE
    # à la masse poutre (m1) → la ligne "incl. ..." ne réaffiche pas le chiffre.
    m_angles = reinf["m_angles_kg"] * 2.0 if reinf["angle"] else 0.0
    m1 += m_angles                       # cornières comptées dans Beam mass
    ltb_final = reinf["ltb"]

    a_cost = m1 * float(steel_price) * FACT_DECOUPE   # inclut les cornières
    b_cost = m2 * float(rail_price)
    c_cost = m3 * float(lasercut_price)
    d_cost = peinture * float(paint_price)
    # ── MO CR ────────────────────────────────────────────────────────────────
    # Posé    : (L_m/atelier + L_m/peinture) × 2 voies
    # Suspendu: 1,5 × n_appuis (forfait)
    # Cornières : même décomposition (atelier + peinture) × 2 voies
    L_m = (L / 1000.0)
    if is_suspendu:
        h_mo_cr = MO_HOURS_PER_SUPPORT_SUSP * float(n_appuis)
    else:
        h_mo_cr = (L_m / MO_M_PER_HOUR_BEAM_WELD
                 + L_m / MO_M_PER_HOUR_BEAM_PAINT) * 2.0
    if reinf["angle"]:
        h_mo_cr += (L_m / MO_M_PER_HOUR_ANGLE_WELD
                  + L_m / MO_M_PER_HOUR_ANGLE_PAINT) * 2.0
    cout_mo_cr = h_mo_cr * float(mo_price)
    cout_mat  = a_cost + b_cost + c_cost + d_cost + cout_boulonnerie + cout_mo_cr

    return {
        "beam_label": beam_label,
        "I_req": I_req,
        "I_chosen": inertie_chosen,
        "Wel_cm3": Wel_cm3,
        "M_max_Nmm": M_max_Nmm,
        "sigma_MPa": sigma_MPa,
        "sigma_adm_MPa": SIGMA_ADM_MPA,
        "gouverne": gov,
        "ltb": ltb_final,
        # Renfort déversement par cornières
        "ltb_reinforced": bool(reinf["angle"]),
        "angle_label": reinf["angle"],
        "ltb_base_util": reinf["base_util"],
        "Iy_reinforced": reinf["Iy_tot"],
        "Iz_reinforced": reinf["Iz_tot"],
        "m_angles": round(m_angles),
        "fleche_mm": fleche_mm,
        "fleche_admissible_mm": fleche_admissible_mm,
        "lc_desc": lc_desc,
        "R_max_kN": round(sum(f*(1.0-p) for p,f,_ in forces_norm), 2),
        "forces_norm": forces_norm,
        "trav_critique": trav_critique,
        "m1":round(m1),"a":round(a_cost),
        "m2":round(m2),"b":round(b_cost),
        "m3":round(m3),"c":round(c_cost),
        "peinture":round(peinture,1),"d":round(d_cost),
        "n_appuis":n_appuis,"cout_boulonnerie":cout_boulonnerie,
        "h_mo_cr": round(h_mo_cr, 1),
        "cout_mo_cr": round(cout_mo_cr),
        "cout_mat":round(cout_mat),
        "rail_label":rail_label,
        "rail_short": _short_rail(rail_label),
        "client":client,"project":project,"username":username,
        "date":date.today().strftime("%d/%m/%Y"),
        "beam_type":beam_type,"total_length_mm":total_length_mm,
        "support_spacing_mm":support_spacing_mm,"appui_type":appui_type,"nbre_pont":nbre_pont,
        "nbre_appuis": nbre_appuis,
        "crane_type": crane_type,
        "is_suspendu": is_suspendu,
        "crane_span_mm": crane_span_mm,
        # Inputs (pour résumé PDF cohérent — distincts des résultats)
        "rv_kN": float(rv_kN) if rv_kN else 0.0,
        "rv2_kN": float(rv2_kN) if (nbre_pont == 2 and rv2_kN) else 0.0,
        "carriage_mm": float(carriage_mm) if carriage_mm else 0.0,
        "carriage2_mm": float(carriage2_mm) if (nbre_pont == 2 and carriage2_mm) else 0.0,
        "space_btw_mm": float(space_mm) if (nbre_pont == 2 and space_mm) else 0.0,
    }


def _recompute_costs(r, steel_price, rail_price, lasercut_price, paint_price,
                     mo_price=DEFAULT_MO_PRICE):
    """
    Recalcule UNIQUEMENT les coûts dans un résultat existant, sans toucher
    aux masses, à la poutre choisie, à la flèche, au moment, etc.
    Utilisé quand l'utilisateur ajuste un prix après un calcul : pas besoin
    de relancer le solver complet.

    Modifie `r` en place et le retourne.
    """
    if not r or r.get("error"):
        return r

    # Récupérer les masses déjà calculées (stockées arrondies dans r)
    m1       = float(r.get("m1", 0))
    m2       = float(r.get("m2", 0))
    m3       = float(r.get("m3", 0))
    peinture = float(r.get("peinture", 0))
    cout_boulonnerie = float(r.get("cout_boulonnerie", 0))

    # Recalculer les coûts avec les nouveaux prix.
    # NB : m1 inclut déjà la masse des cornières de renfort → pas de coût séparé.
    a_cost = m1 * float(steel_price) * FACT_DECOUPE
    b_cost = m2 * float(rail_price)
    c_cost = m3 * float(lasercut_price)
    d_cost = peinture * float(paint_price)
    # MO CR : même logique que compute_railway (posé/suspendu + ×2 voies +
    # bonus cornières si renfort déversement).
    L_mm = float(r.get("total_length_mm", 0))
    L_m  = (L_mm / 1000.0)
    n_app = float(r.get("n_appuis", 2))
    if r.get("is_suspendu"):
        h_mo_cr = MO_HOURS_PER_SUPPORT_SUSP * n_app
    else:
        h_mo_cr = (L_m / MO_M_PER_HOUR_BEAM_WELD
                 + L_m / MO_M_PER_HOUR_BEAM_PAINT) * 2.0
    if r.get("ltb_reinforced"):
        h_mo_cr += (L_m / MO_M_PER_HOUR_ANGLE_WELD
                  + L_m / MO_M_PER_HOUR_ANGLE_PAINT) * 2.0
    cout_mo_cr = h_mo_cr * float(mo_price)
    cout_mat = a_cost + b_cost + c_cost + d_cost + cout_boulonnerie + cout_mo_cr

    # Mettre à jour les clés de coûts dans le résultat
    r["a"] = round(a_cost)
    r["b"] = round(b_cost)
    r["c"] = round(c_cost)
    r["d"] = round(d_cost)
    r["h_mo_cr"]   = round(h_mo_cr, 1)
    r["cout_mo_cr"] = round(cout_mo_cr)
    r["cout_mat"] = round(cout_mat)
    # Mémoriser les prix utilisés (pour le résumé PDF)
    r["price_steel"]    = float(steel_price)
    r["price_rail"]     = float(rail_price)
    r["price_lasercut"] = float(lasercut_price)
    r["price_paint"]    = float(paint_price)
    r["price_mo"]       = float(mo_price)
    return r


# ─────────────────────────────────────────────────────────────────────────────
#  SVG DIAGRAM
# ─────────────────────────────────────────────────────────────────────────────
def _make_svg(forces_norm, beam_label, portee_mm, nbre_appuis=2, travee_critique=1):
    """
    forces_norm = [(pos_norm, F_kN, pont_idx), ...].
    pos_norm ∈ [0, 1] sur la longueur totale (1 span si 2 appuis, 2 spans si 3 appuis).
    portee_mm = longueur totale affichée (= support_spacing_mm si 2 appuis, = 2×support_spacing_mm si 3 appuis).
    """
    W, H, PAD = 480, 150, 45
    BW = W - 2 * PAD
    BY = 70
    lines = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;max-width:480px;background:transparent;">']

    # ── Poutre + (option) zone hachurée de la critical span
    if nbre_appuis == 3:
        # Surligner la critical span
        x_mid = PAD + BW // 2
        if travee_critique == 1:
            x_zone_start, x_zone_end = PAD, x_mid
        else:
            x_zone_start, x_zone_end = x_mid, PAD + BW
        lines.append(
            f'<rect x="{x_zone_start}" y="{BY-12}" width="{x_zone_end - x_zone_start}" height="24" '
            f'fill="#FDAE1B" fill-opacity="0.08" stroke="none"/>'
        )

    # Poutre
    lines.append(f'<rect x="{PAD}" y="{BY-5}" width="{BW}" height="10" '
                 f'rx="2" fill="#2a4a6b" stroke="#4a7fa0" stroke-width="1.5"/>')
    # Label de la poutre : pour 3 appuis, on le centre au milieu de la 1ère
    # span (zone libre, pas masquée par le triangle central).
    # Pour 2 appuis, il reste au centre de la span.
    if nbre_appuis == 3:
        _label_x = PAD + BW // 4   # milieu de la 1ère span
    else:
        _label_x = W // 2
    lines.append(f'<text x="{_label_x}" y="{BY+20}" text-anchor="middle" '
                 f'fill="#a8c4d4" font-size="10" '
                 f'font-family="Barlow,sans-serif">{beam_label}</text>')

    # ── Appuis triangulaires : 2 aux extrémités + 1 central si 3 appuis
    appui_cx_list = [PAD, PAD+BW]
    if nbre_appuis == 3:
        appui_cx_list.insert(1, PAD + BW // 2)
    for cx in appui_cx_list:
        pts = f"{cx},{BY+5} {cx-12},{BY+24} {cx+12},{BY+24}"
        lines.append(f'<polygon points="{pts}" fill="#FDAE1B" stroke="#0A1E32" stroke-width="1"/>')
        lines.append(f'<line x1="{cx-14}" y1="{BY+26}" x2="{cx+14}" y2="{BY+26}" '
                     f'stroke="#FDAE1B" stroke-width="2"/>')

    # ── Cotes : pour 3 appuis on cote chaque span
    if nbre_appuis == 3:
        x_mid = PAD + BW // 2
        for x_start, x_end in [(PAD, x_mid), (x_mid, PAD+BW)]:
            lines.append(f'<line x1="{x_start}" y1="{BY+38}" x2="{x_end}" y2="{BY+38}" '
                         f'stroke="#555" stroke-width="1" stroke-dasharray="3,3"/>')
        lines.append(f'<text x="{PAD + BW//4}" y="{BY+50}" text-anchor="middle" '
                     f'fill="#777" font-size="9" font-family="Barlow,sans-serif">'
                     f'{portee_mm/2:.0f} mm</text>')
        lines.append(f'<text x="{PAD + 3*BW//4}" y="{BY+50}" text-anchor="middle" '
                     f'fill="#777" font-size="9" font-family="Barlow,sans-serif">'
                     f'{portee_mm/2:.0f} mm</text>')
    else:
        lines.append(f'<line x1="{PAD}" y1="{BY+38}" x2="{PAD+BW}" y2="{BY+38}" '
                     f'stroke="#555" stroke-width="1" stroke-dasharray="3,3"/>')
        lines.append(f'<text x="{W//2}" y="{BY+50}" text-anchor="middle" '
                     f'fill="#777" font-size="9" font-family="Barlow,sans-serif">'
                     f'{portee_mm:.0f} mm</text>')

    # Deflections : 1 couleur par pont (galets du même pont = même couleur)
    pont_idxs_used = sorted({(f[2] if len(f) >= 3 else 0) for f in forces_norm})
    for idx in pont_idxs_used:
        col = SVG_COLORS_PONT[idx % len(SVG_COLORS_PONT)]
        aid = f"a_pont_{idx}"
        lines.append(f'<defs><marker id="{aid}" markerWidth="6" markerHeight="6" '
                     f'refX="3" refY="3" orient="auto">'
                     f'<path d="M0,0 L6,3 L0,6 Z" fill="{col}"/></marker></defs>')

    for f in forces_norm:
        pos = f[0]
        F   = f[1]
        idx = f[2] if len(f) >= 3 else 0
        col = SVG_COLORS_PONT[idx % len(SVG_COLORS_PONT)]
        aid = f"a_pont_{idx}"
        x = PAD + int(pos * BW)
        lines.append(f'<line x1="{x}" y1="{BY-35}" x2="{x}" y2="{BY-7}" '
                     f'stroke="{col}" stroke-width="2" marker-end="url(#{aid})"/>')
        lines.append(f'<text x="{x}" y="{BY-38}" text-anchor="middle" '
                     f'fill="{col}" font-size="9" font-family="Barlow,sans-serif">'
                     f'{F:.0f}kN</text>')
    lines.append('</svg>')
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
#  REAL PDF via ReportLab
# ─────────────────────────────────────────────────────────────────────────────
def _make_pdf_bytes(r):
    """Generate a real PDF bytes object using ReportLab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.colors import HexColor, white, black, grey
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
    import io
    from io import BytesIO

    DARK   = HexColor("#0A1E32")
    AMBER  = HexColor("#FDAE1B")
    LIGHT  = HexColor("#f4f6f9")
    GREY   = HexColor("#555555")
    RED    = HexColor("#e05c5c")

    buf = io.BytesIO()

    VERSION_STR = "V2.0w07l"   # synced with app.py

    def _footer_canvas(canvas, doc):
        """Draw footer on every page."""
        canvas.saveState()
        W, H = A4
        # Dark bar at bottom
        canvas.setFillColor(HexColor("#0A1E32"))
        canvas.rect(0, 0, W, 18*mm, fill=1, stroke=0)
        # Amber top border of footer
        canvas.setStrokeColor(HexColor("#FDAE1B"))
        canvas.setLineWidth(1.5)
        canvas.line(0, 18*mm, W, 18*mm)
        # Version left
        canvas.setFillColor(HexColor("#aaaaaa"))
        canvas.setFont("Helvetica", 7)
        canvas.drawString(20*mm, 7*mm, VERSION_STR)
        # Copyright center
        canvas.setFillColor(white)
        canvas.drawCentredString(W/2, 7*mm, "© Floow | All rights reserved")
        # Page number right
        canvas.drawRightString(W - 20*mm, 7*mm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=15*mm, bottomMargin=25*mm)

    styles = getSampleStyleSheet()
    normal = ParagraphStyle("n", parent=styles["Normal"], fontSize=9, leading=13)
    bold   = ParagraphStyle("b", parent=normal, fontName="Helvetica-Bold")
    title  = ParagraphStyle("t", parent=styles["Normal"], fontName="Helvetica-Bold",
                             fontSize=14, textColor=white, alignment=TA_CENTER)
    meta   = ParagraphStyle("m", parent=styles["Normal"], fontSize=8,
                             textColor=AMBER, alignment=TA_RIGHT)
    section= ParagraphStyle("s", parent=styles["Normal"], fontName="Helvetica-Bold",
                             fontSize=10, textColor=DARK)

    story = []

    # ── Header banner ────────────────────────────────────────────────────────
    hdr_data = [[
        Paragraph("OLSEN — Crane Runway", title),
        Paragraph(
            f"{r.get('client') or '—'} / {r.get('project') or '—'}<br/>"
            f"{r.get('username') or '—'}<br/>"
            f"{r.get('date','')}", meta)
    ]]
    hdr_table = Table(hdr_data, colWidths=[120*mm, 50*mm])
    hdr_table.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,-1), DARK),
        ("LINEBELOW",  (0,0), (-1,-1), 3, AMBER),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING", (0,0), (-1,-1), 8),
        ("BOTTOMPADDING",(0,0),(-1,-1), 8),
        ("LEFTPADDING", (0,0),(0,-1), 10),
    ]))
    story.append(hdr_table)
    story.append(Spacer(1, 6*mm))

    # ── Inputs summary (INPUTS uniquement — le profilé est un résultat) ──────
    tag_items = [
        ("Crane type", r.get("crane_type", "Posé")),
        ("Beam family", r.get("beam_type", "")),
    ]
    if not r.get("is_suspendu"):
        tag_items.append(("Rail", r.get("rail_short") or r.get("rail_label", "")))
    tag_items += [
        ("Length", f"{r.get('total_length_mm',0):.0f} mm"),
        ("Spacing", f"{r.get('support_spacing_mm',0):.0f} mm"),
        ("Supports/beam", f"{r.get('nbre_appuis',2)}"),
        ("Support type", r.get("appui_type", "")),
    ]
    if r.get("appui_type") == "Olsen":
        tag_items.append(("Column", r.get("col_type", "")))
        tag_items.append(("Config", r.get("col_config", "")))
        if r.get("rail_height_mm"):
            tag_items.append(("Rail height", f"{r.get('rail_height_mm',0):.0f} mm"))
        if r.get("is_suspendu") and r.get("crane_span_mm"):
            tag_items.append(("Crane span", f"{r.get('crane_span_mm',0):.0f} mm"))
    # Données pont(s)
    _nq = int(r.get("crane_qty", r.get("nbre_pont", 1)) or 1)
    tag_items.append(("Cranes", f"{_nq}"))
    if r.get("rv_kN"):
        tag_items.append(("Rv wheel", f"{r.get('rv_kN',0):.0f} kN"))
    if r.get("carriage_mm"):
        tag_items.append(("Carriage", f"{r.get('carriage_mm',0):.0f} mm"))
    if r.get("appui_type") == "Olsen":
        _h1 = [r.get("HT3_kN", 0), r.get("HS_kN", 0), r.get("HL_kN", 0)]
        if any(_h1):
            tag_items.append(("HT3/HS/HL", "/".join(f"{float(x):.0f}" for x in _h1) + " kN"))
    if _nq == 2:
        if r.get("rv2_kN"):
            tag_items.append(("Rv wheel 2", f"{r.get('rv2_kN',0):.0f} kN"))
        if r.get("carriage2_mm"):
            tag_items.append(("Carriage 2", f"{r.get('carriage2_mm',0):.0f} mm"))
        if r.get("space_btw_mm"):
            tag_items.append(("Space between", f"{r.get('space_btw_mm',0):.0f} mm"))
        if r.get("appui_type") == "Olsen":
            _h2 = [r.get("HT3_2_kN", 0), r.get("HS_2_kN", 0), r.get("HL_2_kN", 0)]
            if any(_h2):
                tag_items.append(("HT3/HS/HL 2", "/".join(f"{float(x):.0f}" for x in _h2) + " kN"))
    # Prix (toujours affichés pour tracer la config de calcul complète)
    tag_items.append(("Steel price", f"{r.get('price_steel', 0):.2f} €/kg"))
    if not r.get("is_suspendu"):
        tag_items.append(("Rail price", f"{r.get('price_rail', 0):.2f} €/kg"))
    tag_items.append(("Lasercut price", f"{r.get('price_lasercut', 0):.2f} €/kg"))
    tag_items.append(("Paint price", f"{r.get('price_paint', 0):.2f} €/L"))
    tag_text = "  |  ".join(f"<b>{k}:</b> {v}" for k, v in tag_items)
    tag_p = ParagraphStyle("tag", parent=normal, backColor=LIGHT,
                           borderPadding=6, leading=14)
    story.append(Paragraph(tag_text, tag_p))
    story.append(Spacer(1, 5*mm))

    # ── SVG diagram (la même image que sur la page) ──────────────────────────
    _nb_app  = int(r.get("nbre_appuis", 2))
    _trav_cr = int(r.get("trav_critique", 1))
    _span = float(r.get("support_spacing_mm", 6000))
    _portee_svg = _span if _nb_app == 2 else 2 * _span
    svg_str = _make_svg(
        r.get("forces_norm", []),
        r.get("beam_label", ""),
        _portee_svg,
        nbre_appuis=_nb_app,
        travee_critique=_trav_cr,
    )
    try:
        from svglib.svglib import svg2rlg
        drawing = svg2rlg(io.StringIO(svg_str))
        if drawing:
            drawing.width  = 160*mm
            drawing.height = 50*mm
            drawing.transform = (160*mm/480, 0, 0, 50*mm/150, 0, 0)
            story.append(drawing)
            story.append(Spacer(1, 4*mm))
    except Exception:
        pass

    # ── Results table ────────────────────────────────────────────────────────
    story.append(Paragraph("Material cost", section))
    story.append(Spacer(1, 2*mm))

    rows = [
        ["Poste", "Quantité", "Cost"],
        ["Type beam",   r.get("beam_label",""),          ""],
        ["Beam mass",  f"{r['m1']} kg",  f"{r['a']} €"],
    ]
    if r.get("ltb_reinforced"):
        rows.append([f"   incl. angle iron {r.get('angle_label','')}", "", ""])
    if not r.get("is_suspendu"):
        rows.append(["Rail mass",    f"{r['m2']} kg",  f"{r['b']} €"])
    rows += [
        ["Lasercut mass",   f"{r['m3']} kg",  f"{r['c']} €"],
        ["Paint",       f"{r['peinture']} L", f"{r['d']} €"],
        ["Bolts",    f"{r['n_appuis']} braces", f"{r['cout_boulonnerie']} €"],
        ["MO",       f"{r.get('h_mo_cr',0)} h",  f"{r.get('cout_mo_cr',0)} €"],
        ["TOTAL", "", f"{r['cout_mat']} €"],
    ]
    tbl = Table(rows, colWidths=[80*mm, 50*mm, 40*mm])
    tbl.setStyle(TableStyle([
        ("BACKGROUND",  (0,0),  (-1,0),  DARK),
        ("TEXTCOLOR",   (0,0),  (-1,0),  white),
        ("FONTNAME",    (0,0),  (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0),  (-1,-1), 9),
        ("BACKGROUND",  (0,-1), (-1,-1), DARK),
        ("TEXTCOLOR",   (0,-1), (-1,-1), AMBER),
        ("FONTNAME",    (0,-1), (-1,-1), "Helvetica-Bold"),
        ("FONTSIZE",    (0,-1), (-1,-1), 10),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [white, LIGHT]),
        ("GRID",        (0,0),  (-1,-1), 0.3, grey),
        ("TOPPADDING",  (0,0),  (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("LEFTPADDING", (0,0),  (-1,-1), 6),
        ("ALIGN",       (1,0),  (-1,-1), "CENTER"),
        ("ALIGN",       (2,0),  (-1,-1), "RIGHT"),
    ]))
    story.append(tbl)

    # ── Colonnes Olsen (si présentes) ────────────────────────────────────────
    col = r.get("col_result")
    if col:
        story.append(Spacer(1, 5*mm))
        story.append(HRFlowable(width="100%", thickness=1, color=AMBER))
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph("Colonnes Olsen", section))
        story.append(Spacer(1, 2*mm))

        # SVG colonne
        col_svg_str = _make_col_svg(col)
        try:
            from svglib.svglib import svg2rlg
            col_drawing = svg2rlg(io.StringIO(col_svg_str))
            if col_drawing:
                col_drawing.width  = 70*mm
                col_drawing.height = 55*mm
                col_drawing.transform = (70*mm/320, 0, 0, 55*mm/210, 0, 0)
                story.append(col_drawing)
                story.append(Spacer(1, 2*mm))
        except Exception:
            pass

        # Caption flèche + utilisation
        _fc_ok = col.get("fleche_ok", False)
        _fb_ok = col.get("flamb_ok",  False)
        story.append(Paragraph(
            f"Deflection: <b>{col.get('delta_mm',0):.2f} mm</b>"
            f" / h/{DEFLECTION_COL_RATIO} = {col.get('f_adm_mm',0):.1f} mm"
            f"  |  λ̄ = {col.get('lam_barre',0):.3f}"
            f"  |  Util. <b>{col.get('util_tot',0):.1%}</b>",
            normal
        ))
        story.append(Spacer(1, 3*mm))

        col_rows = [
            ["Poste", "Quantité", "Cost"],
            ["Type colonne",       col.get("col_label","—"),             ""],
            ["Column mass",     f"{col.get('m_col',0)} kg",           f"{col.get('a_col',0)} €"],
            *([("   incl. bracing 70×70×6", "", "")] if col.get("m_corniere",0) > 0 else []),
            ["Lasercut col.",f"{col.get('m_div_col',0)} kg",       f"{col.get('c_col',0)} €"],
            ["Paint col.",    f"{col.get('peinture_col',0)} L",     f"{col.get('d_col',0)} €"],
            ["Bolts col.", f"{col.get('n_cols_total',0)} col.",  f"{col.get('boul_col',0)} €"],
            ["MO col.",    f"{col.get('h_mo_col',0)} h",          f"{col.get('cout_mo_col',0)} €"],
            ["TOTAL columns",     "",                                    f"{col.get('cout_col',0)} €"],
        ]
        col_tbl = Table(col_rows, colWidths=[80*mm, 50*mm, 40*mm])
        col_tbl.setStyle(TableStyle([
            ("BACKGROUND",     (0,0),  (-1,0),  DARK),
            ("TEXTCOLOR",      (0,0),  (-1,0),  white),
            ("FONTNAME",       (0,0),  (-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",       (0,0),  (-1,-1), 9),
            ("BACKGROUND",     (0,-1), (-1,-1), DARK),
            ("TEXTCOLOR",      (0,-1), (-1,-1), AMBER),
            ("FONTNAME",       (0,-1), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE",       (0,-1), (-1,-1), 10),
            ("ROWBACKGROUNDS", (0,1),  (-1,-2), [white, LIGHT]),
            ("GRID",           (0,0),  (-1,-1), 0.3, grey),
            ("TOPPADDING",     (0,0),  (-1,-1), 4),
            ("BOTTOMPADDING",  (0,0),  (-1,-1), 4),
            ("LEFTPADDING",    (0,0),  (-1,-1), 6),
            ("ALIGN",          (1,0),  (-1,-1), "CENTER"),
            ("ALIGN",          (2,0),  (-1,-1), "RIGHT"),
        ]))
        story.append(col_tbl)

        # ── Cross beam (si présente) ─────────────────────────────────────────
        _xb = r.get("extra_beam")
        if _xb and not _xb.get("error"):
            story.append(Spacer(1, 5*mm))
            story.append(HRFlowable(width="100%", thickness=1, color=AMBER))
            story.append(Spacer(1, 3*mm))
            story.append(Paragraph("Cross beam", section))
            story.append(Spacer(1, 2*mm))

            _xltb = _xb.get("ltb", {})
            story.append(Paragraph(
                f"Length: <b>{_xb.get('L_beam_mm',0)} mm</b>"
                f"  |  Deflection: <b>{_xb.get('delta_mm',0):.2f} mm</b>"
                f" / L/{DEFLECTION_CROSS_RATIO} = {_xb.get('f_adm_mm',0):.2f} mm"
                f"  |  &#963;: <b>{_xb.get('sigma_MPa',0):.1f} MPa</b>"
                f" / {_xb.get('sigma_adm_MPa',0):.1f} MPa"
                f"  |  LTB: &#967;=<b>{_xltb.get('χ_LT', _xltb.get('chi_LT',1.0)):.2f}</b>"
                f" — {_xltb.get('util',0.0):.0%}",
                normal
            ))
            story.append(Spacer(1, 3*mm))

            xb_rows = [
                ["Poste", "Quantité", "Cost"],
                ["Type",            _xb.get("beam_label","—"),            ""],
                ["Cross beam mass", f"{_xb.get('m_beam',0)} kg",          f"{_xb.get('a_beam',0)} €"],
                ["Lasercut",        f"{_xb.get('m_div',0)} kg",           f"{_xb.get('c_div',0)} €"],
                ["Paint",           f"{_xb.get('peinture',0)} L",         f"{_xb.get('d_paint',0)} €"],
                ["Bolts",           f"{r.get('n_appuis',0)} braces",      f"{_xb.get('boul',0)} €"],
                ["MO",              f"{_xb.get('h_mo_xb',0)} h",          f"{_xb.get('cout_mo_xb',0)} €"],
                ["TOTAL cross beam", "",                                  f"{_xb.get('cout_cross',0)} €"],
            ]
            xb_tbl = Table(xb_rows, colWidths=[80*mm, 50*mm, 40*mm])
            xb_tbl.setStyle(TableStyle([
                ("BACKGROUND",     (0,0),  (-1,0),  DARK),
                ("TEXTCOLOR",      (0,0),  (-1,0),  white),
                ("FONTNAME",       (0,0),  (-1,0),  "Helvetica-Bold"),
                ("FONTSIZE",       (0,0),  (-1,-1), 9),
                ("BACKGROUND",     (0,-1), (-1,-1), DARK),
                ("TEXTCOLOR",      (0,-1), (-1,-1), AMBER),
                ("FONTNAME",       (0,-1), (-1,-1), "Helvetica-Bold"),
                ("FONTSIZE",       (0,-1), (-1,-1), 10),
                ("ROWBACKGROUNDS", (0,1),  (-1,-2), [white, LIGHT]),
                ("GRID",           (0,0),  (-1,-1), 0.3, grey),
                ("TOPPADDING",     (0,0),  (-1,-1), 4),
                ("BOTTOMPADDING",  (0,0),  (-1,-1), 4),
                ("LEFTPADDING",    (0,0),  (-1,-1), 6),
                ("ALIGN",          (1,0),  (-1,-1), "CENTER"),
                ("ALIGN",          (2,0),  (-1,-1), "RIGHT"),
            ]))
            story.append(xb_tbl)

        # ── Résumé des coûts ────────────────────────────────────────────────
        story.append(Spacer(1, 5*mm))
        story.append(HRFlowable(width="100%", thickness=1, color=AMBER))
        story.append(Spacer(1, 3*mm))
        story.append(Paragraph("Cost summary", section))
        story.append(Spacer(1, 2*mm))

        _xb_ok2  = bool(_xb and not _xb.get("error"))
        _xb_a2   = _xb.get("a_beam", 0)  if _xb_ok2 else 0
        _xb_c2   = _xb.get("c_div", 0)   if _xb_ok2 else 0
        _xb_d2   = _xb.get("d_paint", 0) if _xb_ok2 else 0
        _xb_b2   = _xb.get("boul", 0)    if _xb_ok2 else 0
        _xb_mo2  = _xb.get("cout_mo_xb",0) if _xb_ok2 else 0
        _steel_tot   = r.get("a",0) + r.get("b",0) + col.get("a_col",0) + _xb_a2
        _div_tot     = r.get("c",0) + col.get("c_col",0) + _xb_c2
        _peinture_tot= r.get("d",0) + col.get("d_col",0) + _xb_d2
        _boul_tot    = r.get("cout_boulonnerie",0) + col.get("boul_col",0) + _xb_b2
        _mo_tot      = r.get("cout_mo_cr",0) + col.get("cout_mo_col",0) + _xb_mo2
        _grand       = _steel_tot + _div_tot + _peinture_tot + _boul_tot + _mo_tot

        res_rows = [
            ["Poste", "Cost"],
            ["Steel",    f"{_steel_tot} €"],
            ["Lasercut", f"{_div_tot} €"],
            ["Paint",    f"{_peinture_tot} €"],
            ["Bolts",    f"{_boul_tot} €"],
            ["MO",       f"{_mo_tot} €"],
            ["GRAND TOTAL",   f"{_grand} €"],
        ]
        res_tbl = Table(res_rows, colWidths=[110*mm, 60*mm])
        res_tbl.setStyle(TableStyle([
            ("BACKGROUND",     (0,0),  (-1,0),  DARK),
            ("TEXTCOLOR",      (0,0),  (-1,0),  white),
            ("FONTNAME",       (0,0),  (-1,0),  "Helvetica-Bold"),
            ("FONTSIZE",       (0,0),  (-1,-1), 9),
            ("BACKGROUND",     (0,-1), (-1,-1), DARK),
            ("TEXTCOLOR",      (0,-1), (-1,-1), AMBER),
            ("FONTNAME",       (0,-1), (-1,-1), "Helvetica-Bold"),
            ("FONTSIZE",       (0,-1), (-1,-1), 11),
            ("ROWBACKGROUNDS", (0,1),  (-1,-2), [white, LIGHT]),
            ("GRID",           (0,0),  (-1,-1), 0.3, grey),
            ("TOPPADDING",     (0,0),  (-1,-1), 5),
            ("BOTTOMPADDING",  (0,0),  (-1,-1), 5),
            ("LEFTPADDING",    (0,0),  (-1,-1), 6),
            ("ALIGN",          (1,0),  (-1,-1), "RIGHT"),
        ]))
        story.append(res_tbl)

    doc.build(story, onFirstPage=_footer_canvas, onLaterPages=_footer_canvas)
    buf.seek(0)
    return buf.read()


# ─────────────────────────────────────────────────────────────────────────────
#  TECHNICAL EXTRACT — fiche client + visuel 3D interactif (Three.js)
#
#  Génère un fichier HTML autonome (aucune dépendance externe sauf le CDN
#  Three.js) contenant :
#    • un en-tête Floow/Olsen
#    • un visuel 3D complet et navigable de la structure : 2 poutres de roulement
#      parallèles, rails, appuis (colonnes encastrées ou appuis client), cross
#      beams (poutres de suspension) et le pont roulant schématisé.
#    • un tableau de synthèse technique destiné au client (sections, portées,
#      flèches, contraintes, hauteurs).
#
#  Le HTML est totalement self-contained : il s'ouvre dans n'importe quel
#  navigateur et peut être envoyé tel quel au client.
# ─────────────────────────────────────────────────────────────────────────────
from datetime import date


def _make_tech_extract_html(r):
    """Construit le contenu HTML (str) de l'extrait technique client + 3D.

    Visuel 3D détaillé et proportionnel :
      • Profilés en I réels (âme + 2 ailes) pour poutres, colonnes, cross beams.
      • Pont POSÉ : 2 voies de roulement sur colonnes, rails dessus, pont qui
        roule sur les rails via sommiers (end carriages).
      • Pont SUSPENDU : cross beams de tête de colonne à tête de colonne ;
        voies de roulement suspendues SOUS les cross beams, à ~500 mm des
        colonnes ; pont suspendu sous les voies avec sommiers.
      • Contreventement en X au milieu si config « Bracing », ou double colonne
        (twin) au milieu si config « Twin column ».
      • Encastrements (platines + goussets) au pied des colonnes.
    """

    col   = r.get("col_result") or {}
    xb    = r.get("extra_beam") or {}
    xb_ok = bool(xb and not xb.get("error"))

    is_susp    = bool(r.get("is_suspendu"))
    col_config = r.get("col_config") or ""          # "Bracing" | "Twin column" | ""
    has_brace  = (col_config == "Bracing") and bool(col)
    has_twin   = (col_config == "Twin column") and bool(col)

    # ── Géométrie principale (mm) ────────────────────────────────────────────
    L_tot   = float(r.get("total_length_mm", 6000) or 6000)
    spacing = float(r.get("support_spacing_mm", 6000) or 6000)
    crane_span = float(r.get("crane_span_mm", 0) or 0)

    if crane_span > 0:
        rail_gap = crane_span
    elif xb_ok and xb.get("crane_span_mm"):
        rail_gap = float(xb.get("crane_span_mm"))
    else:
        rail_gap = max(2000.0, L_tot * 0.35)

    # Hauteur de RAIL (sol → dessus du rail) — h_total_mm côté colonne.
    h_rail = float(col.get("h_total_mm", 0) or col.get("h_col_mm", 0) or 0)
    if h_rail <= 0:
        h_rail = max(2500.0, rail_gap * 0.6)

    # Sections (hauteur du profilé) pour donner du volume — proportions réelles.
    def _sec_h(label, fallback):
        digits = "".join(ch for ch in str(label) if ch.isdigit())
        return float(digits) if digits else float(fallback)

    beam_h = _sec_h(r.get("beam_label", "HEA200"), 200)
    col_h  = _sec_h(col.get("col_label", "HEB200"), 200) if col else 200.0
    xb_h   = _sec_h(xb.get("beam_label", "HEA160"), 160) if xb_ok else 160.0

    rail_short = r.get("rail_short") or r.get("rail_label") or ""

    # Positions des appuis le long de la poutre (mm depuis 0).
    n_sup = max(2, int(r.get("n_appuis", 4)) // 2)
    if n_sup == 1:
        support_x = [L_tot / 2.0]
    else:
        step = L_tot / (n_sup - 1)
        support_x = [round(i * step) for i in range(n_sup)]

    # Débord des voies de roulement (suspendu) : ~500 mm en dehors de l'appui.
    susp_overhang = 500.0
    L_xb = float(xb.get("L_beam_mm", rail_gap + 2 * susp_overhang)) if xb_ok else rail_gap + 2 * susp_overhang

    # Ponts : nombre, empattement des sommiers (= carriage length), espacement.
    nbre_pont = int(r.get("nbre_pont", 1) or 1)
    carriage1 = float(r.get("carriage_mm", 0) or 0)
    carriage2 = float(r.get("carriage2_mm", 0) or 0)
    space_btw = float(r.get("space_btw_mm", 0) or 0)
    if carriage1 <= 0:
        carriage1 = max(1500.0, rail_gap * 0.25)   # fallback raisonnable
    if nbre_pont == 2 and carriage2 <= 0:
        carriage2 = carriage1
    if nbre_pont == 2 and space_btw <= 0:
        space_btw = max(carriage1 * 1.5, rail_gap * 0.3)

    # Réactions au galet (kN) — pilotent la hauteur de la poutre du pont.
    rv1 = float(r.get("rv_kN", 0) or 0)
    rv2 = float(r.get("rv2_kN", 0) or 0) if nbre_pont == 2 else 0.0

    geo = {
        "L_tot": L_tot, "rail_gap": rail_gap, "h_rail": h_rail,
        "beam_h": beam_h, "col_h": col_h, "xb_h": xb_h,
        "support_x": support_x, "crane_span": crane_span if crane_span > 0 else rail_gap,
        "is_suspendu": is_susp, "has_columns": bool(col),
        # Cross beams UNIQUEMENT pour un pont suspendu AVEC portique Olsen
        # (les voies sont suspendues sous les cross beams). En posé OU en
        # Customer (pas de colonnes Olsen), pas de cross beam.
        "has_crossbeam": is_susp and bool(col), "has_brace": has_brace, "has_twin": has_twin,
        "susp_overhang": susp_overhang, "L_xb": L_xb,
        "nbre_pont": nbre_pont, "carriage1": carriage1, "carriage2": carriage2,
        "space_btw": space_btw, "rv1": rv1, "rv2": rv2,
        "ltb_reinforced": bool(r.get("ltb_reinforced")),
        "angle_label": r.get("angle_label") or "",
        "beam_label": r.get("beam_label", ""), "col_label": col.get("col_label", "") if col else "",
        "xb_label": xb.get("beam_label", "") if xb_ok else "",
        "rail_short": rail_short, "appui_type": r.get("appui_type", ""),
    }
    geo_json = json.dumps(geo)

    # ── Tableau de synthèse ──────────────────────────────────────────────────
    def _row(label, value):
        return f"<tr><td class='lbl'>{label}</td><td class='val'>{value}</td></tr>"

    spec_rows = [_row("Profilé poutre de roulement", r.get("beam_label", "—"))]
    if not is_susp:
        spec_rows.append(_row("Rail", rail_short or "—"))
    if r.get("ltb_reinforced"):
        spec_rows.append(_row("Renfort déversement",
                              f"Cornières {r.get('angle_label','')}"))
    spec_rows += [
        _row("Type de pont", r.get("crane_type", "—")),
        _row("Longueur totale", f"{L_tot:.0f} mm"),
        _row("Entraxe appuis", f"{spacing:.0f} mm"),
    ]
    if crane_span > 0:
        spec_rows.append(_row("Portée du pont (entre rails)", f"{crane_span:.0f} mm"))

    _f, _fad = float(r.get("fleche_mm", 0) or 0), float(r.get("fleche_admissible_mm", 0) or 0)
    _sig, _sad = float(r.get("sigma_MPa", 0) or 0), float(r.get("sigma_adm_MPa", 0) or 0)
    _fok = "✔" if _fad and _f <= _fad else "⚠"
    _sok = "✔" if _sad and _sig <= _sad else "⚠"
    spec_rows.append(_row("Flèche poutre", f"{_f:.2f} mm / {_fad:.2f} mm  {_fok}"))
    spec_rows.append(_row("Contrainte poutre", f"{_sig:.1f} MPa / {_sad:.1f} MPa  {_sok}"))

    col_rows = []
    if col:
        col_rows.append(_row("Profilé colonne", col.get("col_label", "—")))
        if col_config:
            col_rows.append(_row("Configuration", col_config))
        col_rows.append(_row("Hauteur rail", f"{col.get('h_total_mm', 0):.0f} mm"))
        _cf, _cfa = float(col.get("delta_mm", 0) or 0), float(col.get("f_adm_mm", 0) or 0)
        _cfok = "✔" if _cfa and _cf <= _cfa else "⚠"
        col_rows.append(_row("Flèche colonne", f"{_cf:.2f} mm / {_cfa:.2f} mm  {_cfok}"))
        col_rows.append(_row("Taux d'utilisation", f"{float(col.get('util_tot', 0) or 0):.0%}"))

    xb_rows = []
    if xb_ok:
        xb_rows.append(_row("Profilé cross beam", xb.get("beam_label", "—")))
        xb_rows.append(_row("Longueur cross beam", f"{xb.get('L_beam_mm', 0):.0f} mm"))
        _xf, _xfa = float(xb.get("delta_mm", 0) or 0), float(xb.get("f_adm_mm", 0) or 0)
        _xfok = "✔" if _xfa and _xf <= _xfa else "⚠"
        xb_rows.append(_row("Flèche cross beam", f"{_xf:.2f} mm / {_xfa:.2f} mm  {_xfok}"))

    spec_html, col_html, xb_html = "".join(spec_rows), "".join(col_rows), "".join(xb_rows)
    client  = r.get("client") or "—"
    project = r.get("project") or "—"
    today   = r.get("date") or date.today().strftime("%d/%m/%Y")

    col_section = f"<h3>Colonnes Olsen</h3><table class='spec'>{col_html}</table>" if col_rows else ""
    xb_section  = f"<h3>Cross beam</h3><table class='spec'>{xb_html}</table>" if xb_rows else ""

    html = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Extrait technique — __PROJECT__</title>
<style>
  :root{--dark:#0A1E32;--amber:#FDAE1B;--light:#f4f6f9;}
  *{box-sizing:border-box;}
  body{margin:0;font-family:'Segoe UI',Arial,sans-serif;color:#1a2733;background:#fff;}
  header{background:var(--dark);color:#fff;padding:18px 28px;border-bottom:4px solid var(--amber);
         display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;}
  header h1{margin:0;font-size:1.25rem;letter-spacing:.5px;}
  header .meta{font-size:.8rem;color:var(--amber);text-align:right;line-height:1.5;}
  .wrap{max-width:1100px;margin:0 auto;padding:24px 28px 60px;}
  .notice{margin:14px 0 0;padding:12px 16px;border-left:4px solid var(--amber);
           background:#fff7e6;color:#5a4a25;font-size:.84rem;border-radius:4px;}
  #view3d{width:100%;height:560px;background:radial-gradient(circle at 50% 35%,#f3f7fb,#cdd9e6);
          border:1px solid #cdd8e3;border-radius:8px;position:relative;overflow:hidden;margin-top:14px;}
  #view3d .hint{position:absolute;bottom:10px;left:12px;font-size:.72rem;color:#5a7088;
                background:rgba(255,255,255,.78);padding:4px 9px;border-radius:5px;}
  .legend{display:flex;gap:18px;flex-wrap:wrap;margin:14px 0 4px;font-size:.78rem;color:#42566a;}
  .legend span{display:inline-flex;align-items:center;gap:6px;}
  .legend i{width:14px;height:14px;border-radius:3px;display:inline-block;}
  h2{color:var(--dark);border-bottom:2px solid var(--amber);padding-bottom:5px;margin-top:34px;font-size:1.1rem;}
  h3{color:var(--dark);margin:22px 0 8px;font-size:.98rem;}
  table.spec{width:100%;border-collapse:collapse;font-size:.86rem;margin-bottom:8px;}
  table.spec td{padding:7px 10px;border:1px solid #e1e8ef;}
  table.spec td.lbl{background:var(--light);color:#33485c;width:55%;}
  table.spec td.val{font-weight:600;color:var(--dark);}
  footer{background:var(--dark);color:#cfd9e3;font-size:.72rem;text-align:center;padding:12px;
         border-top:3px solid var(--amber);}
  .btns{margin:18px 0;}
  .btns button{background:var(--dark);color:#fff;border:none;padding:8px 14px;border-radius:6px;
               cursor:pointer;font-size:.8rem;margin-right:8px;}
  .btns button:hover{background:#143150;}
  @media print{.btns,#view3d .hint{display:none;}}
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
</head>
<body>
<header>
  <h1>OLSEN — Crane Runway · Extrait technique</h1>
  <div class="meta">Client : __CLIENT__<br/>Projet : __PROJECT__<br/>Date : __DATE__</div>
</header>

<div class="wrap">
  <div class="notice">
    <b>Prédimensionnement indicatif.</b> Les sections, hauteurs et quantités présentées
    constituent une estimation préliminaire destinée au chiffrage. Elles sont susceptibles
    d'évoluer après commande et restent soumises à l'étude d'exécution, aux notes de calcul
    définitives et à la validation finale par le bureau d'études. Document non contractuel.
  </div>

  <h2>Synthèse technique</h2>
  <table class="spec">__SPEC_ROWS__</table>
  __COL_SECTION__
  __XB_SECTION__

  <h2>Visualisation 3D de la structure</h2>
  <div id="view3d"><div class="hint">🖱️ Glisser pour pivoter · molette pour zoomer · clic droit pour déplacer</div></div>
  <div class="legend">
    <span><i style="background:#3a5e82"></i>Voies de roulement</span>
    <span><i style="background:#c0392b"></i>Rails</span>
    <span><i style="background:#8a97a6"></i>Colonnes</span>
    <span><i style="background:#5b6b7a"></i>Cross beam</span>
    <span><i style="background:#E1A100"></i>Pont roulant</span>
    <span><i style="background:#27ae60"></i>Contreventement</span>
    <span><i style="background:#6b7783"></i>Cornières renfort</span>
  </div>
  <div class="btns">
    <button onclick="window.print()">🖨️ Imprimer / PDF</button>
    <button onclick="resetView()">↺ Réinitialiser la vue</button>
  </div>
</div>

<footer>© Floow | All rights reserved — Prédimensionnement, document non contractuel soumis à révision</footer>

<script>
const GEO = __GEO_JSON__;
let scene, camera, renderer, root;
let isDown=false, btn=0, lx=0, ly=0;
let rotX=0.52, rotY=0.72, dist, target={x:0,y:0,z:0};

function init(){
  const host=document.getElementById('view3d');
  const W=host.clientWidth,H=host.clientHeight;
  scene=new THREE.Scene(); scene.background=null;
  camera=new THREE.PerspectiveCamera(42,W/H,1,400000);
  renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});
  renderer.setSize(W,H); renderer.setPixelRatio(window.devicePixelRatio);
  host.appendChild(renderer.domElement);
  scene.add(new THREE.AmbientLight(0xffffff,0.62));
  const d1=new THREE.DirectionalLight(0xffffff,0.55); d1.position.set(0.6,1.4,0.9); scene.add(d1);
  const d2=new THREE.DirectionalLight(0xffffff,0.35); d2.position.set(-0.8,0.7,-0.6); scene.add(d2);
  const d3=new THREE.DirectionalLight(0xffffff,0.18); d3.position.set(0,-1,0.3); scene.add(d3);
  root=new THREE.Group(); scene.add(root);
  build();
  // Centrer la structure verticalement
  root.position.y = -GEO.h_rail*0.42;
  dist=Math.max(GEO.L_tot,GEO.rail_gap,GEO.h_rail)*2.0;
  animate(); bindControls(host);
}

function mat(c,opts){return new THREE.MeshLambertMaterial(Object.assign({color:c},opts||{}));}

// Profilé en I. axis :
//   'x' → longueur le long de X, âme verticale (poutres de roulement, cross beams le long de X)
//   'z' → longueur le long de Z, âme verticale (cross beams transversales)
//   'y' → COLONNE verticale, longueur le long de Y. webDir = orientation de l'âme :
//         'z' → âme le long de X, ailes le long de Z (axe fort perpendiculaire à la CR)
//         'x' → âme le long de Z, ailes le long de X (axe fort le long de la CR)
function ibeam(L,h,axis,cx,cy,cz,color,webDir){
  const g=new THREE.Group();
  const b=h*0.62;                 // largeur ailes
  const tf=Math.max(h*0.08,5);    // épaisseur aile
  const tw=Math.max(h*0.05,3);    // épaisseur âme
  const m=mat(color);
  if(axis==='y'){
    // Colonne verticale en I, hauteur L selon Y.
    //   wd==='x' : ailes larges selon X, âme (plat) le long de Z
    //              → axe fort // X, axe faible // Z
    //   wd==='z' : ailes larges selon Z, âme (plat) le long de X
    //              → axe fort // Z, axe faible // X
    const wd = webDir||'x';
    let web, fl1, fl2;
    if(wd==='x'){
      // âme : mince en X, pleine hauteur Y, profonde en Z
      web=new THREE.Mesh(new THREE.BoxGeometry(tw, L, b), m);
      // ailes : larges en X, pleine hauteur Y, minces en Z, décalées en Z
      fl1=new THREE.Mesh(new THREE.BoxGeometry(b, L, tf), m);
      fl2=new THREE.Mesh(new THREE.BoxGeometry(b, L, tf), m);
      fl1.position.z=(b-tf)/2; fl2.position.z=-(b-tf)/2;
    }else{
      // âme : mince en Z, pleine hauteur Y, profonde en X
      web=new THREE.Mesh(new THREE.BoxGeometry(b, L, tw), m);
      // ailes : larges en Z, pleine hauteur Y, minces en X, décalées en X
      fl1=new THREE.Mesh(new THREE.BoxGeometry(tf, L, b), m);
      fl2=new THREE.Mesh(new THREE.BoxGeometry(tf, L, b), m);
      fl1.position.x=(b-tf)/2; fl2.position.x=-(b-tf)/2;
    }
    g.add(web,fl1,fl2);
    g.position.set(cx,cy,cz);
    return g;
  }
  // Poutres horizontales : âme verticale, ailes horizontales.
  const web=new THREE.Mesh(new THREE.BoxGeometry(L,h-2*tf,tw),m);
  const fl1=new THREE.Mesh(new THREE.BoxGeometry(L,tf,b),m);
  const fl2=new THREE.Mesh(new THREE.BoxGeometry(L,tf,b),m);
  fl1.position.y=(h-tf)/2; fl2.position.y=-(h-tf)/2;
  g.add(web,fl1,fl2);
  if(axis==='z') g.rotation.y=Math.PI/2;
  g.position.set(cx,cy,cz);
  return g;
}

function box(w,h,d,color){return new THREE.Mesh(new THREE.BoxGeometry(w,h,d),mat(color));}

// Diagonale (contreventement) entre 2 points, section carrée s.
function strut(x1,y1,z1,x2,y2,z2,s,color){
  const dx=x2-x1,dy=y2-y1,dz=z2-z1;
  const len=Math.sqrt(dx*dx+dy*dy+dz*dz);
  const m=new THREE.Mesh(new THREE.BoxGeometry(s,len,s),mat(color));
  m.position.set((x1+x2)/2,(y1+y2)/2,(z1+z2)/2);
  const dir=new THREE.Vector3(dx,dy,dz).normalize();
  const q=new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0,1,0),dir);
  m.quaternion.copy(q);
  return m;
}

// Embase boulonnée : platine + 4 goussets + 4 boulons.
function footing(x,z,w){
  const g=new THREE.Group();
  const plate=box(w*1.7,26,w*1.7,0x42505c); plate.position.y=13; g.add(plate);
  const gus=0x515f6b;
  [[1,0],[-1,0],[0,1],[0,-1]].forEach(([sx,sz])=>{
    const gg=box(sx?w*0.45:8, w*0.85, sz?w*0.45:8, gus);
    gg.position.set(sx*w*0.5, w*0.42+26, sz*w*0.5);
    g.add(gg);
  });
  // boulons d'ancrage
  [[1,1],[1,-1],[-1,1],[-1,-1]].forEach(([sx,sz])=>{
    const bolt=box(w*0.12,40,w*0.12,0x2b343d);
    bolt.position.set(sx*w*0.72,20,sz*w*0.72); g.add(bolt);
  });
  g.position.set(x,0,z);
  return g;
}

// ─── PONT ROULANT ABUS, jaune RAL 1007, poutre CAISSON (box girder) ─────────
// span     = portée entre les 2 voies (le long de Z).
// carriage = empattement des galets (le long de X).
// xc       = position du pont le long de la voie (X).
// rv       = réaction au galet [kN] → pilote la HAUTEUR de la poutre caisson.
// yRailTop = niveau du DESSUS DU RAIL (posé) ou DESSOUS de la voie (suspendu).
//            Les galets viennent en contact sur ce niveau.
function abusCrane(span, yRailTop, bh, hung, carriage, xc, rv){
  const ABUS=0xE1A100, ABUS_D=0xC98E00;  // RAL 1007 + ton plus foncé (ombres)
  const DARK=0x33373b, STEEL=0x7a8794, BLACK=0x222426;
  const g=new THREE.Group();

  // Hauteur de la poutre du pont, proportionnelle à la réaction au galet.
  // Référence : ~140 mm pour 30 kN, +3 mm par kN supplémentaire.
  const rvEff = (rv && rv>0) ? rv : 30;
  let gh = 140 + (rvEff-30)*3;            // mm
  gh = Math.max(bh*0.35, Math.min(gh, bh*1.6));
  const gw = gh*0.75;                      // largeur caisson (plus marquée)

  const wheelR = bh*0.26;
  const somH   = bh*0.5;                   // hauteur caisson sommier (réduite)
  const somL   = Math.max(carriage*1.1, bh*3.2);
  const somW   = bh*0.6;
  const wheelBase = Math.max(carriage, somL*0.7);

  // ── Empilement vertical à partir du contact galet/rail ───────────────────
  // Posé    : galet SUR le rail → centre galet = yRailTop + wheelR.
  //           sommier au-dessus des galets, le CAISSON PLONGE DANS LE SOMMIER
  //           → bas du caisson aligné avec bas du sommier.
  // Suspendu: galet sous la voie, miroir vertical.
  const yWheel = hung ? (yRailTop - wheelR) : (yRailTop + wheelR);
  const ySom   = hung ? (yWheel - somH*0.5) : (yWheel + somH*0.5);
  // Bas du caisson = bas du sommier (au lieu de "posé sur le sommier")
  //   posé    : centre caisson = ySom - somH/2 + gh/2
  //   suspendu: caisson DÉPASSE de 300 mm au-dessus du haut du sommier
  //             centre caisson = ySom + somH/2 + 300 - gh/2
  const SUSP_OVER = 300;                          // dépassement [mm]
  const yGird  = hung ? (ySom + somH*0.5 + SUSP_OVER - gh*0.5)
                      : (ySom - somH*0.5 + gh*0.5);

  // ── Sommiers + galets (un par voie) ──────────────────────────────────────
  [-1,1].forEach(s=>{
    const z=s*span/2;
    const som=box(somL, somH, somW, ABUS);
    som.position.set(0, ySom, z); g.add(som);
    // chape de roulement
    const sole=box(somL, somH*0.16, somW*1.12, ABUS_D);
    sole.position.set(0, ySom - somH*0.5, z); g.add(sole);
    // galets en contact sur le rail
    [-1,1].forEach(d=>{
      const wheel=new THREE.Mesh(new THREE.CylinderGeometry(wheelR,wheelR,somW*0.8,20),mat(BLACK));
      wheel.rotation.x=Math.PI/2;
      wheel.position.set(d*wheelBase*0.5, yWheel, z); g.add(wheel);
    });
    // tampons de butée
    [-1,1].forEach(d=>{
      const buf=box(bh*0.3,bh*0.45,bh*0.45,DARK);
      buf.position.set(d*somL*0.5, ySom, z); g.add(buf);
    });
  });

  // ── Poutre CAISSON ──────────────────────────────────────────────────────
  //
  // POSÉ : caisson TRAPÉZOÏDAL vu de FACE (regard parallèle à la voie ;
  //   on voit la portée du pont devant nous). Section dans plan X-Y :
  //
  //          ┌─────────┐                ← haut    (y = +gh/2, x = ±(gw/2-ch45))
  //         ╱           ╲
  //        ╱             ╲              ← chanfrein 45° (x grandit en descendant)
  //       │               │             ← arête latérale verticale
  //       │               │
  //       │               │
  //       └───────────────┘             ← bas     (y = -gh/2, x = ±gw/2)
  //
  //   Longueur = span (d'un sommier à l'autre, le caisson s'arrête aux sommiers).
  //   ch45 = partie de caisson qui dépasse le sommier (= gh − somH).
  //
  // SUSPENDU : caisson rectangulaire, sans chanfreins. Dépassement 300 mm
  //   de chaque côté → longueur = span + 600.
  if (!hung) {
    // ── POSÉ : trapèze vu de face ─────────────────────────────────────────
    // Clamp : le chanfrein occupe au plus 30 % de la demi-largeur du caisson,
    // pour garder une "casquette" plate sur le dessus (sinon trapèze trop pointu).
    const ch45 = Math.max(0, Math.min(gh - somH, gw * 0.3));
    const halfZ = span * 0.5;
    const yTop = +gh/2, yBot = -gh/2;
    const xTopL = -(gw/2 - ch45), xTopR = +(gw/2 - ch45);  // arête sup
    const xBotL = -gw/2,          xBotR = +gw/2;           // arête inf
    // 8 sommets : 4 sur la face avant (z = +halfZ), 4 sur la face arrière (z = -halfZ).
    // Face avant (vue de face, devant l'observateur) :
    //   2 ─── 3          ← top (avant)
    //  ╱       ╲
    // 0 ─────── 1        ← bottom (avant)
    // Face arrière (derrière) :
    //   6 ─── 7
    //  ╱       ╲
    // 4 ─────── 5
    const verts = new Float32Array([
      // avant (z = +halfZ)
      xBotL, yBot, +halfZ,   // 0
      xBotR, yBot, +halfZ,   // 1
      xTopL, yTop, +halfZ,   // 2
      xTopR, yTop, +halfZ,   // 3
      // arrière (z = -halfZ)
      xBotL, yBot, -halfZ,   // 4
      xBotR, yBot, -halfZ,   // 5
      xTopL, yTop, -halfZ,   // 6
      xTopR, yTop, -halfZ,   // 7
    ]);
    const indices = [
      // Face avant (z = +halfZ) : trapèze 0-1-3-2
      0, 1, 3,   0, 3, 2,
      // Face arrière (z = -halfZ) : trapèze 5-4-6-7 (sens inverse pour normale arrière)
      5, 4, 6,   5, 6, 7,
      // Face basse (y = yBot)
      0, 4, 5,   0, 5, 1,
      // Face haute (y = yTop)
      2, 3, 7,   2, 7, 6,
      // Chanfrein gauche (entre 0 et 2 à l'avant, entre 4 et 6 à l'arrière)
      0, 2, 6,   0, 6, 4,
      // Chanfrein droit (entre 1 et 3 à l'avant, entre 5 et 7 à l'arrière)
      1, 5, 7,   1, 7, 3,
    ];
    const caissonGeo = new THREE.BufferGeometry();
    caissonGeo.setAttribute('position', new THREE.BufferAttribute(verts, 3));
    caissonGeo.setIndex(indices);
    caissonGeo.computeVertexNormals();
    const caisson = new THREE.Mesh(caissonGeo, mat(ABUS));
    caisson.position.set(0, yGird, 0); g.add(caisson);
  } else {
    // ── SUSPENDU : box rectangulaire avec dépassement 300 mm de chaque côté
    const caisson = box(gw, gh, span + 2*SUSP_OVER, ABUS);
    caisson.position.set(0, yGird, 0); g.add(caisson);
  }

  // ── Sticker ABUS — texture canvas (texte bleu sur fond blanc) ────────────
  // Trois.js ne dessine pas de texte directement : on génère une CanvasTexture
  // côté navigateur, qu'on plaque ensuite comme texture sur 2 plans.
  const stickW = Math.min(span*0.18, gh*4);
  const stickH = gh*0.32;
  (function(){
    const cnv = document.createElement('canvas');
    cnv.width = 256; cnv.height = 96;
    const ctx = cnv.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, 256, 96);
    ctx.fillStyle = '#1f6cb5';
    ctx.font = 'bold 70px Arial,sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('ABUS', 128, 50);
    const tex = new THREE.CanvasTexture(cnv);
    const stickMat = new THREE.MeshBasicMaterial({map: tex});
    [+1,-1].forEach(sgn=>{
      const plane = new THREE.Mesh(
        new THREE.PlaneGeometry(stickW, stickH), stickMat);
      plane.position.set(sgn*(gw*0.5 + 1), yGird, 0);
      plane.rotation.y = sgn > 0 ? -Math.PI/2 : Math.PI/2;
      g.add(plane);
    });
  })();

  // ── Palan : trolley sombre + tambour gris + câbles + moufle ──────────────
  // Suspendu SOUS la semelle inférieure du caisson (ancien modèle qui te
  // plaisait, mais positionné dessous au lieu de dessus).
  const yTrolley = yGird - gh*0.5 - bh*0.3;       // sous le caisson
  const trolley = box(gw*1.6, bh*0.6, bh*1.4, DARK);
  trolley.position.set(0, yTrolley, 0); g.add(trolley);
  const drum = new THREE.Mesh(
    new THREE.CylinderGeometry(bh*0.3, bh*0.3, bh*1.0, 18),
    mat(0x3c3f43));
  drum.rotation.x = Math.PI/2;
  drum.position.set(0, yTrolley, 0); g.add(drum);

  // ── Câbles + moufle + crochet qui pendent sous le palan ──────────────────
  const dropTop = yTrolley - bh*0.3;
  const dropLen = Math.max(bh*2.5, gh*1.8);
  [-1,1].forEach(d=>{
    const cable = box(bh*0.05, dropLen, bh*0.05, BLACK);
    cable.position.set(d*bh*0.18, dropTop - dropLen/2, 0); g.add(cable);
  });
  const blockY = dropTop - dropLen;
  const block = box(bh*0.6, bh*0.55, bh*0.45, DARK);
  block.position.set(0, blockY, 0); g.add(block);
  const hook = new THREE.Mesh(
    new THREE.TorusGeometry(bh*0.17, bh*0.055, 8, 16, Math.PI*1.5),
    mat(STEEL));
  hook.position.set(0, blockY - bh*0.38, 0);
  hook.rotation.z = Math.PI*0.1;
  g.add(hook);

  g.position.x = xc||0;
  return g;
}

function build(){
  const L=GEO.L_tot, gap=GEO.rail_gap, hR=GEO.h_rail;
  const bh=GEO.beam_h, ch=GEO.col_h, xh=GEO.xb_h;
  const x0=-L/2, zL=-gap/2, zR=gap/2;
  const C_BEAM=0x3a5e82, C_RAIL=0xc0392b, C_COL=0x8a97a6, C_XB=0x5b6b7a,
        C_BRACE=0x27ae60;

  // Niveau supérieur de la structure.
  //  • Pont POSÉ : les voies de roulement sont encastrées en tête de colonne
  //    (aucune cross beam transversale, comme une voie de roulement classique).
  //  • Pont SUSPENDU : les cross beams sont en tête de colonne, et les voies
  //    sont suspendues juste dessous.
  const railH=Math.max(bh*0.22,18), railW=Math.max(bh*0.16,12);
  const yColTop = hR - (GEO.is_suspendu ? 0 : railH);
  // Axe des voies de roulement
  const yAxis = GEO.has_crossbeam
      ? (yColTop - xh - bh/2)           // suspendu : CR plaquée sous la cross beam
      : (yColTop - bh/2);               // posé : CR posée sur la tête de colonne
  const yXBaxis = yColTop - xh/2;       // axe cross beam (suspendu uniquement)
  // Hauteur des colonnes :
  //  • Posé     : la colonne s'arrête SOUS le CR (le CR repose dessus).
  //  • Suspendu : la colonne monte jusqu'en tête (la cross beam y est posée).
  const hCol = GEO.is_suspendu ? yColTop : (yAxis - bh/2);

  // Position des voies de roulement (CR) selon Z :
  //  • Posé      : CR encastrées en tête de colonne → au droit des colonnes.
  //  • Suspendu  : CR suspendues sous les cross beams, décalées de 500 mm vers
  //                l'INTÉRIEUR des portiques.
  const runIn = GEO.is_suspendu ? GEO.susp_overhang : 0;   // 500 mm en suspendu
  const zRunL = zL + runIn, zRunR = zR - runIn;
  const runGap = zRunR - zRunL;                            // entraxe réel des CR

  GEO.support_x.forEach(sx=>{
    const x=x0+sx;
    [zL,zR].forEach(z=>{
      if(GEO.has_columns){
        // Colonnes pivotées de 90° sur leur axe : âme le long de X, ailes le
        // long de Z (axe fort dans le sens de la CR).
        root.add(ibeam(hCol, ch, 'y', x, hCol/2, z, C_COL, 'x'));
        root.add(footing(x,z,ch));
      } else {
        const c=box(bh*0.9,hR*0.16,bh*0.9,C_COL); c.position.set(x,hR*0.08,z); root.add(c);
      }
    });
    // Cross beam transversale UNIQUEMENT en pont suspendu (de colonne à
    // colonne). Raccourcie de ch/2 de chaque côté (s'arrête au nu intérieur
    // des colonnes) pour un rendu plus propre.
    if(GEO.has_crossbeam){
      root.add(ibeam(Math.max(gap - ch, gap*0.5), xh, 'z', x, yXBaxis, 0, C_XB));
      // CR plaquée directement contre le bas de la cross beam → pas de suspente.
    }
  });

  // ─── Voies de roulement (le long de X), aux positions zRun ────────────────
  [zRunL,zRunR].forEach(z=>{
    root.add(ibeam(L, bh, 'x', 0, yAxis, z, C_BEAM));

    // UPN d'about en bout de CR (rendu 3D uniquement) : un profilé en U plaqué
    // contre chaque extrémité de la voie. Dépasse vers le HAUT en posé,
    // vers le BAS en suspendu. Largeur adaptée à la hauteur du CR.
    {
      const upnH = bh;                 // hauteur du U (largeur d'âme) = hauteur CR
      const upnW = bh*0.42;            // profondeur des ailes du U (le long de X)
      const upnT = Math.max(bh*0.07,6);// épaisseur tôle
      const over = bh*0.95;            // dépassement au-delà de la semelle
      // Centre vertical du U : décalé vers le haut (posé) ou le bas (suspendu)
      const yU = GEO.is_suspendu ? (yAxis - over*0.5) : (yAxis + over*0.5);
      const hU = bh + over;            // hauteur totale couvrant la CR + dépassement
      [-1,1].forEach(sx=>{             // sx=-1 → bout -X ; sx=+1 → bout +X
        const xEnd = sx*L/2;
        // âme du U (face PLEINE) plaquée contre le bout du CR
        const web=box(upnT, hU, upnH, C_BEAM);
        web.position.set(xEnd + sx*upnT*0.5, yU, z); root.add(web);
        // 2 ailes du U : retours vers l'EXTÉRIEUR (au-delà du bout du CR)
        [-1,1].forEach(sz=>{
          const fl=box(upnW, hU, upnT, C_BEAM);
          fl.position.set(xEnd + sx*upnW*0.5, yU, z + sz*(upnH*0.5 - upnT*0.5));
          root.add(fl);
        });
      });
    }

    // Cornières de renfort déversement, coiffant le bord de la semelle sup :
    //  • aile HORIZONTALE dans la continuité de la semelle (au même niveau,
    //    prolongée vers l'extérieur) ;
    //  • aile VERTICALE rabattue vers le BAS le long du chant de la semelle.
    if(GEO.ltb_reinforced){
      const flHalf = bh*0.31;             // demi-largeur de semelle (b≈0.62h)
      const leg = bh*0.34;                // longueur d'aile cornière (visuel)
      const th  = Math.max(bh*0.05, 5);   // épaisseur cornière
      const yTopFl = yAxis + bh/2;        // niveau du dessus de semelle
      [-1,1].forEach(sw=>{                // sw=-1 → bord -Z ; sw=+1 → bord +Z
        const edge = z + sw*flHalf;       // bord de la semelle de CETTE voie
        // aile HORIZONTALE : prolonge la semelle vers l'extérieur, même niveau
        const fh=box(L, th, leg, 0x5f6b77);
        fh.position.set(0, yTopFl - th*0.5, edge + sw*leg*0.5); root.add(fh);
        // aile VERTICALE : à l'EXTRÉMITÉ extérieure de l'aile horizontale
        // (le plus loin possible de la poutre), descend vers le bas.
        const fv=box(L, leg, th, 0x5f6b77);
        fv.position.set(0, yTopFl - leg*0.5, edge + sw*(leg - th*0.5)); root.add(fv);
      });
    }
  });

  // Niveau de roulement du pont (dessus rail en posé, sous voie en suspendu)
  const railTopY = GEO.is_suspendu ? (yAxis - bh/2) : (yAxis + bh/2 + railH);
  if(!GEO.is_suspendu){
    // Rails posés sur les CR
    [zRunL,zRunR].forEach(z=>{
      const rl=box(L,railH,railW,C_RAIL);
      rl.position.set(0, yAxis + bh/2 + railH/2, z); root.add(rl);
    });
  }

  // ── Pont(s) ABUS : 1 ou 2 selon le config ────────────────────────────────
  const nP = GEO.nbre_pont||1;
  if(nP===2){
    const c1=GEO.carriage1, c2=GEO.carriage2||GEO.carriage1;
    // Longueur réelle de sommier (doit suivre abusCrane : max(carriage*1.25, bh*4))
    const somL1=Math.max(c1*1.1, bh*3.2), somL2=Math.max(c2*1.1, bh*3.2);
    // space_btw = écart LIBRE entre les extrémités des sommiers des 2 ponts.
    // Entraxe = space_btw + demi-sommier de chaque pont.
    let gapEnds = GEO.space_btw||0;
    if(gapEnds < bh*0.5) gapEnds = Math.max(c1,c2)*0.6;   // garde-fou anti-chevauchement
    const entraxe = gapEnds + somL1/2 + somL2/2;
    root.add(abusCrane(runGap, railTopY, bh, GEO.is_suspendu, c1, -entraxe/2, GEO.rv1));
    root.add(abusCrane(runGap, railTopY, bh, GEO.is_suspendu, c2, +entraxe/2, GEO.rv2||GEO.rv1));
  } else {
    root.add(abusCrane(runGap, railTopY, bh, GEO.is_suspendu, GEO.carriage1, 0, GEO.rv1));
  }

  buildMidConfig(x0,L,gap,hCol,ch,C_COL,C_BRACE,zL,zR,yAxis,bh);

  // Plan horizontal (grille) au niveau de l'embase des colonnes (y=0 = base).
  const grid=new THREE.GridHelper(Math.max(L,gap)*1.7,28,0xaebccb,0xd2dce6);
  grid.position.y=0;
  root.add(grid);
}

// Contreventement en X dans le PLAN LONGITUDINAL (le long des CR), entre deux
// colonnes successives d'une MÊME file (plans verticaux z=zL et z=zR) — config
// « Bracing ». OU double colonne (twin) au portique central — « Twin column ».
function buildMidConfig(x0,L,gap,hcol,ch,C_COL,C_BRACE,zL,zR,yAxis,bh){
  if(!GEO.has_columns) return;
  const xs=GEO.support_x;
  // travée centrale : couple d'appuis encadrant le milieu
  let xa=xs[0], xb=xs[xs.length-1];
  for(let i=0;i<xs.length-1;i++){ if(xs[i]<=L/2 && xs[i+1]>=L/2){ xa=xs[i]; xb=xs[i+1]; break; } }
  const X1=x0+xa, X2=x0+xb;

  if(GEO.has_brace){
    // X longitudinal dans chaque file (z=zL et z=zR), entre les 2 colonnes
    // de la travée centrale.
    const s=ch*0.24, yb=40, yt=hcol-40;
    [zL,zR].forEach(z=>{
      root.add(strut(X1,yb,z, X2,yt,z, s,C_BRACE));
      root.add(strut(X2,yb,z, X1,yt,z, s,C_BRACE));
    });
  } else if(GEO.has_twin){
    // Double colonne : 2e colonne à 500 mm d'une colonne CENTRALE réelle.
    // On choisit la colonne la plus proche du milieu (celle de gauche si
    // nombre pair), et la 2e colonne est décalée de 500 mm vers le centre.
    let xc=xs[0], best=1e12;
    for(let i=0;i<xs.length;i++){
      const d=Math.abs(xs[i]-L/2);
      if(d<best-1){ best=d; xc=xs[i]; }   // -1 : tolérance, garde la gauche si égalité
    }
    const xm=x0+xc;                    // colonne centrale (réelle)
    // 2e colonne décalée de 500 mm, du côté du centre de l'ouvrage
    const dir=(xc < L/2) ? +1 : -1;
    const off=500*dir;
    [zL,zR].forEach(z=>{
      root.add(ibeam(hcol,ch,'y',xm+off,hcol/2,z,C_COL,'x'));
      root.add(footing(xm+off,z,ch));
      // Liens horizontaux répartis sur la hauteur, entre les 2 axes de colonnes
      const nTie=4;
      for(let k=1;k<=nTie;k++){
        const yk=hcol*k/(nTie+1);
        const tie=box(Math.abs(off),ch*0.28,ch*0.28,C_COL);
        tie.position.set(xm+off/2,yk,z); root.add(tie);
      }
    });
  }
}

function updateCamera(){
  const cx=target.x+dist*Math.cos(rotX)*Math.sin(rotY);
  const cy=target.y+dist*Math.sin(rotX);
  const cz=target.z+dist*Math.cos(rotX)*Math.cos(rotY);
  camera.position.set(cx,cy,cz); camera.lookAt(target.x,target.y,target.z);
}
function animate(){requestAnimationFrame(animate); updateCamera(); renderer.render(scene,camera);}
function bindControls(host){
  const el=renderer.domElement;
  el.addEventListener('mousedown',e=>{isDown=true;btn=e.button;lx=e.clientX;ly=e.clientY;e.preventDefault();});
  window.addEventListener('mouseup',()=>isDown=false);
  window.addEventListener('mousemove',e=>{
    if(!isDown)return; const dx=e.clientX-lx,dy=e.clientY-ly; lx=e.clientX; ly=e.clientY;
    if(btn===2){const s=dist*0.0012; target.x-=dx*s*Math.cos(rotY); target.z+=dx*s*Math.sin(rotY); target.y+=dy*s;}
    else{rotY-=dx*0.008; rotX+=dy*0.008; rotX=Math.max(-1.45,Math.min(1.45,rotX));}
  });
  el.addEventListener('contextmenu',e=>e.preventDefault());
  el.addEventListener('wheel',e=>{dist*=(1+(e.deltaY>0?0.1:-0.1)); dist=Math.max(500,dist); e.preventDefault();},{passive:false});
  window.addEventListener('resize',()=>{const W=host.clientWidth,H=host.clientHeight; camera.aspect=W/H; camera.updateProjectionMatrix(); renderer.setSize(W,H);});
}
function resetView(){rotX=0.52; rotY=0.72; target={x:0,y:0,z:0}; dist=Math.max(GEO.L_tot,GEO.rail_gap,GEO.h_rail)*2.0;}
window.addEventListener('load',init);
</script>
</body>
</html>"""

    html = (html
            .replace("__GEO_JSON__", geo_json)
            .replace("__SPEC_ROWS__", spec_html)
            .replace("__COL_SECTION__", col_section)
            .replace("__XB_SECTION__", xb_section)
            .replace("__CLIENT__", str(client))
            .replace("__PROJECT__", str(project))
            .replace("__DATE__", str(today)))

    return html.encode("utf-8")



# ─────────────────────────────────────────────────────────────────────────────
#  INPUT HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _ni(label, key):
    """Integer text_input, no pre-fill."""
    if key not in ss: ss[key] = ""
    def _c():
        raw = ss.get(key,"")
        s = re.sub(r"[^0-9]","",str(raw))
        if s != str(raw): ss[key] = s
    return st.text_input(label, key=key, on_change=_c)

def _ni_select(label, key, presets):
    """
    Selectbox avec valeurs prédéfinies + saisie libre (accept_new_options).
    Filtre les chiffres uniquement sur saisie libre. Pas de valeur par défaut
    (selectbox vide au départ).
    Renvoie une chaîne (compatible avec _safe()).
    """
    options = [""] + [str(int(v)) for v in presets]

    # Sanitization : si l'utilisateur tape une valeur libre, on garde uniquement
    # les chiffres. On le fait via on_change car selectbox stocke la string.
    def _clean():
        raw = ss.get(key, "")
        if raw is None:
            ss[key] = ""
            return
        s = re.sub(r"[^0-9]", "", str(raw))
        if s != str(raw):
            ss[key] = s

    val = st.selectbox(
        label,
        options=options,
        index=0,                          # vide par défaut
        accept_new_options=True,
        key=key,
        on_change=_clean,
    )
    return val or ""

def _nf(label, key, default):
    """Float text_input, pre-filled with default (prices only)."""
    if key not in ss or ss[key] == "": ss[key] = str(default)
    def _c():
        raw = ss.get(key,"")
        s = re.sub(r"[^0-9.,]","",str(raw))
        parts = re.split(r"[.,]",s,maxsplit=1)
        s = (parts[0]+"."+parts[1]) if len(parts)==2 else parts[0]
        if s != str(raw): ss[key] = s
    return st.text_input(label, key=key, on_change=_c)

def _nf_nodef(label, key):
    """Float text_input, no pre-fill."""
    if key not in ss: ss[key] = ""
    def _c():
        raw = ss.get(key,"")
        s = re.sub(r"[^0-9.,]","",str(raw))
        parts = re.split(r"[.,]",s,maxsplit=1)
        s = (parts[0]+"."+parts[1]) if len(parts)==2 else parts[0]
        if s != str(raw): ss[key] = s
    return st.text_input(label, key=key, on_change=_c)

def _safe(val, fb=0.0):
    try: return float(str(val).replace(",",".")) if val else fb
    except: return fb


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN RENDER
# ─────────────────────────────────────────────────────────────────────────────
def render_railway_sizing_tab():
    # ── Auto-fill client at project change (AVANT le rendu du widget client) ─
    cur_proj = (ss.get("rs_project") or "").strip()
    prev_proj = ss.get("_rs_prev_project", None)
    if cur_proj != prev_proj:
        if cur_proj and cur_proj in ss.get("sale_orders_ref", {}):
            linked = ss["sale_orders_ref"][cur_proj].get("customer","")
            if linked:
                ss["rs_client"] = linked
        ss["_rs_prev_project"] = cur_proj

    # ── Read all current values for upfront validation (label ❌) ─────────────
    client_cur     = (ss.get("rs_client","") or "").strip()
    total_len_cur  = ss.get("rs_len","")
    rv_cur         = ss.get("rs_rv","")
    clen_cur       = ss.get("rs_clen","")
    sommier_cur    = ss.get("rs_support_spacing","")
    qty_cur        = ss.get("rs_qty", 1)
    rv2_cur        = ss.get("rs_rv2","")
    clen2_cur      = ss.get("rs_clen2","")
    space_cur      = ss.get("rs_space","")
    sp_cur         = ss.get("rs_sp","")
    rp_cur         = ss.get("rs_rp","")
    lcp_cur        = ss.get("rs_lcp","")
    pp_cur         = ss.get("rs_pp","")

    # ── Reset des résultats si une donnée STRUCTURELLE a changé ──────────────
    # (les prix et le client ne déclenchent pas de reset — ils peuvent être
    # ajustés sans invalider le dimensionnement)
    _structural_keys = [
        "rs_len", "rs_crane_type", "rs_btype", "rs_rail",
        "rs_rv", "rs_clen", "rs_qty",
        "rs_rv2", "rs_clen2", "rs_space",
        "rs_appui", "rs_support_spacing", "rs_nb_appuis", "rs_crane_span",
        "rs_col_type", "rs_col_config", "rs_rail_height",
        "rs_HT3", "rs_HS", "rs_HL",
        "rs_HT3_2", "rs_HS_2", "rs_HL_2",
    ]
    _price_keys = ["rs_sp", "rs_rp", "rs_lcp", "rs_pp", "rs_mop"]

    _struct_signature = tuple(str(ss.get(k, "")) for k in _structural_keys)
    _price_signature  = tuple(str(ss.get(k, "")) for k in _price_keys)

    if ss.get("rs_result") is not None:
        # 0) Schéma périmé : un résultat calculé avant l'ajout de σ/LTB sur la
        #    cross beam n'a pas la clé "sigma_MPa". On invalide pour forcer un
        #    recalcul propre (évite l'affichage "σ : 0.0 MPa / 0.0 MPa").
        _xb_cached = ss["rs_result"].get("extra_beam") if isinstance(ss.get("rs_result"), dict) else None
        if (isinstance(_xb_cached, dict) and not _xb_cached.get("error")
                and "sigma_MPa" not in _xb_cached):
            ss["rs_result"] = None
            ss["_rs_result_signature"] = None
            ss["_rs_price_signature"]  = None

    if ss.get("rs_result") is not None:
        # 1) Donnée structurelle changée → reset complet
        if ss.get("_rs_result_signature") != _struct_signature:
            ss["rs_result"] = None
            ss["_rs_result_signature"] = None
            ss["_rs_price_signature"]  = None
        # 2) Sinon, si prix changés → recalcul léger des coûts uniquement
        elif ss.get("_rs_price_signature") != _price_signature:
            r = ss["rs_result"]
            _recompute_costs(
                r,
                steel_price    = _safe(ss.get("rs_sp"),  DEFAULT_STEEL_PRICE),
                rail_price     = _safe(ss.get("rs_rp"),  DEFAULT_RAIL_PRICE),
                lasercut_price = _safe(ss.get("rs_lcp"), DEFAULT_LASERCUT_PRICE),
                paint_price    = _safe(ss.get("rs_pp"),  DEFAULT_PAINT_PRICE),
                mo_price       = _safe(ss.get("rs_mop"), DEFAULT_MO_PRICE),
            )
            ss["_rs_price_signature"] = _price_signature

    # Toutes les lignes ci-dessous ont 4 colonnes (alignement global)
    # car la ligne "Prix" a 4 colonnes.

    # ── Project / Client ─────────────────────────────────────────────────────
    hc1, hc2, hc3, hc4 = st.columns(4)
    with hc1:
        sale_orders = list(ss.get("sale_orders_ref", {}).keys())

        # Au changement de projet :
        #   - Si le projet est connu dans Odoo → on remplit avec le client lié.
        #   - Si le projet est libre (saisie manuelle non présente dans Odoo)
        #     ou vide → on vide le champ client pour que l'utilisateur le saisisse.
        def _on_project_change():
            sel = ss.get("rs_project", "") or ""
            sel = sel.strip() if isinstance(sel, str) else ""
            sale_orders_ref = ss.get("sale_orders_ref", {})
            if sel and sel in sale_orders_ref:
                linked = sale_orders_ref[sel].get("customer", "")
                ss["rs_client"] = linked or ""
            else:
                # Projet libre ou vide → clear du client
                ss["rs_client"] = ""

        project = st.selectbox(
            "Project", options=[""] + sale_orders,
            accept_new_options=True,
            key="rs_project",
            on_change=_on_project_change,
        )
        project = (project or "").strip()
    with hc2:
        _lcli = "Client ❌" if not client_cur else "Client"
        client = st.text_input(_lcli, key="rs_client", placeholder="ex : Acme SA")
    # hc3, hc4 vides

    st.divider()
    st.markdown("<div style='margin-top:1.2rem'></div>", unsafe_allow_html=True)

    # ── Titre principal après le séparateur ──────────────────────────────────
    st.markdown("### 📐 Runway")

    # ── Ligne 1 : Longueur / Crane type / Beam type / Rail type ───────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        _ll = "Total Length [mm] ❌" if not total_len_cur else "Total Length [mm]"
        total_length = _ni(_ll, "rs_len")
    with c2:
        crane_type = st.selectbox(
            "Crane type", ["Posé", "Suspendu"], index=0, key="rs_crane_type",
            help="Posé : pont sur rails (rails comptés). "
                 "Suspendu : pont suspendu sous le CR → pas de rails.",
        )
    is_suspendu = (crane_type == "Suspendu")
    with c3:
        beam_type = st.selectbox("Beam type", ["HEA","HEB","IPE"], key="rs_btype")
    with c4:
        if is_suspendu:
            # Pas de rails sur un pont suspendu → on masque la box et on neutralise
            # la masse / le prix du rail dans les calculs.
            rail_sel = ""
            rail_kgm = 0.0
        else:
            rail_sel = st.selectbox("Rail type", list(RAIL_MASS_KGM.keys()), key="rs_rail")
            rail_kgm = RAIL_MASS_KGM[rail_sel]

    # ── Ligne Appuis : Appuis / Entraxe / Nb appuis / Crane span ─────────────
    c13, c14, c15, c16 = st.columns(4)
    with c13:
        appui_type = st.selectbox("Support type", ["Customer","Olsen"], key="rs_appui")
    with c14:
        _ls = "Support spacing [mm] ❌" if not sommier_cur else "Support spacing [mm]"
        support_spacing = _ni_select(_ls, "rs_support_spacing", SPACING_PRESETS)
    with c15:
        nbre_appuis = st.selectbox(
            "Supports / beam", [2, 3], index=0, key="rs_nb_appuis",
            help="2 appuis = 1 span. 3 appuis = 2 spans égales (poutre continue).",
        )
    with c16:
        # Crane span = portée du pont entre les 2 chemins de roulement.
        # TOUJOURS visible et obligatoire : sert au dimensionnement de la cross
        # beam (suspendu) ET à la géométrie réelle du pont dans le rendu 3D
        # (posé comme suspendu).
        _lcs = "Crane span [mm] ❌" if not ss.get("rs_crane_span","") else "Crane span [mm]"
        crane_span = _ni(_lcs, "rs_crane_span")

    # ── Colonnes Olsen (si appui = Olsen) ───────────────────────────────────
    # Init hidden defaults before any widget.
    # En suspendu il n'y a pas de rail → hauteur de section de rail = 0.
    if is_suspendu:
        ss["rs_rail_section_h"] = "0"
    elif not ss.get("rs_rail_section_h",""):
        _rh_defaults = {"50×30":30,"50×50":50,"60×60":60,"40×40":40,"60×40":40}
        ss["rs_rail_section_h"] = str(_rh_defaults.get(_short_rail(rail_sel), 50))

    if appui_type == "Olsen":
        st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
        st.divider()
        st.markdown("<div style='margin-top:0.3rem'></div>", unsafe_allow_html=True)
        st.markdown("### 🏛️ Olsen Columns")
        oc1, oc2, oc3, _oc4 = st.columns(4)
        with oc1:
            col_type = st.selectbox("Column type", ["HEA","HEB","IPE"], key="rs_col_type")
        with oc2:
            col_config = st.selectbox("Configuration", ["Bracing","Twin column"],
                                      key="rs_col_config")
        with oc3:
            _lrh = "Rail height [mm] ❌" if not ss.get("rs_rail_height","") else "Rail height [mm]"
            rail_height = _ni(_lrh, "rs_rail_height")
        # Hidden fields — only in session state, no widget rendered
        rail_section_h = ss.get("rs_rail_section_h", "50")
        if "rs_Nv" not in ss: ss["rs_Nv"] = "0"
    else:
        col_type = "HEA"; col_config = "Bracing"
        rail_height = ""; rail_section_h = ""
    if "rs_Nv" not in ss: ss["rs_Nv"] = "0"
    # HT3/HS/HL declared below under pont data

    # ── Sous-titre Données Pont(s) ───────────────────────────────────────────
    st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
    st.divider()
    st.markdown("<div style='margin-top:0.3rem'></div>", unsafe_allow_html=True)
    st.markdown("### 🏗️ Crane data")

    # ── Ligne pont 1 : Rv / Carriage / Crane qty ─────────────────────────────
    c5, c6, c7, _c8 = st.columns(4)
    with c5:
        _lrv = "Rv wheel [kN] ❌" if not rv_cur else "Rv wheel [kN]"
        rv = _ni(_lrv, "rs_rv")
    with c6:
        _lcl = "Carriage length [mm] ❌" if not clen_cur else "Carriage length [mm]"
        carriage = _ni_select(_lcl, "rs_clen", CARRIAGE_PRESETS)
    with c7:
        crane_qty = st.selectbox("Crane qty", [1,2], index=0, key="rs_qty")

    # ── Charges horizontales pont 1 (si Olsen) ───────────────────────────────
    HT3 = HS = HL = "0"
    if appui_type == "Olsen":
        h1, h2, h3, _h4 = st.columns(4)
        with h1:
            _lHT3 = "HT3 trolley accel. [kN] ❌" if not ss.get("rs_HT3","") else "HT3 trolley accel. [kN]"
            HT3 = _nf_nodef(_lHT3, "rs_HT3")
        with h2:
            _lHS = "HS skewing [kN] ❌" if not ss.get("rs_HS","") else "HS skewing [kN]"
            HS  = _nf_nodef(_lHS, "rs_HS")
        with h3:
            _lHL = "HL bridge accel. [kN] ❌" if not ss.get("rs_HL","") else "HL bridge accel. [kN]"
            HL  = _nf_nodef(_lHL, "rs_HL")

    # ── Clear pont 2 keys if switched back to 1 pont ────────────────────────
    if crane_qty == 1:
        for _k in ["rs_rv2","rs_clen2","rs_space","rs_HT3_2","rs_HS_2","rs_HL_2"]:
            if _k in ss: ss[_k] = ""

    # ── Clear col_result if switched to Customer ─────────────────────────────
    if appui_type == "Customer" and ss.get("rs_result") and ss["rs_result"].get("col_result"):
        ss["rs_result"]["col_result"] = None

    # ── Ligne pont 2 (si qty = 2) ─────────────────────────────────────────────
    rv2 = carriage2 = space_btw = ""
    HT3_2 = HS_2 = HL_2 = "0"
    if crane_qty == 2:
        st.markdown("<div style='margin-top:0.5rem;border-top:1px solid #1e3a50;'></div>", unsafe_allow_html=True)
        st.markdown("<span style='font-size:0.72rem;color:#4a7fa0;letter-spacing:1px;text-transform:uppercase;font-weight:600;'>▸ Pont 2</span>", unsafe_allow_html=True)
        c9, c10, c11, _c12 = st.columns(4)
        with c9:
            _lrv2 = "Rv wheel 2 [kN] ❌" if not rv2_cur else "Rv wheel 2 [kN]"
            rv2 = _ni(_lrv2, "rs_rv2")
        with c10:
            _lcl2 = "Carriage length 2 [mm] ❌" if not clen2_cur else "Carriage length 2 [mm]"
            carriage2 = _ni_select(_lcl2, "rs_clen2", CARRIAGE_PRESETS)
        with c11:
            _lsb = "Space between [mm] ❌" if not space_cur else "Space between [mm]"
            space_btw = _ni(_lsb, "rs_space")

        # ── Charges horizontales pont 2 (si Olsen) ───────────────────────────
        if appui_type == "Olsen":
            h21, h22, h23, _h24 = st.columns(4)
            with h21:
                _lHT3_2 = "HT3 trolley accel. 2 [kN] ❌" if not ss.get("rs_HT3_2","") else "HT3 trolley accel. 2 [kN]"
                HT3_2 = _nf_nodef(_lHT3_2, "rs_HT3_2")
            with h22:
                _lHS_2 = "HS skewing 2 [kN] ❌" if not ss.get("rs_HS_2","") else "HS skewing 2 [kN]"
                HS_2  = _nf_nodef(_lHS_2, "rs_HS_2")
            with h23:
                _lHL_2 = "HL bridge accel. 2 [kN] ❌" if not ss.get("rs_HL_2","") else "HL bridge accel. 2 [kN]"
                HL_2  = _nf_nodef(_lHL_2, "rs_HL_2")

    st.divider()
    st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
    st.markdown("### 💰 Costs")
    p1, p2, p3, p4, p5 = st.columns(5)
    with p1:
        _lsp = "Steel price [€/kg] ❌" if not sp_cur else "Steel price [€/kg]"
        steel_p = _nf(_lsp, "rs_sp", DEFAULT_STEEL_PRICE)
    with p2:
        _lrp = "Rail price [€/kg] ❌" if not rp_cur else "Rail price [€/kg]"
        rail_p = _nf(_lrp, "rs_rp", DEFAULT_RAIL_PRICE)
    with p3:
        _llcp = "Lasercut price [€/kg] ❌" if not lcp_cur else "Lasercut price [€/kg]"
        lasercut_p = _nf(_llcp, "rs_lcp", DEFAULT_LASERCUT_PRICE)
    with p4:
        _lpp = "Paint price [€/L] ❌" if not pp_cur else "Paint price [€/L]"
        paint_p = _nf(_lpp, "rs_pp", DEFAULT_PAINT_PRICE)
    with p5:
        _mop_cur = ss.get("rs_mop","")
        _lmop = "MO price [€/h] ❌" if not _mop_cur else "MO price [€/h]"
        mo_p = _nf(_lmop, "rs_mop", DEFAULT_MO_PRICE)

    # ── Bouton Calculer (droite) ─────────────────────────────────────────────
    st.markdown("<div style='margin-top:2.0rem'></div>", unsafe_allow_html=True)
    _, bcol = st.columns([4,1])
    with bcol:
        calc = st.button("⚙️ Calculer", type="primary", use_container_width=True, key="rs_calc")

    if calc:
        errs = []
        if not client_cur:                          errs.append("Client requis")
        if not _safe(total_length):                 errs.append("Total Length requis")
        if not _safe(rv):                           errs.append("Rv wheel requis")
        if not _safe(carriage):                     errs.append("Carriage length requis")
        if not _safe(support_spacing):                      errs.append("Entraxe appuis requis")
        if crane_qty==2 and not _safe(rv2):         errs.append("Rv wheel 2 requis")
        if crane_qty==2 and not _safe(carriage2):   errs.append("Carriage length 2 requis")
        if crane_qty==2 and not _safe(space_btw):   errs.append("Space between requis")
        if not _safe(steel_p):                      errs.append("Steel price requis")
        if not is_suspendu and not _safe(rail_p):   errs.append("Rail price requis")
        if not _safe(lasercut_p):                   errs.append("Lasercut price requis")
        if not _safe(paint_p):                      errs.append("Paint price requis")
        if not _safe(mo_p):                         errs.append("MO price requis")
        # Crane span (portée du pont entre rails) : toujours obligatoire — sert
        # au dimensionnement (cross beam en suspendu) et au rendu 3D du pont.
        if not _safe(crane_span):
            errs.append("Crane span requis")
        # Précalcul des dimensions courantes (utilisé pour limites + cohérence)
        _tl = _safe(total_length)
        _s  = _safe(support_spacing)
        # Limites max (valeurs dans railway_constants.py — ajustables là)
        _cs = _safe(crane_span)
        _cs_max = MAX_CRANE_SPAN_MM_SUSPENDU if is_suspendu else MAX_CRANE_SPAN_MM_POSE
        if _cs and _cs > _cs_max:
            errs.append(
                f"Crane span trop grand : {_cs:.0f} mm > {_cs_max:.0f} mm "
                f"(limite {'suspendu' if is_suspendu else 'posé'})"
            )
        if _s and _s > MAX_SUPPORT_SPACING_MM:
            errs.append(
                f"Support spacing trop grand : {_s:.0f} mm > "
                f"{MAX_SUPPORT_SPACING_MM:.0f} mm"
            )
        _rh = _safe(rail_height)
        if _rh and _rh > MAX_RAIL_HEIGHT_MM:
            errs.append(
                f"Rail height trop grande : {_rh:.0f} mm > "
                f"{MAX_RAIL_HEIGHT_MM:.0f} mm"
            )
        # HT3/HS/HL : obligatoires pour chaque pont quand colonnes Olsen
        if appui_type == "Olsen":
            if not _safe(HT3): errs.append("HT3 trolley accel. requis")
            if not _safe(HS):  errs.append("HS skewing requis")
            if not _safe(HL):  errs.append("HL bridge accel. requis")
            if crane_qty == 2:
                if not _safe(HT3_2): errs.append("HT3 trolley accel. 2 requis")
                if not _safe(HS_2):  errs.append("HS skewing 2 requis")
                if not _safe(HL_2):  errs.append("HL bridge accel. 2 requis")

        # Cohérence dimensionnelle : entraxe appuis (= span) ≤ longueur totale
        if _tl and _s and _s > _tl:
            errs.append(
                f"Entraxe appuis ({_s:.0f} mm) > Total Length ({_tl:.0f} mm) : "
                f"une span ne peut pas être plus longue que le chemin de roulement."
            )
        # Pour 3 appuis, il faut au moins 2 spans sur le chemin
        if _tl and _s and int(nbre_appuis) == 3 and 2 * _s > _tl:
            errs.append(
                f"3 appuis demandés mais 2 × Entraxe ({2*_s:.0f} mm) > "
                f"Total Length ({_tl:.0f} mm) : impossible de placer 2 spans "
                f"sur le chemin de roulement."
            )

        if errs:
            for e in errs: st.error(e)
        else:
            _res = compute_railway(
                client=client or "", project=str(project) or "",
                username=ss.get("user_name",""),
                total_length_mm=_safe(total_length),
                beam_type=beam_type, rail_kgm=rail_kgm, rail_label=rail_sel,
                rv_kN=_safe(rv), carriage_mm=_safe(carriage),
                support_spacing_mm=_safe(support_spacing), nbre_pont=crane_qty,
                rv2_kN=_safe(rv2), carriage2_mm=_safe(carriage2),
                space_mm=_safe(space_btw),
                appui_type=appui_type, spacing_appuis_mm=_safe(support_spacing) or 6000,
                steel_price=_safe(steel_p, DEFAULT_STEEL_PRICE),
                rail_price=_safe(rail_p, DEFAULT_RAIL_PRICE),
                lasercut_price=_safe(lasercut_p, DEFAULT_LASERCUT_PRICE),
                paint_price=_safe(paint_p, DEFAULT_PAINT_PRICE),
                nbre_appuis=int(nbre_appuis),
                crane_type=crane_type,
                crane_span_mm=_safe(crane_span),
                mo_price=_safe(mo_p, DEFAULT_MO_PRICE),
            )
            # ── Stocker TOUS les inputs dans le résultat (résumé PDF complet) ──
            if _res is not None:
                _res["col_type"]    = col_type
                _res["col_config"]  = col_config
                _res["rail_height_mm"] = _safe(rail_height)
                _res["crane_qty"]   = crane_qty
                # Forces pont 1
                _res["HT3_kN"] = _safe(HT3); _res["HS_kN"] = _safe(HS); _res["HL_kN"] = _safe(HL)
                # Forces pont 2 (si applicable)
                _res["HT3_2_kN"] = _safe(HT3_2) if crane_qty == 2 else 0.0
                _res["HS_2_kN"]  = _safe(HS_2)  if crane_qty == 2 else 0.0
                _res["HL_2_kN"]  = _safe(HL_2)  if crane_qty == 2 else 0.0
                # Prix
                _res["price_steel"]    = _safe(steel_p,    DEFAULT_STEEL_PRICE)
                _res["price_rail"]     = _safe(rail_p,     DEFAULT_RAIL_PRICE)
                _res["price_lasercut"] = _safe(lasercut_p, DEFAULT_LASERCUT_PRICE)
                _res["price_paint"]    = _safe(paint_p,    DEFAULT_PAINT_PRICE)
                _res["price_mo"]       = _safe(mo_p,       DEFAULT_MO_PRICE)
            # ── Calcul colonnes Olsen ─────────────────────────────────────────
            if appui_type == "Olsen":
                if not _safe(rail_height):
                    st.warning("⚠️ Renseigner Rail height pour dimensionner les colonnes.")
            if appui_type == "Olsen" and _safe(rail_height) > 0:
                # Hauteur poutre CR sélectionnée
                _h_CR = float(_res.get("beam_label","HEA100")[3:]) if not _res.get("error") else 200.0
                # N_v = réaction d'appui max calculée directement depuis le cas de charge CR
                _N_v_input = float(_res.get("R_max_kN", 0.0))
                if _N_v_input == 0:
                    _N_v_input = _safe(rv) * crane_qty * 1.1   # fallback conservatif
                # Cumul charges horizontales des 2 ponts (défavorable = somme)
                _HT3 = _safe(HT3, 0) + (_safe(HT3_2, 0) if crane_qty == 2 else 0)
                _HS  = _safe(HS,  0) + (_safe(HS_2,  0) if crane_qty == 2 else 0)
                _HL  = _safe(HL,  0) + (_safe(HL_2,  0) if crane_qty == 2 else 0)
                # Pont suspendu : la charge horizontale en tête de colonne est
                # divisée par 2 (reprise répartie sur le portique suspendu).
                if is_suspendu:
                    _HT3 *= 0.5
                    _HS  *= 0.5
                    _HL  *= 0.5
                # n_appuis total = depuis le résultat CR
                _n_app = int(_res.get("n_appuis", 4)) if not _res.get("error") else 4

                # Prix (définis tôt : utilisés dès le pré-calcul cross beam)
                _sp  = _safe(steel_p,    DEFAULT_STEEL_PRICE)
                _lcp = _safe(lasercut_p, DEFAULT_LASERCUT_PRICE)
                _pp  = _safe(paint_p,    DEFAULT_PAINT_PRICE)

                # ── Pré-calcul CROSS BEAM (pont suspendu uniquement) ─────────
                # On calcule la cross beam AVANT les colonnes pour récupérer sa
                # hauteur de section : en suspendu, la hauteur de colonne =
                # h_rail + h_poutre_CR + h_cross_beam.
                _xb = None
                _crane_span = float(_res.get("crane_span_mm", 0) or 0)
                if is_suspendu and _crane_span > 0:
                    _xb = compute_cross_beam(
                        col_type        = col_type,
                        crane_span_mm   = _crane_span,
                        R_max_kN        = float(_res.get("R_max_kN", 0.0)),
                        n_appuis_total  = _n_app,
                        steel_price     = _sp,
                        lasercut_price  = _lcp,
                        paint_price     = _pp,
                        mo_price        = _safe(mo_p, DEFAULT_MO_PRICE),
                    )
                _xb_h = float(_xb.get("size", 0)) if (_xb and not _xb.get("error")) else 0.0

                _col_best, _col_all = compute_columns(
                    col_type          = col_type,
                    h_rail_mm         = _safe(rail_height),
                    h_rail_section_mm = _safe(rail_section_h) if _safe(rail_section_h) else 50.0,
                    h_poutre_CR_mm    = _h_CR,
                    HT3_kN            = _HT3,
                    HS_kN             = _HS,
                    HL_kN             = _HL,
                    N_v_kN            = _N_v_input,
                    twin_col          = (col_config == "Twin column"),
                    contrevente       = (col_config == "Bracing"),
                    n_appuis_total     = _n_app,
                    sommier_chariot_mm = _safe(carriage),
                    entraxe_col_mm     = _safe(support_spacing),
                    is_suspendu        = is_suspendu,
                    h_cross_beam_mm    = _xb_h,
                )
                # Compute column costs (using same prices as CR)
                if _col_best:
                    _n_app = int(_res.get("n_appuis", 4))
                    _n_cols_total = (_n_app + 2) if (col_config == "Twin column") else _n_app
                    _h_col_m = _col_best["h_col_mm"] / 1000.0
                    _m_col   = _col_best["masse_lin_unit"] * _h_col_m * _n_cols_total

                    # Bracing diagonals: cornière 70×70×6 (6.38 kg/m)
                    # 4 cornières per portique × n_portiques (= n_appuis/2)
                    # L_diag = sqrt(h_col² + (span/2)²)  [span = support_spacing_mm]
                    _m_corniere = 0.0
                    if col_config == "Bracing":
                        _CORNIERE_MASSE_LIN = 6.38   # kg/m — 70×70×6
                        _span_m  = _safe(support_spacing) / 1000.0   # entraxe colonnes [m]
                        _l_diag  = math.sqrt(_h_col_m**2 + _span_m**2)   # [m]
                        # 1 contreventement par côté = 2 diagonales × 2 cornières = 4 au total
                        _m_corniere  = _CORNIERE_MASSE_LIN * _l_diag * 4
                        _m_col += _m_corniere   # add to column mass
                    # Paint surface
                    _surf_col = _paint_surface(col_type, _col_best["size"])
                    _peinture_col = _surf_col * PAINT_LITERS_PER_M2 * _h_col_m * _n_cols_total
                    # Divers (platines, goussets) — même fonction que CR
                    # Divers colonne: plaque sup CR + embase + goussets
                    _h_col  = _col_best["size"]        # mm
                    _b_col  = _col_best["b_mm"]        # mm
                    _tf_col = next((r[5] for r in TABLES.get(col_type,[])
                                    if r[0]==_col_best["size"]), 10.0)
                    _tf_cap = min(_tf_col, TF_PLAQUE_SUP_CAP)
                    _rho = STEEL_DENSITY_KG_MM3

                    # Plaque supérieure de connexion au CR (b × h × tf capé)
                    _m_plaque_sup = _b_col * _h_col * _tf_cap * _rho

                    # Embase: (b+200)² × 20mm si tf<15, (h+200)² × 25mm si tf>25
                    _embase_tf   = EMBASE_TF_THIN if _tf_col < TF_THIN_LIMIT else \
                                   (EMBASE_TF_THICK if _tf_col > TF_THICK_LIMIT else EMBASE_TF_THIN)
                    _embase_side = (_b_col + 2*EMBASE_OVERHANG) if _tf_col < TF_THIN_LIMIT \
                                   else (_h_col + 2*EMBASE_OVERHANG)
                    _m_embase    = _embase_side**2 * _embase_tf * _rho

                    # Goussets d'embase: (b+overhang×2) × b × tf × 2
                    _m_goussets = (_b_col + 2*EMBASE_OVERHANG) * _b_col * _tf_cap * 2 * _rho

                    _m_div_unit = (_m_plaque_sup + _m_embase + _m_goussets) * (1 + M_DIVERS_COL_MARGIN)
                    _m_div_col  = round(_m_div_unit * _n_cols_total)
                    # Boulonnerie embase: 80€ par colonne
                    _boul_col = _n_cols_total * 80
                    # ── MO colonnes : heures + coût ─────────────────────────
                    # Base : n_cols × heures/colonne
                    # + 1 forfait bracing (si config Bracing)
                    # + 1 forfait double colonne (si config Twin column)
                    _mop = _safe(mo_p, DEFAULT_MO_PRICE)
                    _h_mo_col = _n_cols_total * MO_HOURS_PER_COLUMN
                    if col_config == "Bracing":
                        _h_mo_col += MO_HOURS_BRACING_PER_COL
                    if col_config == "Twin column":
                        _h_mo_col += MO_HOURS_DOUBLE_COL
                    _cout_mo_col = _h_mo_col * _mop
                    _col_best.update({
                        "n_cols_total":  _n_cols_total,
                        "m_corniere":    round(_m_corniere),
                        "m_col":         round(_m_col),
                        "a_col":         round(_m_col * _sp),
                        "m_div_col":     round(_m_div_col),
                        "c_col":         round(_m_div_col * _lcp),
                        "peinture_col":  round(_peinture_col, 1),
                        "d_col":         round(_peinture_col * _pp),
                        "boul_col":      _boul_col,
                        "h_mo_col":      round(_h_mo_col, 1),
                        "cout_mo_col":   round(_cout_mo_col),
                        "cout_col":      round(_m_col*_sp + _m_div_col*_lcp
                                               + _peinture_col*_pp + _boul_col
                                               + _cout_mo_col),
                    })
                _res["col_result"] = _col_best
                _res["col_config"] = col_config
                # Inputs Olsen (pour résumé PDF)
                _res["col_type"]    = col_type
                _res["rail_height_mm"] = _safe(rail_height)
                _res["HT3_kN"] = _safe(HT3); _res["HS_kN"] = _safe(HS); _res["HL_kN"] = _safe(HL)
                _res["crane_qty"] = crane_qty

                # ── CROSS BEAM (poutre de suspension du portique) ────────────
                # Déjà pré-calculée plus haut (_xb) pour la hauteur de colonne.
                # On la réutilise telle quelle en pont suspendu.
                if is_suspendu and _crane_span > 0:
                    _res["extra_beam"] = _xb
                    # Les divers de la cross beam sont comptés SÉPARÉMENT
                    # (dans le bloc Cross beam), pas fusionnés dans Misc. col.
                else:
                    _res["extra_beam"] = None
            else:
                _res["col_result"] = None
                _res["col_config"] = None
                _res["extra_beam"] = None
                _res["crane_qty"] = crane_qty
            ss["rs_result"] = _res
            # Mémoriser la signature des données structurelles utilisées
            # pour ce calcul → permet de détecter une modification ultérieure
            ss["_rs_result_signature"] = tuple(
                str(ss.get(k, "")) for k in _structural_keys
            )
            # Mémoriser aussi la signature des prix → si elle change après
            # coup, on recalcule juste les coûts (pas tout le solver).
            ss["_rs_price_signature"] = tuple(
                str(ss.get(k, "")) for k in _price_keys
            )

    # ── Résultats ────────────────────────────────────────────────────────────
    if ss.get("rs_result"):
        r = ss["rs_result"]

        # Erreur explicite si l'inertie dépasse le catalogue
        if r.get("error"):
            st.divider()
            st.error(f"❌ {r['error']}")
            return

        st.divider()
        st.markdown("<div style='margin-top:1.5rem'></div>", unsafe_allow_html=True)

        res_col, svg_col = st.columns(2)
        with res_col:
            st.markdown("#### 📋 Results")
            # Une ligne par poste — format : Label | Quantité | → Coût
            _rr("Type",          r["beam_label"],                          "")
            if not r.get("is_suspendu"):
                _rr("Rail",          r.get("rail_short") or r["rail_label"],   "")
            _rr("Beam mass", f"{r['m1']} kg",                          f"{r['a']} €")
            if r.get("ltb_reinforced"):
                _rr(f"   incl. angle iron {r.get('angle_label','')}", "", "")
            if not r.get("is_suspendu"):
                _rr("Rail mass",   f"{r['m2']} kg",                          f"{r['b']} €")
            _rr("Lasercut mass",  f"{r['m3']} kg",                          f"{r['c']} €")
            _rr("Paint",      f"{r['peinture']} L",                     f"{r['d']} €")
            _rr("Bolts",   f"{r['n_appuis']} braces",                f"{r['cout_boulonnerie']} €")
            _rr("MO",      f"{r.get('h_mo_cr',0)} h",                f"{r.get('cout_mo_cr',0)} €")
            st.markdown(
                f"<div style='margin-top:6px;padding:6px 10px;background:#0A1E32;border-radius:4px;"
                f"display:flex;justify-content:space-between;font-weight:700;'>"
                f"<span style='color:#fff;'>Coût runway total</span>"
                f"<span style='color:#FDAE1B;'>{r['cout_mat']} €</span></div>",
                unsafe_allow_html=True,
            )

        with svg_col:
            st.markdown("#### 📉 Critical load case")
            _nb_app  = int(r.get("nbre_appuis", 2))
            _trav_cr = int(r.get("trav_critique", 1))
            _sommier = float(r["support_spacing_mm"])
            # Portée affichée sur le SVG : 1 span si 2 appuis, 2 spans si 3 appuis
            _portee_svg = _sommier if _nb_app == 2 else 2 * _sommier
            svg = _make_svg(r["forces_norm"], r["beam_label"], _portee_svg,
                            nbre_appuis=_nb_app, travee_critique=_trav_cr)
            st.markdown(svg, unsafe_allow_html=True)
            # Deflection calculée vs flèche admissible (L_span/600)
            f_calc = r.get("fleche_mm", 0.0)
            f_adm  = r.get("fleche_admissible_mm", 0.0)
            ratio_color = "#5ce07a" if f_calc <= f_adm else "#e05c5c"
            _trav_txt = f" — span {_trav_cr}" if _nb_app == 3 else ""
            # σ_max + LTB
            s_calc = r.get("sigma_MPa", 0.0)
            s_adm  = r.get("sigma_adm_MPa", SIGMA_ADM_MPA)
            sigma_color = "#5ce07a" if s_calc <= s_adm else "#e05c5c"
            _ltb = r.get("ltb", {})
            _ltb_ok  = _ltb.get("ok", True)
            _ltb_col = "#5ce07a" if _ltb_ok else "#e05c5c"
            _ltb_warn = "" if _ltb_ok else " ⚠️"
            st.markdown(
                f"<div style='font-size:0.85rem;color:#aaa;margin-top:4px;line-height:1.6;'>"
                f"Deflection{_trav_txt} : "
                f"<span style='color:{ratio_color};font-weight:700;'>{f_calc:.2f} mm</span> "
                f"<span style='color:#777;'>/ L/{DEFLECTION_RATIO} = {f_adm:.2f} mm</span>"
                f"&nbsp;&nbsp;|&nbsp;&nbsp;"
                f"σ : <span style='color:{sigma_color};font-weight:700;'>{s_calc:.1f} MPa</span> "
                f"<span style='color:#777;'>/ {s_adm:.1f} MPa (Fy/{SAFETY_FACTOR:g})</span>"
                f"&nbsp;&nbsp;|&nbsp;&nbsp;"
                f"LTB : <span style='color:{_ltb_col};font-weight:700;'>"
                f"\u03c7={_ltb.get('\u03c7_LT', _ltb.get('chi_LT', 1.0)):.2f} \u2014 {_ltb.get('util',0.0):.0%}{_ltb_warn}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )

        # ── Résultats colonnes ─────────────────────────────────────────────
        if r.get("col_result"):
            col = r["col_result"]
            st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
            st.divider()
            st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
            st.markdown("#### 🏛️ Columns")

            ci_col, cs_col = st.columns(2)
            with ci_col:
                _rr("Type",               col.get("col_label","—"),       "")
                _rr("Column mass",     f"{col.get('m_col',0)} kg",     f"{col.get('a_col',0)} €")
                if col.get("m_corniere",0) > 0:
                    _rr("   incl. bracing 70×70×6", "", "")
                _rr("Lasercut col.",f"{col.get('m_div_col',0)} kg", f"{col.get('c_col',0)} €")
                _rr("Paint col.",    f"{col.get('peinture_col',0)} L",f"{col.get('d_col',0)} €")
                _rr("Bolts col.", f"{col.get('n_cols_total',0)} col.", f"{col.get('boul_col',0)} €")
                _rr("MO col.",    f"{col.get('h_mo_col',0)} h",        f"{col.get('cout_mo_col',0)} €")
                st.markdown(
                    f"<div style='margin-top:6px;padding:6px 10px;background:#0A1E32;"
                    f"border-radius:4px;display:flex;justify-content:space-between;font-weight:700;'>"
                    f"<span style='color:#fff;'>Coût colonnes total</span>"
                    f"<span style='color:#FDAE1B;'>{col.get('cout_col',0)} €</span></div>",
                    unsafe_allow_html=True,
                )

            with cs_col:
                st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
                st.markdown(_make_col_svg(col), unsafe_allow_html=True)
                _fc = "#5ce07a" if col.get("fleche_ok") else "#e05c5c"
                _bc = "#5ce07a" if col.get("flamb_ok")  else "#e05c5c"
                st.markdown(
                    f"<div style='font-size:0.85rem;color:#aaa;margin-top:4px;line-height:1.6;'>"
                    f"Deflection: <span style='color:{_fc};font-weight:700;'>{col.get('delta_mm',0):.2f} mm</span>"
                    f" <span style='color:#777;'>/ h/{DEFLECTION_COL_RATIO} = {col.get('f_adm_mm',0):.1f} mm</span>"
                    f" &nbsp;|&nbsp; λ̄ = {col.get('lam_barre',0):.3f}"
                    f" &nbsp;|&nbsp; <span style='color:{_bc};font-weight:700;'>Util. {col.get('util_tot',0):.1%}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # ── Cross beam (poutre de suspension) ─────────────────────────────
            _xb = r.get("extra_beam")
            if _xb and not _xb.get("error"):
                st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
                st.divider()
                st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
                st.markdown("#### 🔩 Cross beam")
                xi_col, xs_col = st.columns(2)
                with xi_col:
                    _rr("Type", _xb.get("beam_label","—"), "")
                    _rr("Cross beam mass", f"{_xb.get('m_beam',0)} kg",
                        f"{_xb.get('a_beam',0)} €")
                    _rr("Lasercut", f"{_xb.get('m_div',0)} kg",
                        f"{_xb.get('c_div',0)} €")
                    _rr("Paint", f"{_xb.get('peinture',0)} L",
                        f"{_xb.get('d_paint',0)} €")
                    _rr("Bolts", f"{r.get('n_appuis',0)} braces",
                        f"{_xb.get('boul',0)} €")
                    _rr("MO", f"{_xb.get('h_mo_xb',0)} h",
                        f"{_xb.get('cout_mo_xb',0)} €")
                    st.markdown(
                        f"<div style='margin-top:6px;padding:6px 10px;background:#0A1E32;"
                        f"border-radius:4px;display:flex;justify-content:space-between;font-weight:700;'>"
                        f"<span style='color:#fff;'>Coût cross beam total</span>"
                        f"<span style='color:#FDAE1B;'>{_xb.get('cout_cross',0)} €</span></div>",
                        unsafe_allow_html=True,
                    )
                with xs_col:
                    _xfc = "#5ce07a" if _xb.get("fleche_ok") else "#e05c5c"
                    _xsc = "#5ce07a" if _xb.get("sigma_ok") else "#e05c5c"
                    _x_sadm = _xb.get("sigma_adm_MPa") or SIGMA_ADM_MPA
                    _xltb     = _xb.get("ltb", {})
                    _xltb_ok  = _xltb.get("ok", True)
                    _xltb_col = "#5ce07a" if _xltb_ok else "#e05c5c"
                    _xltb_warn = "" if _xltb_ok else " ⚠️"
                    st.markdown(
                        f"<div style='font-size:0.85rem;color:#aaa;margin-top:4px;line-height:1.6;'>"
                        f"Length : <span style='font-weight:700;'>{_xb.get('L_beam_mm',0)} mm</span><br>"
                        f"Deflection : "
                        f"<span style='color:{_xfc};font-weight:700;'>{_xb.get('delta_mm',0):.2f} mm</span> "
                        f"<span style='color:#777;'>/ L/{DEFLECTION_CROSS_RATIO} = {_xb.get('f_adm_mm',0):.2f} mm</span>"
                        f"&nbsp;&nbsp;|&nbsp;&nbsp;"
                        f"σ : <span style='color:{_xsc};font-weight:700;'>{_xb.get('sigma_MPa',0):.1f} MPa</span> "
                        f"<span style='color:#777;'>/ {_x_sadm:.1f} MPa (Fy/{SAFETY_FACTOR:g})</span>"
                        f"&nbsp;&nbsp;|&nbsp;&nbsp;"
                        f"LTB : <span style='color:{_xltb_col};font-weight:700;'>"
                        f"\u03c7={_xltb.get('\u03c7_LT', _xltb.get('chi_LT', 1.0)):.2f} \u2014 {_xltb.get('util',0.0):.0%}{_xltb_warn}</span>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
            elif _xb and _xb.get("error"):
                st.warning(f"⚠️ Cross beam : {_xb['error']}")

            # ── Résumé total groupé ──────────────────────────────────────────
            st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
            st.divider()
            st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
            _xb_ok = bool(_xb and not _xb.get("error"))
            _xb_a    = _xb.get("a_beam", 0)   if _xb_ok else 0
            _xb_c    = _xb.get("c_div", 0)    if _xb_ok else 0
            _xb_d    = _xb.get("d_paint", 0)  if _xb_ok else 0
            _xb_boul = _xb.get("boul", 0)     if _xb_ok else 0
            _xb_mo   = _xb.get("cout_mo_xb",0) if _xb_ok else 0
            # Chaque ligne additionne le poste de même nature pour runway +
            # colonnes + cross beam.
            _steel_tot = r.get("a",0) + r.get("b",0) + col.get("a_col",0) + _xb_a
            _misc_tot  = r.get("c",0) + col.get("c_col",0) + _xb_c
            _paint_tot = r.get("d",0) + col.get("d_col",0) + _xb_d
            _bolts_tot = r.get("cout_boulonnerie",0) + col.get("boul_col",0) + _xb_boul
            _mo_tot    = r.get("cout_mo_cr",0) + col.get("cout_mo_col",0) + _xb_mo
            _grand     = _steel_tot + _misc_tot + _paint_tot + _bolts_tot + _mo_tot
            st.markdown("### Cost summary")
            rg1, _ = st.columns(2)
            with rg1:
                _rr("Steel", "", f"{_steel_tot} €")
                _rr("Lasercut", "", f"{_misc_tot} €")
                _rr("Paint", "", f"{_paint_tot} €")
                _rr("Bolts", "", f"{_bolts_tot} €")
                _rr("MO", "", f"{_mo_tot} €")
                st.markdown(
                    f"<div style='margin-top:8px;padding:10px 14px;background:#0A1E32;"
                    f"border:2px solid #FDAE1B;border-radius:6px;"
                    f"display:flex;justify-content:space-between;font-weight:700;font-size:1.05rem;'>"
                    f"<span style='color:#fff;'>TOTAL GÉNÉRAL</span>"
                    f"<span style='color:#FDAE1B;font-size:1.15rem;'>{_grand} €</span></div>",
                    unsafe_allow_html=True,
                )

        # Export PDF + Extrait technique client (3D) — boutons côte à côte
        st.divider()
        st.markdown("<div style='margin-top:1.6rem'></div>", unsafe_allow_html=True)
        # CSS pour le bouton "To Add articles" — injecté avant les colonnes
        # pour ne pas créer de décalage vertical à l'intérieur de la colonne
        # (sinon le bouton se retrouve plus bas que ses voisins).
        st.markdown("""
        <style>
          .st-key-rs_to_add_article button {
              background-color: #FDAE1B !important;
              color: #0A1E32 !important;
              border: 1px solid #FDAE1B !important;
              font-weight: 600 !important;
          }
          .st-key-rs_to_add_article button:hover {
              background-color: #e69b14 !important;
              border-color: #e69b14 !important;
              color: #0A1E32 !important;
          }
          .st-key-rs_to_add_article button:focus,
          .st-key-rs_to_add_article button:active {
              background-color: #e69b14 !important;
              border-color: #e69b14 !important;
              color: #0A1E32 !important;
              box-shadow: none !important;
          }
        </style>
        """, unsafe_allow_html=True)
        acol, _, tcol, ecol = st.columns([1, 1, 1, 1])
        with acol:
            try:
                # Masse totale = tout ce qui est en kg (CR + rail + lasercut CR
                #                + colonnes + lasercut col. + cross beam + lasercut xb)
                col_d = r.get("col_result") or {}
                xb_d  = r.get("extra_beam") or {}
                weight_tot = (
                    int(r.get("m1", 0)) + int(r.get("m2", 0)) + int(r.get("m3", 0))
                    + int(col_d.get("m_col", 0)) + int(col_d.get("m_div_col", 0))
                    + (int(xb_d.get("m_beam", 0)) + int(xb_d.get("m_div", 0))
                       if xb_d and not xb_d.get("error") else 0)
                )
                # Coût total = cout_mat (CR) + cout_col + cout_cross
                cout_tot = (
                    int(r.get("cout_mat", 0))
                    + int(col_d.get("cout_col", 0))
                    + (int(xb_d.get("cout_cross", 0))
                       if xb_d and not xb_d.get("error") else 0)
                )
                # Produit : Customer → "Chemin de roulement" ; Olsen → "Structure"
                product_lbl = ("Chemin de roulement"
                               if r.get("appui_type") == "Customer"
                               else "Structure")
                addition_lbl = "Suspendu" if r.get("is_suspendu") else "Posé"
                project_lbl  = r.get("project") or ""

                if st.button("➕ To Add articles", use_container_width=True,
                             type="primary",
                             key="rs_to_add_article",
                             help="Pré-remplit l'onglet Add Article avec ce dimensionnement."):
                    # Résolution des libellés Category / Product à partir des
                    # listes réellement chargées depuis Odoo. Streamlit n'affiche
                    # la valeur d'un selectbox que si elle figure EXACTEMENT dans
                    # ses options — on cherche donc une correspondance souple
                    # (case-insensitive, substring) puis on retient le libellé
                    # de la liste tel quel.
                    def _resolve(target, options):
                        if not target or not options:
                            return None
                        t = str(target).strip().lower()
                        # 1) match exact (insensible à la casse)
                        for o in options:
                            if str(o).strip().lower() == t:
                                return o
                        # 2) match par préfixe / substring
                        for o in options:
                            if t in str(o).strip().lower():
                                return o
                        return None

                    _cat_list  = list(ss.get("category_list", []) or [])
                    _cat_resolved = _resolve("Olsen", _cat_list)
                    _prod_list = (list(ss.get("category_to_products", {})
                                        .get(_cat_resolved, []) or [])
                                  if _cat_resolved else [])
                    _prod_resolved = _resolve(product_lbl, _prod_list)

                    # Si Category/Product introuvables, on prévient et on
                    # n'incrémente pas (l'utilisateur peut compléter à la main).
                    _missing = []
                    if not _cat_resolved:
                        _missing.append(
                            f"Category « Olsen » introuvable parmi : "
                            f"{_cat_list or 'aucune liste chargée'}")
                    if _cat_resolved and not _prod_resolved:
                        _missing.append(
                            f"Product « {product_lbl} » introuvable dans la "
                            f"catégorie « {_cat_resolved} » parmi : "
                            f"{_prod_list or 'aucun produit'}")

                    if _missing:
                        for m_msg in _missing:
                            st.warning("⚠️ " + m_msg)
                        st.info(
                            "Les autres champs (projet, prix, marge, poids, "
                            "délai) seront tout de même pré-remplis ; tu pourras "
                            "compléter Category / Product manuellement.")

                    _ct_new = int(ss.get("clear_trigger", 0)) + 1
                    ss["clear_trigger"] = _ct_new
                    # Mode "création" (pas "update") : on SUPPRIME la clé du
                    # toggle pour qu'il reprenne sa valeur par défaut (False)
                    # au prochain rerun. Une assignation directe est interdite
                    # par Streamlit car le widget a déjà été instancié.
                    if "aa_update_mode" in ss:
                        del ss["aa_update_mode"]
                    ss[f"aa_project_{_ct_new}"]   = project_lbl
                    if _cat_resolved:
                        ss[f"aa_category_{_ct_new}"] = _cat_resolved
                    if _prod_resolved:
                        ss[f"aa_product_{_ct_new}"]  = _prod_resolved
                    # Marge à 40 % — la clé du widget dépend du Product
                    # effectivement sélectionné. On couvre les cas usuels :
                    #   • Product résolu → "aa_margin_{ct}_{Product}"
                    #   • Product None   → "aa_margin_{ct}_None"
                    #   • Product vide   → "aa_margin_{ct}_"
                    # Ainsi quel que soit le suffixe utilisé par le widget, on
                    # garantit qu'il trouve "40" en session_state.
                    for _suf in (_prod_resolved, "None", ""):
                        if _suf is not None:
                            ss[f"aa_margin_{_ct_new}_{_suf}"] = "40"
                    ss[f"aa_addition_{_ct_new}"]  = addition_lbl
                    # Span : crane_span × total_length UNIQUEMENT si pont
                    # suspendu ET appui Olsen ; sinon total_length seule.
                    _tl = int(r.get("total_length_mm", 0) or 0)
                    _cs = int(r.get("crane_span_mm", 0) or 0)
                    if (r.get("is_suspendu") and r.get("appui_type") == "Olsen"
                            and _cs > 0 and _tl > 0):
                        ss[f"aa_span_{_ct_new}"] = f"{_cs}x{_tl}"
                    elif _tl > 0:
                        ss[f"aa_span_{_ct_new}"] = str(_tl)
                    ss[f"aa_net_manual_{_ct_new}"] = str(cout_tot)
                    ss[f"aa_delay_{_ct_new}"]     = "60"
                    ss[f"aa_weight_{_ct_new}"]    = str(weight_tot)
                    # Flag pour afficher une pop-up de confirmation au rerun
                    ss["rs_to_add_done"] = True
                    st.rerun()
                # Pop-up de confirmation après rerun
                if ss.pop("rs_to_add_done", False):
                    st.toast("✔ Infos transmises à l'onglet « Add Article ».",
                             icon="✅")
            except Exception as ex:
                st.warning(f"To Add articles non disponible : {ex}")
        with tcol:
            try:
                tech_bytes = _make_tech_extract_html(r)
                tfname = (
                    f"Extrait-technique-{_sanitize_filename(r.get('project') or 'NA')}"
                    f"-{_sanitize_filename(r.get('client') or 'NA')}.html"
                )
                st.download_button(
                    label="🧩 Extrait technique 3D",
                    data=tech_bytes,
                    file_name=tfname,
                    mime="text/html",
                    use_container_width=True,
                    key="rs_tech_extract",
                    help="Fiche technique client avec un visuel 3D interactif "
                         "de la structure (à ouvrir dans un navigateur).",
                )
            except Exception as ex:
                st.warning(f"Extrait technique non disponible : {ex}")
        with ecol:
            try:
                pdf_bytes = _make_pdf_bytes(r)
                fname = (
                    f"CDR-Sizing-{_sanitize_filename(r.get('project') or 'NA')}"
                    f"-{_sanitize_filename(r.get('client') or 'NA')}.pdf"
                )
                st.download_button(
                    label="📄 Exporter PDF",
                    data=pdf_bytes,
                    file_name=fname,
                    mime="application/pdf",
                    use_container_width=True,
                    type="primary",
                    key="rs_export",
                )
            except Exception as ex:
                st.warning(f"PDF non disponible : {ex}")


def _make_col_svg(col):
    """SVG colonne encastrée : hauteur, type, force horizontale."""
    W, H   = 320, 210
    BX     = 170   # colonne décalée à droite
    BY_top = 20
    BY_bot = 185
    col_w  = 18
    fc_col = "#5ce07a" if col.get("fleche_ok") else "#e05c5c"

    lines = [f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;max-width:320px;background:transparent;">']

    # Encastrement (hachures)
    lines.append(f'<rect x="{BX-30}" y="{BY_bot}" width="60" height="14" '
                 f'fill="#FDAE1B" stroke="#0A1E32" stroke-width="1"/>')
    for xi in range(BX-28, BX+30, 7):
        lines.append(f'<line x1="{xi}" y1="{BY_bot}" x2="{xi-7}" y2="{BY_bot+14}" '
                     f'stroke="#0A1E32" stroke-width="1"/>')

    # Colonne
    lines.append(f'<rect x="{BX - col_w//2}" y="{BY_top}" width="{col_w}" '
                 f'height="{BY_bot - BY_top}" fill="#2a4a6b" stroke="#4a7fa0" stroke-width="1.5"/>')

    # Type colonne — à gauche
    lines.append(f'<text x="{BX - col_w//2 - 8}" y="{(BY_top+BY_bot)//2}" '
                 f'text-anchor="end" fill="#a8c4d4" font-size="10" '
                 f'font-family="Barlow,sans-serif">{col.get("col_label","")}</text>')

    # Cote hauteur — à droite
    hx = BX + col_w//2 + 16
    lines.append(f'<line x1="{hx}" y1="{BY_top}" x2="{hx}" y2="{BY_bot}" '
                 f'stroke="#444" stroke-width="1" stroke-dasharray="3,2"/>')
    lines.append(f'<text x="{hx+4}" y="{(BY_top+BY_bot)//2 + 4}" '
                 f'fill="#666" font-size="9" font-family="Barlow,sans-serif">'
                 f'{col.get("h_total_mm",0):.0f} mm</text>')

    # Force horizontale (HT3+HS) en tête — flèche vers droite
    # Arrow pointing right → toward column
    lines.append(f'<defs><marker id="fh" markerWidth="6" markerHeight="6" '
                 f'refX="6" refY="3" orient="auto">'
                 f'<path d="M0,0 L6,3 L0,6 Z" fill="#e05c5c"/></marker></defs>')
    fx_start = BX - col_w//2 - 55
    fx_end   = BX - col_w//2
    fy       = BY_top + 10
    lines.append(f'<line x1="{fx_start}" y1="{fy}" x2="{fx_end}" y2="{fy}" '
                 f'stroke="#e05c5c" stroke-width="2" marker-end="url(#fh)"/>')
    lines.append(f'<text x="{fx_start - 4}" y="{fy - 3}" text-anchor="end" '
                 f'fill="#e05c5c" font-size="9" font-family="Barlow,sans-serif">'
                 f'{col.get("F_perp_unit",0):.1f} kN</text>')

    lines.append('</svg>')
    return "\n".join(lines)


def _rr(label, val, price):
    """Affiche une ligne : Label | Valeur | → Coût."""
    ca, cb, cc = st.columns([1.4,1,0.9])
    ca.markdown(f"<span style='font-size:0.82rem;color:#aaa;'>{label}</span>", unsafe_allow_html=True)
    cb.markdown(f"<span style='font-size:0.82rem;font-weight:600;'>{val}</span>", unsafe_allow_html=True)
    if price:
        cc.markdown(f"<span style='font-size:0.82rem;color:#FDAE1B;font-weight:600;'>→ {price}</span>",
                    unsafe_allow_html=True)
