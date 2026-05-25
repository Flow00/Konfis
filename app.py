# app.py  —  OlsenKonfis · Streamlit entry point
# Run with:  streamlit run app.py
# ─────────────────────────────────────────────────────────────────────────────

import streamlit as st
import os
import xmlrpc.client

from tab_add_article    import render_add_article_tab, load_article_mappings, fetch_sale_orders
from tab_railway_sizing import render_railway_sizing_tab
from tab_sales_automation import render_sales_automation_tab
from Mapping import _get_key

# ═════════════════════════════════════════════════════════════════════════════
#  CONFIG
# ═════════════════════════════════════════════════════════════════════════════
DEBUG = False
TEST  = False

from cryptography.fernet import Fernet
 

 
_TEST_DB_ADDRESS = b"gAAAAABqE2htdBWcWqIthWexCaxvtxdpvDoGgPrH7_OTBLS2Z0d-2ry8tDPPPaJdjGeuRHnnJFfwNKh8wpM57-yEgBHWuLoVNQmqLwMXcVSklxaR1uOghgC2ahFbVH2dcjAjoIqMNlTulJ3J4ZXR9jf4vyFWG3S5MQ=="
_TEST_DB_NAME    = b"gAAAAABqE2htJKMuRL-0ayeGqfbpViJk7XVQqvJPrOIrmVC6kxsAexvvLBVN3S0h1yTSykgUyONpKJJkUwYHA5SHjXiXzbqLKsi8-ZaHdFLkJVvGqol0hNuJnwo_5VnDDQvU15A6J_mI"
_TEST_USERNAME   = b"gAAAAABqE2ht5ZBGG0b0oqbad2hJFE8azhRVwsl31DI4tP5DPIHyh1adGSJw2HDhO1pshUC6RM8PQWB8ky8xbvI6LsdPhRqB8XYeDPEbrCxBtF1gJHC1Tyk="
_TEST_API_KEY    = b"gAAAAABqE2htG8WE0w9bUxh-RZOLP_p2Ra0Y48t56ztg9BV3fKqARot3GPTnsyK0GwEtcsCyf5s9XcqFNoCph2LvSL3ZJb2TWwubfsUv5upd-_HLjOpAnz0hvyk5GPtlK10JXB60xWqr"
 
_PROD_DB_ADDRESS = b"gAAAAABqE2ht4P3VPlP0GNBqxiZ0WKGIaCLWGIn9XMlbwWcMZEUFmrsXoFWYm9KIBXElkcD6DIVRWQu7RGcJ-nIxg80RFhKkUvxbgQr0FdJThvkDte6dkoQ9v6wvhRNM1oU7mq-yOICq"
_PROD_DB_NAME    = b"gAAAAABqE2htmE_TGqT7ewKivzuDra1VSjud1SJ3961bFZvJAv9lBkYWWj4RnhCFPdCkAelC-nD67N7rL4a0_mB8v0RnXymQ2FuuvpqfLr-6LRNCChN_XIM="
_PROD_USERNAME   = b"gAAAAABqE2htMc8QlJRtKWRHq83S2eCWVNM34jOL3sqEZNeMBtH1Q53Xz2hEROZchrDhpxki2Ef0CI5HPlkJkIBGL-ZQ77tI5uUWjIL_o7uEffTd28_ymSw="
_PROD_API_KEY    = b"gAAAAABqE2htYykEDxjhGVYmwS-_huVC_IFDEQn_WjigPKtm_BtTiaS20Vjx1SAel4RXweJg2u4knQj63m5FkzwTVIbUt0LMf_kzSAKh1OCZ6UTxs8zpiO3hHID2UJvPEL-s-L5m6_M5"

_f = Fernet(_get_key())
 
if TEST:
    db_address     = _f.decrypt(_TEST_DB_ADDRESS).decode()
    db_name        = _f.decrypt(_TEST_DB_NAME).decode()
    ADMIN_USERNAME = _f.decrypt(_TEST_USERNAME).decode()
    ADMIN_API_KEY  = _f.decrypt(_TEST_API_KEY).decode()
