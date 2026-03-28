"""
car_insurance_entry/car_insurance_form_wx.py
============================================
Car Insurance Data Entry Form — wxPython version.

wxPython uses native Windows controls so Windows UI Automation (UIA)
exposes every field. Labels are always created before their corresponding
controls so UIA auto-labeling assigns the correct Name to each field.

Run with:  python car_insurance_entry/car_insurance_form_wx.py
"""

import wx
import wx.lib.scrolledpanel as scrolled
import json
import os
from datetime import datetime

# ── Data constants ─────────────────────────────────────────────────────────────

STATES = [
    "Alabama","Alaska","Arizona","Arkansas","California","Colorado","Connecticut",
    "Delaware","Florida","Georgia","Hawaii","Idaho","Illinois","Indiana","Iowa",
    "Kansas","Kentucky","Louisiana","Maine","Maryland","Massachusetts","Michigan",
    "Minnesota","Mississippi","Missouri","Montana","Nebraska","Nevada","New Hampshire",
    "New Jersey","New Mexico","New York","North Carolina","North Dakota","Ohio",
    "Oklahoma","Oregon","Pennsylvania","Rhode Island","South Carolina","South Dakota",
    "Tennessee","Texas","Utah","Vermont","Virginia","Washington","West Virginia",
    "Wisconsin","Wyoming","D.C.",
]
MAKES = [
    "Acura","Alfa Romeo","Aston Martin","Audi","Bentley","BMW","Buick","Cadillac",
    "Chevrolet","Chrysler","Dodge","Ferrari","Fiat","Ford","Genesis","GMC","Honda",
    "Hyundai","Infiniti","Jaguar","Jeep","Kia","Lamborghini","Land Rover","Lexus",
    "Lincoln","Lotus","Maserati","Mazda","McLaren","Mercedes-Benz","MINI","Mitsubishi",
    "Nissan","Porsche","Ram","Rolls-Royce","Subaru","Tesla","Toyota","Volkswagen","Volvo",
]
BODY_TYPES     = ["Sedan","SUV","Truck","Coupe","Convertible","Hatchback",
                  "Minivan","Wagon","Van","Crossover","Sports Car","Pickup"]
COLORS         = ["Black","White","Silver","Gray","Red","Blue","Green","Brown",
                  "Gold","Orange","Yellow","Purple","Beige","Maroon","Teal","Navy","Burgundy"]
COVERAGE_TYPES = ["Liability Only","Collision","Comprehensive","Full Coverage",
                  "Uninsured/Underinsured Motorist","Medical Payments",
                  "Personal Injury Protection"]
PAYMENT_FREQ   = ["Monthly","Quarterly","Semi-Annual","Annual"]
PAYMENT_METH   = ["Credit Card","Debit Card","Bank Transfer (ACH)","Check",
                  "Money Order","PayPal"]
MARITAL        = ["Single","Married","Divorced","Widowed","Domestic Partner"]
RELATIONSHIP   = ["Self","Spouse","Child","Parent","Sibling",
                  "Other Relative","Employee","Other"]
OCCUPATION     = ["Employed Full-Time","Employed Part-Time","Self-Employed","Retired",
                  "Student","Unemployed","Homemaker","Military"]
EDUCATION      = ["Less than High School","High School / GED","Some College",
                  "Associate's Degree","Bachelor's Degree","Master's Degree",
                  "Doctorate","Trade/Vocational"]
GARAGING       = ["Private Garage","Carport","Driveway","Street Parking",
                  "Parking Lot","Storage Unit"]
USAGE          = ["Personal/Pleasure","Commute","Business","Rideshare (Uber/Lyft)",
                  "Farm/Ranch","Commercial"]
CLAIM_STATUS   = ["Open","Closed","Pending","Under Investigation","Settled","Denied"]
CLAIM_TYPE     = ["Collision","Comprehensive","Liability","Medical",
                  "Uninsured Motorist","Other"]
COUNT_OPTS     = ["0","1","2","3","4","5+"]
GENDER_OPTS    = ["Male","Female","Non-Binary","Prefer not to say"]


# ── Theme ──────────────────────────────────────────────────────────────────────

ACCENT     = wx.Colour(74, 85, 162)    # indigo-blue header/section bars
ACCENT_LT  = wx.Colour(237, 239, 255)  # very light lavender background
WHITE      = wx.Colour(255, 255, 255)
SECT_FG    = wx.Colour(255, 255, 255)  # white text on section bars
HDR_BG     = wx.Colour(30,  41,  99)   # deep navy for header
HDR_FG     = wx.Colour(255, 255, 255)
BORDER_CLR = wx.Colour(200, 205, 230)

