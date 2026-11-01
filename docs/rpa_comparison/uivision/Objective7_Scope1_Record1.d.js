"use desktop-app";
// Objective 7, Scope 1: enter record 1 of the intake packet into the car-insurance form.
// Built once on the NORMAL form (car_insurance_form_wx.py) and NOT edited
// before the modified-form runs. Keyboard-driven: UI.Vision cannot read the form's
// controls, and image matching would fail on the modified form's colours.
uiv.desktop.mouse.click(607, 212);   // first field (fixed screen position)
uiv.sleep(300);
// tab 1 of 8
uiv.desktop.keyboard.type("PAI-2026-00441${KEY_TAB}");   // policy_number
uiv.desktop.keyboard.type("${KEY_TAB}");   // policy_status
uiv.desktop.keyboard.type("${KEY_TAB}");   // policy_type
uiv.desktop.keyboard.type("${KEY_TAB}");   // policy_term
uiv.desktop.keyboard.type("04/01/2026${KEY_TAB}");   // effective_date
uiv.desktop.keyboard.type("10/01/2026${KEY_TAB}");   // expiration_date
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_agent_/_agency
uiv.desktop.keyboard.type("AGT-0092${KEY_TAB}");   // agent_id
uiv.desktop.keyboard.type("Sandra Whitfield${KEY_TAB}");   // agent_name
uiv.desktop.keyboard.type("Pinnacle Auto — Riverside Branch${KEY_TAB}");   // agency_name
uiv.desktop.keyboard.type("Marcus D. Chen${KEY_TAB}");   // underwriter
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_flags
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // renewal_flag
uiv.desktop.keyboard.type("${KEY_TAB}");   // paperless
uiv.desktop.keyboard.type("${KEY_SPACE}");   // esign
uiv.desktop.keyboard.type("${KEY_CTRL+KEY_TAB}");   // next tab
uiv.sleep(400);
// tab 2 of 8
uiv.desktop.keyboard.type("James${KEY_TAB}");   // ph_first
uiv.desktop.keyboard.type("Arthur${KEY_TAB}");   // ph_middle
uiv.desktop.keyboard.type("Delgado${KEY_TAB}");   // ph_last
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_suffix
uiv.desktop.keyboard.type("07/14/1978${KEY_TAB}");   // ph_dob
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_gender
uiv.desktop.keyboard.type("512-88-4401${KEY_TAB}");   // ph_ssn
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // ph_marital
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_occupation
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_education
uiv.desktop.keyboard.type("724${KEY_TAB}");   // ph_credit_score
uiv.desktop.keyboard.type("9${KEY_TAB}");   // ph_years_insured
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_contact_information
uiv.desktop.keyboard.type("jdelgado78@gmail.com${KEY_TAB}");   // ph_email
uiv.desktop.keyboard.type("(951) 440-2281${KEY_TAB}");   // ph_phone_home
uiv.desktop.keyboard.type("(951) 882-5573${KEY_TAB}");   // ph_phone_cell
uiv.desktop.keyboard.type("(951) 300-0044${KEY_TAB}");   // ph_phone_work
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_address
uiv.desktop.keyboard.type("4418 Creekstone Drive${KEY_TAB}");   // ph_addr1
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_addr2
uiv.desktop.keyboard.type("Riverside${KEY_TAB}");   // ph_city
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_state
uiv.desktop.keyboard.type("92506${KEY_TAB}");   // ph_zip
uiv.desktop.keyboard.type("Riverside${KEY_TAB}");   // ph_county
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_country
uiv.desktop.keyboard.type("6${KEY_TAB}");   // ph_years_at_addr
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // ph_homeowner
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_driver's_license
uiv.desktop.keyboard.type("D7734821${KEY_TAB}");   // ph_drivers_license
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_dl_state
uiv.desktop.keyboard.type("07/14/2028${KEY_TAB}");   // ph_dl_exp
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_prior_insurance
uiv.desktop.keyboard.type("State Farm${KEY_TAB}");   // ph_prior_insurer
uiv.desktop.keyboard.type("${KEY_TAB}");   // ph_prior_policy_no
uiv.desktop.keyboard.type("03/31/2026${KEY_TAB}");   // ph_prior_expiry
uiv.desktop.keyboard.type("50/100");   // ph_prior_liability
uiv.desktop.keyboard.type("${KEY_CTRL+KEY_TAB}");   // next tab
uiv.sleep(400);
// tab 3 of 8
uiv.desktop.keyboard.type("1HGCV1F30LA081234${KEY_TAB}");   // v_vin
uiv.desktop.keyboard.type("2020${KEY_TAB}");   // v_year
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // v_make
uiv.desktop.keyboard.type("Accord${KEY_TAB}");   // v_model
uiv.desktop.keyboard.type("Sport 2.0T${KEY_TAB}");   // v_trim
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_body
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // v_color
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_doors
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_engine_&_drivetrain
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // v_cylinders
uiv.desktop.keyboard.type("2.0${KEY_TAB}");   // v_displacement
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_fuel
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_transmission
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_drive
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_usage_&_mileage
uiv.desktop.keyboard.type("38450${KEY_TAB}");   // v_mileage
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_annual_miles
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // v_usage
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_garaging
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_ownership_/_purchase
uiv.desktop.keyboard.type("02/15/2020${KEY_TAB}");   // v_purchase_date
uiv.desktop.keyboard.type("29800${KEY_TAB}");   // v_purchase_price
uiv.desktop.keyboard.type("22400${KEY_TAB}");   // v_market_value
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_condition
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_title_state
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_lien_/_financing
uiv.desktop.keyboard.type("Honda Financial Services${KEY_TAB}");   // v_lienholder
uiv.desktop.keyboard.type("P.O. Box 7829, Orlando, FL 32854${KEY_TAB}");   // v_lienholder_addr
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_loan_number
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_safety_&_features
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_salvage
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // v_anti_theft
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_airbags
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_abs
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_daytime_lights
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // v_backup_camera
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_gps
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_parking_sensors
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // v_lane_assist
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_adaptive_cruise
uiv.desktop.keyboard.type("${KEY_TAB}");   // v_custom_equipment
uiv.desktop.keyboard.type("${KEY_CTRL+KEY_TAB}");   // next tab
uiv.sleep(400);
// tab 4 of 8
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_bodily_limit
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_property
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_collision_&_comprehensive
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_collision_ded
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_comp_ded
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_additional_coverages
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_um_uim
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_pip
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // cov_medpay
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_rental
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // cov_roadside
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_gap
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_rideshare
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_new_car
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // cov_acc_forgive
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_disappear_ded
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_optional_limits
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_um_limit
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_pip_limit
uiv.desktop.keyboard.type("5000${KEY_TAB}");   // cov_medpay_limit
uiv.desktop.keyboard.type("${KEY_TAB}");   // cov_rental_limit
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_discounts_applied
uiv.desktop.keyboard.type("${KEY_TAB}");   // disc_multi_car
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // disc_multi_policy
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // disc_good_driver
uiv.desktop.keyboard.type("${KEY_TAB}");   // disc_good_student
uiv.desktop.keyboard.type("${KEY_TAB}");   // disc_defensive_drv
uiv.desktop.keyboard.type("${KEY_SPACE}${KEY_TAB}");   // disc_loyalty
uiv.desktop.keyboard.type("${KEY_TAB}");   // disc_military
uiv.desktop.keyboard.type("${KEY_TAB}");   // disc_affinity
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_premium_summary
uiv.desktop.keyboard.type("187.42${KEY_TAB}");   // cov_premium_total
uiv.desktop.keyboard.type("${KEY_CTRL+KEY_TAB}");   // next tab
uiv.sleep(400);
// tab 5 of 8
uiv.desktop.keyboard.type("Maria${KEY_TAB}");   // d2_first
uiv.desktop.keyboard.type("Delgado${KEY_TAB}");   // d2_last
uiv.desktop.keyboard.type("03/22/1980${KEY_TAB}");   // d2_dob
uiv.desktop.keyboard.type("${KEY_TAB}");   // d2_gender
uiv.desktop.keyboard.type("${KEY_TAB}");   // d2_relation
uiv.desktop.keyboard.type("F8821047${KEY_TAB}");   // d2_dl
uiv.desktop.keyboard.type("${KEY_TAB}");   // d2_dl_state
uiv.desktop.keyboard.type("03/22/2027${KEY_TAB}");   // d2_dl_exp
uiv.desktop.keyboard.type("${KEY_TAB}");   // d2_accidents
uiv.desktop.keyboard.type("${KEY_TAB}");   // d2_violations
uiv.desktop.keyboard.type("${KEY_TAB}");   // d2_sr22
uiv.desktop.keyboard.type("${KEY_TAB}");   // d2_excluded
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_driver_3
uiv.desktop.keyboard.type("Tyler${KEY_TAB}");   // d3_first
uiv.desktop.keyboard.type("Delgado${KEY_TAB}");   // d3_last
uiv.desktop.keyboard.type("09/05/2006${KEY_TAB}");   // d3_dob
uiv.desktop.keyboard.type("${KEY_TAB}");   // d3_gender
uiv.desktop.keyboard.type("${KEY_TAB}");   // d3_relation
uiv.desktop.keyboard.type("D0012938${KEY_TAB}");   // d3_dl
uiv.desktop.keyboard.type("${KEY_TAB}");   // d3_dl_state
uiv.desktop.keyboard.type("09/05/2027${KEY_TAB}");   // d3_dl_exp
uiv.desktop.keyboard.type("${KEY_TAB}");   // d3_accidents
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // d3_violations
uiv.desktop.keyboard.type("${KEY_TAB}");   // d3_sr22
uiv.desktop.keyboard.type("${KEY_CTRL+KEY_TAB}");   // next tab
uiv.sleep(400);
// tab 6 of 8
uiv.desktop.keyboard.type("${KEY_TAB}");   // hist_at_fault
uiv.desktop.keyboard.type("${KEY_TAB}");   // hist_not_at_fault
uiv.desktop.keyboard.type("${KEY_TAB}");   // hist_accidents_3yr
uiv.desktop.keyboard.type("${KEY_TAB}");   // hist_violations_3yr
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // hist_comp_claims
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // hist_claims_3yr
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_special_flags
uiv.desktop.keyboard.type("${KEY_TAB}");   // hist_dui
uiv.desktop.keyboard.type("${KEY_TAB}");   // hist_sr22
uiv.desktop.keyboard.type("${KEY_CTRL+KEY_TAB}");   // next tab
uiv.sleep(400);
// tab 7 of 8
uiv.desktop.keyboard.type("CLM-2024-88341${KEY_TAB}");   // claim_number
uiv.desktop.keyboard.type("08/03/2024${KEY_TAB}");   // claim_date
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // claim_type
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_DOWN}${KEY_DOWN}${KEY_TAB}");   // claim_status
uiv.desktop.keyboard.type("1240.00${KEY_TAB}");   // claim_amount
uiv.desktop.keyboard.type("250.00${KEY_TAB}");   // claim_deductible
uiv.desktop.keyboard.type("Renee Harmon${KEY_TAB}");   // claim_adjuster
uiv.desktop.keyboard.type("990.00${KEY_TAB}");   // claim_settlement
uiv.desktop.keyboard.type("08/19/2024${KEY_TAB}");   // claim_resolve_date
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_claim_description
uiv.desktop.keyboard.type("Windshield cracked by falling tree branch during${KEY_TAB}");   // claim_desc
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_circumstances
uiv.desktop.keyboard.type("${KEY_TAB}");   // claim_police_rpt
uiv.desktop.keyboard.type("${KEY_TAB}");   // claim_at_fault
uiv.desktop.keyboard.type("${KEY_TAB}");   // claim_injury
uiv.desktop.keyboard.type("${KEY_TAB}");   // claim_third_party
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_third_party_information
uiv.desktop.keyboard.type("${KEY_TAB}");   // claim_report_no
uiv.desktop.keyboard.type("${KEY_TAB}");   // claim_tp_name
uiv.desktop.keyboard.type("${KEY_CTRL+KEY_TAB}");   // next tab
uiv.sleep(400);
// tab 8 of 8
uiv.desktop.keyboard.type("187.42${KEY_TAB}");   // pay_amount
uiv.desktop.keyboard.type("374.84  (first 2 months collected upfront)${KEY_TAB}");   // pay_down_payment
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_balance_due
uiv.desktop.keyboard.type("04/01/2026${KEY_TAB}");   // pay_due_date
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_frequency
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_last_paid_date
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_last_paid_amt
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_auto_pay
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_payment_method
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_method
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_credit_/_debit_card
uiv.desktop.keyboard.type("James A. Delgado${KEY_TAB}");   // pay_cc_name
uiv.desktop.keyboard.type("4532 8812 0044 7761${KEY_TAB}");   // pay_cc_number
uiv.desktop.keyboard.type("09/28${KEY_TAB}");   // pay_cc_exp
uiv.desktop.keyboard.type("341${KEY_TAB}");   // pay_cc_cvv
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_bank_account_(ach)
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_bank_name
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_routing
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_account
uiv.desktop.keyboard.type("${KEY_HOME}${KEY_TAB}");   // pay_account_type
uiv.desktop.keyboard.type("${KEY_TAB}");   // section_billing_address
uiv.desktop.keyboard.type("4418 Creekstone Drive${KEY_TAB}");   // pay_billing_addr1
uiv.desktop.keyboard.type("Riverside${KEY_TAB}");   // pay_billing_city
uiv.desktop.keyboard.type("${KEY_TAB}");   // pay_billing_state
uiv.desktop.keyboard.type("92506");   // pay_billing_zip
uiv.desktop.mouse.click(652, 835);   // Submit (fixed screen position)
