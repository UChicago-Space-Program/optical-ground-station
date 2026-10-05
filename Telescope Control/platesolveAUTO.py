"""
platesolveAUTO.py
------------------
Local, blind plate solving for the star tracker (ASI120MM Mini + 30F4 guide scope).
No network needed -- uses the `astrometry` package (local astrometry.net solver).

Method (see Plate_Solve_Verification.ipynb for the full derivation and measurements):

  * Source detection -- SEP (Source Extractor as a library; Bertin & Arnouts 1996).
    Spatially-varying background subtraction, then keep sources that are
    >= N-sigma over the local background RMS across >= SEP_MINAREA connected pixels,
    deblended and ranked by flux. This is the field-standard "target vs noise"
    criterion, not a fixed count of brightest pixels.

  * Index coverage -- astrometry.net index files spanning 10%-100% of the field of
    view (Lang, Hogg, Mierle, Blanton & Roweis 2010, AJ 139, 1782), computed from the
    measured plate scale, so an arbitrary pointing anywhere on the sky solves blind.
    The star tracker's 2.2 x 1.66 deg field needs index scales ~6-12 (17-170 arcmin);
    the previous config stopped at scale 8 (42 arcmin) and so failed on sparse fields.

  * Time-bounding -- a hopeless frame fails fast instead of grinding. Only the
    brightest MAX_SOURCES stars are used (solve cost grows ~C(N,4)); each solve is
    capped at MAX_QUADS quads (the only reliable per-call bound -- the astrometry C++
    solver can't be interrupted mid-call and its logodds_callback never fires on a
    failing solve); and the adaptive threshold cascade has an overall wall-clock budget.

Public API (unchanged, back-compatible):
    auto_solve(image_path, tel_pixel=None) -> dict | None
        dict keys: boresight_ra, boresight_dec, wcs_header,
                   tel_ra, tel_dec                       (only if tel_pixel given),
                   n_sources, thresh_used, scale_arcsec_per_pixel, solve_time_s
"""

import time
import numpy as np
import pandas as pd
from astropy.io import fits
from astropy.wcs import WCS
import astrometry

# SEP = Source Extractor as a library. Installed as `sep` (>=1.4) or the `sep-pjw` fork.
try:
    import sep_pjw as sep
    _HAVE_SEP = True
except ImportError:
    try:
        import sep
        _HAVE_SEP = True
    except ImportError:
        sep = None
        _HAVE_SEP = False

# twirl is only needed for the retained legacy get_points()/astrowork() path.
try:
    from twirl import find_peaks
except ImportError:
    find_peaks = None


# --- Star tracker optics: ASI120MM Mini + 30F4 guide scope ---
# Pixel: 3.75 um, focal length: 120 mm -> 6.22 arcsec/pixel (measured)
ST_WIDTH = 1280
ST_HEIGHT = 960
ST_PLATE_SCALE = 6.22  # arcsec/pixel

# --- Plate-solve tuning (see module docstring) ---
CACHE_DIR = "astrometry_cache"
MAX_SOURCES = 20                  # brightest N stars fed to the solver (quads ~ C(N,4))
MAX_QUADS = 300                   # hard cap on quads tried per solve (bounds a failing solve)
TIME_BUDGET_S = 10.0              # overall wall-clock budget for the cascade, per image
THRESH_CASCADE = (5.0, 3.0, 2.0)  # adaptive detection thresholds (sigma over background)
SEP_MINAREA = 5                   # min connected pixels above threshold (rejects hot pixels/CRs)
SEP_DEBLEND_CONT = 0.005          # SEP deblending contrast
EDGE_MARGIN = 8                   # drop sources within this many px of the border

# astrometry.net index scale -> skymark diameter band (arcmin), from the official docs.
_SCALE_TABLE = {
    0: (2.0, 2.8), 1: (2.8, 4.0), 2: (4.0, 5.6), 3: (5.6, 8.0), 4: (8, 11),
    5: (11, 16), 6: (16, 22), 7: (22, 30), 8: (30, 42), 9: (42, 60),
    10: (60, 85), 11: (85, 120), 12: (120, 170), 13: (170, 240), 14: (240, 340),
    15: (340, 480), 16: (480, 680), 17: (680, 1000), 18: (1000, 1400), 19: (1400, 2000),
}


