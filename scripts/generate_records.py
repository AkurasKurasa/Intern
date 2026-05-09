"""
Generate N realistic car insurance intake records in the exact format of
data_entry_tasks/data_entry_intake.txt  (RECORD X OF N format).
Output: data_entry_tasks/data_entry_intake_100.txt
"""

import random
import string
from datetime import date, timedelta

random.seed(42)

# ── helpers ──────────────────────────────────────────────────────────────────

def rdate(start_yr=1955, end_yr=2005):
    start = date(start_yr, 1, 1)
    end   = date(end_yr, 12, 31)
    return start + timedelta(days=random.randint(0, (end - start).days))

def fdate(d): return d.strftime("%m/%d/%Y")

def vin():
    chars = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"
    return "".join(random.choices(chars, k=17))

def policy_no(i):
    return f"PAI-2026-{10000 + i:05d}"

def ssn():
    return f"{random.randint(200,799):03d}-{random.randint(10,99):02d}-{random.randint(1000,9999):04d}"

def phone():
    area = random.choice([213,310,323,408,415,510,562,619,626,650,714,760,818,909,916,949,951])
    return f"({area}) {random.randint(200,999)}-{random.randint(1000,9999)}"

def card_no():
    groups = [random.randint(4000,4999)] + [random.randint(1000,9999)]*3
    return " ".join(str(g) for g in groups)

def routing():
    return f"{random.randint(21000000, 121999999):09d}"

def account_no():
    return f"{random.randint(100000000,999999999)}"

def dl_no():
    return random.choice(string.ascii_uppercase) + "".join(random.choices(string.digits, k=7))

def email(first, last, dob_yr):
    domains = ["gmail.com","yahoo.com","outlook.com","icloud.com","hotmail.com"]
    styles  = [
        f"{first.lower()}{last.lower()}{dob_yr % 100}",
        f"{first[0].lower()}{last.lower()}",
        f"{first.lower()}.{last.lower()}",
        f"{last.lower()}{random.randint(10,99)}",
    ]
    return f"{random.choice(styles)}@{random.choice(domains)}"

def weighted(choices, weights=None):
    return random.choices(choices, weights=weights, k=1)[0]

# ── data pools ───────────────────────────────────────────────────────────────

FIRST_M   = ["James","Michael","Robert","David","William","Richard","Charles","Joseph",
             "Thomas","Christopher","Daniel","Paul","Mark","Donald","George","Kenneth",
             "Steven","Edward","Brian","Ronald","Anthony","Kevin","Jason","Matthew",
             "Gary","Timothy","Jose","Larry","Jeffrey","Frank","Scott","Eric",
             "Stephen","Andrew","Raymond","Gregory","Joshua","Jerry","Dennis","Walter"]

FIRST_F   = ["Mary","Patricia","Jennifer","Linda","Barbara","Elizabeth","Susan","Jessica",
             "Sarah","Karen","Lisa","Nancy","Betty","Margaret","Sandra","Ashley",
             "Dorothy","Kimberly","Emily","Donna","Michelle","Carol","Amanda","Melissa",
             "Deborah","Stephanie","Rebecca","Sharon","Laura","Cynthia","Kathleen",
             "Amy","Angela","Shirley","Anna","Brenda","Pamela","Emma","Nicole","Helen"]

LAST      = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis",
             "Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson",
             "Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson",
             "White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson",
             "Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen",
             "Hill","Flores","Green","Adams","Nelson","Baker","Hall","Rivera",
             "Campbell","Mitchell","Carter","Roberts","Phillips","Evans","Turner"]

MIDDLE_M  = ["Arthur","Ray","Eugene","Wayne","Lee","Alan","Dean","Dale","Lynn","Craig",
             "Scott","Glenn","Bruce","Keith","Todd","Brett","Chad","Kyle","Shane","Blake"]
MIDDLE_F  = ["Ann","Marie","Lynn","Kay","Jean","Sue","May","Rose","Grace","Faith",
             "Hope","Joy","Faye","Dawn","Gail","Rae","June","Claire","Beth","Kate"]

SUFFIX    = ["(none)","(none)","(none)","(none)","Jr.","Sr.","II","III"]

STATES    = ["Alabama","Alaska","Arizona","Arkansas","California","Colorado","Connecticut",
             "Delaware","Florida","Georgia","Hawaii","Idaho","Illinois","Indiana","Iowa",
             "Kansas","Kentucky","Louisiana","Maine","Maryland","Massachusetts","Michigan",
             "Minnesota","Mississippi","Missouri","Montana","Nebraska","Nevada",
             "New Hampshire","New Jersey","New Mexico","New York","North Carolina",
             "North Dakota","Ohio","Oklahoma","Oregon","Pennsylvania","Rhode Island",
             "South Carolina","South Dakota","Tennessee","Texas","Utah","Vermont",
             "Virginia","Washington","West Virginia","Wisconsin","Wyoming"]

