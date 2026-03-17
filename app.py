import os
import requests
from flask import Flask, render_template, request
from datetime import datetime
import math

app = Flask(__name__)

# -----------------------------
# CONFIG
# -----------------------------
TRADIER_TOKEN = os.getenv("TRADIER_TOKEN")

TRADIER_EXP_URL = "https://api.tradier.com/v1/markets/options/expirations"
TRADIER_CHAIN_URL = "https://api.tradier.com/v1/markets/options/chains"
TRADIER_QUOTE_URL = "https://api.tradier.com/v1/markets/quotes"

HEADERS = {
    "Authorization": f"Bearer {TRADIER_TOKEN}",
    "Accept": "application/json"
}

RISK_TO_DELTA = {
    "very_safe": 0.10,
    "safe": 0.15,
    "moderate": 0.20,
    "aggressive": 0.25,
    "very_aggressive": 0.30
}

RISK_FREE_RATE = 0.04

# Simple in‑memory cache for chains (per request)
chain_cache = {}


# -----------------------------
# HELPERS
# -----------------------------
def safe_float(x):
    try:
        return float(x)
    except:
        return None


def get_stock_price_tradier(ticker):
    try:
        r = requests.get(TRADIER_QUOTE_URL, headers=HEADERS, params={"symbols": ticker})
        if r.status_code != 200:
            return None

        data = r.json()
        quote = data.get("quotes", {}).get("quote")
        if not quote:
            return None

        return safe_float(quote.get("last"))
    except:
        return None


def get_tradier_expirations(ticker):
    try:
        r = requests.get(TRADIER_EXP_URL, headers=HEADERS, params={"symbol": ticker})
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("expirations", {}).get("date", [])
    except:
        return []


def get_tradier_chain(ticker, expiration):
    key = f"{ticker}_{expiration}"
    if key in chain_cache:
        return chain_cache[key]

    try:
        r = requests.get(TRADIER_CHAIN_URL, headers=HEADERS,
                         params={"symbol": ticker, "expiration": expiration})
        if r.status_code != 200:
            chain_cache[key] = []
        else:
            data = r.json()
            chain_cache[key] = data.get("options", {}).get("option", [])
    except:
        chain_cache[key] = []

    return chain_cache[key]


# -----------------------------
# BLACK-SCHOLES HELPERS
# -----------------------------
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_d1(price, strike, t, r, sigma):
    return (math.log(price / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))


def bs_d2(d1, sigma, t):
    return d1 - sigma * math.sqrt(t)


def black_scholes_iv(price, strike, days, premium):
    try:
        t = days / 365
        if t <= 0 or price <= 0 or premium <= 0:
            return None
        return (premium / price) / math.sqrt(t)
    except:
        return None


# -----------------------------
# SYNTHETIC GREEKS (fallback only)
# -----------------------------
def compute_effective_delta_and_iv(opt, price, days):
    """
    Lightweight: returns (delta, iv, iv_estimated)
    Used for filtering + strike selection.
    """
    t = max(days, 1) / 365.0
    strike = safe_float(opt.get("strike"))
    if price is None or strike is None or t <= 0:
        return None, None, False

    greeks = opt.get("greeks") or {}
    real_delta = safe_float(greeks.get("delta"))
    real_iv = safe_float(greeks.get("iv"))

    # If Tradier provides both → use them
    if real_delta is not None and real_iv is not None:
        return real_delta, real_iv, False

    # Otherwise, estimate IV from premium if possible
    bid = safe_float(opt.get("bid"))
    ask = safe_float(opt.get("ask"))
    premium = None
    if bid and ask and bid > 0 and ask > 0:
        premium = (bid + ask) / 2
    elif bid and bid > 0:
        premium = bid

    iv_estimated = False
    iv = real_iv
    if iv is None and premium:
        iv = black_scholes_iv(price, strike, days, premium)
        if iv:
            iv_estimated = True

    # If we still don't have IV, we can only return real delta
    if iv is None or iv <= 0:
        return real_delta, None, False

    # Compute synthetic delta if needed
    if real_delta is not None:
        delta = real_delta
    else:
        d1 = bs_d1(price, strike, t, RISK_FREE_RATE, iv)
        delta = norm_cdf(d1)

    return delta, iv, iv_estimated


def compute_full_greeks(opt, price, days, iv, delta_override=None):
    """
    Full Greeks for the final chosen contract only.
    Returns (delta, gamma, theta, vega).
    """
    t = max(days, 1) / 365.0
    strike = safe_float(opt.get("strike"))
    if price is None or strike is None or t <= 0 or iv is None or iv <= 0:
        return None, None, None, None

    d1 = bs_d1(price, strike, t, RISK_FREE_RATE, iv)
    d2 = bs_d2(d1, iv, t)

    delta = delta_override if delta_override is not None else norm_cdf(d1)
    gamma = math.exp(-0.5 * d1 * d1) / (price * iv * math.sqrt(2 * math.pi * t))
    theta = (
        -(price * math.exp(-0.5 * d1 * d1) * iv) / (2 * math.sqrt(2 * math.pi * t))
        - RISK_FREE_RATE * strike * math.exp(-RISK_FREE_RATE * t) * norm_cdf(d2)
    )
    vega = price * math.exp(-0.5 * d1 * d1) * math.sqrt(t) / math.sqrt(2 * math.pi)

    return delta, gamma, theta, vega


