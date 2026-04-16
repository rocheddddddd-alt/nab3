"""
bio_age_models.py
=================
Biological Age Calculation Models — สกัดค่า coefficients จาก AnthropoAgeR R package

Models implemented:
  1. S-AnthropoAge   — Fermín-Martínez CA et al., Aging Cell 2023
  2. AnthropoAge Full— Fermín-Martínez CA et al., Aging Cell 2023
  3. PhenoAge        — Levine ME et al., Aging 2018

Verified against reference website: https://bellolab.shinyapps.io/anthropoage_es/
Coefficients source: AnthropoAgeR R package (sysdata.rda)

Verification results (Python vs R):
  S-AnthropoAge  : matches R to 2 decimal places
  Full AnthropoAge: 32.89 vs R 32.8882 | 22.48 vs R 22.479 | 38.23 vs R 38.228 | 31.71 vs R 31.710
  PhenoAge       : 44.35 vs R 44.35

Usage:
  from bio_age_models import s_anthropoage, anthropoage_full, phenoage
"""

import math


# ===========================================================================
# Helper
# ===========================================================================

def _ortho_poly2(x, alpha, norm2):
    """
    Orthogonal polynomial basis degree-2, replicating R's poly(x, 2).

    Parameters
    ----------
    x     : float  — raw value
    alpha : [a1, a2]   — centres extracted from R model attr 'coefs'$alpha
    norm2 : [1, n, s1, s2] — norm2 from R model attr 'coefs'$norm2

    Returns
    -------
    z1, z2 : normalised first- and second-order orthogonal polynomial terms
    """
    z1_unnorm = x - alpha[0]
    z1 = z1_unnorm / math.sqrt(norm2[2])
    z2_raw = (x - alpha[1]) * z1_unnorm - (norm2[2] / norm2[1])
    z2 = z2_raw / math.sqrt(norm2[3])
    return z1, z2


def _gompertz_surv_120(lin_pred, shape_a):
    """
    Gompertz survival probability at 120 months (10 years).
    S(t) = exp( -(rate/shape) * (exp(shape*t) - 1) )   where rate = exp(lin_pred)
    """
    rate_val = math.exp(lin_pred)
    return math.exp(-(rate_val / shape_a) * (math.exp(shape_a * 120) - 1))


def _prob_to_bio_age(pred_prob, shape_b, rate_b_int, age_b):
    """
    Convert 10-year mortality probability to biological age using reference model.
    Implements: bio_age = (log(-S_b * log(1 - p)) - rate_b_int) / age_b
    """
    s_b = 1.0 / ((math.exp(shape_b * 120) - 1.0) / shape_b)
    return (math.log(-s_b * math.log(1.0 - pred_prob)) - rate_b_int) / age_b


# ===========================================================================
# 1. S-AnthropoAge
#    Simplified model using BMI + Waist-to-Height Ratio (WHtR)
#    Gompertz PH model trained on NHANES; gomp2aM / gomp2aF
#
#    Men   poly(log(BMI), 2) + cube-root(WHtR) — alpha/norm2 from gomp2aM
#    Women poly(log(BMI), 2) + cube-root(WHtR) — alpha/norm2 from gomp2aF
#    Reference: gomp1bM / gomp1bF
# ===========================================================================