STATE_ABB = {"California":"CA","Texas":"TX","Florida":"FL","New York":"NY","Illinois":"IL",
             "Arizona":"AZ","Colorado":"CO","Georgia":"GA","Nevada":"NV","Oregon":"OR",
             "Washington":"WA","Virginia":"VA","Tennessee":"TN","Ohio":"OH","Michigan":"MI"}

CITIES    = {
    "California": [("Los Angeles","90001"),("San Diego","92101"),("Riverside","92501"),
                   ("Sacramento","95814"),("Fresno","93721"),("San Jose","95101")],
    "Texas":      [("Houston","77001"),("Dallas","75201"),("Austin","73301"),
                   ("San Antonio","78201"),("Fort Worth","76101")],
    "Florida":    [("Miami","33101"),("Orlando","32801"),("Tampa","33601"),
                   ("Jacksonville","32201"),("Tallahassee","32301")],
    "New York":   [("New York","10001"),("Buffalo","14201"),("Albany","12201"),
                   ("Rochester","14601"),("Syracuse","13201")],
    "default":    [("Springfield","62701"),("Greenville","29601"),("Centerville","45458"),
                   ("Madison","53701"),("Lincoln","68501"),("Salem","97301")],
}

STREETS   = ["Oak Street","Maple Avenue","Cedar Drive","Pine Road","Elm Boulevard",
             "Creekstone Drive","Hillcrest Lane","Sunridge Way","Westfield Court",
             "Lake View Drive","Harbor Blvd","Falcon Ridge Road","Morning Glory Lane",
             "Sunset Drive","Valley Road","Ridgecrest Avenue","Brookside Circle",
             "Canyon Crest Drive","Ironwood Court","Meadowbrook Way"]

MAKES     = {
    "Honda":    (["Accord","Civic","CR-V","Pilot","Odyssey"],
                 [("1HGCV1","LA0","Sedan"),("1HGCM8","7A0","Sedan"),("5J6RW","2H80","SUV")]),
    "Toyota":   (["Camry","Corolla","RAV4","Highlander","Tacoma"],
                 [("4T1B11","HK0","Sedan"),("2T1BUR","HE0","Sedan"),("JTMRFREV","0","SUV")]),
    "Ford":     (["F-150","Mustang","Explorer","Escape","Edge"],
                 [("1FTEW1","E80","Truck"),("1FA6P8","CF0","Coupe"),("1FM5K8","D80","SUV")]),
    "Chevrolet":(["Malibu","Silverado","Equinox","Traverse","Blazer"],
                 [("1G1ZD5","ST0","Sedan"),("3GCUKTE","C0","Truck"),("3GNAXUEV","0","SUV")]),
    "Nissan":   (["Altima","Rogue","Sentra","Pathfinder","Murano"],
                 [("1N4BL4","EV0","Sedan"),("JN8AT2","MV0","SUV"),("3N1AB7","AP0","Sedan")]),
    "Hyundai":  (["Elantra","Tucson","Santa Fe","Sonata","Kona"],
                 [("5NPD84","LF0","Sedan"),("5NMS3C","AD0","SUV"),("KMHE24","LE0","Sedan")]),
    "BMW":      (["3 Series","5 Series","X3","X5","330i"],
                 [("WBA8E9","C50","Sedan"),("WBAJE7","C50","Sedan"),("5UXTY5","C00","SUV")]),
    "Mercedes": (["C300","E350","GLC 300","GLE 350","A220"],
                 [("55SWF4","KB0","Sedan"),("WDDZF4","KB0","Sedan"),("WDC0G4","KB0","SUV")]),
}

TRIMS     = ["Base","SE","LE","XLE","EX","EX-L","Sport","Sport 2.0T","Touring","Limited",
             "Premium","Platinum","LT","LTZ","RS","GT","STI","TRD","Laramie","Lariat"]

COLORS    = ["Black","White","Silver","Gray","Blue","Red","Navy","Dark Gray",
             "Pearl White","Champagne","Bronze","Green","Burgundy","Tan","Orange"]

FUEL      = ["Gasoline","Hybrid","Electric","Diesel","Plug-In Hybrid"]
TRANS     = ["Automatic","Manual","CVT"]
DRIVE     = ["FWD","RWD","AWD","4WD"]
BODY_DOORS= {"Sedan":4,"Coupe":2,"SUV":4,"Truck":4,"Hatchback":4,"Minivan":4,"Convertible":2}
PRIMARY_USE = ["Commute","Pleasure","Business","Farm"]
GARAGE    = ["Private Garage","Street Parking","Public Parking Lot","Carport","Driveway"]

OCCUPATIONS = ["Employed Full-Time","Employed Part-Time","Self-Employed","Retired",
                "Student","Homemaker","Military","Unemployed"]
