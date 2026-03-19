"""
car_insurance_entry/car_insurance_form.py
==========================================
Mock Car Insurance Data Entry Form
Run with: python car_insurance_entry/car_insurance_form.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, date
import json
import os

# ── Design Tokens ─────────────────────────────────────────────────────────────
BG          = "#0f1117"
BG_CARD     = "#1a1d27"
BG_HOVER    = "#22263a"
ACCENT      = "#6c63ff"
ACCENT_DIM  = "#4b44c2"
SUCCESS     = "#22c55e"
DANGER      = "#ef4444"
WARNING     = "#f59e0b"
TEXT        = "#e2e8f0"
TEXT_DIM    = "#64748b"
BORDER      = "#2d3148"

FONT_TITLE  = ("Segoe UI", 18, "bold")
FONT_SECT   = ("Segoe UI", 11, "bold")
FONT_LABEL  = ("Segoe UI", 9)
FONT_SMALL  = ("Segoe UI", 8)
FONT_MONO   = ("Consolas", 9)
FONT_BTN    = ("Segoe UI", 10, "bold")

# ── Helpers ───────────────────────────────────────────────────────────────────

STATES = [
    "Alabama","Alaska","Arizona","Arkansas","California","Colorado","Connecticut",
    "Delaware","Florida","Georgia","Hawaii","Idaho","Illinois","Indiana","Iowa",
    "Kansas","Kentucky","Louisiana","Maine","Maryland","Massachusetts","Michigan",
    "Minnesota","Mississippi","Missouri","Montana","Nebraska","Nevada","New Hampshire",
    "New Jersey","New Mexico","New York","North Carolina","North Dakota","Ohio",
    "Oklahoma","Oregon","Pennsylvania","Rhode Island","South Carolina","South Dakota",
    "Tennessee","Texas","Utah","Vermont","Virginia","Washington","West Virginia",
    "Wisconsin","Wyoming","D.C."
]

MAKES = [
    "Acura","Alfa Romeo","Aston Martin","Audi","Bentley","BMW","Buick","Cadillac",
    "Chevrolet","Chrysler","Dodge","Ferrari","Fiat","Ford","Genesis","GMC","Honda",
    "Hyundai","Infiniti","Jaguar","Jeep","Kia","Lamborghini","Land Rover","Lexus",
    "Lincoln","Lotus","Maserati","Mazda","McLaren","Mercedes-Benz","MINI","Mitsubishi",
    "Nissan","Porsche","Ram","Rolls-Royce","Subaru","Tesla","Toyota","Volkswagen","Volvo"
]

BODY_TYPES = ["Sedan","SUV","Truck","Coupe","Convertible","Hatchback","Minivan",
              "Wagon","Van","Crossover","Sports Car","Pickup"]

COLORS = ["Black","White","Silver","Gray","Red","Blue","Green","Brown","Gold",
          "Orange","Yellow","Purple","Beige","Maroon","Teal","Navy","Burgundy"]

COVERAGE_TYPES = ["Liability Only","Collision","Comprehensive","Full Coverage",
                  "Uninsured/Underinsured Motorist","Medical Payments","Personal Injury Protection"]

PAYMENT_FREQ = ["Monthly","Quarterly","Semi-Annual","Annual"]

PAYMENT_METHODS = ["Credit Card","Debit Card","Bank Transfer (ACH)","Check","Money Order","PayPal"]

MARITAL_STATUS = ["Single","Married","Divorced","Widowed","Domestic Partner"]

RELATIONSHIP = ["Self","Spouse","Child","Parent","Sibling","Other Relative","Employee","Other"]

OCCUPATION = ["Employed Full-Time","Employed Part-Time","Self-Employed","Retired",
               "Student","Unemployed","Homemaker","Military"]

EDUCATION = ["Less than High School","High School / GED","Some College",
             "Associate's Degree","Bachelor's Degree","Master's Degree","Doctorate","Trade/Vocational"]

GARAGING = ["Private Garage","Carport","Driveway","Street Parking","Parking Lot","Storage Unit"]

USAGE = ["Personal/Pleasure","Commute","Business","Rideshare (Uber/Lyft)","Farm/Ranch","Commercial"]

CLAIM_STATUS = ["Open","Closed","Pending","Under Investigation","Settled","Denied"]

CLAIM_TYPE = ["Collision","Comprehensive","Liability","Medical","Uninsured Motorist","Other"]


# ══════════════════════════════════════════════════════════════════════════════
#  Scrollable Frame helper
# ══════════════════════════════════════════════════════════════════════════════
class ScrollableFrame(tk.Frame):
    def __init__(self, parent, bg=BG_CARD, **kw):
        outer = tk.Frame(parent, bg=bg)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=bg, highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        super().__init__(canvas, bg=bg, **kw)
        self._win = canvas.create_window((0, 0), window=self, anchor="nw")

        self.bind("<Configure>", self._on_frame_configure)
        canvas.bind("<Configure>", self._on_canvas_configure)
        canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.bind("<MouseWheel>", self._on_mousewheel)

        self._canvas = canvas

    def _on_frame_configure(self, _):
        self._canvas.configure(scrollregion=self._canvas.bbox("all"))

    def _on_canvas_configure(self, e):
        self._canvas.itemconfig(self._win, width=e.width)

    def _on_mousewheel(self, e):
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

    def bind_all_children(self, widget=None):
        w = widget or self
        w.bind("<MouseWheel>", self._on_mousewheel)
        for child in w.winfo_children():
            self.bind_all_children(child)


# ══════════════════════════════════════════════════════════════════════════════
#  Field builder helpers
# ══════════════════════════════════════════════════════════════════════════════
class FormBuilder:
    """Helper to consistently create styled form rows."""

    def __init__(self, parent, col_width=22):
        self.parent = parent
        self.col_width = col_width
        self._vars = {}

    def _lbl(self, parent, text):
        return tk.Label(parent, text=text, font=FONT_LABEL, bg=BG_CARD,
                        fg=TEXT, width=self.col_width, anchor="w")

    def _entry(self, parent, var, width=28, placeholder=""):
        e = tk.Entry(parent, textvariable=var, font=FONT_MONO,
                     bg=BG_HOVER, fg=TEXT, bd=0, relief="flat",
                     insertbackground=TEXT, width=width)
        e.configure(highlightbackground=BORDER, highlightthickness=1)
        if placeholder and not var.get():
            var.set(placeholder)
            e.config(fg=TEXT_DIM)
            def _focus_in(ev, v=var, pl=placeholder):
                if v.get() == pl:
                    v.set("")
                    ev.widget.config(fg=TEXT)
            def _focus_out(ev, v=var, pl=placeholder):
                if not v.get():
                    v.set(pl)
                    ev.widget.config(fg=TEXT_DIM)
            e.bind("<FocusIn>", _focus_in)
            e.bind("<FocusOut>", _focus_out)
        return e

    def _combo(self, parent, var, values, width=26):
        style = ttk.Style()
        style.configure("Dark.TCombobox",
                         fieldbackground=BG_HOVER, background=BG_HOVER,
                         foreground=TEXT, selectbackground=ACCENT,
                         selectforeground=TEXT)
        c = ttk.Combobox(parent, textvariable=var, values=values,
                          font=FONT_MONO, width=width, style="Dark.TCombobox",
                          state="readonly")
        return c

    def _check(self, parent, var, text):
        return tk.Checkbutton(parent, text=text, variable=var,
                              font=FONT_LABEL, bg=BG_CARD, fg=TEXT,
                              selectcolor=ACCENT_DIM, activebackground=BG_CARD,
                              activeforeground=TEXT)

    def _radio_group(self, parent, var, options):
        """Return a frame with horizontal radio buttons."""
        f = tk.Frame(parent, bg=BG_CARD)
        for val, lbl in options:
            tk.Radiobutton(f, text=lbl, variable=var, value=val,
                           font=FONT_LABEL, bg=BG_CARD, fg=TEXT,
                           selectcolor=ACCENT_DIM, activebackground=BG_CARD,
                           activeforeground=TEXT).pack(side="left", padx=(0, 12))
        return f

    def row(self, label, widget_factory, var_name=None, pady=3):
        """Add a label + widget row to the parent."""
        row = tk.Frame(self.parent, bg=BG_CARD)
        row.pack(fill="x", pady=pady, padx=4)
        self._lbl(row, label).pack(side="left")
        widget = widget_factory(row)
        widget.pack(side="left", fill="x", expand=True, ipady=3, padx=(4, 8))
        return widget

    def section(self, title):
        f = tk.Frame(self.parent, bg=BG_CARD, pady=6)
        f.pack(fill="x", padx=4, pady=(12, 4))
        tk.Label(f, text=f"  {title}", font=FONT_SECT, bg=ACCENT_DIM,
                 fg="white", anchor="w").pack(fill="x", ipady=4)

    def text_area(self, label, height=3, pady=3):
        row = tk.Frame(self.parent, bg=BG_CARD)
        row.pack(fill="x", pady=pady, padx=4)
        self._lbl(row, label).pack(side="left", anchor="n")
        t = tk.Text(row, font=FONT_MONO, bg=BG_HOVER, fg=TEXT, bd=0,
                    relief="flat", height=height, wrap="word",
                    insertbackground=TEXT)
        t.pack(side="left", fill="x", expand=True, ipady=3, padx=(4, 8))
        return t

    def sep(self):
        tk.Frame(self.parent, bg=BORDER, height=1).pack(fill="x", pady=6, padx=4)


# ══════════════════════════════════════════════════════════════════════════════
#  Main Application
# ══════════════════════════════════════════════════════════════════════════════
class CarInsuranceForm(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Car Insurance — Data Entry Form")
        self.geometry("980x760")
        self.minsize(820, 600)
        self.configure(bg=BG)

        self._setup_styles()
        self._init_vars()
        self._build_ui()

    # ── Style ─────────────────────────────────────────────────────────────────
    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=BG_HOVER, foreground=TEXT_DIM,
                         font=("Segoe UI", 10), padding=[14, 8])
        style.map("TNotebook.Tab",
                  background=[("selected", ACCENT)],
                  foreground=[("selected", "white")])
        style.configure("Dark.TCombobox",
                         fieldbackground=BG_HOVER, background=BG_HOVER,
                         foreground=TEXT)

    # ── Variables ─────────────────────────────────────────────────────────────
    def _init_vars(self):
        S = tk.StringVar
        B = tk.BooleanVar
        I = tk.IntVar

        # ── Policy Info ──────────────────────────────────────────────────────
        self.policy_number      = S(value="")
        self.policy_type        = S(value="Full Coverage")
        self.effective_date     = S(value="")
        self.expiration_date    = S(value="")
        self.policy_term        = S(value="6 Month")
        self.agent_id           = S(value="")
        self.agent_name         = S(value="")
        self.agency_name        = S(value="")
        self.underwriter        = S(value="")
        self.policy_status      = S(value="Active")
        self.renewal_flag       = B(value=False)
        self.paperless          = B(value=True)
        self.esign              = B(value=False)

        # ── Policyholder ─────────────────────────────────────────────────────
        self.ph_first           = S()
        self.ph_middle          = S()
        self.ph_last            = S()
        self.ph_suffix          = S()
        self.ph_dob             = S()
        self.ph_gender          = S(value="Male")
        self.ph_ssn             = S()
        self.ph_drivers_license = S()
        self.ph_dl_state        = S(value="California")
        self.ph_dl_exp          = S()
        self.ph_marital         = S(value="Single")
        self.ph_occupation      = S(value="Employed Full-Time")
        self.ph_education       = S(value="Bachelor's Degree")
        self.ph_email           = S()
        self.ph_phone_home      = S()
        self.ph_phone_cell      = S()
        self.ph_phone_work      = S()
        self.ph_addr1           = S()
        self.ph_addr2           = S()
        self.ph_city            = S()
        self.ph_state           = S(value="California")
        self.ph_zip             = S()
        self.ph_county          = S()
        self.ph_country         = S(value="United States")
        self.ph_years_at_addr   = S()
        self.ph_homeowner       = B(value=False)
        self.ph_credit_score    = S()
        self.ph_years_insured   = S()
        self.ph_prior_insurer   = S()
        self.ph_prior_policy_no = S()
        self.ph_prior_expiry    = S()
        self.ph_prior_liability = S()

        # ── Vehicle ───────────────────────────────────────────────────────────
        self.v_vin              = S()
        self.v_year             = S()
        self.v_make             = S(value="Toyota")
        self.v_model            = S()
        self.v_trim             = S()
        self.v_body             = S(value="Sedan")
        self.v_color            = S(value="Black")
        self.v_doors            = S(value="4")
        self.v_cylinders        = S()
        self.v_displacement     = S()
        self.v_fuel             = S(value="Gasoline")
        self.v_transmission     = S(value="Automatic")
        self.v_drive            = S(value="FWD")
        self.v_mileage          = S()
        self.v_annual_miles     = S()
        self.v_usage            = S(value="Personal/Pleasure")
        self.v_garaging         = S(value="Private Garage")
        self.v_purchase_date    = S()
        self.v_purchase_price   = S()
        self.v_condition        = S(value="Good")
        self.v_title_state      = S(value="California")
        self.v_lienholder       = S()
        self.v_lienholder_addr  = S()
        self.v_loan_number      = S()
        self.v_market_value     = S()
        self.v_salvage          = B(value=False)
        self.v_anti_theft       = B(value=False)
        self.v_airbags          = B(value=True)
        self.v_abs              = B(value=True)
        self.v_daytime_lights   = B(value=True)
        self.v_backup_camera    = B(value=False)
        self.v_gps              = B(value=False)
        self.v_parking_sensors  = B(value=False)
        self.v_lane_assist      = B(value=False)
        self.v_adaptive_cruise  = B(value=False)
        self.v_custom_equipment = B(value=False)
        self.v_custom_value     = S()

        # ── Coverage ──────────────────────────────────────────────────────────
        self.cov_bodily_limit   = S(value="100/300")
        self.cov_property       = S(value="100,000")
        self.cov_collision_ded  = S(value="500")
        self.cov_comp_ded       = S(value="250")
        self.cov_um_uim         = B(value=True)
        self.cov_um_limit       = S(value="100/300")
        self.cov_pip            = B(value=False)
        self.cov_pip_limit      = S()
        self.cov_medpay         = B(value=False)
        self.cov_medpay_limit   = S()
        self.cov_rental         = B(value=True)
        self.cov_rental_limit   = S(value="$30/day")
        self.cov_roadside       = B(value=False)
        self.cov_gap            = B(value=False)
        self.cov_rideshare      = B(value=False)
        self.cov_new_car        = B(value=False)
        self.cov_acc_forgive    = B(value=False)
        self.cov_disappear_ded  = B(value=False)
        self.cov_premium_total  = S()
        self.cov_premium_period = S(value="Monthly")
        self.disc_multi_car     = B(value=False)
        self.disc_multi_policy  = B(value=False)
        self.disc_good_driver   = B(value=False)
        self.disc_good_student  = B(value=False)
        self.disc_defensive_drv = B(value=False)
        self.disc_loyalty       = B(value=False)
        self.disc_military      = B()
        self.disc_affinity      = B()

        # ── Additional Drivers ────────────────────────────────────────────────
        self.d2_first           = S()
        self.d2_last            = S()
        self.d2_dob             = S()
        self.d2_gender          = S(value="Female")
        self.d2_relation        = S(value="Spouse")
        self.d2_dl              = S()
        self.d2_dl_state        = S(value="California")
        self.d2_dl_exp          = S()
        self.d2_sr22            = B(value=False)
        self.d2_excluded        = B(value=False)
        self.d2_accidents       = S(value="0")
        self.d2_violations      = S(value="0")

        self.d3_first           = S()
        self.d3_last            = S()
        self.d3_dob             = S()
        self.d3_gender          = S(value="Male")
        self.d3_relation        = S(value="Child")
        self.d3_dl              = S()
        self.d3_dl_state        = S(value="California")
        self.d3_dl_exp          = S()
        self.d3_sr22            = B(value=False)
        self.d3_excluded        = B(value=False)
        self.d3_accidents       = S(value="0")
        self.d3_violations      = S(value="0")

        # ── Driving History ────────────────────────────────────────────────────
        self.hist_accidents_3yr = S(value="0")
        self.hist_violations_3yr= S(value="0")
        self.hist_claims_3yr    = S(value="0")
        self.hist_dui           = B(value=False)
        self.hist_sr22          = B(value=False)
        self.hist_license_susp  = B(value=False)
        self.hist_at_fault      = S(value="0")
        self.hist_not_at_fault  = S(value="0")
        self.hist_comp_claims   = S(value="0")
        self.hist_total_claims  = S(value="0")

        # ── Claims ────────────────────────────────────────────────────────────
        self.claim_number       = S()
        self.claim_date         = S()
        self.claim_type         = S(value="Collision")
        self.claim_desc         = None   # Text widget
        self.claim_status       = S(value="Open")
        self.claim_amount       = S()
        self.claim_deductible   = S()
        self.claim_adjuster     = S()
        self.claim_at_fault     = B(value=False)
        self.claim_police_rpt   = B(value=False)
        self.claim_report_no    = S()
        self.claim_injury       = B(value=False)
        self.claim_third_party  = B(value=False)
        self.claim_tp_name      = S()
        self.claim_tp_policy    = S()
        self.claim_resolve_date = S()
        self.claim_settlement   = S()

        # ── Payment / Billing ─────────────────────────────────────────────────
        self.pay_method         = S(value="Credit Card")
        self.pay_frequency      = S(value="Monthly")
        self.pay_amount         = S()
        self.pay_due_date       = S()
        self.pay_auto_pay       = B(value=True)
        self.pay_cc_name        = S()
        self.pay_cc_number      = S()
        self.pay_cc_exp         = S()
        self.pay_cc_cvv         = S()
        self.pay_bank_name      = S()
        self.pay_routing        = S()
        self.pay_account        = S()
        self.pay_account_type   = S(value="Checking")
        self.pay_billing_addr1  = S()
        self.pay_billing_city   = S()
        self.pay_billing_state  = S(value="California")
        self.pay_billing_zip    = S()
        self.pay_down_payment   = S()
        self.pay_balance_due    = S()
        self.pay_last_paid_date = S()
        self.pay_last_paid_amt  = S()

    # ── UI Build ──────────────────────────────────────────────────────────────
    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG, pady=16)
        hdr.pack(fill="x", padx=28)
        tk.Label(hdr, text="🚗  Car Insurance", font=FONT_TITLE,
                 bg=BG, fg=ACCENT).pack(side="left")
        tk.Label(hdr, text="Data Entry Form", font=("Segoe UI", 11),
                 bg=BG, fg=TEXT_DIM).pack(side="left", padx=(10, 0), pady=(5, 0))

        # ── Notebook ──────────────────────────────────────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=16, pady=(0, 8))

        tabs = [
            ("📋 Policy",       self._build_policy_tab),
            ("👤 Policyholder", self._build_policyholder_tab),
            ("🚙 Vehicle",      self._build_vehicle_tab),
            ("🛡️ Coverage",     self._build_coverage_tab),
            ("👥 Drivers",      self._build_drivers_tab),
            ("📜 History",      self._build_history_tab),
            ("⚠️ Claims",       self._build_claims_tab),
            ("💳 Payment",      self._build_payment_tab),
        ]
        for title, builder in tabs:
            sf = ScrollableFrame(nb)
            nb.add(sf._canvas.master, text=title)
            fb = FormBuilder(sf)
            builder(fb, sf)

        # ── Footer Buttons ────────────────────────────────────────────────────
        footer = tk.Frame(self, bg=BG, pady=10)
        footer.pack(fill="x", padx=16)

        def btn(text, color, cmd):
            b = tk.Button(footer, text=text, font=FONT_BTN, bg=color, fg="white",
                           bd=0, relief="flat", padx=20, pady=8, cursor="hand2",
                           activebackground=ACCENT_DIM, activeforeground="white",
                           command=cmd)
            b.bind("<Enter>", lambda e: b.config(bg=ACCENT_DIM))
            b.bind("<Leave>", lambda e: b.config(bg=color))
            return b

        btn("💾  Save Record",    SUCCESS,  self._save).pack(side="right", padx=6)
        btn("📂  Load Record",    ACCENT,   self._load).pack(side="right", padx=6)
        btn("🖨️  Print Preview",  WARNING,  self._print_preview).pack(side="right", padx=6)
        btn("🗑️  Clear All",      DANGER,   self._clear_all).pack(side="right", padx=6)
        btn("✅  Submit",         "#16a34a", self._submit).pack(side="right", padx=6)

        self._status = tk.Label(footer, text="Ready", font=FONT_SMALL,
                                bg=BG, fg=TEXT_DIM)
        self._status.pack(side="left", padx=6)

    # ══════════════════════════════════════════════════════════════════════════
    #  TAB BUILDERS
    # ══════════════════════════════════════════════════════════════════════════

    def _build_policy_tab(self, fb: FormBuilder, sf: ScrollableFrame):
        fb.section("Policy Information")
        fb.row("Policy Number",    lambda p: fb._entry(p, self.policy_number, placeholder="e.g. POL-2024-000001"))
        fb.row("Policy Status",    lambda p: fb._combo(p, self.policy_status,
                                                ["Active","Inactive","Cancelled","Expired","Pending","Lapsed"]))
        fb.row("Policy Type",      lambda p: fb._combo(p, self.policy_type, COVERAGE_TYPES))
        fb.row("Policy Term",      lambda p: fb._combo(p, self.policy_term, ["6 Month","12 Month","Monthly"]))
        fb.row("Effective Date",   lambda p: fb._entry(p, self.effective_date, placeholder="MM/DD/YYYY"))
        fb.row("Expiration Date",  lambda p: fb._entry(p, self.expiration_date, placeholder="MM/DD/YYYY"))

        fb.section("Agent / Agency")
        fb.row("Agent ID",         lambda p: fb._entry(p, self.agent_id, placeholder="AGT-0001"))
        fb.row("Agent Name",       lambda p: fb._entry(p, self.agent_name))
        fb.row("Agency Name",      lambda p: fb._entry(p, self.agency_name))
        fb.row("Underwriter",      lambda p: fb._entry(p, self.underwriter))

        fb.section("Flags")
        for var, lbl in [(self.renewal_flag, "Renewal Policy"),
                         (self.paperless, "Paperless / e-Delivery"),
                         (self.esign, "E-Signature Obtained")]:
            row = tk.Frame(sf, bg=BG_CARD)
            row.pack(fill="x", padx=8, pady=2)
            fb._check(row, var, lbl).pack(side="left", padx=(28, 0))

    def _build_policyholder_tab(self, fb: FormBuilder, sf: ScrollableFrame):
        fb.section("Personal Information")
        fb.row("First Name",       lambda p: fb._entry(p, self.ph_first))
        fb.row("Middle Name",      lambda p: fb._entry(p, self.ph_middle))
        fb.row("Last Name",        lambda p: fb._entry(p, self.ph_last))
        fb.row("Suffix",           lambda p: fb._combo(p, self.ph_suffix,
                                                ["","Jr.","Sr.","II","III","IV","V"], width=8))
        fb.row("Date of Birth",    lambda p: fb._entry(p, self.ph_dob, placeholder="MM/DD/YYYY"))
        fb.row("Gender",           lambda p: fb._radio_group(p, self.ph_gender,
                                                [("Male","Male"),("Female","Female"),("Non-Binary","Non-Binary"),("Prefer not to say","Other")]))
        fb.row("SSN",              lambda p: fb._entry(p, self.ph_ssn, placeholder="XXX-XX-XXXX"))
        fb.row("Marital Status",   lambda p: fb._combo(p, self.ph_marital, MARITAL_STATUS))
        fb.row("Occupation",       lambda p: fb._combo(p, self.ph_occupation, OCCUPATION))
        fb.row("Education Level",  lambda p: fb._combo(p, self.ph_education, EDUCATION))
        fb.row("Credit Score",     lambda p: fb._entry(p, self.ph_credit_score, placeholder="300–850"))
        fb.row("Years Continuously Insured", lambda p: fb._entry(p, self.ph_years_insured))

        fb.section("Contact Information")
        fb.row("Email Address",    lambda p: fb._entry(p, self.ph_email, placeholder="name@example.com"))
        fb.row("Home Phone",       lambda p: fb._entry(p, self.ph_phone_home, placeholder="(555) 000-0000"))
        fb.row("Cell Phone",       lambda p: fb._entry(p, self.ph_phone_cell, placeholder="(555) 000-0000"))
        fb.row("Work Phone",       lambda p: fb._entry(p, self.ph_phone_work, placeholder="(555) 000-0000"))

        fb.section("Address")
        fb.row("Street Address 1", lambda p: fb._entry(p, self.ph_addr1))
        fb.row("Street Address 2", lambda p: fb._entry(p, self.ph_addr2, placeholder="Apt/Suite (optional)"))
        fb.row("City",             lambda p: fb._entry(p, self.ph_city))
        fb.row("State",            lambda p: fb._combo(p, self.ph_state, STATES))
        fb.row("ZIP Code",         lambda p: fb._entry(p, self.ph_zip, placeholder="XXXXX"))
        fb.row("County",           lambda p: fb._entry(p, self.ph_county))
        fb.row("Country",          lambda p: fb._entry(p, self.ph_country))
        fb.row("Years at Address", lambda p: fb._entry(p, self.ph_years_at_addr))

        row = tk.Frame(sf, bg=BG_CARD)
        row.pack(fill="x", padx=8, pady=2)
        fb._check(row, self.ph_homeowner, "Homeowner").pack(side="left", padx=(28, 0))

        fb.section("Driver's License")
        fb.row("DL Number",        lambda p: fb._entry(p, self.ph_drivers_license))
        fb.row("DL Issuing State", lambda p: fb._combo(p, self.ph_dl_state, STATES))
        fb.row("DL Expiration",    lambda p: fb._entry(p, self.ph_dl_exp, placeholder="MM/DD/YYYY"))

        fb.section("Prior Insurance")
        fb.row("Prior Insurer",    lambda p: fb._entry(p, self.ph_prior_insurer))
        fb.row("Prior Policy No.", lambda p: fb._entry(p, self.ph_prior_policy_no))
        fb.row("Prior Expiry Date",lambda p: fb._entry(p, self.ph_prior_expiry, placeholder="MM/DD/YYYY"))
        fb.row("Prior Liability Limits", lambda p: fb._entry(p, self.ph_prior_liability, placeholder="e.g. 50/100"))

    def _build_vehicle_tab(self, fb: FormBuilder, sf: ScrollableFrame):
        fb.section("Vehicle Identification")
        fb.row("VIN",              lambda p: fb._entry(p, self.v_vin, placeholder="17-character VIN"))
        fb.row("Year",             lambda p: fb._entry(p, self.v_year, placeholder="YYYY"))
        fb.row("Make",             lambda p: fb._combo(p, self.v_make, MAKES))
        fb.row("Model",            lambda p: fb._entry(p, self.v_model))
        fb.row("Trim / Sub-model", lambda p: fb._entry(p, self.v_trim, placeholder="e.g. EX-L, Sport"))
        fb.row("Body Type",        lambda p: fb._combo(p, self.v_body, BODY_TYPES))
        fb.row("Color",            lambda p: fb._combo(p, self.v_color, COLORS))
        fb.row("Number of Doors",  lambda p: fb._combo(p, self.v_doors, ["2","3","4","5"]))

        fb.section("Engine & Drivetrain")
        fb.row("Cylinders",        lambda p: fb._combo(p, self.v_cylinders,
                                                ["3","4","5","6","8","10","12","Electric","Hybrid"]))
        fb.row("Displacement (L)", lambda p: fb._entry(p, self.v_displacement, placeholder="e.g. 2.0"))
        fb.row("Fuel Type",        lambda p: fb._combo(p, self.v_fuel,
                                                ["Gasoline","Diesel","Electric","Hybrid","Plug-in Hybrid",
                                                 "Natural Gas","Flex Fuel"]))
        fb.row("Transmission",     lambda p: fb._combo(p, self.v_transmission,
                                                ["Automatic","Manual","CVT","DCT","Semi-Automatic"]))
        fb.row("Drive Type",       lambda p: fb._combo(p, self.v_drive,
                                                ["FWD","RWD","AWD","4WD"]))

        fb.section("Usage & Mileage")
        fb.row("Current Mileage",  lambda p: fb._entry(p, self.v_mileage, placeholder="e.g. 45000"))
        fb.row("Annual Miles Est.",lambda p: fb._entry(p, self.v_annual_miles, placeholder="e.g. 12000"))
        fb.row("Primary Use",      lambda p: fb._combo(p, self.v_usage, USAGE))
        fb.row("Garaging Location",lambda p: fb._combo(p, self.v_garaging, GARAGING))

        fb.section("Ownership / Purchase")
        fb.row("Purchase Date",    lambda p: fb._entry(p, self.v_purchase_date, placeholder="MM/DD/YYYY"))
        fb.row("Purchase Price ($)",lambda p: fb._entry(p, self.v_purchase_price))
        fb.row("Current Market Value ($)", lambda p: fb._entry(p, self.v_market_value))
        fb.row("Vehicle Condition",lambda p: fb._combo(p, self.v_condition,
                                                ["Excellent","Good","Fair","Poor"]))
        fb.row("Title State",      lambda p: fb._combo(p, self.v_title_state, STATES))

        fb.section("Lien / Financing")
        fb.row("Lienholder/Lender",lambda p: fb._entry(p, self.v_lienholder))
        fb.row("Lienholder Address",lambda p: fb._entry(p, self.v_lienholder_addr))
        fb.row("Loan / Lease No.", lambda p: fb._entry(p, self.v_loan_number))

        fb.section("Safety & Features")
        checks = [
            (self.v_salvage,          "Salvage Title"),
            (self.v_anti_theft,       "Anti-Theft Device"),
            (self.v_airbags,          "Airbags"),
            (self.v_abs,              "ABS Brakes"),
            (self.v_daytime_lights,   "Daytime Running Lights"),
            (self.v_backup_camera,    "Backup Camera"),
            (self.v_gps,              "GPS Tracking"),
            (self.v_parking_sensors,  "Parking Sensors"),
            (self.v_lane_assist,      "Lane Departure Warning"),
            (self.v_adaptive_cruise,  "Adaptive Cruise Control"),
            (self.v_custom_equipment, "Custom Equipment / Mods"),
        ]
        grid = tk.Frame(sf, bg=BG_CARD)
        grid.pack(fill="x", padx=12, pady=4)
        for i, (var, lbl) in enumerate(checks):
            fb._check(grid, var, lbl).grid(row=i // 3, column=i % 3, sticky="w", padx=8, pady=2)
        fb.row("Custom Equipment Value ($)", lambda p: fb._entry(p, self.v_custom_value))

    def _build_coverage_tab(self, fb: FormBuilder, sf: ScrollableFrame):
        fb.section("Liability Limits")
        fb.row("Bodily Injury (k$/k$)", lambda p: fb._combo(p, self.cov_bodily_limit,
                                                     ["25/50","50/100","100/300","250/500","500/500","300/300"]))
        fb.row("Property Damage ($)",   lambda p: fb._combo(p, self.cov_property,
                                                     ["25,000","50,000","100,000","250,000","500,000"]))

        fb.section("Collision & Comprehensive")
        fb.row("Collision Deductible",  lambda p: fb._combo(p, self.cov_collision_ded,
                                                     ["0","100","250","500","1000","2000","2500","5000"]))
        fb.row("Comprehensive Deductible", lambda p: fb._combo(p, self.cov_comp_ded,
                                                     ["0","100","250","500","1000","2000"]))

        fb.section("Additional Coverages")
        rows_cov = [
            (self.cov_um_uim,       "Uninsured/Underinsured Motorist"),
            (self.cov_pip,          "Personal Injury Protection (PIP)"),
            (self.cov_medpay,       "Medical Payments"),
            (self.cov_rental,       "Rental Reimbursement"),
            (self.cov_roadside,     "Roadside Assistance"),
            (self.cov_gap,          "GAP Insurance"),
            (self.cov_rideshare,    "Rideshare Coverage"),
            (self.cov_new_car,      "New Car Replacement"),
            (self.cov_acc_forgive,  "Accident Forgiveness"),
            (self.cov_disappear_ded,"Diminishing Deductible"),
        ]
        for var, lbl in rows_cov:
            row = tk.Frame(sf, bg=BG_CARD)
            row.pack(fill="x", padx=8, pady=1)
            fb._check(row, var, lbl).pack(side="left", padx=(28, 0))

        fb.section("Optional Limits")
        fb.row("UM/UIM Limit",       lambda p: fb._entry(p, self.cov_um_limit, placeholder="e.g. 100/300"))
        fb.row("PIP Limit ($)",       lambda p: fb._entry(p, self.cov_pip_limit))
        fb.row("MedPay Limit ($)",    lambda p: fb._entry(p, self.cov_medpay_limit))
        fb.row("Rental Limit",        lambda p: fb._entry(p, self.cov_rental_limit))

        fb.section("Discounts Applied")
        disc = [
            (self.disc_multi_car,    "Multi-Car"),
            (self.disc_multi_policy, "Multi-Policy / Bundle"),
            (self.disc_good_driver,  "Good Driver (5+ yr clean)"),
            (self.disc_good_student, "Good Student"),
            (self.disc_defensive_drv,"Defensive Driving Course"),
            (self.disc_loyalty,      "Loyalty Discount"),
            (self.disc_military,     "Military"),
            (self.disc_affinity,     "Affinity Group"),
        ]
        grid_d = tk.Frame(sf, bg=BG_CARD)
        grid_d.pack(fill="x", padx=12, pady=4)
        for i, (var, lbl) in enumerate(disc):
            fb._check(grid_d, var, lbl).grid(row=i // 2, column=i % 2, sticky="w", padx=8, pady=2)

        fb.section("Premium Summary")
        fb.row("Total Premium ($)",   lambda p: fb._entry(p, self.cov_premium_total))
        fb.row("Payment Frequency",   lambda p: fb._combo(p, self.cov_premium_period, PAYMENT_FREQ))

    def _build_drivers_tab(self, fb: FormBuilder, sf: ScrollableFrame):
        for prefix, (first, last, dob, gender, rel, dl, dls, dlex, sr22, excl, acc, vio) in [
            ("Driver 2", (self.d2_first, self.d2_last, self.d2_dob, self.d2_gender,
                          self.d2_relation, self.d2_dl, self.d2_dl_state, self.d2_dl_exp,
                          self.d2_sr22, self.d2_excluded, self.d2_accidents, self.d2_violations)),
            ("Driver 3", (self.d3_first, self.d3_last, self.d3_dob, self.d3_gender,
                          self.d3_relation, self.d3_dl, self.d3_dl_state, self.d3_dl_exp,
                          self.d3_sr22, self.d3_excluded, self.d3_accidents, self.d3_violations)),
        ]:
            fb.section(prefix)
            fb.row("First Name",        lambda p, v=first:  fb._entry(p, v))
            fb.row("Last Name",         lambda p, v=last:   fb._entry(p, v))
            fb.row("Date of Birth",     lambda p, v=dob:    fb._entry(p, v, placeholder="MM/DD/YYYY"))
            fb.row("Gender",            lambda p, v=gender: fb._radio_group(p, v,
                                                         [("Male","Male"),("Female","Female"),("Non-Binary","Non-Binary")]))
            fb.row("Relationship",      lambda p, v=rel:    fb._combo(p, v, RELATIONSHIP))
            fb.row("DL Number",         lambda p, v=dl:     fb._entry(p, v))
            fb.row("DL Issuing State",  lambda p, v=dls:    fb._combo(p, v, STATES))
            fb.row("DL Expiration",     lambda p, v=dlex:   fb._entry(p, v, placeholder="MM/DD/YYYY"))
            fb.row("Accidents (3 yr)",  lambda p, v=acc:    fb._combo(p, v, ["0","1","2","3","4","5+"]))
            fb.row("Violations (3 yr)", lambda p, v=vio:    fb._combo(p, v, ["0","1","2","3","4","5+"]))
            chk_row = tk.Frame(sf, bg=BG_CARD)
            chk_row.pack(fill="x", padx=8, pady=2)
            fb._check(chk_row, sr22, f"{prefix} — SR-22 Required").pack(side="left", padx=(28, 0))
            fb._check(chk_row, excl, f"{prefix} — Excluded Driver").pack(side="left", padx=(20, 0))
            fb.sep()

    def _build_history_tab(self, fb: FormBuilder, sf: ScrollableFrame):
        fb.section("Driving Record — Primary Driver (3-Year Look-Back)")
        fb.row("At-Fault Accidents",        lambda p: fb._combo(p, self.hist_at_fault,
                                                         ["0","1","2","3","4","5+"]))
        fb.row("Not-At-Fault Accidents",    lambda p: fb._combo(p, self.hist_not_at_fault,
                                                         ["0","1","2","3","4","5+"]))
        fb.row("Total Accidents",           lambda p: fb._combo(p, self.hist_accidents_3yr,
                                                         ["0","1","2","3","4","5+"]))
        fb.row("Moving Violations",         lambda p: fb._combo(p, self.hist_violations_3yr,
                                                         ["0","1","2","3","4","5+"]))
        fb.row("Comprehensive Claims",      lambda p: fb._combo(p, self.hist_comp_claims,
                                                         ["0","1","2","3","4","5+"]))
        fb.row("Total Claims Filed",        lambda p: fb._combo(p, self.hist_claims_3yr,
                                                         ["0","1","2","3","4","5+"]))

        fb.section("Special Flags")
        flags = [
            (self.hist_dui,          "DUI / DWI on Record"),
            (self.hist_sr22,         "SR-22 / FR-44 Filed"),
            (self.hist_license_susp, "License Suspended or Revoked"),
        ]
        for var, lbl in flags:
            row = tk.Frame(sf, bg=BG_CARD)
            row.pack(fill="x", padx=8, pady=2)
            fb._check(row, var, lbl).pack(side="left", padx=(28, 0))

    def _build_claims_tab(self, fb: FormBuilder, sf: ScrollableFrame):
        fb.section("Claim Details")
        fb.row("Claim Number",      lambda p: fb._entry(p, self.claim_number, placeholder="CLM-2024-000001"))
        fb.row("Date of Loss",      lambda p: fb._entry(p, self.claim_date, placeholder="MM/DD/YYYY"))
        fb.row("Claim Type",        lambda p: fb._combo(p, self.claim_type, CLAIM_TYPE))
        fb.row("Claim Status",      lambda p: fb._combo(p, self.claim_status, CLAIM_STATUS))
        fb.row("Claim Amount ($)",  lambda p: fb._entry(p, self.claim_amount))
        fb.row("Deductible ($)",    lambda p: fb._entry(p, self.claim_deductible))
        fb.row("Adjuster Name",     lambda p: fb._entry(p, self.claim_adjuster))
        fb.row("Settlement Amount ($)", lambda p: fb._entry(p, self.claim_settlement))
        fb.row("Resolution Date",   lambda p: fb._entry(p, self.claim_resolve_date, placeholder="MM/DD/YYYY"))

        # Description text area
        desc_row = tk.Frame(sf, bg=BG_CARD)
        desc_row.pack(fill="x", pady=4, padx=4)
        tk.Label(desc_row, text="Claim Description", font=FONT_LABEL,
                 bg=BG_CARD, fg=TEXT, width=22, anchor="nw").pack(side="left", anchor="n")
        self.claim_desc = tk.Text(desc_row, font=FONT_MONO, bg=BG_HOVER, fg=TEXT,
                                   bd=0, relief="flat", height=4, wrap="word",
                                   insertbackground=TEXT)
        self.claim_desc.pack(side="left", fill="x", expand=True, ipady=3, padx=(4, 8))

        fb.section("Circumstances")
        flags2 = [
            (self.claim_police_rpt, "Police Report Filed"),
            (self.claim_at_fault,   "Policyholder At Fault"),
            (self.claim_injury,     "Injury Involved"),
            (self.claim_third_party,"Third Party Involved"),
        ]
        for var, lbl in flags2:
            row = tk.Frame(sf, bg=BG_CARD)
            row.pack(fill="x", padx=8, pady=2)
            fb._check(row, var, lbl).pack(side="left", padx=(28, 0))

        fb.section("Third Party Information")
        fb.row("Police Report No.",  lambda p: fb._entry(p, self.claim_report_no))
        fb.row("Third Party Name",   lambda p: fb._entry(p, self.claim_tp_name))
        fb.row("Third Party Policy", lambda p: fb._entry(p, self.claim_tp_policy))

    def _build_payment_tab(self, fb: FormBuilder, sf: ScrollableFrame):
        fb.section("Billing Summary")
        fb.row("Total Premium ($)",  lambda p: fb._entry(p, self.pay_amount))
        fb.row("Down Payment ($)",   lambda p: fb._entry(p, self.pay_down_payment))
        fb.row("Balance Due ($)",    lambda p: fb._entry(p, self.pay_balance_due))
        fb.row("Payment Due Date",   lambda p: fb._entry(p, self.pay_due_date, placeholder="MM/DD/YYYY"))
        fb.row("Payment Frequency",  lambda p: fb._combo(p, self.pay_frequency, PAYMENT_FREQ))
        fb.row("Last Payment Date",  lambda p: fb._entry(p, self.pay_last_paid_date, placeholder="MM/DD/YYYY"))
        fb.row("Last Payment Amount ($)", lambda p: fb._entry(p, self.pay_last_paid_amt))

        row = tk.Frame(sf, bg=BG_CARD)
        row.pack(fill="x", padx=8, pady=2)
        fb._check(row, self.pay_auto_pay, "Auto-Pay Enrolled").pack(side="left", padx=(28, 0))

        fb.section("Payment Method")
        fb.row("Method",             lambda p: fb._combo(p, self.pay_method, PAYMENT_METHODS))

        fb.section("Credit / Debit Card")
        fb.row("Cardholder Name",    lambda p: fb._entry(p, self.pay_cc_name))
        fb.row("Card Number",        lambda p: fb._entry(p, self.pay_cc_number, placeholder="XXXX-XXXX-XXXX-XXXX"))
        fb.row("Expiration (MM/YY)", lambda p: fb._entry(p, self.pay_cc_exp, placeholder="MM/YY"))
        fb.row("CVV",                lambda p: fb._entry(p, self.pay_cc_cvv, placeholder="XXX", width=6))

        fb.section("Bank Account (ACH)")
        fb.row("Bank Name",          lambda p: fb._entry(p, self.pay_bank_name))
        fb.row("Routing Number",     lambda p: fb._entry(p, self.pay_routing, placeholder="9-digit ABA"))
        fb.row("Account Number",     lambda p: fb._entry(p, self.pay_account))
        fb.row("Account Type",       lambda p: fb._combo(p, self.pay_account_type,
                                                  ["Checking","Savings"]))

        fb.section("Billing Address")
        fb.row("Street Address",     lambda p: fb._entry(p, self.pay_billing_addr1))
        fb.row("City",               lambda p: fb._entry(p, self.pay_billing_city))
        fb.row("State",              lambda p: fb._combo(p, self.pay_billing_state, STATES))
        fb.row("ZIP Code",           lambda p: fb._entry(p, self.pay_billing_zip))

    # ══════════════════════════════════════════════════════════════════════════
    #  Actions
    # ══════════════════════════════════════════════════════════════════════════

    def _collect_data(self):
        """Gather all StringVar / BooleanVar values into a dict."""
        data = {}
        for attr, val in vars(self).items():
            if isinstance(val, (tk.StringVar, tk.BooleanVar, tk.IntVar)):
                data[attr] = val.get()
        # Grab claim description text
        if self.claim_desc:
            data["claim_desc"] = self.claim_desc.get("1.0", "end").strip()
        data["_timestamp"] = datetime.now().isoformat()
        return data

    def _save(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON files","*.json"),("All files","*.*")],
            initialfile=f"policy_{self.policy_number.get() or 'draft'}.json"
        )
        if not path:
            return
        with open(path, "w") as f:
            json.dump(self._collect_data(), f, indent=2)
        self._status.config(text=f"Saved → {os.path.basename(path)}", fg=SUCCESS)

    def _load(self):
        path = filedialog.askopenfilename(filetypes=[("JSON files","*.json"),("All files","*.*")])
        if not path:
            return
        with open(path) as f:
            data = json.load(f)
        for attr, val in data.items():
            obj = getattr(self, attr, None)
            if isinstance(obj, (tk.StringVar, tk.BooleanVar, tk.IntVar)):
                try:
                    obj.set(val)
                except Exception:
                    pass
        if "claim_desc" in data and self.claim_desc:
            self.claim_desc.delete("1.0", "end")
            self.claim_desc.insert("1.0", data["claim_desc"])
        self._status.config(text=f"Loaded ← {os.path.basename(path)}", fg=ACCENT)

    def _print_preview(self):
        data = self._collect_data()
        win = tk.Toplevel(self)
        win.title("Print Preview")
        win.geometry("700x560")
        win.configure(bg=BG)
        tk.Label(win, text="Record Preview", font=FONT_SECT, bg=BG, fg=ACCENT).pack(pady=10)
        txt = tk.Text(win, font=FONT_MONO, bg=BG_CARD, fg=TEXT, bd=0, wrap="word")
        txt.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        txt.insert("1.0", json.dumps(data, indent=2))
        txt.config(state="disabled")

    def _clear_all(self):
        if not messagebox.askyesno("Clear All", "Clear all fields? This cannot be undone."):
            return
        for attr, val in vars(self).items():
            if isinstance(val, tk.StringVar):
                val.set("")
            elif isinstance(val, tk.BooleanVar):
                val.set(False)
        if self.claim_desc:
            self.claim_desc.delete("1.0", "end")
        self._status.config(text="Cleared", fg=WARNING)

    def _submit(self):
        required = {
            "Policy Number": self.policy_number.get(),
            "Policyholder First Name": self.ph_first.get(),
            "Policyholder Last Name": self.ph_last.get(),
            "VIN": self.v_vin.get(),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            messagebox.showwarning("Missing Fields",
                "Please fill in required fields:\n• " + "\n• ".join(missing))
            return
        data = self._collect_data()
        messagebox.showinfo("Submitted",
            f"Record submitted successfully.\nPolicy: {self.policy_number.get()}\n"
            f"Insured: {self.ph_first.get()} {self.ph_last.get()}\n"
            f"Vehicle: {self.v_year.get()} {self.v_make.get()} {self.v_model.get()}")
        self._status.config(text="Submitted ✓", fg=SUCCESS)


# ══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    app = CarInsuranceForm()
    app.mainloop()
