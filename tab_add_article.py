# tab_add_article.py
# ─────────────────────────────────────────────────────────────────────────────
#  All logic and rendering for the "Add Articles" tab
# ─────────────────────────────────────────────────────────────────────────────

import streamlit as st
import xmlrpc.client
import difflib
import re
import os
import base64
from collections import defaultdict

from Mapping import (
    map_supplier, map_abbreviation,
    map_abus_product_discount, map_abus_type_discount,
    map_abus_product_margin, map_exclude_words,
)

# ── pulled from config.py at runtime ──────────────────────────────────────────
# (imported in app.py and passed via st.session_state)

ss = st.session_state   # shorthand – same object everywhere


# ═════════════════════════════════════════════════════════════════════════════
#  PURE HELPERS  (no Streamlit, no Odoo)
# ═════════════════════════════════════════════════════════════════════════════

def safe_int(value):
    try:    return int(value)
    except: return 0

def safe_float(value):
    try:    return round(float(str(value).replace(",", ".")), 2)
    except: return 0.0

def validate_float_str(s: str) -> bool:
    if s == "": return True
    s = s.replace(",", ".")
    try:
        float(s)
        return s.count(".") <= 1
    except ValueError:
        return False

def _num_input(label, key, help=None, disabled=False, max_val=None):
    """Numeric float text_input. Pre-sanitizes session_state before render."""
    def _clean(raw):
        s = re.sub(r"[^0-9.,]", "", str(raw))          # strip non-numeric
        parts = re.split(r"[.,]", s, maxsplit=1)
        s = (parts[0] + "." + parts[1]) if len(parts) == 2 else parts[0]
        if max_val is not None and s:
            try:
                if float(s) > max_val:
                    s = str(int(max_val) if float(max_val) == int(max_val) else max_val)
            except ValueError:
                pass
        return s

    # Pre-sanitize BEFORE rendering so the widget shows the clean value
    if key in ss:
        clean = _clean(ss[key])
        if clean != str(ss[key]):
            ss[key] = clean

    def _on_change():
        ss[key] = _clean(ss.get(key, ""))

    return st.text_input(label, help=help, disabled=disabled, key=key, on_change=_on_change)

def _int_input(label, key, help=None, disabled=False, max_val=None):
    """Integer text_input. Pre-sanitizes session_state before render."""
    def _clean(raw):
        s = re.sub(r"[^0-9]", "", str(raw))
        if max_val is not None and s:
            try:
                if int(s) > max_val:
                    s = str(max_val)
            except ValueError:
                pass
        return s

    if key in ss:
        clean = _clean(ss[key])
        if clean != str(ss[key]):
            ss[key] = clean

    def _on_change():
        ss[key] = _clean(ss.get(key, ""))

    return st.text_input(label, help=help, disabled=disabled, key=key, on_change=_on_change)

def _load_input(key, label="Load [kg,t]"):
    """Load: digits, decimal point, and unit chars (t/k/g) + operators (+/-/x)."""
    # Valid chars: 0-9  .  t  k  g  +  -  /  space
    # (k and g only valid together as 'kg', t alone as 't' — we allow the chars,
    #  validate_load() enforces the full pattern and shows the red border if wrong)
    def _clean(raw):
        return re.sub(r"[^0-9.tkgTKG+\-/\s]", "", str(raw)).strip()

    if key in ss:
        c = _clean(ss[key])
        if c != str(ss[key]):
            ss[key] = c

    def _on_change():
        ss[key] = _clean(ss.get(key, ""))

    return st.text_input(
        label,
        help="ex : 10t, 500kg, 3.2t+3.2t …",
        key=key,
        on_change=_on_change,
    )

def _span_input(key, label="Span [mm]"):
    """Span text_input: digits and one 'x' only (e.g. 4000x7500)."""
    def _clean(raw):
        s = re.sub(r"[^0-9xX]", "", str(raw)).replace("X", "x")
        parts = s.split("x")
        return (parts[0] + "x" + parts[1]) if len(parts) > 2 else s

    if key in ss:
        clean = _clean(ss[key])
        if clean != str(ss[key]):
            ss[key] = clean

    def _on_change():
        ss[key] = _clean(ss.get(key, ""))

    return st.text_input(label, key=key, on_change=_on_change)