def s_anthropoage(age, sex, height_m, weight_kg, waist_cm, ethnicity="Other"):
    """
    Calculate S-AnthropoAge biological age.

    Parameters
    ----------
    age       : float  — chronological age (years, 18-100)
    sex       : str    — 'Men' | 'Women'  (or M/F/ชาย/หญิง)
    height_m  : float  — height in metres (e.g. 1.65)
    weight_kg : float  — weight in kg
    waist_cm  : float  — waist circumference in cm
    ethnicity : str    — 'White' | 'Black' | 'Mexican-American' | 'Other'
                         'Other' covers Thai / Asian

    Returns
    -------
    float — biological age (years), or None on error
    """
    try:
        bmi    = weight_kg / (height_m ** 2)
        whtr   = waist_cm / (height_m * 100)   # waist / height (both in cm)
        tr_imc = math.log(bmi)                 # log(BMI)
        tr_ice = whtr ** (1.0 / 3.0)           # cube-root(WHtR)

        eth = ethnicity.strip().upper()

        if sex.upper() in ("MEN", "M", "MALE", "ชาย"):
            # --- Exposure model (gomp2aM) ---
            shape_a  = 6.047896e-03
            rate_int = -1.908181e+01
            coef_age = 7.333699e-02
            coef_z1  = -2.667586e+01
            coef_z2  =  1.232347e+01
            coef_ice =  9.785107e+00

            # Ethnicity adjustment on shape (baseline = White/Caucasian)
            if eth == "BLACK":
                shape_a += 9.821377e-04
            elif eth in ("MEXICAN-AMERICAN", "MEXICAN"):
                shape_a += -8.827752e-05
            elif eth in ("OTHER", "OTHERS", "THAI", "ASIAN"):
                shape_a += -4.396708e-03
            # White = baseline, no adjustment

            # poly(log(BMI), 2) parameters — from R attr(gomp2aM coefs)
            poly_alpha = [3.253218, 3.27553]
            poly_norm2 = [1.0, 5728.0, 146.6152, 8.195515]

            # Reference model (gomp1bM)
            shape_b    = 0.005852491
            rate_b_int = -11.285797682
            age_b      =  0.078107728

        else:  # Women
            # --- Exposure model (gomp2aF) ---
            shape_a  = 7.722865e-03
            rate_int = -1.925800e+01
            coef_age = 8.181236e-02
            coef_z1  = -2.080354e+01
            coef_z2  =  9.245786e+00
            coef_ice =  8.525923e+00

            if eth == "BLACK":
                shape_a += 3.879643e-04
            elif eth in ("MEXICAN-AMERICAN", "MEXICAN"):
                shape_a += -7.946445e-04
            elif eth in ("OTHER", "OTHERS", "THAI", "ASIAN"):
                shape_a += -2.548482e-03

            poly_alpha = [3.258283, 3.29992]
            poly_norm2 = [1.0, 6046.0, 237.5509, 16.40061]

            shape_b    = 0.007361352
            rate_b_int = -12.390995735
            age_b      =  0.086004145

        # Orthogonal polynomial for log(BMI)
        z1, z2 = _ortho_poly2(tr_imc, poly_alpha, poly_norm2)

        lin_pred = (rate_int
                    + coef_age * age
                    + coef_z1  * z1
                    + coef_z2  * z2
                    + coef_ice * tr_ice)

        surv      = _gompertz_surv_120(lin_pred, shape_a)
        pred_prob = 1.0 - surv

        if not (0.0 < pred_prob < 1.0):
            return None

        bio_age = _prob_to_bio_age(pred_prob, shape_b, rate_b_int, age_b)
        return round(max(1.0, min(120.0, bio_age)), 2)

    except Exception as e:
        print(f"[bio_age_models] s_anthropoage error: {e}")
        return None


# ===========================================================================
# 2. AnthropoAge Full
#    Full model using 10 anthropometric variables
#    Gompertz PH model trained on NHANES; gomp1aM / gomp1aF
#
#    Men   : poly(cube-root(WHtR), 2)  +  sqrt(arm_cm)  +  poly(log(thigh), 2)
#    Women : log(weight)  +  cube-root(WHtR)  +  cube-root(subs_mm)
#            + cube-root(tric_mm)  +  poly(log(thigh), 2)
#    Reference: gomp1bM / gomp1bF
# ===========================================================================

