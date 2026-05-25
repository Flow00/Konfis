# tab_sales_automation.py
# ─────────────────────────────────────────────────────────────────────────────
#  Sales Automation tab — Streamlit
#  1) Sale order selector → auto-fill client + linked articles
#  2) Quote calculator (replaces Calculateur_DEVIS.xlsx)
# ─────────────────────────────────────────────────────────────────────────────

import streamlit as st
import xmlrpc.client
import math
from sales_constants import (
    TEAMS, BUILDING_TYPES, FIXATION_TYPES, CLIENT_TYPES,
    NACELLE_TYPES, SCENARIOS, INSTALLATION_TYPES, GAINE_TYPES,
    MAX_CONFIGS,
    H_RELEVE_PAR_CONFIG, H_TRAJET_RELEVE, H_REMISE_DOSSIER,
    H_RECEPTION, H_TRAJET_RECEPTION,
    H_TRAJET_MONTAGE_BASE, H_TRAJET_MONTAGE_C1, H_TRAJET_MONTAGE_C2,
    DEFAULT_TARIF_H, DEFAULT_KM_AR, DEFRAIMENT_EUR_KM,
    COUT_NACELLE_LOUEE_EUR_J, SECURITE_RENFORCEE_OPTIONS,
)

ss = st.session_state


