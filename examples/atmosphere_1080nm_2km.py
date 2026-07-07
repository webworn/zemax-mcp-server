"""
examples/atmosphere_1080nm_2km.py
=================================
Atmospheric channel model for the 1080 nm beam over a 2 km path, using the
opm_naren telescope's real parameters. Two effects:

  1. ATMOSPHERIC ABSORPTION / EXTINCTION  -> the link budget (Beer-Lambert,
     Kruse/Kim aerosol model + Rayleigh).
  2. SCINTILLATION (turbulence-induced irradiance fluctuations) -> Andrews &
     Phillips theory: Rytov variance, scintillation index with saturation,
     aperture averaging by the large receive aperture, and fade statistics.
     Plus the turbulence ANGLE-OF-ARRIVAL jitter and transmit beam wander, which
     are exactly what the 2-FSM stabilization loop (examples/beam_stabilization.py)
     corrects.

WHY THIS IS ANALYTICAL, NOT RAY-TRACED: turbulence and scintillation are random
wave-optics effects (refractive-index phase screens), outside geometric ray
tracing. Zemax can show bulk absorption as an attenuating medium, but the channel
statistics are computed here from the design's aperture / wavelength / range.

Design inputs (read from opm_naren.zmx):
    wavelength   lambda = 1.08 um   (WAVM 1, primary)
    range        L      = 2.0 km    (image conjugate 2.09 km ~ operating range)
    aperture     D      = 0.641 m   (entrance pupil = 2 * 700 m * OBNA 0.000458)

References:
    L. C. Andrews & R. L. Phillips, "Laser Beam Propagation through Random Media",
        2nd ed., SPIE Press 2005 (Rytov variance, scintillation index, aperture
        averaging, angle-of-arrival, beam wander).
    P. W. Kruse et al. (1962); I. I. Kim et al., SPIE 4214 (2001) -- aerosol
        extinction vs visibility.
"""
import math

# ----- design-derived constants -----
LAMBDA = 1.08e-6      # m
L      = 2000.0       # m  (2 km path; design image conjugate 2.09 km)
D      = 0.641        # m  entrance pupil (2 * 700 m * OBNA 0.000458)
K      = 2 * math.pi / LAMBDA


# ===================================================================== ABSORPTION
def extinction_per_km(vis_km, lam_nm=1080.0):
    """Total atmospheric extinction coefficient [1/km] at wavelength lam_nm.
    Aerosol via Kruse/Kim visibility model + Rayleigh molecular scattering."""
    # aerosol size-distribution exponent q (Kim 2001)
    V = vis_km
    if V > 50:   q = 1.6
    elif V > 6:  q = 1.3
    elif V > 1:  q = 0.16 * V + 0.34
    elif V > 0.5:q = V - 0.5
    else:        q = 0.0
    beta_aer = (3.912 / V) * (lam_nm / 550.0) ** (-q)
    # Rayleigh (molecular) scattering ~ lambda^-4, ~0.012/km at 550 nm sea level
    beta_ray = 0.012 * (550.0 / lam_nm) ** 4
    return beta_aer + beta_ray


def transmission(vis_km, path_km=L / 1000.0):
    beta = extinction_per_km(vis_km)
    T = math.exp(-beta * path_km)
    return T, beta


# =================================================================== SCINTILLATION
def rytov_variance(cn2):
    """Plane-wave Rytov variance sigma_R^2 = 1.23 Cn^2 k^(7/6) L^(11/6)."""
    return 1.23 * cn2 * K ** (7.0 / 6.0) * L ** (11.0 / 6.0)


def scintillation_index_point(cn2):
    """Point-aperture scintillation index across weak->strong regimes
    (Andrews & Phillips saturation formula)."""
    sr2 = rytov_variance(cn2)
    a = 0.49 * sr2 / (1 + 1.11 * sr2 ** (6.0 / 5.0)) ** (7.0 / 6.0)
    b = 0.51 * sr2 / (1 + 0.69 * sr2 ** (6.0 / 5.0)) ** (5.0 / 6.0)
    return math.exp(a + b) - 1.0


def aperture_averaging_factor():
    """Plane-wave aperture-averaging factor A = sigma_I^2(D)/sigma_I^2(0)
    (Andrews):  A = [1 + 1.062 * k D^2 / (4 L)]^(-7/6)."""
    return (1 + 1.062 * K * D * D / (4 * L)) ** (-7.0 / 6.0)


def angle_of_arrival_jitter(cn2):
    """RMS angle-of-arrival fluctuation at the receive aperture [rad]:
    sigma_AoA = sqrt(2.914 Cn^2 L D^(-1/3)) -- the tilt the FSM loop tracks."""
    return math.sqrt(2.914 * cn2 * L * D ** (-1.0 / 3.0))


def beam_wander(cn2, w0):
    """RMS transmit beam-wander displacement at range L [m] for beam radius w0:
    sigma_bw^2 ~ 2.42 Cn^2 L^3 w0^(-1/3)."""
    return math.sqrt(2.42 * cn2 * L ** 3 * w0 ** (-1.0 / 3.0))