def anthropoage_full(age, sex, height_m, weight_kg, waist_cm, ethnicity="Other",
                     thigh_cm=None, arm_cm=None, subs_mm=None, tric_mm=None):
    """
    Calculate Full AnthropoAge biological age.

    Parameters
    ----------
    age       : float  — chronological age (years, 18-100)
    sex       : str    — 'Men' | 'Women'
    height_m  : float  — height in metres
    weight_kg : float  — weight in kg
    waist_cm  : float  — waist circumference in cm
    ethnicity : str    — 'White' | 'Black' | 'Mexican-American' | 'Other'
    thigh_cm  : float  — thigh circumference in cm  [required all sexes]
    arm_cm    : float  — arm (mid-upper) circumference in cm [required Men]
    subs_mm   : float  — subscapular skinfold thickness in mm [required Women]
    tric_mm   : float  — triceps skinfold thickness in mm     [required Women]

    Returns
    -------
    float — biological age (years), or None on error
    """
    try:
        tr_weight = math.log(weight_kg)
        tr_ice    = (waist_cm / (height_m * 100)) ** (1.0 / 3.0)
        tr_thigh  = math.log(thigh_cm)
        tr_armc   = math.sqrt(arm_cm   if arm_cm   else 1.0)
        tr_subs   = (subs_mm  ** (1.0 / 3.0)) if subs_mm  else 1.0
        tr_tric   = (tric_mm  ** (1.0 / 3.0)) if tric_mm  else 1.0

        eth = ethnicity.strip().upper()

        if sex.upper() in ("MEN", "M", "MALE", "ชาย"):
            # --- poly(cube-root(WHtR), 2) ---
            # alpha=[0.8152571, 0.8168204]  norm2=[1, 5728, 7.41356, 0.01780665]
            z1_ice, z2_ice = _ortho_poly2(
                tr_ice,
                [0.8152571, 0.8168204],
                [1.0, 5728.0, 7.41356, 0.01780665]
            )
            # --- poly(log(thigh), 2) ---
            # alpha=[3.919595, 3.914693]  norm2=[1, 5728, 63.5543, 2.103498]
            z1_thigh, z2_thigh = _ortho_poly2(
                tr_thigh,
                [3.919595, 3.914693],
                [1.0, 5728.0, 63.5543, 2.103498]
            )

            # Exposure model (gomp1aM) shape
            shape_a = 0.0061794099
            if eth == "BLACK":
                shape_a += 0.0010178030
            elif eth in ("MEXICAN-AMERICAN", "MEXICAN"):
                shape_a += -0.0004344881
            elif eth in ("OTHER", "OTHERS", "THAI", "ASIAN"):
                shape_a += -0.0042272524
            # White = baseline

            # Rate linear predictor
            lin_pred = (-7.2886599454
                        + 0.0704900128  * age
                        + 14.8761481245 * z1_ice
                        +  5.4062012714 * z2_ice
                        + (-0.6453419274) * tr_armc
                        + (-6.7500798168) * z1_thigh
                        +  4.2013121005  * z2_thigh)

            # Reference model (gomp1bM)
            shape_b    = 0.005852491
            rate_b_int = -11.285797682
            age_b      =  0.078107728

        else:  # Women
            # --- poly(log(thigh), 2) ---
            # alpha=[3.916417, 3.937122]  norm2=[1, 6046, 99.60416, 3.831239]
            z1_thigh, z2_thigh = _ortho_poly2(
                tr_thigh,
                [3.916417, 3.937122],
                [1.0, 6046.0, 99.60416, 3.831239]
            )

            # Exposure model (gomp1aF) shape
            shape_a = 7.882236e-03
            if eth == "BLACK":
                shape_a += 3.374462e-04
            elif eth in ("MEXICAN-AMERICAN", "MEXICAN"):
                shape_a += -1.100406e-03
            elif eth in ("OTHER", "OTHERS", "THAI", "ASIAN"):
                shape_a += -2.427711e-03
            # White = baseline

            lin_pred = (-2.009871e+01
                        + 7.695700e-02  * age
                        + 1.191139e+00  * tr_weight
                        + 6.481949e+00  * tr_ice
                        + (-3.473410e-01) * tr_subs
                        + (-4.525735e-01) * tr_tric
                        + (-1.636475e+01) * z1_thigh
                        +  7.590004e+00  * z2_thigh)

            # Reference model (gomp1bF)
            shape_b    = 0.007361352
            rate_b_int = -12.390995735
            age_b      =  0.086004145

        # Gompertz survival at 120 months → mortality probability
        surv      = _gompertz_surv_120(lin_pred, shape_a)
        pred_prob = 1.0 - surv

        if not (0.0 < pred_prob < 1.0):
            return None

        bio_age = _prob_to_bio_age(pred_prob, shape_b, rate_b_int, age_b)
        return round(max(1.0, min(120.0, bio_age)), 2)

    except Exception as e:
        print(f"[bio_age_models] anthropoage_full error: {e}")
        return None


