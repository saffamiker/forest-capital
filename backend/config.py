"""
All configuration parameters. Defaults only — every value is overridable
at runtime via API request body.
"""
from __future__ import annotations
import os
from dotenv import load_dotenv

load_dotenv()

# ── DATA & DATE RANGES ────────────────────────────────────────────────────────
TRAIN_START   = "2000-01-01"
TRAIN_END     = "2018-12-31"
VALIDATION_START = "2019-01-01"
VALIDATION_END   = "2021-12-31"
TEST_START    = "2022-01-01"
TEST_END      = "2024-12-31"

# ── ASSET UNIVERSE ────────────────────────────────────────────────────────────
EQUITIES     = ["SPY", "QQQ", "IWM"]
SECTORS      = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLB", "XLU"]
FIXED_INCOME = ["TLT", "IEF", "SHY", "BND", "HYG", "LQD", "TIP", "AGG"]
ALTERNATIVES = ["GLD", "VNQ"]
BENCHMARK    = "SPY"

# ── PORTFOLIO CONSTRUCTION ────────────────────────────────────────────────────
REBALANCE_FREQ        = "monthly"
TRANSACTION_COST_BPS  = 10
MIN_WEIGHT            = 0.00
MAX_WEIGHT            = 0.40
RISK_FREE_RATE_FALLBACK = 0.045   # Used only if FRED unavailable
USE_DYNAMIC_RISK_FREE = True      # Fetch actual DFF from FRED daily
TARGET_VOLATILITY     = 0.10
BL_TAU                = 0.05
RISK_AVERSION         = 3.0
REBALANCE_BAND        = 0.05
OPTIMIZATION_WINDOW   = 36
ANNUALIZATION_FACTOR  = 252       # ALWAYS use 252 — never 260 or 365

# ── MOMENTUM SIGNALS ─────────────────────────────────────────────────────────
MOMENTUM_LOOKBACKS = [21, 63, 126, 252]
MOMENTUM_WEIGHTS   = [0.10, 0.20, 0.30, 0.40]
SIGNAL_SMOOTHING   = 5

# ── REGIME DETECTION ─────────────────────────────────────────────────────────
VIX_LOW_THRESHOLD      = 18
VIX_HIGH_THRESHOLD     = 28
BEAR_MARKET_THRESHOLD  = -0.20
YIELD_CURVE_INVERSION  = 0.00
REGIME_WINDOW          = 63
CREDIT_SPREAD_WIDE     = 4.50
HMM_N_STATES           = 3

# ── STATISTICAL TESTING — TIERED THRESHOLDS ──────────────────────────────────
# Tier 1 — Primary gates (full period, adequate power)
P_THRESHOLD_PRIMARY     = 0.005
FDR_Q_VALUE             = 0.005
P_THRESHOLD_DSR         = 0.005
P_THRESHOLD_OOS         = 0.005
P_THRESHOLD_PERMUTATION = 0.005

# Tier 2 — Sub-period / regime tests (reduced power, relax threshold)
P_THRESHOLD_SUBPERIOD   = 0.050
P_THRESHOLD_CV_FOLDS    = 0.050

# Stress tests: directional only — too few observations for p-value testing
STRESS_TEST_USE_PVALUES = False

MIN_OBSERVATIONS_FOR_POWER = 220
MIN_OBSERVATIONS_SUBPERIOD = 60
BOOTSTRAP_SAMPLES       = 10_000
BLOCK_SIZE              = 21
WALK_FORWARD_TRAIN      = 36
WALK_FORWARD_TEST       = 12
CONFIDENCE_LEVELS       = [0.95, 0.99]
RANDOM_SEED             = 42
ECONOMIC_SIGNIFICANCE_BPS = 50

# ── CROSS-VALIDATION ─────────────────────────────────────────────────────────
CV_N_SPLITS             = 5
CV_EMBARGO_PERIODS      = 252
CPCV_N_SPLITS           = 6
CPCV_N_TEST_SPLITS      = 2
CV_STABILITY_THRESHOLD  = 0.60
EXPANDING_WF_DIVERGENCE = 0.30

# ── STRESS TEST SCENARIOS ─────────────────────────────────────────────────────
STRESS_SCENARIOS = {
    "GFC_2008":       ("2008-09-01", "2009-03-31"),
    "COVID_2020":     ("2020-02-01", "2020-04-30"),
    "RATE_HIKE_2022": ("2022-01-01", "2022-12-31"),
    "DOTCOM_2000":    ("2000-03-01", "2002-10-31"),
    "TAPER_TANTRUM":  ("2013-05-01", "2013-09-30"),
}

# ── DATA CACHE ────────────────────────────────────────────────────────────────
CACHE_DIR          = "data/cache"
CACHE_EXPIRY_HOURS = 24

# ── MACRO DATA (FRED) ─────────────────────────────────────────────────────────
# 60-second timeout guards against FRED gateway stalls that previously caused
# 3-minute dashboard load times — regime cache (15-min TTL) absorbs most hits.
FRED_TIMEOUT_SECONDS = 60

FRED_SERIES = {
    "fed_funds":   "DFF",
    "treasury_10y": "DGS10",
    "treasury_2y":  "DGS2",
    "vix":          "VIXCLS",
    "hy_spread":    "BAMLH0A0HYM2",
}