# ============================================== DELIVERED POWER + THERMAL BLOOMING
# air thermo-optic constants (near STP, ~15 C, 1 atm)
DN_DT   = -1.0e-6     # d(n)/dT of air  [1/K]  (negative -> defocusing thermal lens)
RHO_AIR = 1.2        # kg/m^3
CP_AIR  = 1005.0     # J/(kg K)


def absorption_coeff(vis_km, aerosol_absorb_frac=0.15, molecular_per_km=0.02):
    """Absorption part of the extinction [1/m] -- ONLY absorption heats the air and
    drives thermal blooming (aerosol/molecular scattering does not). Aerosol
    absorption ~ frac of aerosol extinction; plus a molecular (H2O) term [1/km]."""
    beta_tot = extinction_per_km(vis_km)                 # 1/km
    beta_ray = 0.012 * (550.0 / 1080.0) ** 4
    beta_aer = max(beta_tot - beta_ray, 0.0)
    return (aerosol_absorb_frac * beta_aer + molecular_per_km) / 1000.0


def fried_parameter(cn2):
    """Fried coherence length r0 [m] (plane wave): (0.423 k^2 Cn^2 L)^(-3/5)."""
    return (0.423 * K * K * cn2 * L) ** (-3.0 / 5.0)


def thermal_blooming(P, alpha_abs, w0, v_wind):
    """First-order steady-state thermal blooming (uniform crosswind v_wind).
    Returns (deltaT[K], distortion[waves rms], Strehl). ORDER-OF-MAGNITUDE:
    accurate blooming needs wave-optics propagation (split-step Fresnel)."""
    # steady-state temp rise in the beam: absorbed power / (mass flux * cp)
    dT = 2 * alpha_abs * P / (math.pi * w0 * v_wind * RHO_AIR * CP_AIR)
    waves = abs(DN_DT) * dT * L / LAMBDA          # accumulated OPD in waves
    sigma_phi = 2 * math.pi * waves               # rad (treat as ~rms wavefront)
    strehl = math.exp(-sigma_phi * sigma_phi)     # Marechal
    return dT, waves, strehl


def delivered_power(P_in, vis_km=23.0, M2=1.5, w0=D / 2, v_wind=5.0, cn2=1e-14,
                    telescope_T=1.0, fsm_tilt_corrected=True):
    """Power on target + beam quality over the 2 km path.

    P_in W input; vis_km visibility; M2 beam quality; w0 transmit beam radius [m];
    v_wind crosswind/slew speed [m/s]; cn2 turbulence; telescope_T optics
    transmission if routed through the director; fsm_tilt_corrected removes the
    turbulence tilt (beam wander) from the spot (the FSM loop's job).
    """
    T, _ = transmission(vis_km)
    P_del = P_in * T * telescope_T                       # <-- total power on target

    # spot radius (1/e^2) at range L: diffraction*M^2 (+) turbulence
    w_diff = M2 * LAMBDA * L / (math.pi * w0)
    r0 = fried_parameter(cn2)
    w_turb = LAMBDA * L / (math.pi * r0)                 # higher-order turbulence spread
    # tilt/wander term (removed if the FSM corrects it)
    wander = 0.0 if fsm_tilt_corrected else beam_wander(cn2, w0)
    w_total = math.sqrt(w_diff ** 2 + w_turb ** 2 + wander ** 2)

    alpha_abs = absorption_coeff(vis_km)
    dT, bloom_waves, strehl = thermal_blooming(P_del, alpha_abs, w0, v_wind)

    peak_I = 2 * P_del * strehl / (math.pi * w_total ** 2)   # W/m^2 (Gaussian peak)
    return dict(P_del=P_del, T=T, w_diff=w_diff, w_turb=w_turb, r0=r0,
                w_total=w_total, alpha_abs=alpha_abs, deltaT=dT,
                bloom_waves=bloom_waves, strehl=strehl, peak_I=peak_I)


def lognormal_fade_margin(sigma_I2, availability=0.999):
    """Fade margin [dB] so that P(I < I_thresh) = 1-availability, log-normal I."""
    # inverse complementary error function via bisection
    p = 1 - availability
    # solve 0.5*erfc(x/sqrt2) = p  -> x = sqrt2 * erfcinv(2p)
    lo, hi = 0.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if 0.5 * math.erfc(mid / math.sqrt(2)) > p: lo = mid
        else: hi = mid
    z = (lo + hi) / 2
    sig = math.sqrt(math.log(1 + sigma_I2))
    # fade margin in dB (Andrews): F = 4.343*(z*sig - 0.5*sig^2 ... ) use standard
    return 4.343 * (2 * z * sig)  # approx symmetric dB margin


