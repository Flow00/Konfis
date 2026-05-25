# tab_railway_sizing.py
import streamlit as st
import re, math, base64
from datetime import date

from railway_constants import (
    DEFAULT_STEEL_PRICE, DEFAULT_RAIL_PRICE,
    DEFAULT_LASERCUT_PRICE, DEFAULT_PAINT_PRICE,
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
def _check_ltb(size_mm, b_mm, tf_mm, Iy_cm4, Iz_cm4, M_max_Nmm, Lc_mm):
    """
    LTB check for rolled I/H section.
    size_mm  : section height h [mm]
    b_mm     : flange width [mm]
    tf_mm    : flange thickness [mm]
    Iy_cm4   : strong-axis inertia [cm⁴]
    Iz_cm4   : weak-axis inertia [cm⁴]  (from IZ_TABLES)
    M_max_Nmm: max bending moment [N·mm]
    Lc_mm    : lateral restraint spacing [mm]  (= entraxe appuis)
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

    # Critical moment (EN 1993-1-1 annex F, C1=1):
    # Mcr = (π/L)·√(E·Iz·G·It + (π/L)²·E·Iz·E·Iw)
    if L <= 0:
        return {"λ_LT": 0.0, "χ_LT": 1.0, "Mcr_kNm": 9999.0, "util": 0.0, "ok": True}

    pi_L = math.pi / L
    Mcr  = pi_L * math.sqrt(E * Iz * G * It + (pi_L**2) * E * Iz * E * Iw)   # N·mm

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
    }


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
):
    """
    Dimensionne la colonne et retourne un dict complet.
    Utilise h_rail_mm pour la flèche (hauteur totale).
    h_col = h_rail_mm - h_rail_section_mm - h_poutre_CR_mm (hauteur nette colonne).
    """
    h_total = float(h_rail_mm)        # mm — hauteur pour critère flèche
    h_col   = max(100.0, h_total - float(h_rail_section_mm) - float(h_poutre_CR_mm))

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
                       steel_price, lasercut_price, paint_price):
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
        "cout_cross":  round(a_cost + c_cost + d_cost + boul),
    }


def compute_railway(client, project, username, total_length_mm, beam_type, rail_kgm, rail_label,
                    rv_kN, carriage_mm, support_spacing_mm, nbre_pont,
                    rv2_kN, carriage2_mm, space_mm,
                    appui_type, spacing_appuis_mm,
                    steel_price, rail_price, lasercut_price, paint_price,
                    nbre_appuis=2,
                    crane_type="Posé", crane_span_mm=0.0):
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

    a_cost = m1 * float(steel_price) * FACT_DECOUPE
    b_cost = m2 * float(rail_price)
    c_cost = m3 * float(lasercut_price)
    d_cost = peinture * float(paint_price)
    cout_mat = a_cost + b_cost + c_cost + d_cost + cout_boulonnerie

    return {
        "beam_label": beam_label,
        "I_req": I_req,
        "I_chosen": inertie_chosen,
        "Wel_cm3": Wel_cm3,
        "M_max_Nmm": M_max_Nmm,
        "sigma_MPa": sigma_MPa,
        "sigma_adm_MPa": SIGMA_ADM_MPA,
        "gouverne": gov,
        "ltb": _check_ltb(taille, b_mm, tf_mm, inertie_chosen, 
                          IZ_TABLES.get(beam_type, {}).get(taille, inertie_chosen * 0.04),
                          M_max_Nmm, float(support_spacing_mm)),
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
    }


def _recompute_costs(r, steel_price, rail_price, lasercut_price, paint_price):
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

    # Recalculer les coûts avec les nouveaux prix
    a_cost = m1 * float(steel_price) * FACT_DECOUPE
    b_cost = m2 * float(rail_price)
    c_cost = m3 * float(lasercut_price)
    d_cost = peinture * float(paint_price)
    cout_mat = a_cost + b_cost + c_cost + d_cost + cout_boulonnerie

    # Mettre à jour les clés de coûts dans le résultat
    r["a"] = round(a_cost)
    r["b"] = round(b_cost)
    r["c"] = round(c_cost)
    r["d"] = round(d_cost)
    r["cout_mat"] = round(cout_mat)
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

    # ── Inputs summary (sans cas de charge — il est sur l'image) ─────────────
    tag_items = [
        ("Beam", r.get("beam_label","")),
    ]
    if not r.get("is_suspendu"):
        tag_items.append(("Rail", r.get("rail_short") or r.get("rail_label","")))
    tag_items += [
        ("Crane type", r.get("crane_type","Posé")),
        ("Length", f"{r.get('total_length_mm',0):.0f} mm"),
        ("Spacing", f"{r.get('support_spacing_mm',0):.0f} mm"),
        ("Support type", r.get("appui_type","")),
    ]
    tag_text = "  |  ".join(f"<b>{k}:</b> {v}" for k,v in tag_items)
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
        ["Beam mass",  f"{r['m1']} kg",  f"{r['a']} €"],
    ]
    if not r.get("is_suspendu"):
        rows.append(["Rail mass",    f"{r['m2']} kg",  f"{r['b']} €"])
    rows += [
        ["Lasercut mass",   f"{r['m3']} kg",  f"{r['c']} €"],
        ["Paint",       f"{r['peinture']} L", f"{r['d']} €"],
        ["Bolts",    f"{r['n_appuis']} braces", f"{r['cout_boulonnerie']} €"],
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
            *([(f"  incl. angle iron 70×70×6", f"{col.get('m_corniere',0)} kg", "")] if col.get("m_corniere",0) > 0 else []),
            ["Lasercut col.",f"{col.get('m_div_col',0)} kg",       f"{col.get('c_col',0)} €"],
            ["Paint col.",    f"{col.get('peinture_col',0)} L",     f"{col.get('d_col',0)} €"],
            ["Bolts col.", f"{col.get('n_cols_total',0)} col.",  f"{col.get('boul_col',0)} €"],
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
        _steel_tot   = r.get("a",0) + r.get("b",0) + col.get("a_col",0) + _xb_a2
        _div_tot     = r.get("c",0) + col.get("c_col",0) + _xb_c2
        _peinture_tot= r.get("d",0) + col.get("d_col",0) + _xb_d2
        _boul_tot    = r.get("cout_boulonnerie",0) + col.get("boul_col",0) + _xb_b2
        _grand       = _steel_tot + _div_tot + _peinture_tot + _boul_tot

        res_rows = [
            ["Poste", "Cost"],
            ["Steel",    f"{_steel_tot} €"],
            ["Lasercut", f"{_div_tot} €"],
            ["Paint",    f"{_peinture_tot} €"],
            ["Bolts",    f"{_boul_tot} €"],
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
    _price_keys = ["rs_sp", "rs_rp", "rs_lcp", "rs_pp"]

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
            )
            ss["_rs_price_signature"] = _price_signature

    # Toutes les lignes ci-dessous ont 4 colonnes (alignement global)
    # car la ligne "Prix" a 4 colonnes.

    # ── Client / Projet ──────────────────────────────────────────────────────
    hc1, hc2, hc3, hc4 = st.columns(4)
    with hc1:
        _lcli = "Client ❌" if not client_cur else "Client"
        client = st.text_input(_lcli, key="rs_client", placeholder="ex : Acme SA")
    with hc2:
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
        # N'apparaît que pour les portiques Olsen (poutre supplémentaire à
        # dimensionner). En "Customer", la box reste masquée et la valeur nulle.
        if appui_type == "Olsen":
            _lcs = "Crane span [mm] ❌" if not ss.get("rs_crane_span","") else "Crane span [mm]"
            crane_span = _ni(_lcs, "rs_crane_span")
        else:
            crane_span = ""

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
            with h21: HT3_2 = _nf_nodef("HT3 trolley accel. 2 [kN]", "rs_HT3_2")
            with h22: HS_2  = _nf_nodef("HS skewing 2 [kN]",   "rs_HS_2")
            with h23: HL_2  = _nf_nodef("HL bridge accel. 2 [kN]",      "rs_HL_2")

    st.divider()
    st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
    st.markdown("### 💰 Costs")
    p1, p2, p3, p4 = st.columns(4)
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

        # Cohérence dimensionnelle : entraxe appuis (= span) ≤ longueur totale
        _tl = _safe(total_length)
        _s  = _safe(support_spacing)
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
            )
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
                    _sp  = _safe(steel_p,    DEFAULT_STEEL_PRICE)
                    _lcp = _safe(lasercut_p, DEFAULT_LASERCUT_PRICE)
                    _pp  = _safe(paint_p,    DEFAULT_PAINT_PRICE)
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
                        "cout_col":      round(_m_col*_sp + _m_div_col*_lcp
                                               + _peinture_col*_pp + _boul_col),
                    })
                _res["col_result"] = _col_best
                _res["col_config"] = col_config

                # ── CROSS BEAM (poutre de suspension du portique) ────────────
                # Une poutre par portique (n_appuis_total / 2), longueur =
                # crane_span + 2×500 mm, chargée par 2× R_max du CR espacées
                # de crane_span. Sélection même type que la colonne, flèche L/600.
                _crane_span = float(_res.get("crane_span_mm", 0) or 0)
                if _crane_span > 0:
                    _R_max = float(_res.get("R_max_kN", 0.0))
                    _xb = compute_cross_beam(
                        col_type        = col_type,
                        crane_span_mm   = _crane_span,
                        R_max_kN        = _R_max,
                        n_appuis_total  = int(_res.get("n_appuis", 4)),
                        steel_price     = _sp,
                        lasercut_price  = _lcp,
                        paint_price     = _pp,
                    )
                    _res["extra_beam"] = _xb
                    # Les divers de la cross beam sont comptés SÉPARÉMENT
                    # (dans le bloc Cross beam), pas fusionnés dans Misc. col.
                else:
                    _res["extra_beam"] = None
            else:
                _res["col_result"] = None
                _res["col_config"] = None
                _res["extra_beam"] = None
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
            if not r.get("is_suspendu"):
                _rr("Rail mass",   f"{r['m2']} kg",                          f"{r['b']} €")
            _rr("Lasercut mass",  f"{r['m3']} kg",                          f"{r['c']} €")
            _rr("Paint",      f"{r['peinture']} L",                     f"{r['d']} €")
            _rr("Bolts",   f"{r['n_appuis']} braces",                f"{r['cout_boulonnerie']} €")
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
                    _rr("  incl. angle iron 70×70×6", f"{col.get('m_corniere',0)} kg", "")
                _rr("Lasercut col.",f"{col.get('m_div_col',0)} kg", f"{col.get('c_col',0)} €")
                _rr("Paint col.",    f"{col.get('peinture_col',0)} L",f"{col.get('d_col',0)} €")
                _rr("Bolts col.", f"{col.get('n_cols_total',0)} col.", f"{col.get('boul_col',0)} €")
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
            # Chaque ligne additionne le poste de même nature pour runway +
            # colonnes + cross beam.
            _steel_tot = r.get("a",0) + r.get("b",0) + col.get("a_col",0) + _xb_a
            _misc_tot  = r.get("c",0) + col.get("c_col",0) + _xb_c
            _paint_tot = r.get("d",0) + col.get("d_col",0) + _xb_d
            _bolts_tot = r.get("cout_boulonnerie",0) + col.get("boul_col",0) + _xb_boul
            _grand     = _steel_tot + _misc_tot + _paint_tot + _bolts_tot
            st.markdown("### Cost summary")
            rg1, _ = st.columns(2)
            with rg1:
                _rr("Steel", "", f"{_steel_tot} €")
                _rr("Lasercut", "", f"{_misc_tot} €")
                _rr("Paint", "", f"{_paint_tot} €")
                _rr("Bolts", "", f"{_bolts_tot} €")
                st.markdown(
                    f"<div style='margin-top:8px;padding:10px 14px;background:#0A1E32;"
                    f"border:2px solid #FDAE1B;border-radius:6px;"
                    f"display:flex;justify-content:space-between;font-weight:700;font-size:1.05rem;'>"
                    f"<span style='color:#fff;'>TOTAL GÉNÉRAL</span>"
                    f"<span style='color:#FDAE1B;font-size:1.15rem;'>{_grand} €</span></div>",
                    unsafe_allow_html=True,
                )

        # Export PDF — bouton un peu plus bas
        st.divider()
        st.markdown("<div style='margin-top:1.6rem'></div>", unsafe_allow_html=True)
        _, ecol = st.columns([4,1])
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