def fov_scales(low_frac=0.07, high_frac=1.0):
    """Index scales whose skymarks are low_frac..high_frac of the FOV diagonal.

    Implements the astrometry.net "10%-100% of image size" rule (extended slightly
    to 7% on the small end for robustness in sparse fields).
    """
    diag_arcmin = np.hypot(ST_WIDTH, ST_HEIGHT) * ST_PLATE_SCALE / 60.0
    lo, hi = low_frac * diag_arcmin, high_frac * diag_arcmin
    return sorted(s for s, (a, b) in _SCALE_TABLE.items() if not (b < lo or a > hi))


# Solvers (lazy-initialized). _SOLVER = production wide-FOV solver.
_SOLVER = None
_LEGACY_SOLVER = None


def _get_solver():
    """Build (once) an astrometry.net solver covering the full field of view.

    5200 series (Tycho-2 + Gaia-DR2) provides scales 0-6; 4100 (Tycho-2) provides
    scales 7-19. Missing index files are downloaded to CACHE_DIR on first use.
    """
    global _SOLVER
    if _SOLVER is None:
        scales = fov_scales()
        scales_5200 = {s for s in scales if s <= 6}
        scales_4100 = {s for s in scales if s >= 7}
        index_files = []
        if scales_5200:
            index_files += astrometry.series_5200.index_files(
                cache_directory=CACHE_DIR, scales=scales_5200)
        if scales_4100:
            index_files += astrometry.series_4100.index_files(
                cache_directory=CACHE_DIR, scales=scales_4100)
        _SOLVER = astrometry.Solver(index_files)
    return _SOLVER


def extract_sources(data, thresh_sigma=5.0, max_sources=MAX_SOURCES,
                    minarea=SEP_MINAREA, deblend_cont=SEP_DEBLEND_CONT,
                    edge_margin=EDGE_MARGIN):
    """Detect stars with SEP (SExtractor standard).

    Background-subtract, then keep sources >= thresh_sigma over the local background
    RMS across >= minarea connected pixels, deblended and ranked by flux. Returns a
    DataFrame with columns x, y, flux (brightest `max_sources` rows).
    """
    if not _HAVE_SEP:
        raise RuntimeError(
            "SEP is required for plate solving. Install it with `pip install sep` "
            "(or `pip install sep-pjw`).")
    # SEP needs a C-contiguous, native-byte-order float array.
    d = np.ascontiguousarray(data, dtype=np.float32)
    bkg = sep.Background(d)
    data_sub = d - bkg.back()
    objs = sep.extract(data_sub, thresh=thresh_sigma, err=bkg.globalrms,
                       minarea=minarea, deblend_cont=deblend_cont)
    if len(objs) == 0:
        return pd.DataFrame(columns=['x', 'y', 'flux'])
    df = pd.DataFrame({'x': objs['x'], 'y': objs['y'], 'flux': objs['flux']})
    h, w = d.shape
    keep = ((df['x'] > edge_margin) & (df['x'] < w - edge_margin) &
            (df['y'] > edge_margin) & (df['y'] < h - edge_margin))
    return (df[keep].sort_values('flux', ascending=False)
            .head(max_sources).reset_index(drop=True))


def _solve_stars(stars, solver=None, max_quads=MAX_QUADS):
    """Blind-solve a list of [x, y] star positions. Returns an astrometry match or None."""
    if solver is None:
        solver = _get_solver()
    solution = solver.solve(
        stars=stars,
        size_hint=astrometry.SizeHint(
            lower_arcsec_per_pixel=ST_PLATE_SCALE * 0.5,
            upper_arcsec_per_pixel=ST_PLATE_SCALE * 2.0),
        position_hint=None,          # blind: no prior -> solves anywhere on the sky
        solution_parameters=astrometry.SolutionParameters(maximum_quads=max_quads),
    )
    return solution.best_match() if solution.has_match() else None


def centerpoint(wcs_header):
    """Sky coordinate of the star tracker boresight (image center)."""
    return WCS(wcs_header, relax=True).pixel_to_world(ST_WIDTH / 2, ST_HEIGHT / 2)


def telescope_boresight_radec(wcs_header, tel_pixel_x, tel_pixel_y):
    """Sky coordinate of the telescope boresight from its known ST pixel."""
    return WCS(wcs_header, relax=True).pixel_to_world(tel_pixel_x, tel_pixel_y)