FONT_TITLE = (15, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
FONT_SECT  = (9,  wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD)
FONT_LABEL = (9,  wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)
FONT_MONO  = (9,  wx.FONTFAMILY_TELETYPE, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL)

def _font(*args):
    return wx.Font(*args)


class _CtrlAccessible(wx.Accessible):
    """Explicitly sets the IAccessible Name so UIA sees the correct field label
    regardless of widget Z-order or spatial layout quirks."""
    def __init__(self, label: str):
        super().__init__()
        self._label = label

    def GetName(self, childId: int):
        return wx.ACC_OK, self._label


# ══════════════════════════════════════════════════════════════════════════════
#  Main Frame
# ══════════════════════════════════════════════════════════════════════════════

class CarInsuranceFrame(wx.Frame):

    def __init__(self, parent):
        super().__init__(parent,
                         title="Car Insurance — Data Entry Form",
                         size=(1040, 820),
                         style=wx.DEFAULT_FRAME_STYLE,
                         name="CarInsuranceForm")
        self.SetMinSize((860, 620))
        self.SetBackgroundColour(ACCENT_LT)

        self._controls: dict = {}       # field_name → widget
        self._accessibles: list = []    # hold _CtrlAccessible refs — prevents Python GC freeing them

        self._build_ui()
        self.Centre()

    # ── Top-level UI ──────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = wx.Panel(self, name="outer_panel")
        outer.SetBackgroundColour(ACCENT_LT)
        outer_sz = wx.BoxSizer(wx.VERTICAL)

        # ── Header banner ─────────────────────────────────────────────────────
        hdr = wx.Panel(outer, name="header_panel", size=(-1, 56))
        hdr.SetBackgroundColour(HDR_BG)
        hdr_sz = wx.BoxSizer(wx.HORIZONTAL)
        title = wx.StaticText(hdr, label="  Car Insurance — Data Entry Form",
                              name="form_title")
        title.SetFont(_font(*FONT_TITLE))
        title.SetForegroundColour(HDR_FG)
        hdr_sz.Add(title, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 8)
        hdr.SetSizer(hdr_sz)
        outer_sz.Add(hdr, 0, wx.EXPAND)

        # ── Notebook ──────────────────────────────────────────────────────────
        self.nb = wx.Notebook(outer, name="main_notebook")
        outer_sz.Add(self.nb, 1, wx.EXPAND | wx.ALL, 8)

        for builder in [
            self._build_policy_tab,
            self._build_policyholder_tab,
            self._build_vehicle_tab,
            self._build_coverage_tab,
            self._build_drivers_tab,
            self._build_history_tab,
            self._build_claims_tab,
            self._build_payment_tab,
        ]:
            builder()

        # ── Footer ────────────────────────────────────────────────────────────
        footer = wx.Panel(outer, name="footer_panel")
        footer.SetBackgroundColour(WHITE)
        footer_sz = wx.BoxSizer(wx.HORIZONTAL)
        self._status_lbl = wx.StaticText(footer, label="  Ready",
                                         name="status_label")
        footer_sz.Add(self._status_lbl, 0, wx.ALIGN_CENTER_VERTICAL)
        footer_sz.AddStretchSpacer()

        btn_specs = [
            ("Submit",        "btn_submit",        self._on_submit),
            ("Clear All",     "btn_clear",         self._on_clear),
            ("Print Preview", "btn_print_preview", self._on_print_preview),
            ("Load Record",   "btn_load",          self._on_load),
            ("Save Record",   "btn_save",          self._on_save),
        ]
        for lbl, name, handler in btn_specs:
            b = wx.Button(footer, label=lbl, name=name)
            b.Bind(wx.EVT_BUTTON, handler)
            footer_sz.Add(b, 0, wx.ALL, 5)

        footer.SetSizer(footer_sz)
        outer_sz.Add(footer, 0, wx.EXPAND)

        outer.SetSizer(outer_sz)

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _make_page(self, title: str, tab_name: str):
        page = scrolled.ScrolledPanel(self.nb, name=tab_name)
        page.SetBackgroundColour(WHITE)
        page.SetupScrolling(scroll_x=False, scroll_y=True)
        self.nb.AddPage(page, title)
        sz = wx.BoxSizer(wx.VERTICAL)
        page.SetSizer(sz)
        return page, sz

    def _section(self, parent, sizer, label: str):
        """Coloured section-header banner."""
        bar = wx.Panel(parent,
                       name=f"section_{label.lower().replace(' ','_')[:30]}")
        bar.SetBackgroundColour(ACCENT)
        bar_sz = wx.BoxSizer(wx.HORIZONTAL)
        lbl = wx.StaticText(bar, label=f"  {label}")
        lbl.SetFont(_font(*FONT_SECT))
        lbl.SetForegroundColour(SECT_FG)
        bar_sz.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.TOP | wx.BOTTOM, 4)
        bar.SetSizer(bar_sz)
        sizer.Add(bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)

    def _form_grid(self, parent, sizer):
        grid = wx.FlexGridSizer(cols=2, vgap=5, hgap=10)
        grid.AddGrowableCol(1)
        sizer.Add(grid, 0, wx.EXPAND | wx.ALL, 10)
        return grid

    def _row(self, grid, parent, label: str, ctrl_factory):
        """
        Create label StaticText FIRST, then call ctrl_factory so that
        Windows UIA auto-labeling correctly links the label to the control.
        Also explicitly sets IAccessible Name via wx.Accessible as a guarantee.
        """
        lbl = wx.StaticText(parent, label=label)
        lbl.SetFont(_font(*FONT_LABEL))
        grid.Add(lbl, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT, 6)
        ctrl = ctrl_factory(parent)
        try:
            # Explicitly fix Windows Z-order: ctrl must come IMMEDIATELY after lbl
            # so UIA auto-labeling assigns the correct Name to the control.
            ctrl.MoveAfterInTabOrder(lbl)
        except Exception:
            pass
        try:
            # Belt-and-suspenders: also set IAccessible name
            acc = _CtrlAccessible(label)
            self._accessibles.append(acc)
            ctrl.SetAccessible(acc)
        except Exception:
            pass
        grid.Add(ctrl, 1, wx.EXPAND | wx.RIGHT, 6)
        return ctrl

    # ── Control factories ─────────────────────────────────────────────────────

    def _text(self, parent, name: str, value: str = ""):
        ctrl = wx.TextCtrl(parent, value=value, name=name)
        ctrl.SetFont(_font(*FONT_MONO))
        self._controls[name] = ctrl
        return ctrl

    def _choice(self, parent, name: str, choices: list, default: str = ""):
        ctrl = wx.Choice(parent, choices=choices, name=name)
        ctrl.SetFont(_font(*FONT_LABEL))
        idx = choices.index(default) if default in choices else 0
        ctrl.SetSelection(idx)
        self._controls[name] = ctrl
        return ctrl

    def _check(self, parent, name: str, label: str, default: bool = False):
        ctrl = wx.CheckBox(parent, label=label, name=name)
        ctrl.SetFont(_font(*FONT_LABEL))
        ctrl.SetValue(default)
        self._controls[name] = ctrl
        return ctrl

    def _check_row(self, parent, sizer, checks: list):
        """Horizontal strip of checkboxes added directly to sizer."""
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(WHITE)
        psz = wx.BoxSizer(wx.HORIZONTAL)
        for name, label, default in checks:
            cb = self._check(panel, name, label, default)
            psz.Add(cb, 0, wx.ALIGN_CENTER_VERTICAL | wx.LEFT | wx.RIGHT, 10)
        panel.SetSizer(psz)
        sizer.Add(panel, 0, wx.LEFT | wx.BOTTOM, 10)

    def _check_grid(self, parent, sizer, checks: list, cols: int = 3):
        """Grid of checkboxes added directly to sizer."""
        panel = wx.Panel(parent)
        panel.SetBackgroundColour(WHITE)
        g = wx.GridSizer(rows=0, cols=cols, vgap=5, hgap=20)
        for name, label, default in checks:
            cb = self._check(panel, name, label, default)
            g.Add(cb, 0, wx.ALIGN_CENTER_VERTICAL)
        panel.SetSizer(g)
        sizer.Add(panel, 0, wx.EXPAND | wx.ALL, 10)

    # ══════════════════════════════════════════════════════════════════════════
    #  Tab builders
    # ══════════════════════════════════════════════════════════════════════════

    def _build_policy_tab(self):
        page, sz = self._make_page("Policy", "tab_policy")

        self._section(page, sz, "Policy Information")
        g = self._form_grid(page, sz)
        self._row(g, page, "Policy Number",
            lambda p: self._text(p, "policy_number"))
        self._row(g, page, "Policy Status",
            lambda p: self._choice(p, "policy_status",
                ["Active","Inactive","Cancelled","Expired","Pending","Lapsed"], "Active"))
        self._row(g, page, "Policy Type",
            lambda p: self._choice(p, "policy_type", COVERAGE_TYPES, "Full Coverage"))
        self._row(g, page, "Policy Term",
            lambda p: self._choice(p, "policy_term",
                ["6 Month","12 Month","Monthly"], "6 Month"))
        self._row(g, page, "Effective Date",
            lambda p: self._text(p, "effective_date"))
        self._row(g, page, "Expiration Date",
            lambda p: self._text(p, "expiration_date"))

        self._section(page, sz, "Agent / Agency")
        g2 = self._form_grid(page, sz)
        self._row(g2, page, "Agent ID",    lambda p: self._text(p, "agent_id"))
        self._row(g2, page, "Agent Name",  lambda p: self._text(p, "agent_name"))
        self._row(g2, page, "Agency Name", lambda p: self._text(p, "agency_name"))
        self._row(g2, page, "Underwriter", lambda p: self._text(p, "underwriter"))

        self._section(page, sz, "Flags")
        self._check_row(page, sz, [
            ("renewal_flag", "Renewal Policy",         False),
            ("paperless",    "Paperless / e-Delivery", True),
            ("esign",        "E-Signature Obtained",   False),
        ])
        page.Layout(); page.SetupScrolling(scroll_x=False)

    def _build_policyholder_tab(self):
        page, sz = self._make_page("Policyholder", "tab_policyholder")

        self._section(page, sz, "Personal Information")
        g = self._form_grid(page, sz)
        self._row(g, page, "First Name",    lambda p: self._text(p, "ph_first"))
        self._row(g, page, "Middle Name",   lambda p: self._text(p, "ph_middle"))
        self._row(g, page, "Last Name",     lambda p: self._text(p, "ph_last"))
        self._row(g, page, "Suffix",
            lambda p: self._choice(p, "ph_suffix",
                ["","Jr.","Sr.","II","III","IV","V"]))
        self._row(g, page, "Date of Birth", lambda p: self._text(p, "ph_dob"))
        self._row(g, page, "Gender",
            lambda p: self._choice(p, "ph_gender", GENDER_OPTS, "Male"))
        self._row(g, page, "SSN",           lambda p: self._text(p, "ph_ssn"))
        self._row(g, page, "Marital Status",
            lambda p: self._choice(p, "ph_marital", MARITAL, "Single"))
        self._row(g, page, "Occupation",
            lambda p: self._choice(p, "ph_occupation", OCCUPATION, "Employed Full-Time"))
        self._row(g, page, "Education Level",
            lambda p: self._choice(p, "ph_education", EDUCATION, "Bachelor's Degree"))
        self._row(g, page, "Credit Score",  lambda p: self._text(p, "ph_credit_score"))
        self._row(g, page, "Years Continuously Insured",
            lambda p: self._text(p, "ph_years_insured"))

        self._section(page, sz, "Contact Information")
        g2 = self._form_grid(page, sz)
        self._row(g2, page, "Email Address", lambda p: self._text(p, "ph_email"))
        self._row(g2, page, "Home Phone",    lambda p: self._text(p, "ph_phone_home"))
        self._row(g2, page, "Cell Phone",    lambda p: self._text(p, "ph_phone_cell"))
        self._row(g2, page, "Work Phone",    lambda p: self._text(p, "ph_phone_work"))

        self._section(page, sz, "Address")
        g3 = self._form_grid(page, sz)
        self._row(g3, page, "Street Address 1", lambda p: self._text(p, "ph_addr1"))
        self._row(g3, page, "Street Address 2", lambda p: self._text(p, "ph_addr2"))
        self._row(g3, page, "City",             lambda p: self._text(p, "ph_city"))
        self._row(g3, page, "State",
            lambda p: self._choice(p, "ph_state", STATES, "California"))
        self._row(g3, page, "ZIP Code",         lambda p: self._text(p, "ph_zip"))
        self._row(g3, page, "County",           lambda p: self._text(p, "ph_county"))
        self._row(g3, page, "Country",
            lambda p: self._text(p, "ph_country", value="United States"))
        self._row(g3, page, "Years at Address", lambda p: self._text(p, "ph_years_at_addr"))
        self._check_row(page, sz, [("ph_homeowner", "Homeowner", False)])

        self._section(page, sz, "Driver's License")
        g4 = self._form_grid(page, sz)
        self._row(g4, page, "DL Number",
            lambda p: self._text(p, "ph_drivers_license"))
        self._row(g4, page, "DL Issuing State",
            lambda p: self._choice(p, "ph_dl_state", STATES, "California"))
        self._row(g4, page, "DL Expiration",
            lambda p: self._text(p, "ph_dl_exp"))

        self._section(page, sz, "Prior Insurance")
        g5 = self._form_grid(page, sz)
        self._row(g5, page, "Prior Insurer",
            lambda p: self._text(p, "ph_prior_insurer"))
        self._row(g5, page, "Prior Policy No.",
            lambda p: self._text(p, "ph_prior_policy_no"))
        self._row(g5, page, "Prior Expiry Date",
            lambda p: self._text(p, "ph_prior_expiry"))
        self._row(g5, page, "Prior Liability Limits",
            lambda p: self._text(p, "ph_prior_liability"))
        page.Layout(); page.SetupScrolling(scroll_x=False)

    def _build_vehicle_tab(self):
        page, sz = self._make_page("Vehicle", "tab_vehicle")

        self._section(page, sz, "Vehicle Identification")
        g = self._form_grid(page, sz)
        self._row(g, page, "VIN",              lambda p: self._text(p, "v_vin"))
        self._row(g, page, "Year",             lambda p: self._text(p, "v_year"))
        self._row(g, page, "Make",
            lambda p: self._choice(p, "v_make", MAKES, "Toyota"))
        self._row(g, page, "Model",            lambda p: self._text(p, "v_model"))
        self._row(g, page, "Trim / Sub-model", lambda p: self._text(p, "v_trim"))
        self._row(g, page, "Body Type",
            lambda p: self._choice(p, "v_body", BODY_TYPES, "Sedan"))
        self._row(g, page, "Color",
            lambda p: self._choice(p, "v_color", COLORS, "Black"))
        self._row(g, page, "Number of Doors",
            lambda p: self._choice(p, "v_doors", ["2","3","4","5"], "4"))

        self._section(page, sz, "Engine & Drivetrain")
        g2 = self._form_grid(page, sz)
        self._row(g2, page, "Cylinders",
            lambda p: self._choice(p, "v_cylinders",
                ["3","4","5","6","8","10","12","Electric","Hybrid"]))
        self._row(g2, page, "Displacement (L)",
            lambda p: self._text(p, "v_displacement"))
        self._row(g2, page, "Fuel Type",
            lambda p: self._choice(p, "v_fuel",
                ["Gasoline","Diesel","Electric","Hybrid","Plug-in Hybrid",
                 "Natural Gas","Flex Fuel"], "Gasoline"))
        self._row(g2, page, "Transmission",
            lambda p: self._choice(p, "v_transmission",
                ["Automatic","Manual","CVT","DCT","Semi-Automatic"], "Automatic"))
        self._row(g2, page, "Drive Type",
            lambda p: self._choice(p, "v_drive", ["FWD","RWD","AWD","4WD"], "FWD"))

        self._section(page, sz, "Usage & Mileage")
        g3 = self._form_grid(page, sz)
        self._row(g3, page, "Current Mileage",
            lambda p: self._text(p, "v_mileage"))
        self._row(g3, page, "Annual Miles Est.",
            lambda p: self._text(p, "v_annual_miles"))
        self._row(g3, page, "Primary Use",
            lambda p: self._choice(p, "v_usage", USAGE, "Personal/Pleasure"))
        self._row(g3, page, "Garaging Location",
            lambda p: self._choice(p, "v_garaging", GARAGING, "Private Garage"))

        self._section(page, sz, "Ownership / Purchase")
        g4 = self._form_grid(page, sz)
        self._row(g4, page, "Purchase Date",
            lambda p: self._text(p, "v_purchase_date"))
        self._row(g4, page, "Purchase Price ($)",
            lambda p: self._text(p, "v_purchase_price"))
        self._row(g4, page, "Current Market Value ($)",
            lambda p: self._text(p, "v_market_value"))
        self._row(g4, page, "Vehicle Condition",
            lambda p: self._choice(p, "v_condition",
                ["Excellent","Good","Fair","Poor"], "Good"))
        self._row(g4, page, "Title State",
            lambda p: self._choice(p, "v_title_state", STATES, "California"))

        self._section(page, sz, "Lien / Financing")
        g5 = self._form_grid(page, sz)
        self._row(g5, page, "Lienholder/Lender",
            lambda p: self._text(p, "v_lienholder"))
        self._row(g5, page, "Lienholder Address",
            lambda p: self._text(p, "v_lienholder_addr"))
        self._row(g5, page, "Loan / Lease No.",
            lambda p: self._text(p, "v_loan_number"))

        self._section(page, sz, "Safety & Features")
        self._check_grid(page, sz, [
            ("v_salvage",         "Salvage Title",              False),
            ("v_anti_theft",      "Anti-Theft Device",          False),
            ("v_airbags",         "Airbags",                    True),
            ("v_abs",             "ABS Brakes",                 True),
            ("v_daytime_lights",  "Daytime Running Lights",     True),
            ("v_backup_camera",   "Backup Camera",              False),
            ("v_gps",             "GPS Tracking",               False),
            ("v_parking_sensors", "Parking Sensors",            False),
            ("v_lane_assist",     "Lane Departure Warning",     False),
            ("v_adaptive_cruise", "Adaptive Cruise Control",    False),
            ("v_custom_equipment","Custom Equipment / Mods",    False),
        ], cols=3)
        g6 = self._form_grid(page, sz)
        self._row(g6, page, "Custom Equipment Value ($)",
            lambda p: self._text(p, "v_custom_value"))
        page.Layout(); page.SetupScrolling(scroll_x=False)

    def _build_coverage_tab(self):
        page, sz = self._make_page("Coverage", "tab_coverage")

        self._section(page, sz, "Liability Limits")
        g = self._form_grid(page, sz)
        self._row(g, page, "Bodily Injury (k$/k$)",
            lambda p: self._choice(p, "cov_bodily_limit",
                ["25/50","50/100","100/300","250/500","500/500","300/300"], "100/300"))
        self._row(g, page, "Property Damage ($)",
            lambda p: self._choice(p, "cov_property",
                ["25,000","50,000","100,000","250,000","500,000"], "100,000"))

        self._section(page, sz, "Collision & Comprehensive")
        g2 = self._form_grid(page, sz)
        self._row(g2, page, "Collision Deductible",
            lambda p: self._choice(p, "cov_collision_ded",
                ["0","100","250","500","1000","2000","2500","5000"], "500"))
        self._row(g2, page, "Comprehensive Deductible",
            lambda p: self._choice(p, "cov_comp_ded",
                ["0","100","250","500","1000","2000"], "250"))

        self._section(page, sz, "Additional Coverages")
        self._check_grid(page, sz, [
            ("cov_um_uim",       "Uninsured/Underinsured Motorist", True),
            ("cov_pip",          "Personal Injury Protection (PIP)", False),
            ("cov_medpay",       "Medical Payments",                False),
            ("cov_rental",       "Rental Reimbursement",            True),
            ("cov_roadside",     "Roadside Assistance",             False),
            ("cov_gap",          "GAP Insurance",                   False),
            ("cov_rideshare",    "Rideshare Coverage",              False),
            ("cov_new_car",      "New Car Replacement",             False),
            ("cov_acc_forgive",  "Accident Forgiveness",            False),
            ("cov_disappear_ded","Diminishing Deductible",          False),
        ], cols=2)

        self._section(page, sz, "Optional Limits")
        g3 = self._form_grid(page, sz)
        self._row(g3, page, "UM/UIM Limit",
            lambda p: self._text(p, "cov_um_limit", value="100/300"))
        self._row(g3, page, "PIP Limit ($)",
            lambda p: self._text(p, "cov_pip_limit"))
        self._row(g3, page, "MedPay Limit ($)",
            lambda p: self._text(p, "cov_medpay_limit"))
        self._row(g3, page, "Rental Limit",
            lambda p: self._text(p, "cov_rental_limit", value="$30/day"))

        self._section(page, sz, "Discounts Applied")
        self._check_grid(page, sz, [
            ("disc_multi_car",    "Multi-Car",                  False),
            ("disc_multi_policy", "Multi-Policy / Bundle",      False),
            ("disc_good_driver",  "Good Driver (5+ yr clean)",  False),
            ("disc_good_student", "Good Student",               False),
            ("disc_defensive_drv","Defensive Driving Course",   False),
            ("disc_loyalty",      "Loyalty Discount",           False),
            ("disc_military",     "Military",                   False),
            ("disc_affinity",     "Affinity Group",             False),
        ], cols=2)

        self._section(page, sz, "Premium Summary")
        g4 = self._form_grid(page, sz)
        self._row(g4, page, "Total Premium ($)",
            lambda p: self._text(p, "cov_premium_total"))
        self._row(g4, page, "Payment Frequency",
            lambda p: self._choice(p, "cov_premium_period", PAYMENT_FREQ, "Monthly"))
        page.Layout(); page.SetupScrolling(scroll_x=False)

    def _build_drivers_tab(self):
        page, sz = self._make_page("Drivers", "tab_drivers")

        for prefix, pfx, g_def, r_def in [
            ("Driver 2", "d2", "Female", "Spouse"),
            ("Driver 3", "d3", "Male",   "Child"),
        ]:
            self._section(page, sz, prefix)
            g = self._form_grid(page, sz)
            self._row(g, page, "First Name",
                lambda p, x=pfx: self._text(p, f"{x}_first"))
            self._row(g, page, "Last Name",
                lambda p, x=pfx: self._text(p, f"{x}_last"))
            self._row(g, page, "Date of Birth",
                lambda p, x=pfx: self._text(p, f"{x}_dob"))
            # Gender as Choice — avoids radio panel Z-order disruption
            self._row(g, page, "Gender",
                lambda p, x=pfx, d=g_def: self._choice(p, f"{x}_gender",
                    GENDER_OPTS, d))
            self._row(g, page, "Relationship",
                lambda p, x=pfx, d=r_def: self._choice(p, f"{x}_relation",
                    RELATIONSHIP, d))
            self._row(g, page, "DL Number",
                lambda p, x=pfx: self._text(p, f"{x}_dl"))
            self._row(g, page, "DL Issuing State",
                lambda p, x=pfx: self._choice(p, f"{x}_dl_state",
                    STATES, "California"))
            self._row(g, page, "DL Expiration",
                lambda p, x=pfx: self._text(p, f"{x}_dl_exp"))
            self._row(g, page, "Accidents (3 yr)",
                lambda p, x=pfx: self._choice(p, f"{x}_accidents",
                    COUNT_OPTS, "0"))
            self._row(g, page, "Violations (3 yr)",
                lambda p, x=pfx: self._choice(p, f"{x}_violations",
                    COUNT_OPTS, "0"))
            self._check_row(page, sz, [
                (f"{pfx}_sr22",     f"{prefix} — SR-22 Required",  False),
                (f"{pfx}_excluded", f"{prefix} — Excluded Driver",  False),
            ])

        page.Layout(); page.SetupScrolling(scroll_x=False)

    def _build_history_tab(self):
        page, sz = self._make_page("History", "tab_history")

        self._section(page, sz, "Driving Record — Primary Driver (3-Year Look-Back)")
        g = self._form_grid(page, sz)
        self._row(g, page, "At-Fault Accidents",
            lambda p: self._choice(p, "hist_at_fault", COUNT_OPTS, "0"))
        self._row(g, page, "Not-At-Fault Accidents",
            lambda p: self._choice(p, "hist_not_at_fault", COUNT_OPTS, "0"))
        self._row(g, page, "Total Accidents",
            lambda p: self._choice(p, "hist_accidents_3yr", COUNT_OPTS, "0"))
        self._row(g, page, "Moving Violations",
            lambda p: self._choice(p, "hist_violations_3yr", COUNT_OPTS, "0"))
        self._row(g, page, "Comprehensive Claims",
            lambda p: self._choice(p, "hist_comp_claims", COUNT_OPTS, "0"))
        self._row(g, page, "Total Claims Filed",
            lambda p: self._choice(p, "hist_claims_3yr", COUNT_OPTS, "0"))

        self._section(page, sz, "Special Flags")
        self._check_row(page, sz, [
            ("hist_dui",          "DUI / DWI on Record",           False),
            ("hist_sr22",         "SR-22 / FR-44 Filed",           False),
            ("hist_license_susp", "License Suspended or Revoked",  False),
        ])
        page.Layout(); page.SetupScrolling(scroll_x=False)

    def _build_claims_tab(self):
        page, sz = self._make_page("Claims", "tab_claims")

        self._section(page, sz, "Claim Details")
        g = self._form_grid(page, sz)
        self._row(g, page, "Claim Number",
            lambda p: self._text(p, "claim_number"))
        self._row(g, page, "Date of Loss",
            lambda p: self._text(p, "claim_date"))
        self._row(g, page, "Claim Type",
            lambda p: self._choice(p, "claim_type", CLAIM_TYPE, "Collision"))
        self._row(g, page, "Claim Status",
            lambda p: self._choice(p, "claim_status", CLAIM_STATUS, "Open"))
        self._row(g, page, "Claim Amount ($)",
            lambda p: self._text(p, "claim_amount"))
        self._row(g, page, "Deductible ($)",
            lambda p: self._text(p, "claim_deductible"))
        self._row(g, page, "Adjuster Name",
            lambda p: self._text(p, "claim_adjuster"))
        self._row(g, page, "Settlement Amount ($)",
            lambda p: self._text(p, "claim_settlement"))
        self._row(g, page, "Resolution Date",
            lambda p: self._text(p, "claim_resolve_date"))

        self._section(page, sz, "Claim Description")
        desc_lbl = wx.StaticText(page, label="Description")
        desc_lbl.SetFont(_font(*FONT_LABEL))
        sz.Add(desc_lbl, 0, wx.LEFT | wx.TOP, 10)
        desc = wx.TextCtrl(page, name="claim_desc",
                           style=wx.TE_MULTILINE | wx.TE_WORDWRAP,
                           size=(-1, 80))
        desc.SetFont(_font(*FONT_MONO))
        self._controls["claim_desc"] = desc
        sz.Add(desc, 0, wx.EXPAND | wx.ALL, 8)

        self._section(page, sz, "Circumstances")
        self._check_row(page, sz, [
            ("claim_police_rpt",  "Police Report Filed",    False),
            ("claim_at_fault",    "Policyholder At Fault",  False),
            ("claim_injury",      "Injury Involved",        False),
            ("claim_third_party", "Third Party Involved",   False),
        ])

        self._section(page, sz, "Third Party Information")
        g2 = self._form_grid(page, sz)
        self._row(g2, page, "Police Report No.",
            lambda p: self._text(p, "claim_report_no"))
        self._row(g2, page, "Third Party Name",
            lambda p: self._text(p, "claim_tp_name"))
        self._row(g2, page, "Third Party Policy",
            lambda p: self._text(p, "claim_tp_policy"))
        page.Layout(); page.SetupScrolling(scroll_x=False)

    def _build_payment_tab(self):
        page, sz = self._make_page("Payment", "tab_payment")

        self._section(page, sz, "Billing Summary")
        g = self._form_grid(page, sz)
        self._row(g, page, "Total Premium ($)",
            lambda p: self._text(p, "pay_amount"))
        self._row(g, page, "Down Payment ($)",
            lambda p: self._text(p, "pay_down_payment"))
        self._row(g, page, "Balance Due ($)",
            lambda p: self._text(p, "pay_balance_due"))
        self._row(g, page, "Payment Due Date",
            lambda p: self._text(p, "pay_due_date"))
        self._row(g, page, "Payment Frequency",
            lambda p: self._choice(p, "pay_frequency", PAYMENT_FREQ, "Monthly"))
        self._row(g, page, "Last Payment Date",
            lambda p: self._text(p, "pay_last_paid_date"))
        self._row(g, page, "Last Payment Amount ($)",
            lambda p: self._text(p, "pay_last_paid_amt"))
        self._check_row(page, sz, [("pay_auto_pay", "Auto-Pay Enrolled", True)])

        self._section(page, sz, "Payment Method")
        g2 = self._form_grid(page, sz)
        self._row(g2, page, "Method",
            lambda p: self._choice(p, "pay_method", PAYMENT_METH, "Credit Card"))

        self._section(page, sz, "Credit / Debit Card")
        g3 = self._form_grid(page, sz)
        self._row(g3, page, "Cardholder Name",
            lambda p: self._text(p, "pay_cc_name"))
        self._row(g3, page, "Card Number",
            lambda p: self._text(p, "pay_cc_number"))
        self._row(g3, page, "Expiration (MM/YY)",
            lambda p: self._text(p, "pay_cc_exp"))
        self._row(g3, page, "CVV",
            lambda p: self._text(p, "pay_cc_cvv"))

        self._section(page, sz, "Bank Account (ACH)")
        g4 = self._form_grid(page, sz)
        self._row(g4, page, "Bank Name",      lambda p: self._text(p, "pay_bank_name"))
        self._row(g4, page, "Routing Number", lambda p: self._text(p, "pay_routing"))
        self._row(g4, page, "Account Number", lambda p: self._text(p, "pay_account"))
        self._row(g4, page, "Account Type",
            lambda p: self._choice(p, "pay_account_type",
                ["Checking","Savings"], "Checking"))

        self._section(page, sz, "Billing Address")
        g5 = self._form_grid(page, sz)
        self._row(g5, page, "Street Address",
            lambda p: self._text(p, "pay_billing_addr1"))
        self._row(g5, page, "City",
            lambda p: self._text(p, "pay_billing_city"))
        self._row(g5, page, "State",
            lambda p: self._choice(p, "pay_billing_state", STATES, "California"))
        self._row(g5, page, "ZIP Code",
            lambda p: self._text(p, "pay_billing_zip"))
        page.Layout(); page.SetupScrolling(scroll_x=False)

    # ══════════════════════════════════════════════════════════════════════════
    #  Data collection & actions
    # ══════════════════════════════════════════════════════════════════════════

    def _collect_data(self) -> dict:
        data = {}
        for name, ctrl in self._controls.items():
            if isinstance(ctrl, wx.TextCtrl):
                data[name] = ctrl.GetValue()
            elif isinstance(ctrl, wx.Choice):
                sel = ctrl.GetSelection()
                data[name] = ctrl.GetString(sel) if sel != wx.NOT_FOUND else ""
            elif isinstance(ctrl, wx.CheckBox):
                data[name] = ctrl.GetValue()
        data["_timestamp"] = datetime.now().isoformat()
        return data

    def _on_save(self, event):
        with wx.FileDialog(self, "Save Record",
                           wildcard="JSON files (*.json)|*.json",
                           style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._collect_data(), f, indent=2)
            self._status_lbl.SetLabel(f"  Saved → {os.path.basename(path)}")
        except Exception as e:
            wx.MessageBox(str(e), "Save Error", wx.OK | wx.ICON_ERROR)

    def _on_load(self, event):
        with wx.FileDialog(self, "Load Record",
                           wildcard="JSON files (*.json)|*.json",
                           style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST) as dlg:
            if dlg.ShowModal() == wx.ID_CANCEL:
                return
            path = dlg.GetPath()
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            wx.MessageBox(str(e), "Load Error", wx.OK | wx.ICON_ERROR)
            return
        for name, val in data.items():
            ctrl = self._controls.get(name)
            if ctrl is None:
                continue
            if isinstance(ctrl, wx.TextCtrl):
                ctrl.SetValue(str(val))
            elif isinstance(ctrl, wx.Choice):
                idx = ctrl.FindString(str(val))
                if idx != wx.NOT_FOUND:
                    ctrl.SetSelection(idx)
            elif isinstance(ctrl, wx.CheckBox):
                ctrl.SetValue(bool(val))
        self._status_lbl.SetLabel(f"  Loaded ← {os.path.basename(path)}")

    def _on_print_preview(self, event):
        dlg = wx.Dialog(self, title="Record Preview", size=(700, 560))
        dsz = wx.BoxSizer(wx.VERTICAL)
        txt = wx.TextCtrl(dlg, name="preview_text",
                          style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_WORDWRAP)
        txt.SetValue(json.dumps(self._collect_data(), indent=2))
        dsz.Add(txt, 1, wx.EXPAND | wx.ALL, 8)
        btn = wx.Button(dlg, wx.ID_CLOSE, label="Close", name="btn_preview_close")
        btn.Bind(wx.EVT_BUTTON, lambda e: dlg.EndModal(wx.ID_CLOSE))
        dsz.Add(btn, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        dlg.SetSizer(dsz)
        dlg.ShowModal()
        dlg.Destroy()

    def _on_clear(self, event):
        if wx.MessageBox("Clear all fields? This cannot be undone.",
                         "Clear All", wx.YES_NO | wx.ICON_WARNING) != wx.YES:
            return
        for ctrl in self._controls.values():
            if isinstance(ctrl, wx.TextCtrl):
                ctrl.SetValue("")
            elif isinstance(ctrl, wx.CheckBox):
                ctrl.SetValue(False)
            elif isinstance(ctrl, wx.Choice) and ctrl.GetCount() > 0:
                ctrl.SetSelection(0)
        self._status_lbl.SetLabel("  Cleared")

    def _on_submit(self, event):
        data = self._collect_data()
        missing = [f.replace("_"," ").title()
                   for f in ["policy_number","ph_first","ph_last","v_vin"]
                   if not str(data.get(f,"")).strip()]
        if missing:
            wx.MessageBox("Please fill in required fields:\n• " + "\n• ".join(missing),
                          "Missing Fields", wx.OK | wx.ICON_WARNING)
            return
        wx.MessageBox(
            f"Record submitted successfully.\n"
            f"Policy : {data.get('policy_number','')}\n"
            f"Insured: {data.get('ph_first','')} {data.get('ph_last','')}\n"
            f"Vehicle: {data.get('v_year','')} {data.get('v_make','')} "
            f"{data.get('v_model','')}",
            "Submitted", wx.OK | wx.ICON_INFORMATION)
        self._status_lbl.SetLabel("  Submitted")


# ══════════════════════════════════════════════════════════════════════════════

class CarInsuranceApp(wx.App):
    def OnInit(self):
        frame = CarInsuranceFrame(None)
        frame.Show()
        return True


if __name__ == "__main__":
    app = CarInsuranceApp()
    app.MainLoop()