def is_near(value, exclude_list, threshold):
    value = value.lower()
    for word in exclude_list:
        if difflib.SequenceMatcher(None, value, word.lower()).ratio() >= threshold:
            return True
    return False

def _norm(s):
    s = s or ""
    return " ".join("".join(c for c in s if c.isalnum() or c.isspace()).split()).lower()

def get_supplier_info(supplier_name):
    best_match, best_score = None, 0
    for name, sid, abbr in map_supplier():
        score = difflib.SequenceMatcher(None, _norm(name), _norm(supplier_name)).ratio()
        if score > best_score:
            best_score = score
            best_match = (sid, abbr)
    return best_match if best_score >= 0.8 else (None, None)

def get_abbreviation(product_name, product_values, has_product_selected):
    if product_name == "" and has_product_selected:
        return "XX"
    if not (product_name or "").strip() and not all(v == "" for v in product_values):
        return ""
    best_match, best_score = None, 0
    for prod, abbr, _ in map_abbreviation():
        score = difflib.SequenceMatcher(None, _norm(prod), _norm(product_name)).ratio()
        if score > best_score:
            best_score = score
            best_match = abbr
    return best_match if best_score >= 0.75 else "XX"

def get_detailed_type(input_product_name):
    best_serial, best_score = None, 0
    for mapped, _, serial in map_abbreviation():
        score = difflib.SequenceMatcher(None, _norm(mapped), _norm(input_product_name)).ratio()
        if score > best_score:
            best_score = score
            best_serial = serial
    if best_score >= 0.7:
        return "product" if best_serial else "consu"
    return "consu"

def get_article_category(article_table, supplier, product, type_):
    for row in article_table:
        if row[0] == supplier and row[1] == product and row[2] == type_:
            return row[3]
    return None

def compute_discounts(category, product, type_):
    d1, d2 = 0, 0
    if category == "ABUS":
        apd = map_abus_product_discount()
        atd = map_abus_type_discount()
        if product in apd: d1, d2 = apd[product]
        if type_  in atd:  d1, d2 = atd[type_]
    elif category == "DEMAG":
        d1 = 25
    return d1, d2

def compute_net_price(gross, d1, d2, discountS):
    return round(
        safe_float(gross)
        * (1 - d1 / 100)
        * (1 - d2 / 100)
        * (1 - safe_float(discountS) / 100),
        2,
    )

def compute_sale_price(net, margin):
    if margin and str(margin).strip():
        return round(safe_float(net) * (1 + safe_float(margin) / 100), 2)
    return 0.0

def get_margin_for_product(product):
    return map_abus_product_margin().get(product, None)

def build_article_name(product, category, type_, addition, load, span):
    if type_ == "":
        elements = [product, addition, category, type_, load]
    else:
        elements = [product, category, type_, addition, load]
    name = " ".join(e for e in elements if e)
    if span and (re.fullmatch(r'\d+', span) or re.fullmatch(r'\d+x\d+', span)):
        name += f" x {span}mm"
    return re.sub(r"\s*\([^)]*\)", "", name)

def build_reference(supplier_abbr, product, addition, project, product_values, has_product):
    token = (product or "") if (product or "") not in (" ", "") else (addition or "")
    abbr  = get_abbreviation(token, product_values, has_product)
    return "_".join(c for c in [supplier_abbr, abbr, project] if c)

def build_purchase_desc(category, gross_price, net_price_display, d1, d2, discountS):
    if category in ("ABUS", "DEMAG") and gross_price and d1 != 0:
        dis2 = f"x(1-{d2}%)" if d2 != 0 else ""
        dis3 = f"x(1-{discountS}%)" if discountS not in (0, "", "0") else ""
        return " ".join(e for e in [
            gross_price, f"x(1-{d1}%)", dis2, dis3, f"= {net_price_display} €"
        ] if e)
    return ""