# ========================================================================= REPORT
def main():
    print("=" * 70)
    print("Atmospheric channel: 1080 nm, 2 km, opm_naren telescope")
    print(f"  lambda={LAMBDA*1e9:.0f} nm  L={L/1000:.1f} km  D={D:.3f} m  "
          f"k={K:.3e} rad/m")
    fresnel = math.sqrt(LAMBDA * L)
    print(f"  Fresnel zone sqrt(lambda*L) = {fresnel*1000:.1f} mm  "
          f"(D/r_F = {D/fresnel:.1f})")

    print("\n--- 1. ABSORPTION / EXTINCTION (link budget) ---")
    print(f"  {'visibility':>11} {'ext(1/km)':>10} {'T 1-way':>9} {'dB 1-way':>9} "
          f"{'T round':>9} {'dB round':>9}")
    for V in (50, 23, 10, 5, 2):
        T, beta = transmission(V)
        dB = -10 * math.log10(T)
        print(f"  {V:>9} km {beta:>10.3f} {T:>9.3f} {dB:>9.2f} "
              f"{T*T:>9.3f} {2*dB:>9.2f}")
    print("  (round-trip = illuminate target + return to telescope, 2x2 km)")

    print("\n--- 2. SCINTILLATION ---")
    A = aperture_averaging_factor()
    print(f"  aperture-averaging factor A = {A:.4f}  "
          f"(the {D:.2f} m aperture cuts scintillation variance ~{1/A:.0f}x)")
    print(f"  {'Cn^2 (m^-2/3)':>14} {'regime':>8} {'sigmaR^2':>9} "
          f"{'sig_I(pt)':>10} {'sig_I(D)':>9} {'AoA jitter':>11}")
    for cn2, name in ((1e-16, "weak"), (1e-15, "weak"), (1e-14, "moder."),
                      (3e-14, "strong"), (1e-13, "strong")):
        sr2 = rytov_variance(cn2)
        si2_pt = scintillation_index_point(cn2)
        si2_ap = A * si2_pt
        aoa = angle_of_arrival_jitter(cn2) * 1e6   # urad
        print(f"  {cn2:>14.0e} {name:>8} {sr2:>9.3f} {math.sqrt(si2_pt):>10.3f} "
              f"{math.sqrt(si2_ap):>9.3f} {aoa:>9.1f} ur")

    print("\n--- 3. TIE-IN TO THE 2-FSM STABILIZATION LOOP ---")
    cn2 = 1e-14
    aoa = angle_of_arrival_jitter(cn2) * 1e6
    bw = beam_wander(cn2, D / 2) * 1000  # mm at target
    si2_ap = A * scintillation_index_point(cn2)
    fade = lognormal_fade_margin(si2_ap, 0.999)
    print(f"  At Cn^2=1e-14 (typical daytime, moderate):")
    print(f"    - turbulence angle-of-arrival jitter ~ {aoa:.1f} urad rms")
    print(f"      -> corrected by the FSM loop (demo held <=14.7 urad tracking).")
    print(f"    - transmit beam wander ~ {bw:.0f} mm at 2 km "
          f"({bw/2000*1000:.1f} urad) -> FSM point-ahead / tilt.")
    print(f"    - aperture-averaged scintillation sigma_I ~ {math.sqrt(si2_ap):.3f} "
          f"(few %): the big aperture, NOT the FSM, mitigates amplitude fade.")
    print(f"    - 99.9%-availability fade margin ~ {fade:.1f} dB (small, thanks")
    print(f"      to aperture averaging).")
    print("\n  Summary: absorption sets the link budget; the 0.64 m aperture")
    print("  averages scintillation away; turbulence TILT (AoA + wander) is the")
    print("  dominant correctable term and is exactly the FSM loop's job")
    print("  (Beam Stabilization report, Layer 4 / Loop 1-3).")

    print("\n--- 4. DELIVERED POWER + THERMAL BLOOMING (6 kW, M^2=1.5) ---")
    P_in = 6000.0
    print(f"  input {P_in/1000:.1f} kW, M^2=1.5, transmit beam radius w0={D/2:.2f} m, "
          f"crosswind 5 m/s")
    print(f"  {'visibility':>11} {'P_target':>9} {'spot r':>8} {'bloom':>7} "
          f"{'Strehl':>7} {'peak I':>11}")
    for V in (50, 23, 10, 5):
        r = delivered_power(P_in, vis_km=V, M2=1.5, v_wind=5.0, cn2=1e-14)
        print(f"  {V:>9} km {r['P_del']/1000:>7.2f}kW {r['w_total']*1000:>6.1f}mm "
              f"{r['bloom_waves']:>6.3f}w {r['strehl']:>7.2f} "
              f"{r['peak_I']/1e4:>8.0f} W/cm2")
    print("  (P_target = power ON target; only absorption removes power. M^2,")
    print("   turbulence & blooming spread the spot -> lower peak I, same watts.)")
    print("\n  wind sensitivity (blooming), clear 23 km, 6 kW:")
    for v in (10, 5, 2, 1):
        r = delivered_power(P_in, vis_km=23, v_wind=v, cn2=1e-14)
        print(f"    v={v:>2} m/s -> bloom {r['bloom_waves']:.3f} waves, "
              f"Strehl {r['strehl']:.2f}, peak I {r['peak_I']/1e4:.0f} W/cm2")
    print("  Note: thermal-blooming numbers are first-order (Marechal/distortion-")
    print("  number); accurate blooming needs wave-optics split-step propagation.")
    print("=" * 70)


if __name__ == "__main__":
    main()