EDUCATION   = ["High School Diploma","Some College","Associate's Degree",
                "Bachelor's Degree","Master's Degree","Doctorate","Trade Certificate"]
MARITAL     = ["Single","Married","Divorced","Widowed","Domestic Partner"]

INSURERS  = ["State Farm","Geico","Progressive","Allstate","USAA","Farmers",
             "Liberty Mutual","Nationwide","Travelers","AAA"]

AGENTS    = [("AGT-0092","Sandra Whitfield","Riverside Branch"),
             ("AGT-0104","Kevin Marsh","Downtown Office"),
             ("AGT-0117","Lena Vasquez","East Side Branch"),
             ("AGT-0031","Brian O'Neil","Westside Office"),
             ("AGT-0058","Priya Patel","South Branch"),
             ("AGT-0075","Derek Fontaine","North Branch"),
             ("AGT-0088","Tamara Kline","Central Office"),
             ("AGT-0122","Marcus Webb","Valley Branch")]

UNDERWRITERS = ["Marcus D. Chen","Patricia Osei","Alan Torres","Diana Krause",
                 "Javier Ruiz","Stephanie Lam","Owen Bradley","Fiona Nakamura"]

CLAIM_TYPES   = ["Collision","Comprehensive","Liability","Theft","Vandalism","Flood","Fire"]
ADJUSTERS     = ["Renee Harmon","Tom Birch","Aisha Coleman","Derek Watts",
                 "Sandra Moe","Paul Nguyen","Christine Farley","Harold Vance"]

POLICY_TYPES  = ["Full Coverage","Liability Only","Comprehensive Only","Collision Only"]
POLICY_TERMS  = ["6 Month","12 Month"]
PAY_FREQ      = ["Monthly","Quarterly","Semi-Annual","Annual"]
PAY_METHOD    = ["Credit Card","Debit Card","Bank Transfer (ACH)","Check"]
ACCT_TYPES    = ["Checking","Savings"]

RELATIONSHIPS = ["Spouse","Child","Parent","Sibling","Domestic Partner","Other"]

CLAIM_DESCS   = [
    "Vehicle rear-ended at a red light. Other driver admitted fault. Minor bumper and trunk damage.",
    "Windshield cracked by falling tree branch during storm. No collision involved.",
    "Hail damage to roof and hood sustained during severe weather event.",
    "Vehicle stolen from parking lot; recovered three days later with interior damage.",
    "Side-swipe while parked on residential street. No witnesses. Police report filed.",
    "Flooded engine after driving through standing water during heavy rain.",
    "Front-end collision at intersection. Both vehicles sustained moderate damage.",
    "Vandalism: keyed along driver's side and passenger door. Overnight incident.",
    "Deer strike on rural highway. Significant front-end damage. No injuries.",
    "Fire damage originating from engine compartment. Vehicle totaled.",
]

# ── record builder ───────────────────────────────────────────────────────────

def yn(flag): return "YES (check)" if flag else "NO"
def blank():  return "(leave blank)"