def get_reference_image_from_category(category_name):
    folder = os.path.join(os.path.dirname(__file__), "image_1920")
    for ext in [".png", ".jpg", ".jpeg"]:
        fp = os.path.join(folder, f"{category_name}{ext}")
        if os.path.isfile(fp):
            with open(fp, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
    return None


# ═════════════════════════════════════════════════════════════════════════════
#  VALIDATION
# ═════════════════════════════════════════════════════════════════════════════

def validate_project(p):
    return bool(
        (re.fullmatch(r'S\d{2}-\d{5}', p) or re.fullmatch(r'S\d{2}-\d{5}_[a-zA-Z0-9]+', p))
        and len(p) < 17
    )

def validate_load(load):
    if load == "": return True
    return bool(re.fullmatch(
        r'(?:\d+(?:\.\d+)?\s*t(?:\s*[\+\/-]\s*\d+(?:\.\d+)?\s*t)*)|\d+(?:\.\d+)?\s*kg',
        load,
    ))

def validate_span(span):
    if span == "": return True
    return bool(re.fullmatch(r'\d+', span) or re.fullmatch(r'\d+x\d+', span))

def check_article_fields(project, category, product, type_, load, span, product_values, type_values):
    ok, errs = True, {}
    if not validate_project(project):
        errs["project"] = "Format: Sxx-xxxxx ou Sxx-xxxxx_x"
        ok = False
    if product_values and category == "":
        errs["category"] = "Requis"
        ok = False
    if product_values and not product:
        errs["product"] = "Requis"
        ok = False
    if type_values and type_values != [""] and not type_:
        errs["type"] = "Requis"
        ok = False
    if not validate_load(load):
        errs["load"] = "Format: 10t, 500kg, 3.2t+3.2t …"
        ok = False
    if not validate_span(span):
        errs["span"] = "Format: 10000 ou 4000x7500"
        ok = False
    return ok, errs

def check_purchase_sale_fields(category, user_address, purchase_ref,
                                gross_price, net_price, delay, margin, discountS):
    ok, errs = True, {}
    olsen_bel = (category == "OLSEN" and user_address == "OLSEN ENGINEERING (BEL)")

    if gross_price == "" and net_price == "" and not olsen_bel:
        errs["prix"] = "Prix brut ou net requis"
        ok = False
    if delay == "" and not olsen_bel:
        errs["délai"] = "Requis"
        ok = False
    if (margin == "" or not re.fullmatch(r'\d+', str(margin).strip())) and not olsen_bel:
        errs["marge"] = "Requis (entier)"
        ok = False
    if purchase_ref == "" and not olsen_bel:
        errs["ref fournisseur"] = "Requis"
        ok = False
    elif purchase_ref == "017600-0000000-" and category == "ABUS":
        errs["ref fournisseur"] = "Compléter la référence ABUS"
        ok = False
    return ok, errs


# ═════════════════════════════════════════════════════════════════════════════
#  ODOO CALLS  (article tab only)
# ═════════════════════════════════════════════════════════════════════════════

def load_article_mappings(uid, password, db_name, models_url):
    exclude_words = map_exclude_words()
    brands  = [row[0] for row in map_supplier()]
    rows    = set()
    models  = xmlrpc.client.ServerProxy(models_url)

    for brand in brands:
        cat_ids = models.execute_kw(
            db_name, uid, password,
            "product.category", "search",
            [[["complete_name", "ilike", brand]]],
            {"limit": 10000},
        )
        if not cat_ids:
            continue
        cats = models.execute_kw(
            db_name, uid, password,
            "product.category", "read",
            [cat_ids], {"fields": ["id", "complete_name"]},
        )
        for cat in cats:
            levels = (cat["complete_name"] or "").split(" / ")
            l1 = levels[1] if len(levels) > 1 else ""
            l2 = levels[2] if len(levels) > 2 else " "
            l3 = levels[3] if len(levels) > 3 else ""
            if is_near(l2, exclude_words, 0.8): continue
            if is_near(l3, exclude_words, 0.8): continue
            if l1:
                rows.add((l1, l2, l3, cat["id"]))

    table = sorted(rows)
    c2p   = defaultdict(set)
    cp2t  = defaultdict(list)
    for cat, prod, type_, _ in table:
        c2p[cat].add(prod)
        cp2t[(cat, prod)].append(type_)

    return (
        table,
        sorted(c2p.keys()),
        {k: sorted(v) for k, v in c2p.items()},
        dict(cp2t),
    )

def fetch_sale_orders(uid, password, user_name, db_name, models_url):
    models = xmlrpc.client.ServerProxy(models_url)
    domain = [("user_id", "=", user_name), ("state", "not in", ["sale", "cancel"])]
    try:
        orders = models.execute_kw(
            db_name, uid, password,
            "sale.order", "search_read",
            [domain],
            {"fields": ["name", "partner_id", "x_studio_description_sales_order"]},
        )
        lst = [
            {
                "id": o["id"],
                "name": o["name"],
                "customer": o["partner_id"][1],
                "description": o.get("x_studio_description_sales_order", ""),
            }
            for o in orders
        ]
        return {o["name"]: o for o in lst}
    except Exception as e:
        st.warning(f"⚠️ Impossible de charger les commandes : {e}")
        return {}

def do_add_article(
    db_name, models_url,
    uid, password,
    user_name, user_address,
    name, reference,
    category, product,
    supplier_id, supplier,
    categ_id,
    purchase_ref, gross_price, discountS,
    net_price, delay,
    sale_desc, sale_price, margin, weight,
    purchase_desc,
    project,
    tech_text,
    debug=False,
):
    models_proxy = xmlrpc.client.ServerProxy(models_url)

    # ── duplicate check ──────────────────────────────────────────────────────
    existing = models_proxy.execute_kw(
        db_name, uid, password,
        "product.product", "search",
        [[("default_code", "=", reference)]],
    )
    if existing:
        st.warning("⚠️ Un produit avec cette référence existe déjà.")
        return

    user_id = models_proxy.execute_kw(
        db_name, uid, password,
        "res.users", "search",
        [[["name", "=", user_name]]],
    )

    # ── routes & flags ───────────────────────────────────────────────────────
    FLAG_SUPPLIER = 1
    if supplier == "OLSEN":
        purchase = False
        FLAG_SUPPLIER = 0
        routes = [1, 6]
        if user_address == "OLSEN ENGINEERING (LUX)":
            purchase = True
            FLAG_SUPPLIER = 1
            routes = [1, 5, 6]
    else:
        purchase = True
        routes   = [1, 5]

    detailed_type = get_detailed_type(product)
    tracking      = "serial" if detailed_type == "product" else "none"
    company_id    = 2 if user_address == "OLSEN ENGINEERING (LUX)" else 1

    intrastat = models_proxy.execute_kw(
        db_name, uid, password,
        "account.intrastat.code", "search",
        [[["code", "=", "84261900"]]],
    )

    # ── create product ───────────────────────────────────────────────────────
    product_id = models_proxy.execute_kw(
        db_name, uid, password,
        "product.product", "create",
        [{
            "name":            name,
            "default_code":    reference,
            "type":            detailed_type,
            "detailed_type":   detailed_type,
            "invoice_policy":  "delivery",
            "list_price":      safe_float(sale_price),
            "standard_price":  safe_float(net_price),
            "sale_delay":      safe_int(delay),
            "categ_id":        categ_id,
            "purchase_ok":     purchase,
            "sale_ok":         True,
            "tracking":        tracking,
            "weight":          safe_float(weight),
            "route_ids":       [(6, 0, routes)],
            "company_id":      company_id,
            "intrastat_code_id": intrastat[0] if intrastat else False,
        }],
    )

    # ── supplier country → intrastat ─────────────────────────────────────────
    eff_sid = 4262 if "ABU_ALIM" in reference else supplier_id
    if eff_sid:
        s_data = models_proxy.execute_kw(
            db_name, uid, password,
            "res.partner", "read",
            [eff_sid], {"fields": ["country_id"]},
        )
        if s_data and s_data[0].get("country_id"):
            cid = s_data[0]["country_id"][0]
            models_proxy.execute_kw(
                db_name, uid, password,
                "product.product", "write",
                [[product_id], {"intrastat_origin_country_id": cid, "country_of_origin": cid}],
            )

    # ── category image ───────────────────────────────────────────────────────
    img = get_reference_image_from_category(category)
    if img:
        models_proxy.execute_kw(
            db_name, uid, password,
            "product.product", "write",
            [[product_id], {"image_1920": img}],
        )

    # ── template id ──────────────────────────────────────────────────────────
    tmpl_data = models_proxy.execute_kw(
        db_name, uid, password,
        "product.product", "read",
        [product_id], {"fields": ["product_tmpl_id"]},
    )
    tmpl_id = tmpl_data[0]["product_tmpl_id"][0]

    # ── responsible user ─────────────────────────────────────────────────────
    models_proxy.execute_kw(
        db_name, uid, password,
        "product.product", "write",
        [[product_id], {"responsible_id": user_id[0]}],
        {"context": {"allowed_company_ids": [company_id], "company_id": company_id}},
    )

    # ── standard price for LUX company ───────────────────────────────────────
    if company_id == 2:
        models_proxy.execute_kw(
            db_name, uid, password,
            "product.template", "write",
            [[tmpl_id], {"standard_price": safe_float(net_price)}],
            {"context": {"allowed_company_ids": [company_id], "company_id": company_id}},
        )

    # ── descriptions ─────────────────────────────────────────────────────────
    models_proxy.execute_kw(
        db_name, uid, password,
        "product.template", "write",
        [[tmpl_id], {"description_purchase": purchase_desc, "description_sale": sale_desc}],
    )

    # ── supplier info ────────────────────────────────────────────────────────
    if FLAG_SUPPLIER == 1:
        models_proxy.execute_kw(
            db_name, uid, password,
            "product.supplierinfo", "create",
            [{
                "product_tmpl_id": tmpl_id,
                "partner_id":      safe_int(eff_sid),
                "product_code":    purchase_ref,
                "price":           safe_float(net_price),
                "delay":           safe_int(delay),
                "min_qty":         1,
            }],
            {"context": {"allowed_company_ids": [company_id], "company_id": company_id}},
        )

    st.success(f"✅ Produit créé ! ID : {product_id}  |  Réf : {reference}")

    # ── technical data ───────────────────────────────────────────────────────
    if tech_text:
        _append_technical_data(db_name, models_url, uid, password, project, tech_text)


def _append_technical_data(db_name, models_url, uid, password, project, new_text):
    models_proxy = xmlrpc.client.ServerProxy(models_url)
    order_id = models_proxy.execute_kw(
        db_name, uid, password,
        "sale.order", "search",
        [[["name", "=", project[:9]]]],
    )
    if not order_id:
        st.warning("⚠️ Commande introuvable pour les données techniques.")
        return
    result = models_proxy.execute_kw(
        db_name, uid, password,
        "sale.order", "read",
        [order_id[0]], {"fields": ["technical_data"]},
    )
    existing = result[0].get("technical_data", "")
    formatted = f"<p>{new_text.replace(chr(10), '<br/>')}</p>"
    updated   = (existing + formatted) if existing else formatted
    models_proxy.execute_kw(
        db_name, uid, password,
        "sale.order", "write",
        [[order_id[0]], {"technical_data": updated}],
    )
    st.success(f"✅ Données techniques ajoutées à la commande : {project[:9]}")


# ═════════════════════════════════════════════════════════════════════════════
#  STREAMLIT UI
# ═════════════════════════════════════════════════════════════════════════════

def render_add_article_tab(db_name, models_url, debug=False):
    """Main entry point — call from app.py inside the tab."""
    ct = ss.get("clear_trigger", 0)

    def _sv(key, default=""):
        return ss.get(key, default) or default

    # ── Key names ────────────────────────────────────────────────────────────
    project_key = f"aa_project_{ct}"
    category_key= f"aa_category_{ct}"
    product_key = f"aa_product_{ct}"
    type_key    = f"aa_type_{ct}"
    load_key    = f"aa_load_{ct}"
    span_key    = f"aa_span_{ct}"
    gross_key   = f"aa_gross_{ct}"
    discS_key   = f"aa_discS_{ct}"
    delay_key   = f"aa_delay_{ct}"
    net_key_man = f"aa_net_manual_{ct}"

    # ── Read current values from session_state for upfront validation ─────────
    project_cur  = _sv(project_key)
    category_cur = _sv(category_key)
    product_cur  = _sv(product_key)
    type_cur     = _sv(type_key)
    load_cur     = _sv(load_key)
    span_cur     = _sv(span_key)
    gross_cur    = _sv(gross_key)
    discS_cur    = _sv(discS_key)
    delay_cur    = _sv(delay_key)

    product_values_cur = ss.get("category_to_products", {}).get(category_cur, []) if category_cur else []
    type_values_cur    = ss.get("category_product_to_types", {}).get((category_cur, product_cur), []) if (category_cur and product_cur) else []
    d1_cur, d2_cur     = compute_discounts(category_cur, product_cur, type_cur)
    margin_key  = f"aa_margin_{ct}_{product_cur}"
    pref_key    = f"aa_pref_{ct}_{category_cur}"
    margin_cur  = _sv(margin_key)
    pref_cur    = _sv(pref_key)
    net_cur     = _sv(net_key_man)

    is_olsen_bel = (category_cur == "OLSEN" and ss.get("user_address") == "OLSEN ENGINEERING (BEL)")

    net_val_cur = compute_net_price(gross_cur, d1_cur, d2_cur, discS_cur) if (gross_cur and validate_float_str(gross_cur)) else 0.0
    net_display_cur = str(net_val_cur) if gross_cur else net_cur

    _, art_errs = check_article_fields(project_cur, category_cur, product_cur, type_cur,
                                        load_cur, span_cur, product_values_cur, type_values_cur)
    _, ps_errs  = check_purchase_sale_fields(category_cur, ss.get("user_address",""),
                                              pref_cur, gross_cur, net_display_cur,
                                              delay_cur, margin_cur, discS_cur)

    # ── CSS: color labels red for invalid fields using input[id] selector ─────
    # Streamlit sets id="key" on the actual input element.
    # We inject a <style> that targets label[for="key"] → colors the label red.
    err_keys = {
        "project":         project_key    if "project"        in art_errs else None,
        "category":        category_key   if "category"       in art_errs else None,
        "product":         product_key    if "product"        in art_errs else None,
        "type":            type_key       if "type"           in art_errs else None,
        "load":            load_key       if "load"           in art_errs else None,
        "span":            span_key       if "span"           in art_errs else None,
        "pref":            pref_key       if "ref fournisseur" in ps_errs else None,
        "gross":           gross_key      if "prix"           in ps_errs else None,
        "net":             net_key_man    if ("prix" in ps_errs and not gross_cur) else None,
        "delay":           delay_key      if "délai"          in ps_errs else None,
        "margin":          margin_key     if "marge"          in ps_errs else None,
    }



    # ── Section : Article details ─────────────────────────────────────────────
    st.markdown("### 📦 Article details")
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        _lp = "Project ❌" if "project" in art_errs else "Project"
        project = st.selectbox(
            _lp,
            options=[""] + list(ss.get("sale_orders_ref", {}).keys()),
            help="Sxx-xxxxx ou Sxx-xxxxx_x",
            accept_new_options=True,
            key=project_key,
        )
        project = project or ""
    with col2:
        _lc = "Category ❌" if "category" in art_errs else "Category"
        category = st.selectbox(
            _lc,
            options=ss.get("category_list", []),
            index=None,
            key=category_key,
        )

    product_values = ss.get("category_to_products", {}).get(category, []) if category else []
    with col3:
        _lprod = "Product ❌" if "product" in art_errs else "Product"
        product = st.selectbox(
            _lprod,
            options=product_values,
            index=None,
            disabled=not product_values,
            key=product_key,
        )

    type_values = ss.get("category_product_to_types", {}).get((category, product), []) if (category and product) else []
    with col4:
        _ltype = "Type ❌" if "type" in art_errs else "Type"
        type_ = st.selectbox(
            _ltype,
            options=type_values,
            index=None,
            disabled=not type_values,
            key=type_key,
        )

    # Normalize None → empty string after selectboxes

    with col5:
        addition = st.text_input(
            "Addition",
            help="Ajout au produit — non obligatoire",
            key=f"aa_addition_{ct}",
        )

    ca, cb, _ = st.columns([1, 1, 3])
    with ca:
        _lload = "Load [kg,t] ❌" if "load" in art_errs else "Load [kg,t]"
        load = _load_input(load_key, label=_lload)
    with cb:
        _lspan = "Span [mm] ❌" if "span" in art_errs else "Span [mm]"
        span = _span_input(span_key, label=_lspan)

    # ── Computed : article name & reference ───────────────────────────────────
    sid, sabb = get_supplier_info(category)
    categ_id  = get_article_category(ss.get("article_table", []), category, product, type_)
    d1, d2    = compute_discounts(category, product, type_)
    art_name  = build_article_name(product, category, type_, addition, load, span)
    art_ref   = build_reference(sabb or "", product, addition, project,
                                product_values, bool(product_values and product))

    st.markdown("<div style='margin-top:0.6rem'></div>", unsafe_allow_html=True)
    cn, cr = st.columns([3, 2])
    with cn:
        st.markdown(
            f"**Product Name :**&nbsp;&nbsp;"
            f'<span style="color:#ffcc00;font-weight:700;font-style:italic;">{art_name or "—"}</span>',
            unsafe_allow_html=True,
        )
    with cr:
        st.markdown(
            f"**Reference :**&nbsp;&nbsp;"
            f'<span style="color:#ffcc00;font-weight:700;font-style:italic;">{art_ref or "—"}</span>',
            unsafe_allow_html=True,
        )

    # ── Section : Purchase details ────────────────────────────────────────────
    st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
    st.divider()
    st.markdown("<div style='margin-top:0.3rem'></div>", unsafe_allow_html=True)
    st.markdown("### 🛒 Purchase details")

    abus_def_ref = "017600-0000000-" if category == "ABUS" else ""

    pc1, pc2, pc3 = st.columns(3)
    with pc1:
        _lpref = "Supplier reference ❌" if "ref fournisseur" in ps_errs else "Supplier reference"
        purchase_ref = st.text_input(
            _lpref,
            value=abus_def_ref,
            help="ex : 1929534 ou 017600-0000000-20241018-18/1",
            disabled=is_olsen_bel,
            key=f"aa_pref_{ct}_{category}",
        )
    with pc2:
        _lgross = "Gross price [€] ❌" if "prix" in ps_errs else "Gross price [€]"
        gross_price = _num_input(_lgross, key=gross_key, disabled=is_olsen_bel)
    with pc3:
        discountS = _num_input("Special Discount [%]", key=discS_key, max_val=100.0, disabled=is_olsen_bel)

    # ── Calculated net price ──────────────────────────────────────────────────
    gross_price = ss.get(gross_key, "") or ""
    discountS   = ss.get(discS_key,  "") or ""
    if gross_price and validate_float_str(gross_price):
        net_val           = compute_net_price(gross_price, d1, d2, discountS)
        net_price_display = str(net_val)
        net_disabled      = True
    else:
        net_val           = 0.0
        net_price_display = ""
        net_disabled      = False

    show_purchase_desc = category in ("ABUS", "DEMAG") and bool(gross_price) and d1 != 0
    show_remise_only   = bool(discountS) and not show_purchase_desc
    if show_purchase_desc:
        purchase_desc_auto = build_purchase_desc(category, gross_price, net_price_display, d1, d2, discountS)
    elif show_remise_only:
        purchase_desc_auto = f"Remise : {discountS}%"
    else:
        purchase_desc_auto = ""

    pdesc_key = f"aa_pdesc_{ct}_{category}_{gross_price}_{d1}_{d2}_{discountS}"

    pc4, pc5, pc6 = st.columns(3)
    with pc4:
        if show_purchase_desc or show_remise_only:
            purchase_desc = st.text_input(
                "Purchase description",
                value=purchase_desc_auto, disabled=True, key=pdesc_key,
            )
        else:
            purchase_desc = st.text_input(
                "Purchase description",
                value="", disabled=is_olsen_bel,
                key=f"aa_pdesc_empty_{ct}_{category}_{gross_price}",
            )
    with pc5:
        if net_disabled:
            net_price = st.text_input(
                "Net price [€]",
                value=net_price_display, disabled=True,
                help="Calculé depuis : Prix brut, remises fournisseur, remise spéciale",
                key=f"aa_net_{ct}_{gross_price}_{d1}_{d2}_{discountS}",
            )
        else:
            _lnet = "Net price [€] ❌" if ("prix" in ps_errs and not gross_price) else "Net price [€]"
            net_price = _num_input(_lnet, key=net_key_man, disabled=is_olsen_bel)
    with pc6:
        _ldelay = "Delay [j] ❌" if "délai" in ps_errs else "Delay [j]"
        delay = _int_input(_ldelay, key=delay_key, disabled=is_olsen_bel)

    if not net_disabled:
        net_price = ss.get(net_key_man, "") or ""
    effective_net = net_val if gross_price else safe_float(net_price)

    # ── Section : Sale details ────────────────────────────────────────────────
    st.markdown("<div style='margin-top:0.8rem'></div>", unsafe_allow_html=True)
    st.divider()
    st.markdown("<div style='margin-top:0.3rem'></div>", unsafe_allow_html=True)
    st.markdown("### 💶 Sale details")

    sc1, sc2, sc3, sc4 = st.columns(4)
    with sc1:
        sale_desc = st.text_input(
            "Sale description",
            value="-- Voir description complète en fin de document --",
            key=f"aa_sdesc_{ct}",
        )
    with sc2:
        margin_default = get_margin_for_product(product)
        margin_key = f"aa_margin_{ct}_{product}"
        if margin_default is not None and not is_olsen_bel:
            if margin_key not in ss:
                ss[margin_key] = str(int(margin_default))
            _lmargin = "Margin [%] ❌" if "marge" in ps_errs else "Margin [%]"
            margin = _int_input(_lmargin, key=margin_key, max_val=100)
        else:
            _lmargin = "Margin [%] ❌" if "marge" in ps_errs else "Margin [%]"
            margin = _int_input(_lmargin, key=margin_key, max_val=100, disabled=is_olsen_bel)
    with sc3:
        sale_price_val     = compute_sale_price(effective_net, margin) if (effective_net and margin) else 0.0
        sale_price_display = str(sale_price_val) if sale_price_val else ""
        st.text_input(
            "Sale price [€]",
            value=sale_price_display, disabled=True,
            help="Calculé depuis : Net price + Margin",
            key=f"aa_sprice_{ct}_{effective_net}_{margin}",
        )
    with sc4:
        weight = _num_input("Weight [kg]", key=f"aa_weight_{ct}")

    # ── Validation for submit ────────────────────────────────────────────────
    art_ok, _ = check_article_fields(project, category, product, type_, load, span,
                                      product_values, type_values)
    ps_ok, _  = check_purchase_sale_fields(
        category, ss.get("user_address", ""),
        purchase_ref, gross_price,
        net_price_display if net_disabled else net_price,
        delay, margin, discountS,
    )

    # ── Buttons ───────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("<div style='margin-top:2.2rem'></div>", unsafe_allow_html=True)
    _, bb1, bb2 = st.columns([4, 1, 1])
    with bb1:
        add_clicked = st.button("➕ Add to Odoo", type="primary", use_container_width=True)
    with bb2:
        clear_clicked = st.button("🧹 Clear", use_container_width=True)

    if clear_clicked:
        ss.new_tech_text  = ""
        ss.clear_trigger  = ct + 1
        ss.sale_orders_ref = fetch_sale_orders(ss.uid, ss.password, ss.user_name, db_name, models_url)
        st.rerun()

    if add_clicked:
        a_ok, a_err = check_article_fields(project, category, product, type_, load, span,
                                            product_values, type_values)
        p_ok, p_err = check_purchase_sale_fields(
            category, ss.get("user_address", ""),
            purchase_ref, gross_price,
            net_price_display if net_disabled else net_price,
            delay, margin, discountS,
        )
        if not a_ok or not p_ok:
            pass  # ❌ shown inline in field labels
        if a_ok and p_ok:
            with st.spinner("Création dans Odoo …"):
                do_add_article(
                    db_name=db_name, models_url=models_url,
                    uid=ss.uid, password=ss.password,
                    user_name=ss.user_name, user_address=ss.get("user_address", ""),
                    name=art_name, reference=art_ref,
                    category=category, product=product,
                    supplier_id=sid, supplier=category,
                    categ_id=categ_id,
                    purchase_ref=purchase_ref,
                    gross_price=gross_price, discountS=discountS,
                    net_price=net_price_display if net_disabled else net_price,
                    delay=delay,
                    sale_desc=sale_desc,
                    sale_price=str(sale_price_val),
                    margin=margin, weight=weight,
                    purchase_desc=purchase_desc,
                    project=project,
                    tech_text=ss.get("new_tech_text", ""),
                    debug=debug,
                )
