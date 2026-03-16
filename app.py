import os
import requests
import yfinance as yf
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
    try:
        # Simple fallback IV estimate
        t = days / 365
        if t <= 0:
            return None
        approx_iv = (premium / price) / math.sqrt(t)
        return approx_iv
    except:
        return None


# -----------------------------
# ROUTES
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

        # Stock price
        stock = yf.Ticker(ticker)
        price = safe_float(stock.fast_info.get("last_price"))

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
        # IV HANDLING (rounded to 3 decimals)
        # -----------------------------
        iv_raw = safe_float(best.get("greeks", {}).get("iv"))

        if iv_raw is None:
            iv_raw = black_scholes_iv(price, strike, days_out, premium)

        if iv_raw is None:
            iv = "N/A"
        else:
            iv = f"{iv_raw:.3f}"   # <--- ROUND TO 3 DECIMALS

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
            "assign_prob": assign_prob,
            "premium": premium
        }

    return render_template("index.html",
                           expirations=expirations,
                           result=result,
                           error=error)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