def build_record(idx, total):
    # ── identity ──
    gender   = random.choice(["Male","Female"])
    first    = random.choice(FIRST_M if gender == "Male" else FIRST_F)
    middle   = random.choice(MIDDLE_M if gender == "Male" else MIDDLE_F)
    last     = random.choice(LAST)
    suffix   = random.choice(SUFFIX)
    dob      = rdate(1955, 2000)
    dob_yr   = dob.year

    marital  = weighted(MARITAL, [30,40,15,5,10])
    occ      = random.choice(OCCUPATIONS)
    edu      = random.choice(EDUCATION)
    credit   = random.randint(580, 820)
    yrs_ins  = random.randint(0, 20)

    # ── contact ──
    em       = email(first, last, dob_yr)
    ph_home  = phone()
    ph_cell  = phone()
    ph_work  = phone() if occ not in ("Retired","Homemaker","Unemployed") else blank()

    # ── address ──
    _state_pool = list(CITIES.keys())  # California, Texas, Florida, New York, default
    _state_weights = [20, 10, 10, 10, 1]
    state = weighted(_state_pool, _state_weights)
    if state == "default": state = random.choice(STATES)
    city_zip = random.choice(CITIES.get(state, CITIES["default"]))
    city, zip_code = city_zip
    street_no = random.randint(100, 9999)
    street   = random.choice(STREETS)
    addr1    = f"{street_no} {street}"
    addr2    = random.choice([blank(),"Apt 2B","Unit 5","Suite 100","#302",blank(),blank()])
    county   = city  # simplification
    yrs_addr = random.randint(0, 15)
    homeowner = random.random() > 0.45

    # ── DL ──
    dl_num   = dl_no()
    dl_state = state
    dl_exp   = rdate(2026, 2030)

    # ── prior insurance ──
    prior_ins = random.choice(INSURERS)
    prior_pol = f"{prior_ins[:2].upper()}-{state[:2].upper()}-2025-{random.randint(1000,9999)}"
    prior_exp = date(2026, random.randint(1,6), random.randint(1,28))
    bi_opts   = ["25/50","50/100","100/300","15/30"]
    prior_bi  = random.choice(bi_opts)

    # ── policy ──
    agent_id, agent_name, agency_branch = random.choice(AGENTS)
    agency_name = f"Pinnacle Auto — {agency_branch}"
    underwriter = random.choice(UNDERWRITERS)
    pol_type  = random.choice(POLICY_TYPES)
    pol_term  = random.choice(POLICY_TERMS)
    eff_date  = date(2026, random.randint(3,9), random.randint(1,28))
    months    = 6 if pol_term == "6 Month" else 12
    exp_date  = date(eff_date.year + (1 if eff_date.month + months > 12 else 0),
                     (eff_date.month + months - 1) % 12 + 1, eff_date.day)
    renewal   = random.random() > 0.5
    paperless = random.random() > 0.3
    esig      = random.random() > 0.2

    # ── vehicle ──
    make      = random.choice(list(MAKES.keys()))
    models, vin_parts = MAKES[make]
    model     = random.choice(models)
    vp        = random.choice(vin_parts)
    v_vin     = vp[0] + "".join(random.choices("0123456789ABCDEFGHJKLMNPRSTUVWXYZ", k=17-len(vp[0])))
    trim      = random.choice(TRIMS)
    veh_yr    = random.randint(2015, 2024)
    body      = vp[2] if len(vp) > 2 else "Sedan"
    doors     = BODY_DOORS.get(body, 4)
    color     = random.choice(COLORS)
    cyls      = random.choice([4,6,8])
    disp      = {4:random.choice([1.5,1.6,2.0,2.5]),
                 6:random.choice([2.5,3.0,3.5,3.6]),
                 8:random.choice([5.0,6.2])}[cyls]
    fuel      = random.choices(FUEL, weights=[70,15,10,3,2])[0]
    trans     = random.choices(TRANS, weights=[75,15,10])[0]
    drive     = random.choice(DRIVE)
    mileage   = random.randint(5000, 120000)
    ann_miles = random.choice([8000,10000,12000,15000,18000,20000])
    prim_use  = random.choice(PRIMARY_USE)
    garage    = random.choice(GARAGE)
    purch_yr  = max(veh_yr, 2015)
    purch_date= date(purch_yr, random.randint(1,12), random.randint(1,28))
    purch_price= random.randint(18000, 72000)
    mkt_val   = int(purch_price * random.uniform(0.4, 0.9))
    conditions= ["Excellent","Good","Fair","Poor"]
    condition = random.choice(conditions)
    title_state= state
    has_lien  = random.random() > 0.35
    lien_cos  = ["Honda Financial Services","Toyota Financial Services","Ford Motor Credit",
                 "Chase Auto","Wells Fargo Auto","Capital One Auto","Credit Union Auto Loan"]
    lien_name = random.choice(lien_cos) if has_lien else blank()
    lien_addr = f"P.O. Box {random.randint(1000,9999)}, {random.choice(['Orlando, FL 32854','Dallas, TX 75201','Atlanta, GA 30301'])}" if has_lien else blank()
    loan_no   = f"LN-{veh_yr}-{random.randint(100000,999999)}" if has_lien else blank()

    salvage   = random.random() > 0.95
    anti_theft= random.random() > 0.3
    airbags   = True
    abs_brk   = True
    drl       = random.random() > 0.2
    backup_cam= random.random() > 0.3
    gps       = random.random() > 0.7
    parking   = random.random() > 0.5
    ldw       = random.random() > 0.5
    acc       = random.random() > 0.6
    custom    = random.random() > 0.85
    custom_val= f"{random.randint(500,5000)}" if custom else blank()

    # ── coverage ──
    bi_limits  = random.choice(["25/50","50/100","100/300","250/500"])
    prop_dmg   = random.choice(["50000","100000","250000","500000"])
    col_ded    = random.choice([250,500,1000])
    comp_ded   = random.choice([100,250,500])
    um_uim     = random.random() > 0.3
    pip        = state in ["Florida","Michigan","New York"] and random.random() > 0.3
    medpay     = random.random() > 0.5
    rental     = random.random() > 0.4
    roadside   = random.random() > 0.4
    gap        = has_lien and random.random() > 0.5
    rideshare  = random.random() > 0.8
    new_car    = veh_yr >= 2023 and random.random() > 0.6
    acc_forg   = random.random() > 0.5
    dim_ded    = random.random() > 0.7
    um_lim     = bi_limits if um_uim else blank()
    pip_lim    = f"{random.choice([5000,10000,25000])}" if pip else blank()
    medpay_lim = f"{random.choice([1000,2500,5000])}" if medpay else blank()
    rent_lim   = random.choice(["$30/day","$40/day","$50/day"]) if rental else blank()
    base_prem  = round(random.uniform(80, 350), 2)
    pay_freq   = random.choice(PAY_FREQ)
    multi_car  = random.random() > 0.6
    multi_pol  = random.random() > 0.5
    good_drv   = yrs_ins >= 5 and random.random() > 0.3
    good_std   = random.random() > 0.85
    def_drv    = random.random() > 0.8
    loyalty    = yrs_ins >= 3 and random.random() > 0.4
    military   = occ == "Military" or random.random() > 0.9
    affinity   = random.random() > 0.85

    # ── additional drivers ──
    n_drivers  = random.choices([0,1,2], weights=[40,40,20])[0]
    extra_drvs = []
    for _ in range(n_drivers):
        dg  = random.choice(["Male","Female"])
        df  = random.choice(FIRST_M if dg=="Male" else FIRST_F)
        ddob= rdate(1970, 2007)
        rel = random.choice(RELATIONSHIPS)
        extra_drvs.append({
            "first": df, "last": last, "dob": fdate(ddob), "gender": dg,
            "relation": rel,
            "dl": dl_no(), "dl_state": state,
            "dl_exp": fdate(rdate(2026,2030)),
            "acc": random.randint(0,2), "viol": random.randint(0,3),
            "sr22": yn(random.random() > 0.92),
            "excl": yn(random.random() > 0.95),
        })

    # ── history ──
    at_fault   = random.randint(0,2)
    not_fault  = random.randint(0,1)
    tot_acc    = at_fault + not_fault
    violations = random.randint(0,3)
    comp_claims= random.randint(0,2)
    tot_claims = at_fault + comp_claims
    dui        = random.random() > 0.93
    sr22       = dui or (at_fault >= 2)
    susp       = dui and random.random() > 0.5

    # ── claim ──
    has_claim  = tot_claims > 0
    claim_no   = f"CLM-{random.randint(2022,2025)}-{random.randint(10000,99999)}" if has_claim else None
    loss_date  = rdate(2022, 2025) if has_claim else None
    claim_type = random.choice(CLAIM_TYPES) if has_claim else None
    claim_stat = random.choice(["Closed","Open","Pending"]) if has_claim else None
    claim_amt  = round(random.uniform(500, 15000), 2) if has_claim else None
    claim_ded  = col_ded if claim_type == "Collision" else comp_ded if claim_type == "Comprehensive" else 500
    adjuster   = random.choice(ADJUSTERS) if has_claim else None
    settle     = round(max(0, claim_amt - claim_ded), 2) if has_claim else None
    res_days   = random.randint(5, 45)
    res_date   = (loss_date + timedelta(days=res_days)) if has_claim else None
    claim_desc = random.choice(CLAIM_DESCS) if has_claim else None
    police_rep = has_claim and random.random() > 0.6
    at_flt     = claim_type == "Collision" and at_fault > 0
    injury     = at_flt and random.random() > 0.7
    third_pty  = at_flt
    police_no  = f"RPT-{random.randint(2022,2025)}-{random.randint(10000,99999)}" if police_rep else blank()
    tp_name    = f"{random.choice(FIRST_M)} {random.choice(LAST)}" if third_pty else blank()
    tp_pol     = f"{random.choice(INSURERS[:3])[:2].upper()}-{random.randint(100000,999999)}" if third_pty else blank()

    # ── payment ──
    down_pmt   = round(base_prem * 2, 2)
    due_date   = eff_date
    last_pmt_d = blank()
    last_pmt_a = blank()
    autopay    = random.random() > 0.4
    pay_method = random.choice(PAY_METHOD)
    card_nm    = f"{first} {last[0]}. {last}" if "Card" in pay_method else blank()
    card_num   = card_no() if "Card" in pay_method else blank()
    card_exp   = f"{random.randint(1,12):02d}/{random.randint(26,30)}" if "Card" in pay_method else blank()
    card_cvv   = str(random.randint(100,999)) if "Card" in pay_method else blank()
    bank_name  = random.choice(["Chase","Wells Fargo","Bank of America","Citibank","US Bank"]) if pay_method == "Bank Transfer (ACH)" else blank()
    rout_no    = routing() if pay_method == "Bank Transfer (ACH)" else blank()
    acct_no    = account_no() if pay_method == "Bank Transfer (ACH)" else blank()
    acct_type  = random.choice(ACCT_TYPES) if pay_method == "Bank Transfer (ACH)" else blank()
    bill_addr  = addr1
    bill_city  = city
    bill_state = state
    bill_zip   = zip_code

    # ── format ───────────────────────────────────────────────────────────────
    lines = []
    def L(s=""): lines.append(s)

    # header
    L("=" * 80)
    L(f"  RECORD {idx} OF {total}")
    L("=" * 80)
    L()

    # TAB 1 — POLICY
    L("━" * 80)
    L("  TAB 1 — POLICY")
    L("━" * 80)
    L()
    L("[ Policy Information ]")
    L(f"Policy Number        : {policy_no(idx)}")
    L(f"Policy Status        : Active")
    L(f"Policy Type          : {pol_type}")
    L(f"Policy Term          : {pol_term}")
    L(f"Effective Date       : {fdate(eff_date)}")
    L(f"Expiration Date      : {fdate(exp_date)}")
    L()
    L("[ Agent / Agency ]")
    L(f"Agent ID             : {agent_id}")
    L(f"Agent Name           : {agent_name}")
    L(f"Agency Name          : {agency_name}")
    L(f"Underwriter          : {underwriter}")
    L()
    L("[ Flags ]")
    L(f"Renewal Policy       : {yn(renewal)}")
    L(f"Paperless / e-Delivery : {yn(paperless)}")
    L(f"E-Signature Obtained : {yn(esig)}")
    L()

    # TAB 2 — POLICYHOLDER
    L("━" * 80)
    L("  TAB 2 — POLICYHOLDER")
    L("━" * 80)
    L()
    L("[ Personal Information ]")
    L(f"First Name           : {first}")
    L(f"Middle Name          : {middle}")
    L(f"Last Name            : {last}")
    L(f"Suffix               : {suffix}")
    L(f"Date of Birth        : {fdate(dob)}")
    L(f"Gender               : {gender}")
    L(f"SSN                  : {ssn()}  [VERIFY]")
    L(f"Marital Status       : {marital}")
    L(f"Occupation           : {occ}")
    L(f"Education Level      : {edu}")
    L(f"Credit Score         : {credit}")
    L(f"Years Continuously Insured : {yrs_ins}")
    L()
    L("[ Contact Information ]")
    L(f"Email Address        : {em}")
    L(f"Home Phone           : {ph_home}")
    L(f"Cell Phone           : {ph_cell}")
    L(f"Work Phone           : {ph_work}")
    L()
    L("[ Address ]")
    L(f"Street Address 1     : {addr1}")
    L(f"Street Address 2     : {addr2}")
    L(f"City                 : {city}")
    L(f"State                : {state}")
    L(f"ZIP Code             : {zip_code}")
    L(f"County               : {county}")
    L(f"Country              : United States")
    L(f"Years at Address     : {yrs_addr}")
    L(f"Homeowner            : {yn(homeowner)}")
    L()
    L("[ Driver's License ]")
    L(f"DL Number            : {dl_num}")
    L(f"DL Issuing State     : {dl_state}")
    L(f"DL Expiration        : {fdate(dl_exp)}")
    L()
    L("[ Prior Insurance ]")
    L(f"Prior Insurer        : {prior_ins}")
    L(f"Prior Policy No.     : {prior_pol}")
    L(f"Prior Expiry Date    : {fdate(prior_exp)}")
    L(f"Prior Liability Limits : {prior_bi}")
    L()

    # TAB 3 — VEHICLE
    L("━" * 80)
    L("  TAB 3 — VEHICLE")
    L("━" * 80)
    L()
    L("[ Vehicle Identification ]")
    L(f"VIN                  : {v_vin}")
    L(f"Year                 : {veh_yr}")
    L(f"Make                 : {make}")
    L(f"Model                : {model}")
    L(f"Trim / Sub-model     : {trim}")
    L(f"Body Type            : {body}")
    L(f"Color                : {color}")
    L(f"Number of Doors      : {doors}")
    L()
    L("[ Engine & Drivetrain ]")
    L(f"Cylinders            : {cyls}")
    L(f"Displacement (L)     : {disp}")
    L(f"Fuel Type            : {fuel}")
    L(f"Transmission         : {trans}")
    L(f"Drive Type           : {drive}")
    L()
    L("[ Usage & Mileage ]")
    L(f"Current Mileage      : {mileage}")
    L(f"Annual Miles Est.    : {ann_miles}")
    L(f"Primary Use          : {prim_use}")
    L(f"Garaging Location    : {garage}")
    L()
    L("[ Ownership / Purchase ]")
    L(f"Purchase Date        : {fdate(purch_date)}")
    L(f"Purchase Price ($)   : {purch_price}")
    L(f"Current Market Value ($) : {mkt_val}")
    L(f"Vehicle Condition    : {condition}")
    L(f"Title State          : {title_state}")
    L()
    L("[ Lien / Financing ]")
    L(f"Lienholder/Lender    : {lien_name}")
    L(f"Lienholder Address   : {lien_addr}")
    L(f"Loan / Lease No.     : {loan_no}")
    L()
    L("[ Safety & Features ]")
    L(f"Salvage Title        : {yn(salvage)}")
    L(f"Anti-Theft Device    : {yn(anti_theft)}")
    L(f"Airbags              : {yn(airbags)}")
    L(f"ABS Brakes           : {yn(abs_brk)}")
    L(f"Daytime Running Lights : {yn(drl)}")
    L(f"Backup Camera        : {yn(backup_cam)}")
    L(f"GPS Tracking         : {yn(gps)}")
    L(f"Parking Sensors      : {yn(parking)}")
    L(f"Lane Departure Warning : {yn(ldw)}")
    L(f"Adaptive Cruise Control : {yn(acc)}")
    L(f"Custom Equipment / Mods : {yn(custom)}")
    L(f"Custom Equipment Value ($) : {custom_val}")
    L()

    # TAB 4 — COVERAGE
    L("━" * 80)
    L("  TAB 4 — COVERAGE")
    L("━" * 80)
    L()
    L("[ Liability Limits ]")
    L(f"Bodily Injury (k$/k$)    : {bi_limits}")
    L(f"Property Damage ($)      : {prop_dmg}")
    L()
    L("[ Collision & Comprehensive ]")
    L(f"Collision Deductible     : {col_ded}")
    L(f"Comprehensive Deductible : {comp_ded}")
    L()
    L("[ Additional Coverages ]")
    L(f"Uninsured/Underinsured Motorist : {yn(um_uim)}")
    L(f"Personal Injury Protection (PIP) : {yn(pip)}")
    L(f"Medical Payments         : {yn(medpay)}")
    L(f"Rental Reimbursement     : {yn(rental)}")
    L(f"Roadside Assistance      : {yn(roadside)}")
    L(f"GAP Insurance            : {yn(gap)}")
    L(f"Rideshare Coverage       : {yn(rideshare)}")
    L(f"New Car Replacement      : {yn(new_car)}")
    L(f"Accident Forgiveness     : {yn(acc_forg)}")
    L(f"Diminishing Deductible   : {yn(dim_ded)}")
    L()
    L("[ Optional Limits ]")
    L(f"UM/UIM Limit             : {um_lim}")
    L(f"PIP Limit ($)            : {pip_lim}")
    L(f"MedPay Limit ($)         : {medpay_lim}")
    L(f"Rental Limit             : {rent_lim}")
    L()
    L("[ Discounts Applied ]")
    L(f"Multi-Car                : {yn(multi_car)}")
    L(f"Multi-Policy / Bundle    : {yn(multi_pol)}")
    L(f"Good Driver (5+ yr clean): {yn(good_drv)}")
    L(f"Good Student             : {yn(good_std)}")
    L(f"Defensive Driving Course : {yn(def_drv)}")
    L(f"Loyalty Discount         : {yn(loyalty)}")
    L(f"Military                 : {yn(military)}")
    L(f"Affinity Group           : {yn(affinity)}")
    L()
    L("[ Premium Summary ]")
    L(f"Total Premium ($)        : {base_prem}")
    L(f"Payment Frequency        : {pay_freq}")
    L()

    # TAB 5 — DRIVERS
    L("━" * 80)
    L("  TAB 5 — DRIVERS")
    L("━" * 80)
    L()
    if not extra_drvs:
        L("[ No Additional Drivers ]")
        L("Additional Drivers   : NONE")
    else:
        for di, d in enumerate(extra_drvs, start=2):
            L(f"[ Driver {di} ]")
            L(f"First Name           : {d['first']}")
            L(f"Last Name            : {d['last']}")
            L(f"Date of Birth        : {d['dob']}")
            L(f"Gender               : {d['gender']}")
            L(f"Relationship         : {d['relation']}")
            L(f"DL Number            : {d['dl']}")
            L(f"DL Issuing State     : {d['dl_state']}")
            L(f"DL Expiration        : {d['dl_exp']}")
            L(f"Accidents (3 yr)     : {d['acc']}")
            L(f"Violations (3 yr)    : {d['viol']}")
            L(f"SR-22 Required       : {d['sr22']}")
            L(f"Excluded Driver      : {d['excl']}")
            L()
    L()

    # TAB 6 — HISTORY
    L("━" * 80)
    L("  TAB 6 — HISTORY")
    L("━" * 80)
    L()
    L("[ Driving Record — Primary Driver (3-Year Look-Back) ]")
    L(f"At-Fault Accidents       : {at_fault}")
    L(f"Not-At-Fault Accidents   : {not_fault}")
    L(f"Total Accidents          : {tot_acc}")
    L(f"Moving Violations        : {violations}")
    L(f"Comprehensive Claims     : {comp_claims}")
    L(f"Total Claims Filed       : {tot_claims}")
    L()
    L("[ Special Flags ]")
    L(f"DUI / DWI on Record      : {yn(dui)}")
    L(f"SR-22 / FR-44 Filed      : {yn(sr22)}")
    L(f"License Suspended or Revoked : {yn(susp)}")
    L()

    # TAB 7 — CLAIMS
    L("━" * 80)
    L("  TAB 7 — CLAIMS")
    L("━" * 80)
    L()
    if not has_claim:
        L("[ No Claims on Record ]")
        L("Claim Number             : (none)")
    else:
        L("[ Claim Details ]")
        L(f"Claim Number             : {claim_no}")
        L(f"Date of Loss             : {fdate(loss_date)}")
        L(f"Claim Type               : {claim_type}")
        L(f"Claim Status             : {claim_stat}")
        L(f"Claim Amount ($)         : {claim_amt:.2f}")
        L(f"Deductible ($)           : {claim_ded:.2f}")
        L(f"Adjuster Name            : {adjuster}")
        L(f"Settlement Amount ($)    : {settle:.2f}")
        L(f"Resolution Date          : {fdate(res_date)}")
        # wrap description at 70 chars
        desc_words = claim_desc.split()
        line1, line2 = [], []
        cur = line1
        total_chars = 0
        for w in desc_words:
            if total_chars + len(w) + 1 > 58 and cur is line1:
                cur = line2
                total_chars = 0
            cur.append(w)
            total_chars += len(w) + 1
        desc1 = " ".join(line1)
        desc2 = "                           " + " ".join(line2) if line2 else ""
        L(f"Claim Description        : {desc1}")
        if desc2.strip():
            L(desc2)
        L()
        L("[ Circumstances ]")
        L(f"Police Report Filed      : {yn(police_rep)}")
        L(f"Policyholder At Fault    : {yn(at_flt)}")
        L(f"Injury Involved          : {yn(injury)}")
        L(f"Third Party Involved     : {yn(third_pty)}")
        L()
        L("[ Third Party Information ]")
        L(f"Police Report No.        : {police_no}")
        L(f"Third Party Name         : {tp_name}")
        L(f"Third Party Policy       : {tp_pol}")
    L()

    # TAB 8 — PAYMENT
    L("━" * 80)
    L("  TAB 8 — PAYMENT")
    L("━" * 80)
    L()
    L("[ Billing Summary ]")
    L(f"Total Premium ($)        : {base_prem}")
    L(f"Down Payment ($)         : {down_pmt}  (first 2 months collected upfront)")
    L(f"Balance Due ($)          : {blank()}")
    L(f"Payment Due Date         : {fdate(due_date)}")
    L(f"Payment Frequency        : {pay_freq}")
    L(f"Last Payment Date        : {last_pmt_d}")
    L(f"Last Payment Amount ($)  : {last_pmt_a}")
    L(f"Auto-Pay Enrolled        : {yn(autopay)}")
    L()
    L("[ Payment Method ]")
    L(f"Method                   : {pay_method}")
    L()
    if "Card" in pay_method:
        L("[ Credit / Debit Card ]")
        L(f"Cardholder Name          : {card_nm}")
        L(f"Card Number              : {card_num}  [VERIFY before saving]")
        L(f"Expiration (MM/YY)       : {card_exp}")
        L(f"CVV                      : {card_cvv}  [VERIFY — do not store]")
    elif pay_method == "Bank Transfer (ACH)":
        L("[ Bank Account (ACH) ]")
        L(f"Bank Name                : {bank_name}")
        L(f"Routing Number           : {rout_no}")
        L(f"Account Number           : {acct_no}")
        L(f"Account Type             : {acct_type}")
    else:
        L("[ Payment Details ]")
        L(f"Method                   : {pay_method}")
    L()
    L("[ Billing Address ]")
    L(f"Street Address           : {bill_addr}")
    L(f"City                     : {bill_city}")
    L(f"State                    : {bill_state}")
    L(f"ZIP Code                 : {bill_zip}")
    L()

    return "\n".join(lines)


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    import os, sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data_entry_tasks")
    out_path = os.path.join(out_dir, f"data_entry_intake_{n}.txt")

    header = f"""{"=" * 80}
  PINNACLE AUTO INSURANCE — DATA ENTRY INTAKE PACKET
  Internal Use Only | Prepared by: Operations Manager
  Packet Date: 2026-04-01 | Batch ID: PAI-20260401-{n:03d}
{"=" * 80}

INSTRUCTIONS FOR DATA ENTRY STAFF
-----------------------------------
Enter each record below into the Car Insurance Data Entry Form in the order
listed. Fields are organized tab-by-tab and section-by-section to match the
exact sequence of the form. Work top-to-bottom within each tab, then move to
the next tab.

Flag any field marked [VERIFY] with your supervisor before submitting.
All dates use MM/DD/YYYY format unless noted.

"""
    records = [build_record(i, n) for i in range(1, n + 1)]
    content = header + "\n\n".join(records) + "\n"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"Wrote {n} records -> {out_path}")
    print(f"File size: {os.path.getsize(out_path):,} bytes")

if __name__ == "__main__":
    main()