# -----------------------------
# EXPIRATION FILTERING
# -----------------------------
def expiration_has_usable_calls(ticker, expiration, price, days):
    chain = get_tradier_chain(ticker, expiration)
    if not chain:
        return False
    for opt in chain:
        if opt.get("option_type") != "call":
            continue
        delta, iv, _ = compute_effective_delta_and_iv(opt, price, days)
        if delta is not None:
            return True
    return False


# -----------------------------
# DEBUG
# -----------------------------
@app.route("/debug_chain")
def debug_chain():
    ticker = request.args.get("ticker", "").upper().strip()
    expiration = request.args.get("expiration", "").strip()
    chain = get_tradier_chain(ticker, expiration)
    return {
        "ticker": ticker,
        "expiration": expiration,
        "chain_length": len(chain),
        "sample": chain[:5]
    }


# -----------------------------
# MAIN ROUTE
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    global chain_cache
    chain_cache = {}  # reset per request

    expirations = []
    result = None
    error = None

    if request.method == "POST":
        action = request.form.get("action")
        ticker = request.form.get("ticker", "").upper().strip()

        if not ticker:
            return render_template("index.html", error="Enter a ticker.")

        price = get_stock_price_tradier(ticker)
        if price is None:
            return render_template("index.html", error="Unable to fetch stock price.")

        # -----------------------------
        # LOAD EXPIRATIONS (filtered)
        # -----------------------------
        if action == "load":
            raw_exps = get_tradier_expirations(ticker)
            filtered = []
            now = datetime.now()

            for e in raw_exps:
                try:
                    d = datetime.strptime(e, "%Y-%m-%d")
                    days_out = (d - now).days
                except:
                    continue

                if days_out <= 0:
                    continue

                if expiration_has_usable_calls(ticker, e, price, days_out):
                    filtered.append({"date": e, "earnings_week": False})

            return render_template("index.html", expirations=filtered)

        # -----------------------------
        # CALCULATE
        # -----------------------------
        expiration = request.form.get("expiration")
        risk = request.form.get("risk")

        # Repopulate filtered expirations
        raw_exps = get_tradier_expirations(ticker)
        filtered = []
        now = datetime.now()

        for e in raw_exps:
            try:
                d = datetime.strptime(e, "%Y-%m-%d")
                days_out_tmp = (d - now).days
            except:
                continue

            if days_out_tmp <= 0:
                continue

            if expiration_has_usable_calls(ticker, e, price, days_out_tmp):
                filtered.append({"date": e, "earnings_week": False})

        expirations = filtered

        if not expiration:
            return render_template("index.html", expirations=expirations, error="Select an expiration.")

        try:
            d = datetime.strptime(expiration, "%Y-%m-%d")
            days_out = (d - datetime.now()).days
        except:
            return render_template("index.html", expirations=expirations, error="Invalid expiration date.")

        target_delta = RISK_TO_DELTA.get(risk, 0.20)

        chain = get_tradier_chain(ticker, expiration)
        if not chain:
            return render_template("index.html", expirations=expirations, error="Unable to pull option data.")

        best = None
        best_diff = 999
        best_delta = None
        best_iv = None
        best_iv_estimated = False

        # 1) Find best contract using delta + IV (lightweight)
        for opt in chain:
            if opt.get("option_type") != "call":
                continue

            delta, iv_val, iv_estimated = compute_effective_delta_and_iv(opt, price, days_out)
            if delta is None:
                continue

            diff = abs(delta - target_delta)
            if diff < best_diff:
                best_diff = diff
                best = opt
                best_delta = delta
                best_iv = iv_val
                best_iv_estimated = iv_estimated

        if not best or best_delta is None:
            return render_template("index.html", expirations=expirations, error="No valid call options found.")

        # 2) Compute full Greeks only for the chosen contract
        full_delta, gamma, theta, vega = compute_full_greeks(best, price, days_out, best_iv, delta_override=best_delta)

        # 3) Premium: mid × 100, fallback to bid × 100
        bid = safe_float(best.get("bid"))
        ask = safe_float(best.get("ask"))
        if bid and ask and bid > 0 and ask > 0:
            premium = round((bid + ask) / 2 * 100, 2)
        elif bid and bid > 0:
            premium = round(bid * 100, 2)
        else:
            premium = None

        iv_display = "N/A" if best_iv is None else f"{best_iv:.3f}"
        assign_prob = round(best_delta * 100, 1)

        result = {
            "ticker": ticker,
            "stock_price": price,
            "expiration": expiration,
            "days_out": days_out,
            "risk_label": risk.replace("_", " ").title(),
            "strike": best.get("strike"),
            "iv": iv_display,
            "iv_estimated": best_iv_estimated,
            "assign_prob": assign_prob,
            "premium": premium,
            "delta": full_delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega
        }

    return render_template("index.html",
                           expirations=expirations,
                           result=result,
                           error=error)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