# ── AUTH & ENVIRONMENT ────────────────────────────────────────────────────────
ENVIRONMENT              = os.getenv("ENVIRONMENT", "development")
FRONTEND_URL             = os.getenv("FRONTEND_URL", "http://localhost:5173")
SECRET_KEY               = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
MASTER_API_KEY           = os.getenv("MASTER_API_KEY", "michael_dev_key_here")

# Fail fast in production if either security-critical secret was never set.
# The dev defaults above are committed to the repo, so running production on
# them would allow anyone to forge session tokens / magic links (SECRET_KEY)
# or use a publicly known master key (MASTER_API_KEY). config.py is imported
# at startup, so an unset secret aborts the process before it serves traffic.
if ENVIRONMENT == "production":
    if SECRET_KEY == "dev-secret-key-change-in-production":
        raise RuntimeError(
            "SECRET_KEY must be set in production — the committed dev default "
            "is public and would allow forged session tokens."
        )
    if MASTER_API_KEY == "michael_dev_key_here":
        raise RuntimeError(
            "MASTER_API_KEY must be set in production — the committed dev "
            "default is public."
        )
MAGIC_LINK_EXPIRY_MINUTES = int(os.getenv("MAGIC_LINK_EXPIRY_MINUTES", "15"))
SESSION_EXPIRY_HOURS     = int(os.getenv("SESSION_EXPIRY_HOURS", "8"))
ALLOWED_EMAILS           = set(
    e.strip()
    for e in os.getenv(
        "ALLOWED_EMAILS",
        "ruurdsm@queens.edu,thaob@queens.edu,murdockm@queens.edu,panttserk@queens.edu",
    ).split(",")
    if e.strip()
)
DAILY_CREDIT_CAP_USD     = float(os.getenv("DAILY_CREDIT_CAP_USD", "5.00"))

# ── TEAM ACTIVITY LOGGING ─────────────────────────────────────────────────────
# Only the three project-team accounts have their UI and agent activity
# logged. Any other authenticated email (e.g. Dr. Panttser reviewing the
# platform) is silently skipped — the filter runs before every
# session_events / agent_interactions insert. It deliberately does NOT
# gate commit_activity (commits are attributed by git author, logged
# regardless) or login_failed events (kept for security visibility).
# Expanding this set later automatically starts logging the new accounts;
# anything not in it stays excluded.
PROJECT_TEAM_EMAILS = {
    "ruurdsm@queens.edu",   # Michael Ruurds
    "murdockm@queens.edu",  # Molly Murdock
    "thaob@queens.edu",     # Bob Thao
}

# Git commit author email → platform login email. Michael commits under a
# personal git identity; resolving it here merges his commit history with
# his platform activity under one identity in the Team Activity view. A
# git author with no mapping is displayed by its git email as-is.
GIT_AUTHOR_EMAIL_MAP = {
    "mikeruurds@gmail.com": "ruurdsm@queens.edu",
}

# Platform email → display name, for the Team Activity summary and timeline.
TEAM_MEMBER_NAMES = {
    "ruurdsm@queens.edu": "Michael Ruurds",
    "murdockm@queens.edu": "Molly Murdock",
    "thaob@queens.edu": "Bob Thao",
}

# ── GENERATOR-EVALUATOR HARNESS ───────────────────────────────────────────────
# The harness wraps an agent's text generation in an evaluate-and-retry
# loop: a response scoring below EVALUATOR_THRESHOLD is regenerated with
# the evaluator's feedback injected, up to EVALUATOR_MAX_RETRIES times.
# Invisible to the end user — only output quality changes.
EVALUATOR_THRESHOLD = 7.0       # 0-10; at or above this, a response is accepted
EVALUATOR_MAX_RETRIES = 2       # 2 retries → 3 generation attempts at most
EVALUATOR_MODEL = "claude-sonnet-4-6"   # the scoring model — not a persona agent
# When the evaluator itself errors, assume a passing score rather than
# blocking the primary response on an evaluator failure.
EVALUATOR_PASSTHROUGH_ON_ERROR = True

# GitHub repository the commit-sync endpoint and push webhook target.
GITHUB_REPO = os.getenv("GITHUB_REPO", "saffamiker/forest-capital")
# Personal access token for the commit-sync endpoint — the repo is private,
# so the GitHub API needs a token to read its commits. Optional: when unset
# the sync endpoint returns a clear "token required" message rather than 500.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
# Shared secret for validating GitHub push-webhook signatures (X-Hub-Signature-256).
# Optional locally; REQUIRED on Render before the webhook endpoint accepts events.
GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "")

# ── SITE TOUR ─────────────────────────────────────────────────────────────────
# The current site-tour version. /api/v1/changelog/unseen compares this
# against each user's last_tour_version_seen: when last_tour_version_seen is
# lower, has_tour_update is true — the What's New modal offers the tour and
# SiteTour auto-starts once per login session.
# Increment this by 1 whenever the tour's steps change materially, and ship
# a changelog entry in the same migration (see migration 013). Version 2
# corresponds to the initial guided tour built in migration 013.
TOUR_VERSION = 2

# The current UAT test-script version. The guided test runner's scripts
# live in frontend/src/constants/testScripts.ts, versioned with the code.
# GET /api/v1/testing/unseen compares this against each tester's most
# recent attestation: when a tester has results below this version (or
# none), the script's steps need re-attestation and a login notification
# surfaces. Increment by 1 whenever a test script's steps change
# materially, and bump the matching `version` field in testScripts.ts.
TEST_SCRIPT_VERSION = 1