def auto_solve(image_path, tel_pixel=None, thresh_cascade=THRESH_CASCADE,
               max_sources=MAX_SOURCES, max_quads=MAX_QUADS,
               time_budget_s=TIME_BUDGET_S, verbose=True):
    """
    Blind plate-solve a FITS image (anywhere on the sky). Returns a result dict or None.

    Bounded so a hopeless frame fails fast: only the brightest `max_sources` stars are
    used, each solve is capped at `max_quads` quads, and the adaptive threshold cascade
    (5 -> 3 -> 2 sigma by default) stops once `time_budget_s` is exceeded.

    Result keys: boresight_ra, boresight_dec, wcs_header
                 tel_ra, tel_dec                      (only if tel_pixel given)
                 n_sources, thresh_used, scale_arcsec_per_pixel, solve_time_s
    """
    try:
        data = fits.open(image_path)[0].data
    except Exception as e:
        if verbose:
            print(f"  Could not read {image_path}: {e}")
        return None

    t_start = time.time()
    for thresh in thresh_cascade:
        if time.time() - t_start > time_budget_s:
            if verbose:
                print(f"  Time budget {time_budget_s:.0f}s exceeded -- giving up.")
            break
        sources = extract_sources(data, thresh_sigma=thresh, max_sources=max_sources)
        if len(sources) < 4:            # need >= 4 stars to form a quad
            continue
        stars = sources[['x', 'y']].values.tolist()
        match = _solve_stars(stars, max_quads=max_quads)
        if match is not None:
            wcs_header = match.astropy_wcs().to_header(relax=True)
            center = centerpoint(wcs_header)
            result = {
                'boresight_ra': center.icrs.ra.deg,
                'boresight_dec': center.icrs.dec.deg,
                'wcs_header': wcs_header,
                'n_sources': len(sources),
                'thresh_used': thresh,
                'scale_arcsec_per_pixel': match.scale_arcsec_per_pixel,
                'solve_time_s': time.time() - t_start,
            }
            if tel_pixel is not None:
                tel = telescope_boresight_radec(wcs_header, tel_pixel[0], tel_pixel[1])
                result['tel_ra'] = tel.icrs.ra.deg
                result['tel_dec'] = tel.icrs.dec.deg
            return result

    if verbose:
        print("  Plate solve: no match found.")
    return None


# ---------------------------------------------------------------------------
# Legacy API (twirl top-N peak finder + the original narrow index-scale solver).
# Retained for backward compatibility and for the before/after comparison in
# Plate_Solve_Verification.ipynb. auto_solve() no longer uses this path.
# ---------------------------------------------------------------------------

def _get_legacy_solver():
    """The original narrow-scale solver: 5200{4,5,6} + 4100{7,8} (skymarks 8-42 arcmin)."""
    global _LEGACY_SOLVER
    if _LEGACY_SOLVER is None:
        _LEGACY_SOLVER = astrometry.Solver(
            astrometry.series_5200.index_files(cache_directory=CACHE_DIR, scales={4, 5, 6})
            + astrometry.series_4100.index_files(cache_directory=CACHE_DIR, scales={7, 8})
        )
    return _LEGACY_SOLVER


def get_points(path, p=15):
    """[legacy] Extract the p brightest peaks from a FITS image via twirl.find_peaks."""
    if find_peaks is None:
        raise RuntimeError("twirl is not installed (needed for the legacy get_points).")
    data = fits.open(path)[0].data
    xy = find_peaks(data)[0:p]
    if xy.ndim < 2 or len(xy) < 3:
        print(f"  Only {len(xy)} peak(s) found -- need at least 3.")
        return None
    x, y = xy[:, 0], xy[:, 1]
    flux = data[np.round(y).astype(int), np.round(x).astype(int)]
    return pd.DataFrame({'x': x, 'y': y, 'flux': flux})


def astrowork(source_list):
    """[legacy] Plate-solve a source-list DataFrame with the narrow-scale solver.

    Returns a WCS header or None. Matches the original behaviour (narrow index scales,
    no quad cap) so the notebook can reproduce the pre-change results exactly.
    """
    stars = [[r['x'], r['y']] for _, r in source_list.iterrows()]
    solver = _get_legacy_solver()
    solution = solver.solve(
        stars=stars,
        size_hint=astrometry.SizeHint(
            lower_arcsec_per_pixel=ST_PLATE_SCALE * 0.5,
            upper_arcsec_per_pixel=ST_PLATE_SCALE * 2.0),
        position_hint=None,
        solution_parameters=astrometry.SolutionParameters(),
    )
    if solution.has_match():
        return solution.best_match().astropy_wcs().to_header(relax=True)
    print('  Plate solve: no match found.')
    return None