# ─────────────────────────────────────────────────────────────────────────────
#  ODOO HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def fetch_articles_for_project(uid, password, db_name, models_url, project_ref):
    """Fetch product.template where default_code contains the project reference."""
    try:
        models = xmlrpc.client.ServerProxy(models_url)
        records = models.execute_kw(
            db_name, uid, password,
            'product.template', 'search_read',
            [[['default_code', 'ilike', project_ref]]],
            {'fields': ['default_code', 'name', 'list_price', 'description_sale', 'weight', 'categ_id'], 'order': 'default_code asc'},
        )
        return records
    except Exception as e:
        st.error(f"Odoo error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  ARTICLE RULE PARSER
#  Parses Odoo product data to extract installation type, capacity, etc.
#  ref format: [SUPPLIER]_[TYPE_ABBR]_[PROJECT]
# ─────────────────────────────────────────────────────────────────────────────

# Map: abbreviation in ref → possible INSTALLATION_TYPES keys (ordered by priority)
_ABBR_TO_TYPES = {
    "PR":   ["EDK","EDL","ELK","ELS","ELV","DLVM","EHPK","ZLK","ZLV",
             "EHB","EHB-I","EHB-X","ESB","ESK","ZHB","ZHB-3","ZHB-I","ZHB-X","ZSB"],
    "HB":   ["EHB","EHB-I","EHB-X","ZHB","ZHB-3"],
    "KBK":  ["LPK","LS","LSX","VS","LW","LWX","VW"],
    "PT":   ["GMC","GM2","GM4","GM6","GM8"],
    "PO":   ["GMC"],
    "MONO": ["EDK","ELK"],
    "PA":   [],  # Palan — accessoire, pas de config installation
    "ALIM": [],  # Gaine — handled separately
    "CHA":  [],  # Chariot — accessoire
    "CR":   [],  # Chemin de roulement — handled by Railway tab
    "GRU":  ["ZLK","ZLV"],
}

# Gaine KBH types detection from name
_KBH_SIZES = ["63","80","100","125","160","200"]


def _parse_capacity(name):
    """Extract capacity from product name e.g. '3.2t', '500kg', '1000 kg'."""
    import re
    m = re.search(r"(\d+\.?\d*)\s*t\b", name, re.IGNORECASE)
    if m: return f"{m.group(1)}t"
    m = re.search(r"(\d+\.?\d*)\s*kg\b", name, re.IGNORECASE)
    if m: return f"{m.group(1)} kg"
    return ""


def _parse_span(name):
    """Extract span from product name e.g. '10m', '6000mm', '6 m'."""
    import re
    m = re.search(r"(\d+\.?\d*)\s*m\b", name, re.IGNORECASE)
    if m: return f"{m.group(1)} m"
    m = re.search(r"(\d+)\s*mm\b", name, re.IGNORECASE)
    if m: return f"{m.group(1)} mm"
    return ""


def parse_article_rule(article):
    """
    Returns a dict describing what rule applies to this article:
    {
      "rule_type": "installation"|"gaine"|"none",
      "install_type": "EDK" | None,       # best guess
      "candidates": [...],                 # all possible types
      "capacity": "3.2t",
      "span": "10 m",
      "kbh_size": "100" | None,
      "prix_brut": 30700.0,
      "no_rule_msg": None | "No rules for this article: ALIM"
    }
    """
    ref  = article.get("default_code", "") or ""
    name = article.get("name", "") or ""
    prix = float(article.get("list_price", 0) or 0)
    capacity = _parse_capacity(name)
    span     = _parse_span(name)

    parts = ref.split("_")
    abbr  = parts[1].upper() if len(parts) >= 2 else ""

    # ── Gaine check ──────────────────────────────────────────────────────────
    if abbr == "ALIM":
        kbh_size = None
        for sz in _KBH_SIZES:
            if f"KBH {sz}" in name or f"KBH{sz}" in name:
                kbh_size = sz
                break
        if kbh_size:
            return {
                "rule_type": "gaine",
                "install_type": f"KBH {kbh_size}",
                "candidates": [f"KBH {kbh_size}"],
                "capacity": capacity, "span": span,
                "kbh_size": kbh_size,
                "prix_brut": prix,
                "no_rule_msg": None,
            }
        return {
            "rule_type": "none",
            "install_type": None, "candidates": [],
            "capacity": capacity, "span": span, "kbh_size": None,
            "prix_brut": prix,
            "no_rule_msg": f"Alimentation — KBH size not detected in name",
        }

    # ── Installation check ───────────────────────────────────────────────────
    # Build a combined search text: name + description_sale
    desc_sale = article.get("description_sale", "") or ""
    search_text = f"{name} {desc_sale}".upper().strip()

    # 1. Abbr IS the install type directly (EDK, ZLK, ELK etc.)
    if abbr in INSTALLATION_TYPES:
        return {
            "rule_type": "installation",
            "install_type": abbr,
            "candidates": [abbr],
            "capacity": capacity, "span": span, "kbh_size": None,
            "prix_brut": prix,
            "no_rule_msg": None,
        }
    # 2. Abbr is a product abbreviation (PR, HB, KBK etc.) → scan name+desc for type
    candidates = _ABBR_TO_TYPES.get(abbr, [])
    if candidates:
        # Search in combined name+description for a specific type match
        best = next((t for t in candidates if t.upper() in search_text), None)
        if best:
            return {
                "rule_type": "installation",
                "install_type": best,
                "candidates": candidates,
                "capacity": capacity, "span": span, "kbh_size": None,
                "prix_brut": prix,
                "no_rule_msg": None,
            }
        # No specific type found in text → return unknown, ask user
        return {
            "rule_type": "installation_unknown",
            "install_type": None,
            "candidates": candidates,
            "capacity": capacity, "span": span, "kbh_size": None,
            "prix_brut": prix,
            "no_rule_msg": None,   # Not an error — user will pick
        }

    # ── No rule ───────────────────────────────────────────────────────────────
    return {
        "rule_type": "none",
        "install_type": None, "candidates": [],
        "capacity": capacity, "span": span, "kbh_size": None,
        "prix_brut": prix,
        "no_rule_msg": f"No rules for this article  (type: {abbr or '?'})",
    }


def _display_name(a):
    """Return best display name: description_sale if available, else name."""
    desc = (a.get("description_sale") or "").strip()
    name = (a.get("name") or "").strip()
    # If description_sale is set and not just a reference pattern, prefer it
    if desc and not desc.startswith(("ABU_","OLS_","FLO_")):
        return desc
    return name


# ─────────────────────────────────────────────────────────────────────────────
#  CALCULATION ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def _safe(v, fb=0.0):
    try:
        return float(str(v).replace(",", ".")) if v else fb
    except:
        return fb

def _safe_int(v, fb=0):
    try:
        return int(v) if v else fb
    except:
        return fb


def compute_config_price(cfg):
    """
    cfg = dict with keys: type, qty, sale_price (already net from Odoo), fixation
    The sale_price from Odoo IS the net sale price (set via Add Articles tab).
    Returns dict with sale, hours.
    """
    if not cfg.get("type"):
        return None

    t         = cfg["type"]
    heures_base = INSTALLATION_TYPES.get(t, 16)
    sale      = _safe(cfg.get("sale_price"), 0.0)
    qty       = _safe_int(cfg.get("qty"), 1)
    fix_ratio = FIXATION_TYPES.get(cfg.get("fixation","Posé"), 1.0)
    hours     = heures_base * fix_ratio * qty

    return {
        "sale":  round(sale * qty, 2),
        "hours": round(hours, 1),
        "qty":   qty,
    }





def compute_gaine(g):
    """
    g = dict: type, qty, longueur, sale_price (already net sale price from Odoo)
    """
    if not g.get("type"):
        return None
    t        = g["type"]
    h_per_m  = GAINE_TYPES.get(t, 0.32)
    sale     = _safe(g.get("sale_price"), 0.0)
    qty      = _safe_int(g.get("qty"), 1)
    longueur = _safe(g.get("longueur"))
    hours    = h_per_m * longueur * qty
    return {"sale": round(sale * qty, 2), "hours": round(hours, 1)}


def compute_prestations(n_configs, configs_hours, gaine_hours, ratio_bat, ratio_client, scenario_ratio, tarif_h, km_ar, nacelle, n_nacelle_j):
    """Compute service hours and costs."""
    n  = n_configs
    r  = ratio_bat * ratio_client

    # Relevés
    h_releve = (H_RELEVE_PAR_CONFIG + H_TRAJET_RELEVE) * n

    # Montage
    h_config_mount = sum(configs_hours) * r
    h_gaine_mount  = gaine_hours * r

    h_trajet_mount = (H_TRAJET_MONTAGE_C1
                      + (H_TRAJET_MONTAGE_C2 if n >= 2 else 0)
                      + H_TRAJET_MONTAGE_BASE * max(0, n - 2))
    h_montage = h_config_mount + h_gaine_mount + h_trajet_mount

    # Réception
    h_recep = (H_RECEPTION + H_TRAJET_RECEPTION) * min(n, 2)

    # Remise dossier
    h_dossier = H_REMISE_DOSSIER * n

    h_total = h_releve + h_montage + h_recep + h_dossier

    # Costs
    cout_mo    = h_total * tarif_h
    defraiement = km_ar * 2 * DEFRAIMENT_EUR_KM * (n + 2)  # ×2 = aller-retour
    cout_nacelle = 0.0
    if nacelle == "Louée":
        cout_nacelle = n_nacelle_j * COUT_NACELLE_LOUEE_EUR_J

    cout_total_mo = (cout_mo + defraiement + cout_nacelle) * scenario_ratio

    return {
        "h_releve":   round(h_releve, 1),
        "h_montage":  round(h_montage, 1),
        "h_recep":    round(h_recep, 1),
        "h_dossier":  round(h_dossier, 1),
        "h_total":    round(h_total, 1),
        "cout_mo":    round(cout_mo, 2),
        "defraiement":round(defraiement, 2),
        "cout_nacelle":round(cout_nacelle, 2),
        "cout_total_mo": round(cout_total_mo, 2),
    }


# ─────────────────────────────────────────────────────────────────────────────
#  UI HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def _nf(label, key, default=""):
    if key not in ss: ss[key] = str(default) if default != "" else ""
    return st.text_input(label, key=key)

def _pct(label, key, default=0.0):
    """Percentage input shown as % (0-100), stored as float 0-1."""
    disp_key = f"{key}_disp"
    if disp_key not in ss:
        ss[disp_key] = f"{default*100:.0f}"
    val = st.text_input(label, key=disp_key)
    try:
        return float(val.replace(",", ".")) / 100.0
    except:
        return default

def _price_row(label, val, sub=""):
    ca, cb, cc = st.columns([2, 1, 1])
    ca.markdown(f"<span style='font-size:0.82rem;color:#aaa;'>{label}</span>", unsafe_allow_html=True)
    cb.markdown(f"<span style='font-size:0.82rem;font-weight:600;'>{val}</span>", unsafe_allow_html=True)
    if sub:
        cc.markdown(f"<span style='font-size:0.82rem;color:#FDAE1B;'>→ {sub}</span>", unsafe_allow_html=True)

def _total_box(label, val, big=False):
    sz = "1.1rem" if big else "1rem"
    border = "border:2px solid #FDAE1B;" if big else ""
    st.markdown(
        f"<div style='padding:7px 12px;background:#0A1E32;border-radius:4px;"
        f"{border}display:flex;justify-content:space-between;font-weight:700;margin-top:6px;'>"
        f"<span style='color:#fff;font-size:{sz};'>{label}</span>"
        f"<span style='color:#FDAE1B;font-size:{sz};'>{val} €</span></div>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
#  MAIN RENDER
# ─────────────────────────────────────────────────────────────────────────────
def render_sales_automation_tab(db_name, models_url):
    st.markdown("### 📋 Sales Automation")

    uid      = ss.get("uid")
    password = ss.get("password")
    if not uid:
        st.warning("Please log in first.")
        return

    sale_orders = ss.get("sale_orders_ref", {})

    # ── Project change detection — runs BEFORE any widget renders ───────────
    # Read project from ss (committed value from previous render cycle)
    _proj_committed = (ss.get("sa_project") or "").strip()
    _prev_proj      = ss.get("sa_project_prev", "")
    if _proj_committed != _prev_proj:
        ss["sa_project_prev"] = _proj_committed
        if _proj_committed and _proj_committed in sale_orders:
            order = sale_orders[_proj_committed]
            ss["sa_customer_disp"] = order.get("customer", "") or ""
            _d = order.get("description", "") or ""
            ss["sa_desc_disp"] = _d if _d != "0" else ""
        elif _proj_committed:
            try:
                import xmlrpc.client as _xc2
                _mx = _xc2.ServerProxy(models_url)
                _ords = _mx.execute_kw(
                    db_name, uid, password,
                    "sale.order", "search_read",
                    [[["name", "=", _proj_committed]]],
                    {"fields": ["name","partner_id","x_studio_description_sales_order"], "limit": 1},
                )
                if _ords:
                    o = _ords[0]
                    ss["sa_customer_disp"] = o["partner_id"][1] if o.get("partner_id") else ""
                    _d2 = o.get("x_studio_description_sales_order", "") or ""
                    ss["sa_desc_disp"] = _d2 if _d2 != "0" else ""
                else:
                    ss["sa_customer_disp"] = ""
                    ss["sa_desc_disp"] = ""
            except Exception:
                ss["sa_customer_disp"] = ""
                ss["sa_desc_disp"] = ""
        else:
            ss["sa_customer_disp"] = ""
            ss["sa_desc_disp"] = ""

    # ── Section 1 : Sale order ────────────────────────────────────────────────
    st.markdown("### 📁 Sale order")
    h1, h2, h3, h4 = st.columns([2, 2, 2, 0.4])
    with h1:
        project = st.selectbox(
            "Project", options=[""] + list(sale_orders.keys()),
            accept_new_options=True, key="sa_project",
        )
        project = (project or "").strip()
    with h2:
        st.text_input("Customer", key="sa_customer_disp", disabled=True)
    with h3:
        st.text_input("Description", key="sa_desc_disp", disabled=True)
    with h4:
        st.markdown("&nbsp;", unsafe_allow_html=True)  # label spacer
        if st.button("🔄", key="sa_refresh_proj", help="Refresh customer & articles"):
            ss["sa_project_prev"] = "__force_refresh__"
            ss["sa_remove_ids"] = set()
            ss["sa_manual_remove_ids"] = set()
            st.rerun()

    # ── Fetch + display linked articles ──────────────────────────────────────
    if project:
        articles = fetch_articles_for_project(uid, password, db_name, models_url, project)
    else:
        articles = []

    st.markdown(f"<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
    st.markdown("### 📦 Linked articles")

    if not articles:
        st.caption("No articles found for this project." if project else "Select a project above.")
    else:
        # Init quantities
        for a in articles:
            qk = f"sa_qty_{a['id']}"
            if qk not in ss: ss[qk] = "1"

        # Header
        hdr = st.columns([2.5, 3.5, 1, 1, 1.5, 0.5])
        for col, txt in zip(hdr, ["Reference", "Name", "Price", "Qty", "Rule", ""]):
            col.markdown(
                f"<span style='font-size:0.78rem;color:#4a7fa0;font-weight:600;"
                f"text-transform:uppercase;'>{txt}</span>", unsafe_allow_html=True)

        to_remove = ss.get("sa_remove_ids", set())
        new_remove = set(to_remove)
        for a in articles:
            if a["id"] in to_remove:
                continue
            qk = f"sa_qty_{a['id']}"
            rule = parse_article_rule(a)
            r1, r2, r3, r4, r5, r6 = st.columns([2.5, 3.5, 1, 1, 1.5, 0.5])
            r1.markdown(f"<span style='font-size:0.82rem;'>{a.get('default_code','')}</span>",
                        unsafe_allow_html=True)
            # Name + capacity/span in muted
            _cap = f" · {rule['capacity']}" if rule['capacity'] else ""
            _spn = f" · {rule['span']}"     if rule['span']     else ""
            r2.markdown(
                f"<span style='font-size:0.82rem;'>{a.get('name','')}</span>"
                f"<span style='font-size:0.75rem;color:#555;'>{_cap}{_spn}</span>",
                unsafe_allow_html=True)
            r3.markdown(f"<span style='font-size:0.82rem;font-weight:600;'>{a.get('list_price',0):.2f} €</span>",
                        unsafe_allow_html=True)
            r4.text_input("", key=qk, label_visibility="collapsed")
            # Rule badge
            if rule["no_rule_msg"]:
                r5.markdown(
                    f"<span style='font-size:0.75rem;color:#e05c5c;'>{rule['no_rule_msg']}</span>",
                    unsafe_allow_html=True)
            elif rule["rule_type"] in ("installation", "installation_unknown"):
                _badge = f"📦 {rule['install_type']}" if rule["install_type"] else "📦 ? (select type)"
                _col   = "#5ce07a" if rule["install_type"] else "#FDAE1B"
                r5.markdown(
                    f"<span style='font-size:0.78rem;color:{_col};font-weight:600;'>{_badge}</span>",
                    unsafe_allow_html=True)
            elif rule["rule_type"] == "gaine":
                r5.markdown(
                    f"<span style='font-size:0.78rem;color:#5caadf;font-weight:600;'>"
                    f"⚡ {rule['install_type']}</span>",
                    unsafe_allow_html=True)
            if r6.button("✕", key=f"sa_rm_{a['id']}", help="Remove"):
                new_remove.add(a["id"])
        ss["sa_remove_ids"] = new_remove

        # ── Manual article add ────────────────────────────────────────────
        st.markdown("<div style='margin-top:0.6rem'></div>", unsafe_allow_html=True)
        with st.expander("➕ Add article manually by reference"):
            # Input + button on same line, result inline
            if ss.pop("_sa_clear_ref", False):
                ss["sa_manual_ref"] = ""
            ms1, ms2 = st.columns([3, 1])
            with ms1:
                manual_ref = st.text_input("Odoo reference", key="sa_manual_ref",
                                           placeholder="ex : ABU_EDK_S26-01833",
                                           label_visibility="collapsed")
            with ms2:
                _do_search = st.button("🔍 Search", key="sa_manual_search",
                                       use_container_width=True)
            if _do_search and manual_ref:
                try:
                    import xmlrpc.client as _xc
                    _models = _xc.ServerProxy(models_url)
                    _found = _models.execute_kw(
                        db_name, uid, password,
                        "product.template", "search_read",
                        [[["default_code", "=", manual_ref]]],
                        {"fields": ["default_code","name","list_price","description_sale","weight","categ_id"]},
                    )
                    if _found:
                        _art = _found[0]
                        _manual = ss.get("sa_manual_articles", [])
                        _removed = ss.get("sa_manual_remove_ids", set())
                        # Check not in active list (ignore removed ones)
                        _active_ids = {a["id"] for a in _manual if a["id"] not in _removed}
                        if _art["id"] not in _active_ids:
                            # If previously removed, un-remove it
                            if _art["id"] in _removed:
                                _removed.discard(_art["id"])
                                ss["sa_manual_remove_ids"] = _removed
                            else:
                                _manual.append(_art)
                                ss["sa_manual_articles"] = _manual
                            ss["_sa_clear_ref"] = True
                            st.rerun()
                        else:
                            st.warning("Already in list.")
                    else:
                        st.warning(f"Not found: {manual_ref}")
                except Exception as e:
                    st.error(f"Error: {e}")

        # Show manual articles
        _manual_arts = ss.get("sa_manual_articles", [])
        _manual_remove = ss.get("sa_manual_remove_ids", set())
        if _manual_arts:
            st.markdown("<span style='font-size:0.78rem;color:#4a7fa0;'>Manually added articles</span>",
                        unsafe_allow_html=True)
            for a in _manual_arts:
                if a["id"] in _manual_remove:
                    continue
                qk = f"sa_qty_m_{a['id']}"
                if qk not in ss: ss[qk] = "1"
                rule = parse_article_rule(a)
                rm1, rm2, rm3, rm4, rm5, rm6 = st.columns([2.5, 3.5, 1, 1, 1.5, 0.5])
                rm1.markdown(f"<span style='font-size:0.82rem;'>{a.get('default_code','')}</span>", unsafe_allow_html=True)
                rm2.markdown(f"<span style='font-size:0.82rem;'>{_display_name(a)}</span>", unsafe_allow_html=True)
                rm3.markdown(f"<span style='font-size:0.82rem;font-weight:600;'>{a.get('list_price',0):.2f} €</span>", unsafe_allow_html=True)
                rm4.text_input("", key=qk, label_visibility="collapsed")
                if rule["no_rule_msg"]:
                    rm5.markdown(f"<span style='font-size:0.75rem;color:#e05c5c;'>{rule['no_rule_msg']}</span>", unsafe_allow_html=True)
                else:
                    rm5.markdown(f"<span style='font-size:0.78rem;color:#5ce07a;font-weight:600;'>{'📦' if rule['rule_type']=='installation' else '⚡'} {rule['install_type']}</span>", unsafe_allow_html=True)
                if rm6.button("✕", key=f"sa_rm_m_{a['id']}", help="Remove"):
                    _new_mr = set(_manual_remove); _new_mr.add(a["id"])
                    ss["sa_manual_remove_ids"] = _new_mr
                    st.rerun()

        # All articles combined for autofill
        _all_articles = [a for a in articles if a["id"] not in to_remove] +                         [a for a in _manual_arts if a["id"] not in _manual_remove]



    # ── Section 2 : Quote calculator ─────────────────────────────────────────
    st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
    st.divider()
    st.markdown("<div style='margin-top:0.3rem'></div>", unsafe_allow_html=True)
    st.markdown("### 🧮 Quote calculator")

    # General parameters
    g1, g2, g3, g4 = st.columns(4)
    with g1:
        team     = st.selectbox("Team", TEAMS, key="sa_team")
    with g2:
        securite = st.selectbox("Security", list(SECURITE_RENFORCEE_OPTIONS.keys()), key="sa_securite")
    with g3:
        scenario = st.selectbox("Scenario", list(SCENARIOS.keys()), index=1, key="sa_scenario")
    with g4:
        bat_type = st.selectbox("Building type", list(BUILDING_TYPES.keys()), key="sa_bat")

    g5, g6, g7, g8 = st.columns(4)
    with g5:
        client_type = st.selectbox("Client type", list(CLIENT_TYPES.keys()), key="sa_ctype")
    with g6:
        nacelle = st.selectbox("Nacelle", NACELLE_TYPES, key="sa_nacelle")
    with g7:
        # Detect project or team change → refresh client address
        _addr_sig = f"{_proj_committed}|{ss.get('sa_team','')}"
        if ss.get("_sa_addr_sig") != _addr_sig:
            ss["_sa_addr_sig"] = _addr_sig
            _client_zip = ""; _client_city = ""
            if _proj_committed:
                try:
                    import xmlrpc.client as _xc3
                    _mx2 = _xc3.ServerProxy(models_url)
                    _ords2 = _mx2.execute_kw(db_name, uid, password, "sale.order", "search_read",
                        [[["name","=",_proj_committed]]],
                        {"fields":["partner_id"],"limit":1})
                    if _ords2 and _ords2[0].get("partner_id"):
                        _pid = _ords2[0]["partner_id"][0]
                        _p = _mx2.execute_kw(db_name, uid, password, "res.partner", "search_read",
                            [[["id","=",_pid]]],
                            {"fields":["zip","city","partner_latitude","partner_longitude"],"limit":1})
                        if _p:
                            _client_zip  = _p[0].get("zip","") or ""
                            _client_city = _p[0].get("city","") or ""
                            _clat = float(_p[0].get("partner_latitude") or 0)
                            _clng = float(_p[0].get("partner_longitude") or 0)
                            if _clat and _clng:
                                import math as _m
                                _OLSEN_GPS = {
                                    "LIG": (50.593, 5.888),
                                    "CRL": (50.408, 4.475),
                                    "LUX": (49.532, 5.989),
                                }
                                _olat, _olng = _OLSEN_GPS.get(ss.get("sa_team","LIG"), (50.593, 5.888))
                                def _hav(la1,lo1,la2,lo2):
                                    R=6371; d1=_m.radians(la2-la1); d2=_m.radians(lo2-lo1)
                                    a=_m.sin(d1/2)**2+_m.cos(_m.radians(la1))*_m.cos(_m.radians(la2))*_m.sin(d2/2)**2
                                    return max(5, round(R*2*_m.asin(_m.sqrt(a))*1.3))
                                ss["sa_km"] = str(_hav(_olat, _olng, _clat, _clng))
                except Exception:
                    pass
            ss["_sa_client_zip"]  = _client_zip
            ss["_sa_client_city"] = _client_city

        _client_zip  = ss.get("_sa_client_zip","")
        _client_city = ss.get("_sa_client_city","")
        _team_cur    = ss.get("sa_team","LIG")
        _olsen_zip, _olsen_city = {
            "LIG": ("4821", "Andrimont"),
            "CRL": ("6061", "Montignies-sur-Sambre"),
            "LUX": ("L-3922", "Mondercange"),
        }.get(_team_cur, ("",""))
        km_ar = _nf("KM (one way)", "sa_km", DEFAULT_KM_AR)
        if _client_zip or _client_city:
            st.caption(f"📍 {_client_zip} {_client_city}")
    with g8:
        tarif_h_val = _nf("Rate [€/h]", "sa_tarif", DEFAULT_TARIF_H)

    # ── Configurations — driven by articles ─────────────────────────────────
    st.markdown("<div style='margin-top:0.6rem'></div>", unsafe_allow_html=True)
    st.markdown("#### Configurations — Installation")

    _art_all   = [a for a in articles if a["id"] not in ss.get("sa_remove_ids", set())]
    _art_all  += [a for a in ss.get("sa_manual_articles", [])
                  if a["id"] not in ss.get("sa_manual_remove_ids", set())]
    def _effective_rule_type(a):
        _rk = f"sa_rule_{a['id']}"
        override = ss.get(_rk, "")
        if override in INSTALLATION_TYPES: return "installation"
        if override in GAINE_TYPES:        return "gaine"
        return parse_article_rule(a)["rule_type"]
    def _effective_type(a):
        _rk = f"sa_rule_{a['id']}"
        override = ss.get(_rk, "")
        if override and override != "— (no rule)": return override
        return parse_article_rule(a)["install_type"] or "—"

    _inst_arts = [a for a in _art_all
                  if _effective_rule_type(a) in ("installation","installation_unknown")]
    _seen_ids = set()
    _art_all_dedup = []
    for _a in _art_all:
        if _a["id"] not in _seen_ids:
            _seen_ids.add(_a["id"]); _art_all_dedup.append(_a)
    _art_all = _art_all_dedup
    _gaine_arts = [a for a in _art_all if _effective_rule_type(a) == "gaine"]

    cfg_results  = []
    gaine_result = None

    # Auto-fill button
    _, _btn_col = st.columns([4,1])
    with _btn_col:
        if st.button("⚡ Auto-fill", key="sa_autofill", use_container_width=True):
            for i, a in enumerate(_inst_arts[:MAX_CONFIGS]):
                rule = parse_article_rule(a)
                if rule["rule_type"] == "installation_unknown" and rule["candidates"]:
                    ss[f"sa_ctype_pick_{i}"] = rule["candidates"][0]
                if f"sa_cfix_{i}" not in ss:
                    ss[f"sa_cfix_{i}"] = list(FIXATION_TYPES.keys())[0]
            import re as _re2
            for ga in _gaine_arts:
                rule_g = parse_article_rule(ga)
                span = rule_g.get("span","")
                if span:
                    _m  = _re2.search(r"([\d.]+)\s*m",  span)
                    _mm = _re2.search(r"([\d.]+)\s*mm", span)
                    glk = f"sa_glong_{ga['id']}"
                    if _m:   ss[glk] = _m.group(1)
                    elif _mm: ss[glk] = str(round(float(_mm.group(1))/1000,2))
            st.rerun()

    if not _inst_arts:
        st.caption("No installation articles detected. Add articles above.")
    else:
        for i, a in enumerate(_inst_arts[:MAX_CONFIGS]):
            itype  = _effective_type(a)
            sale_p = float(a.get("list_price", 0) or 0)
            qk     = f"sa_qty_{a['id']}"
            if qk not in ss: ss[qk] = "1"

            ic1, ic2 = st.columns([1, 1])
            with ic1:
                st.text_input("Type", value=itype, disabled=True,
                              key=f"sa_itype_disp_{i}")
            with ic2:
                cfix = st.selectbox("Fixation", list(FIXATION_TYPES.keys()),
                                    key=f"sa_cfix_{i}")

            cqty = ss.get(qk, "1")
            res  = compute_config_price({
                "type": itype, "qty": cqty,
                "sale_price": sale_p, "fixation": cfix,
            })
            if res:
                cfg_results.append(res)
                st.caption(f"Sale: {res['sale']} €  |  {res['hours']} h install")
            else:
                cfg_results.append(None)

    # ── Gaine ─────────────────────────────────────────────────────────────────
    if _gaine_arts:
        st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
        st.markdown("#### Gaine")
        import re as _re3
        for ga in _gaine_arts:
            gtype   = _effective_type(ga)
            sale_pg = float(ga.get("list_price", 0) or 0)
            gqk     = f"sa_qty_{ga['id']}"
            if gqk not in ss: ss[gqk] = "1"
            glk = f"sa_glong_{ga['id']}"
            if glk not in ss:
                span = parse_article_rule(ga).get("span","")
                _m  = _re3.search(r"([\d.]+)\s*m",  span) if span else None
                _mm = _re3.search(r"([\d.]+)\s*mm", span) if span else None
                ss[glk] = _m.group(1) if _m else (str(round(float(_mm.group(1))/1000,2)) if _mm else "")

            gc1, gc2 = st.columns([1, 1])
            with gc1:
                st.text_input("Gaine type", value=gtype, disabled=True,
                              key=f"sa_gtype_disp_{ga['id']}")
            with gc2:
                glongueur = st.text_input("Length [m]", key=glk, placeholder="ex : 12.5")

            gqty = ss.get(gqk, "1")
            _glongueur_val = ss.get(glk, "") or glongueur
            if _glongueur_val and gtype != "—":
                gaine_result = compute_gaine({
                    "type": gtype, "qty": gqty,
                    "sale_price": sale_pg, "longueur": _glongueur_val,
                })
                if gaine_result:
                    st.caption(f"Sale: {gaine_result['sale']} €  |  {gaine_result['hours']} h install")
            else:
                st.caption("⚠️ Enter length to see install hours")

    # ── Prestations ──────────────────────────────────────────────────────────
    st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
    st.markdown("#### Prestations")

    has_nacelle_louee = (nacelle == "Louée")
    n_nacelle_j = 0
    if has_nacelle_louee:
        _lnj = "Nacelle days ❌" if not ss.get("sa_nacelle_j","") else "Nacelle days"
        n_nacelle_j = _safe_int(_nf(_lnj, "sa_nacelle_j", ""), 0)

    # ── Auto-clear results if inputs changed ─────────────────────────────────
    _input_sig = str({
        "project": project,
        "bat": ss.get("sa_bat"), "client": ss.get("sa_ctype"),
        "scenario": ss.get("sa_scenario"), "securite": ss.get("sa_securite"),
        "nacelle": ss.get("sa_nacelle"), "km": ss.get("sa_km"),
        "cfgs": str([(ss.get(f"sa_cfix_{i}"), ss.get(f"sa_ctype_pick_{i}"))
                     for i in range(MAX_CONFIGS)]),
        "gaines": str([ss.get(f"sa_glong_{ga['id']}") for ga in _gaine_arts]),
    })
    if ss.get("sa_input_sig") and ss["sa_input_sig"] != _input_sig:
        ss.pop("sa_result", None)
    ss["sa_input_sig"] = _input_sig

    # ── Clear results if inputs changed ──────────────────────────────────────
    _sig = str([
        project, ss.get("sa_bat"), ss.get("sa_ctype"), ss.get("sa_scenario"),
        ss.get("sa_securite"), ss.get("sa_nacelle"), ss.get("sa_km"), ss.get("sa_tarif"),
        str([(ss.get(f"sa_cfix_{i}"), ss.get(f"sa_ctype_pick_{i}"))
             for i in range(MAX_CONFIGS)]),
        str([ss.get(f"sa_glong_{ga['id']}") for ga in _gaine_arts]),
    ])
    if ss.get("_sa_sig") and ss["_sa_sig"] != _sig:
        ss.pop("sa_result", None)
    ss["_sa_sig"] = _sig

    # ── Compute button ────────────────────────────────────────────────────────
    st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
    _, btn = st.columns([4, 1])
    with btn:
        calc = st.button("🧮 Calculate", type="primary", use_container_width=True, key="sa_calc")

    if calc:
        _errs = []
        valid_cfgs = [r for r in cfg_results if r is not None]
        if not valid_cfgs:
            _errs.append("No valid configuration.")
        for _ga in _gaine_arts:
            _glk = f"sa_glong_{_ga['id']}"
            if not str(ss.get(_glk,"")).strip():
                _errs.append(f"Gaine length required ({_ga.get('default_code','?')})")
        if has_nacelle_louee and not ss.get("sa_nacelle_j","").strip():
            _errs.append("Nacelle days required")
        if _errs:
            for _e in _errs: st.error(_e)
        else:
            ratio_bat    = BUILDING_TYPES.get(bat_type, 1.0)
            ratio_client = CLIENT_TYPES.get(client_type, 1.0)
            ratio_sec    = SECURITE_RENFORCEE_OPTIONS.get(securite, 1.0)
            ratio_scen   = SCENARIOS.get(scenario, 1.0)

            configs_hours = [r["hours"] for r in cfg_results if r]
            gaine_hours   = gaine_result["hours"] if gaine_result else 0.0

            prest = compute_prestations(
                n_configs      = len([r for r in cfg_results if r]),
                configs_hours  = configs_hours,
                gaine_hours    = gaine_hours,
                ratio_bat      = ratio_bat,
                ratio_client   = ratio_client,
                scenario_ratio = ratio_scen,
                tarif_h        = _safe(tarif_h_val, DEFAULT_TARIF_H),
                km_ar          = _safe(km_ar, DEFAULT_KM_AR),
                nacelle        = nacelle,
                n_nacelle_j    = n_nacelle_j,
            )

            # Totals
            total_sale = sum(r["sale"] for r in cfg_results if r)
            if gaine_result:
                total_sale += gaine_result["sale"]

            total_sale_with_sec = total_sale * ratio_sec
            grand_total = round(total_sale_with_sec + prest["cout_total_mo"], 2)

            ss["sa_result"] = {
                "prest": prest,
                "total_sale": total_sale_with_sec,
                "grand_total": grand_total,
                "scenario": scenario,
                "ratio_scen": ratio_scen,
            }

    # ── Results ───────────────────────────────────────────────────────────────
    if ss.get("sa_result"):
        r = ss["sa_result"]
        p = r["prest"]
        st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
        st.divider()
        st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
        st.markdown("### Results")

        res_mat, res_mo = st.columns(2)

        with res_mat:
            st.markdown("**Material**")
            _price_row("Total sale price", f"{r['total_sale']:.2f} €")

        with res_mo:
            st.markdown("**Services**")
            _rows_h = [
                ("Survey",       p['h_releve']),
                ("Installation", p['h_montage']),
                ("Reception",    p['h_recep']),
                ("Handover",     p['h_dossier']),
            ]
            _n_h = len(_rows_h)
            _rh  = _n_h * 32
            # Brace pointing LEFT ← (opening to the right)
            _brace = (
                f'<svg viewBox="0 0 16 {_rh}" width="16" height="{_rh}" '
                f'xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0;margin:0 2px;">'
                f'<path d="M2,2 C10,2 10,{_rh//2-4} 14,{_rh//2} C10,{_rh//2+4} 10,{_rh-2} 2,{_rh-2}" '
                f'fill="none" stroke="#FDAE1B" stroke-width="1.5"/>'
                f'</svg>'
            )
            _rows_html = "".join(
                f"<div style='display:flex;justify-content:space-between;height:32px;"
                f"align-items:center;padding:0 8px 0 0;'>"
                f"<span style='font-size:0.82rem;color:#aaa;'>{lbl}</span>"
                f"<span style='font-size:0.82rem;font-weight:600;color:#fff;'>{h} h</span></div>"
                for lbl, h in _rows_h
            )
            # Price column aligned with _price_row (uses [1.4,1,0.9] columns)
            # We mirror that with a fixed-width right block
            st.markdown(
                f"<div style='display:flex;align-items:center;'>"
                f"<div style='flex:1.6;'>{_rows_html}</div>"
                f"<div style='width:12px;'></div>"
                f"{_brace}"
                f"<div style='flex:1;text-align:right;padding-right:4px;'>"
                f"<div style='font-size:0.90rem;font-weight:700;color:#fff;'>{p['h_total']} h</div>"
                f"<div style='font-size:0.82rem;color:#FDAE1B;'>→ {p['cout_mo']:.2f} €</div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-top:6px'></div>", unsafe_allow_html=True)
            # Travel + Nacelle aligned with brace total (flex:1.6 + 12px + brace + flex:1)
            def _svc_row(lbl, val):
                st.markdown(
                    f"<div style='display:flex;'>"  
                    f"<div style='flex:1.6;padding-right:8px;'>"  
                    f"<span style='font-size:0.82rem;color:#aaa;'>{lbl}</span></div>"  
                    f"<div style='width:12px;'></div>"  
                    f"<div style='width:16px;'></div>"  
                    f"<div style='flex:1;text-align:right;padding-right:4px;'>"  
                    f"<span style='font-size:0.82rem;color:#FDAE1B;'>→ {val}</span></div>"  
                    f"</div>",
                    unsafe_allow_html=True,
                )
            _svc_row("Travel", f"{p['defraiement']:.2f} €")
            if p['cout_nacelle']:
                st.markdown("<div style='margin-top:2px'></div>", unsafe_allow_html=True)
                _svc_row("Nacelle", f"{p['cout_nacelle']:.2f} €")
            _total_service = p['cout_total_mo']
            _total_box("Services total", f"{_total_service:.2f}")

        st.markdown("<div style='margin-top:0.5rem'></div>", unsafe_allow_html=True)
        _, total_col = st.columns([1, 1])
        with total_col:
            _total_box("GRAND TOTAL", f"{r['grand_total']:.2f}", big=True)