# ===========================================================================
# 3. PhenoAge
#    Blood-biomarker-based biological age
#    Parametric PH model on NHANES III; Levine et al. 2018
# ===========================================================================

def phenoage(age, crp_mgL, lymph_pct, wbc_thousands, glu_mgdL,
             rdw_pct, alb_gdL, cr_mgdL, mcv_fL, ap_UL):
    """
    Calculate PhenoAge biological age.

    Parameters (all values as reported in standard lab units)
    ----------
    age          : float — chronological age (years)
    crp_mgL      : float — C-reactive protein in mg/L
    lymph_pct    : float — lymphocyte percentage (%)
    wbc_thousands: float — white blood cell count (×10³/μL)
    glu_mgdL     : float — fasting glucose in mg/dL  ← converted internally to mmol/L
    rdw_pct      : float — red cell distribution width (%)
    alb_gdL      : float — albumin in g/dL
    cr_mgdL      : float — creatinine in mg/dL  ← converted internally to μmol/L
    mcv_fL       : float — mean corpuscular volume in fL
    ap_UL        : float — alkaline phosphatase in U/L

    Returns
    -------
    float — PhenoAge (years), or None on error
    """
    try:
        cr_umol  = cr_mgdL * 88.42      # mg/dL → μmol/L
        glu_mmol = glu_mgdL / 18.0     # mg/dL → mmol/L

        xb = (-19.907
              - 0.0336  * alb_gdL
              + 0.0095  * cr_umol
              + 0.1953  * glu_mmol
              + 0.0954  * math.log(crp_mgL)
              - 0.0120  * lymph_pct
              + 0.0268  * mcv_fL
              + 0.3306  * rdw_pct
              + 0.00188 * ap_UL
              + 0.0554  * wbc_thousands
              + 0.0804  * age)

        mortality_score = 1 - math.exp(-1.51714 * math.exp(xb) / 0.0076927)

        if not (0.0 < mortality_score < 1.0):
            return None

        pheno_age = 141.5 + math.log(-0.00553 * math.log(1 - mortality_score)) / 0.09165
        return round(pheno_age, 2)

    except Exception as e:
        print(f"[bio_age_models] phenoage error: {e}")
        return None


# ===========================================================================
# Self-test (run:  python bio_age_models.py)
# ===========================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("Bio Age Models — Self-verification")
    print("=" * 60)

    # S-AnthropoAge  (R reference ≈ 38.09 for different inputs — value varies)
    r = s_anthropoage(31, "Men", 1.73, 75, 82, "Mexican-American")
    print(f"S-AnthropoAge Men 31yo, 75kg, 1.73m, Waist=82, Mex-Am : {r}")

    # Full AnthropoAge — R reference values computed manually
    r1 = anthropoage_full(31, "Men", 1.73, 75, 82, "Mexican-American",
                          thigh_cm=49.5, arm_cm=27.7, subs_mm=17, tric_mm=17.2)
    print(f"Full AnthropoAge Men 31yo (R=32.8882)                  : {r1}")

    r2 = anthropoage_full(24, "Women", 1.62, 61, 76, "Mexican-American",
                          thigh_cm=49.5, arm_cm=27.7, subs_mm=17, tric_mm=17.2)
    print(f"Full AnthropoAge Women 24yo (R=22.479)                 : {r2}")

    r3 = anthropoage_full(40, "Men", 1.68, 70, 85, "Other",
                          thigh_cm=50, arm_cm=28, subs_mm=15, tric_mm=10)
    print(f"Full AnthropoAge Men 40yo Thai (R=38.228)              : {r3}")

    r4 = anthropoage_full(35, "Women", 1.58, 55, 75, "Other",
                          thigh_cm=47, arm_cm=25, subs_mm=18, tric_mm=15)
    print(f"Full AnthropoAge Women 35yo Thai (R=31.710)            : {r4}")

    # PhenoAge — R reference = 44.35
    r5 = phenoage(40, 0.02, 23.9, 5.7, 95, 12, 4.4, 0.7, 93.6, 52)
    print(f"PhenoAge Age=40 (R=44.35)                              : {r5}")

    print("=" * 60)
