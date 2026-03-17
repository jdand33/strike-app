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

# -----------------------------
# HELPERS
# -----------------------------
def safe_float(x):
    try:
        return float(x)
    except:
        return None


def get_stock_price_tradier(ticker):
    """Pull stock price directly from Tradier."""
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
    try:
        r = requests.get(TRADIER_CHAIN_URL, headers=HEADERS,
                         params={"symbol": ticker, "expiration": expiration})
        if r.status_code != 200:
            return []
        data = r.json()
        return data.get("options", {}).get("option", [])
    except:
        return []


def black_scholes_iv(price, strike, days, premium):
    """Simple fallback IV estimate."""
    try:
        t = days / 365
        if t <= 0:
            return None
        approx_iv = (premium / price) / math.sqrt(t)
        return approx_iv
    except:
        return None


# -----------------------------
# DEBUG ENDPOINT
# -----------------------------
@app.route("/debug")
def debug():
    token_present = TRADIER_TOKEN is not None and len(TRADIER_TOKEN.strip()) > 0

    try:
        r = requests.get(TRADIER_QUOTE_URL, headers=HEADERS, params={"symbols": "AAPL"})
        status = r.status_code
        valid_token = (status == 200)
    except Exception as e:
        status = f"Error: {e}"
        valid_token = False

    return {
        "token_present": token_present,
        "token_valid": valid_token,
        "tradier_status_code": status
    }


# -----------------------------
# MAIN ROUTE
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    expirations = []
    result = None
    error = None

    if request.method == "POST":
        action = request.form.get("action")
        ticker = request.form.get("ticker", "").upper().strip()

        if not ticker:
            return render_template("index.html", error="Enter a ticker.")

        # -----------------------------
        # LOAD EXPIRATIONS
        # -----------------------------
        if action == "load":
            raw_exps = get_tradier_expirations(ticker)
            expirations = [{"date": e, "earnings_week": False} for e in raw_exps]

            return render_template("index.html",
                                   expirations=expirations,
                                   error=None)

        # -----------------------------
        # CALCULATE
        # -----------------------------
        expiration = request.form.get("expiration")
        risk = request.form.get("risk")

        if not expiration:
            error = "Select an expiration."
            return render_template("index.html", error=error)

        # Stock price from Tradier
        price = get_stock_price_tradier(ticker)
        if price is None:
            error = "Unable to fetch stock price."
            return render_template("index.html", error=error)

        # Days until expiration
        try:
            d = datetime.strptime(expiration, "%Y-%m-%d")
            days_out = (d - datetime.now()).days
        except:
            error = "Invalid expiration date."
            return render_template("index.html", error=error)

        # Target delta
        target_delta = RISK_TO_DELTA.get(risk, 0.20)

        # Option chain
        chain = get_tradier_chain(ticker, expiration)
        if not chain:
            error = "Unable to pull option data."
            return render_template("index.html", error=error)

        # Find strike closest to target delta
        best = None
        best_diff = 999

        for opt in chain:
            if opt.get("option_type") != "call":
                continue

            delta = safe_float(opt.get("greeks", {}).get("delta"))
            if delta is None:
                continue

            diff = abs(delta - target_delta)
            if diff < best_diff:
                best_diff = diff
                best = opt

        if not best:
            error = "No valid call options found."
            return render_template("index.html", error=error)

        strike = best.get("strike")
        premium = safe_float(best.get("bid"))

        # -----------------------------
        # IV HANDLING (with estimated flag)
        # -----------------------------
        iv_raw = safe_float(best.get("greeks", {}).get("iv"))
        iv_estimated = False

        if iv_raw is None:
            iv_raw = black_scholes_iv(price, strike, days_out, premium)
            iv_estimated = True

        if iv_raw is None:
            iv = "N/A"
        else:
            iv = f"{iv_raw:.3f}"

        # Assignment probability (approx)
        assign_prob = round(best.get("greeks", {}).get("delta", 0) * 100, 1)

        result = {
            "ticker": ticker,
            "stock_price": price,
            "expiration": expiration,
            "days_out": days_out,
            "risk_label": risk.replace("_", " ").title(),
            "strike": strike,
            "iv": iv,
            "iv_estimated": iv_estimated,
            "assign_prob": assign_prob,
            "premium": premium
        }

    return render_template("index.html",
                           expirations=expirations,
                           result=result,
                           error=error)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