else:
    db_address     = _f.decrypt(_PROD_DB_ADDRESS).decode()
    db_name        = _f.decrypt(_PROD_DB_NAME).decode()
    ADMIN_USERNAME = _f.decrypt(_PROD_USERNAME).decode()
    ADMIN_API_KEY  = _f.decrypt(_PROD_API_KEY).decode()

common_url = f"{db_address}/xmlrpc/common"
models_url = f"{db_address}/xmlrpc/object"
VERSION    = "V2.0w07l"


# ═════════════════════════════════════════════════════════════════════════════
#  PAGE CONFIG  (must be first Streamlit call)
# ═════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="OlsenKonfis",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ═════════════════════════════════════════════════════════════════════════════
#  GLOBAL CSS
# ═════════════════════════════════════════════════════════════════════════════
st.markdown("""
<style>
/* ── Keep Streamlit toolbar visible (rerun, deploy, etc.) ── */
/* Only hide the redundant hamburger menu */
#MainMenu { display: none !important; }

/* ── Always show scrollbar to prevent layout shift ── */
html { overflow-y: scroll !important; }

/* ── Content padding ── */
.block-container { padding-top: 4rem !important; padding-bottom: 3rem !important; padding-left: 0 !important; padding-right: 0 !important; }
/* Inner content still has side padding */
.block-container > div { padding-left: 2rem; padding-right: 2rem; }

/* ── Banner: inline flow, sits above tabs ── */
.olsen-banner {
    background: #0A1E32;
    padding: 10px 2rem;
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    border-top: 3px solid #FDAE1B;
    border-bottom: 3px solid #FDAE1B;
    width: 100%;
    margin: 0 0 1.8rem 0;
    box-sizing: border-box;
}

/* ── Footer ── */
.footer {
    position:fixed; bottom:0; left:0; right:0;
    background:#0A1E32; color:#fff;
    font-size:0.72rem; padding:4px 20px;
    display:flex; justify-content:space-between;
    z-index:999;
}

/* ── Gaps ── */
div[data-testid="stVerticalBlock"]   { gap: 0.25rem !important; }
div[data-testid="stHorizontalBlock"] { gap: 0.5rem  !important; }

/* ── Labels ── */
div[data-testid="stTextInput"] label,
div[data-testid="stSelectbox"] label {
    font-size: 0.8rem !important;
    margin-bottom: 2px !important;
    padding-left: 2px !important;
}

/* ── Inputs — compact height, comfortable horizontal padding ── */
div[data-testid="stTextInput"] input {
    font-size: 0.82rem !important;
    padding: 5px 10px !important;   /* height: comfortable; sides: spacious */
    line-height: 1.3 !important;
}

/* Selectbox inner text */
div[data-testid="stSelectbox"] [data-baseweb="select"] > div {
    font-size: 0.78rem !important;
    padding: 4px 8px !important;
    line-height: 1.2 !important;
    min-height: unset !important;
}
/* Dropdown list items: smaller for more visible at once */
[data-testid="stSelectbox"] li {
    font-size: 0.78rem !important;
    padding: 4px 8px !important;
    min-height: unset !important;
}

/* ── Section headings (h3) ── */
h3 { font-size: 0.9rem !important; margin: 2px 0 4px 0 !important; padding-left: 2px; }

/* ── Dividers ── */
hr { margin: 8px 0 4px 0 !important; }

/* ── Tab bar ── */
button[data-baseweb="tab"] { font-size: 0.82rem !important; padding: 5px 12px !important; }

/* ── Calculated / disabled fields: subtle teal-grey, dark-theme friendly ── */
div[data-testid="stTextInput"] input:disabled {
    background-color: #1e2d3d !important;
    color: #a8c4d4 !important;
    -webkit-text-fill-color: #a8c4d4 !important;
    border-color: #2e4a5e !important;
    opacity: 1 !important;
}

/* ── Buttons ── */
div[data-testid="stButton"] > button[kind="primary"] {
    background: #FDAE1B; color: #0A1E32; font-weight: 700; border: none;
    font-size: 0.82rem !important; padding: 6px 14px !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover { opacity: 0.85; }
div[data-testid="stButton"] > button { font-size: 0.82rem !important; padding: 6px 14px !important; }
</style>
""", unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
#  SESSION STATE INIT
# ═════════════════════════════════════════════════════════════════════════════
def _init_session():
    defaults = {
        "logged_in": False, "uid": None, "password": None,
        "user_name": "", "user_department": "", "user_address": "",
        "db_loaded": False,           # True only after mappings are fully loaded
        "article_table": [], "category_list": [],
        "category_to_products": {}, "category_product_to_types": {},
        "sale_orders_ref": {},
        "new_tech_text": "",
        "clear_trigger": 0,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init_session()
ss = st.session_state


# ═════════════════════════════════════════════════════════════════════════════
#  BANNER / FOOTER
# ═════════════════════════════════════════════════════════════════════════════
def _render_banner():
    # Try multiple locations for logo.jpg
    _candidates = [
        (os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.png"), "image/png"),
        (os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo.jpg"), "image/jpeg"),
        (os.path.join(os.getcwd(), "logo.png"), "image/png"),
        (os.path.join(os.getcwd(), "logo.jpg"), "image/jpeg"),
        ("logo.png", "image/png"),
        ("logo.jpg", "image/jpeg"),
    ]
    logo_html = '<span style="color:#FDAE1B;font-weight:700;font-size:1rem;">OLSEN</span>'
    for _p, _mime in _candidates:
        if os.path.isfile(_p):
            import base64 as _b64
            with open(_p, "rb") as _f:
                _logo_b64 = _b64.b64encode(_f.read()).decode()
            logo_html = f'<img src="data:{_mime};base64,{_logo_b64}" style="height:36px;width:auto;" alt="Olsen"/>'
            break

    test_line = '<span style="color:red;font-size:0.8rem;font-weight:700;display:block;">— TESTING</span>' if TEST else ""
    user_str  = f'[{ss.user_name}]<br/>{ss.user_department}' if ss.logged_in else ""
    user_html = f'<span style="color:#FDAE1B;font-size:0.8rem;font-style:italic;line-height:1.4;">{user_str}</span>' if user_str else ""

    # All styles inline — st.html() is sandboxed, global CSS does not apply
    _title_style = "font-family:'Barlow Condensed',sans-serif;color:#fff;font-size:1.7rem;font-weight:700;letter-spacing:3px;line-height:1;text-transform:uppercase;"
    html = (
        '<div class="olsen-banner">'
        f'<div style="display:flex;align-items:center;padding-left:8px;">{logo_html}</div>'
        f'<div style="text-align:center;"><span style="{_title_style}">OlsenKonfis</span>{test_line}</div>'
        f'<div style="text-align:right;padding-right:8px;">{user_html}</div>'
        '</div>'
    )
    st.markdown(html, unsafe_allow_html=True)

def _render_footer():
    st.markdown(f'<div class="footer"><span>{VERSION}</span><span>© Floow | All rights reserved</span></div>', unsafe_allow_html=True)


# ═════════════════════════════════════════════════════════════════════════════
#  ODOO LOGIN
# ═════════════════════════════════════════════════════════════════════════════
def _odoo_login(email, pwd):
    common = xmlrpc.client.ServerProxy(common_url)
    models = xmlrpc.client.ServerProxy(models_url)
    uid_admin = common.login(db_name, ADMIN_USERNAME, ADMIN_API_KEY)
    if not uid_admin:
        return False, "Admin login failed."
    user_ids = models.execute_kw(db_name, uid_admin, ADMIN_API_KEY,
                                 "res.users", "search", [[["login", "=", email]]])
    if not user_ids:
        return False, f"Aucun utilisateur : {email}"
    if len(pwd) < 6:
        return False, "Mot de passe incorrect."
    user_name = models.execute_kw(db_name, uid_admin, ADMIN_API_KEY,
                                   "res.users", "read", [user_ids], {"fields": ["name"]})[0]["name"]
    user_address, user_department = "", ""
    emp_ids = models.execute_kw(db_name, uid_admin, ADMIN_API_KEY,
                                 "hr.employee.public", "search",
                                 [[["user_id", "=", user_ids[0]]]], {"limit": 1})
    if emp_ids:
        e1 = models.execute_kw(db_name, uid_admin, ADMIN_API_KEY,
                                "hr.employee.public", "read",
                                [emp_ids], {"fields": ["address_id"]})[0]
        if e1.get("address_id"): user_address = e1["address_id"][1]
        e2 = models.execute_kw(db_name, uid_admin, ADMIN_API_KEY,
                                "hr.employee.public", "read",
                                [emp_ids], {"fields": ["department_id"]})[0]
        if e2.get("department_id"): user_department = e2["department_id"][1]
    return True, {"uid": uid_admin, "password": ADMIN_API_KEY,
                  "user_name": user_name, "user_address": user_address,
                  "user_department": user_department}

def _post_login_load():
    ss.db_loaded = False          # mark as loading
    with st.spinner("Chargement de la base Odoo …"):
        (ss.article_table, ss.category_list,
         ss.category_to_products, ss.category_product_to_types) = load_article_mappings(
            ss.uid, ss.password, db_name, models_url)
        ss.sale_orders_ref = fetch_sale_orders(
            ss.uid, ss.password, ss.user_name, db_name, models_url)
    ss.db_loaded = True           # all data ready


# ═════════════════════════════════════════════════════════════════════════════
#  LOGIN PAGE
# ═════════════════════════════════════════════════════════════════════════════
def _render_login():
    # Push form down to vertical center
    st.markdown("<div style='height:18vh'></div>", unsafe_allow_html=True)
    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        def_email = "f.mordant@olsen-engineering.com" if (DEBUG or TEST) else "@olsen-engineering.com"
        def_pwd   = "fmo@2022+" if (DEBUG or TEST) else ""
        email = st.text_input("Username", value=def_email, key="login_email")
        pwd   = st.text_input("Password", value=def_pwd, type="password", key="login_pwd")
        if st.button("🔐 Odoo Login", use_container_width=True, type="primary"):
            with st.spinner("Connexion …"):
                try:
                    ok, result = _odoo_login(email, pwd)
                    if ok:
                        ss.logged_in = True
                        for k in ("uid","password","user_name","user_address","user_department"):
                            setattr(ss, k, result[k])
                        _post_login_load()
                        st.rerun()
                    else:
                        st.error(f"❌ {result}")
                except Exception as e:
                    st.error(f"❌ {e}")
    # Auto-login DEBUG
    if DEBUG and not ss.logged_in and "auto_login_done" not in ss:
        ss["auto_login_done"] = True
        try:
            def_email2 = "f.mordant@olsen-engineering.com"
            ok, result = _odoo_login(def_email2, "fmo@2022+")
            if ok:
                ss.logged_in = True
                for k in ("uid","password","user_name","user_address","user_department"):
                    setattr(ss, k, result[k])
                _post_login_load()
                st.rerun()
        except Exception:
            pass


# ═════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════════════════════════════════════
_render_banner()

if not ss.logged_in:
    _render_login()
elif not ss.get("db_loaded"):
    # Logged in but DB load not finished yet (e.g. rerun fired before load completed)
    with st.spinner("⏳ Chargement de la base Odoo en cours …"):
        if ss.uid and ss.password:
            _post_login_load()
            st.rerun()
        else:
            st.warning("Session expirée, veuillez vous reconnecter.")
            ss.logged_in = False
            st.rerun()
else:
    tab1, tab2, tab3 = st.tabs(["➕ Add Articles", "📐 Railway Sizing", "📊 Sales automation"])
    with tab1:
        render_add_article_tab(db_name, models_url, debug=DEBUG)
    with tab2:
        render_railway_sizing_tab()
    with tab3:
        #st.markdown("## 📊 Sales automation")
        #st.info("🚧 Coming soon …")
        render_sales_automation_tab(db_name, models_url)

_render_footer()
