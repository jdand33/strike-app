import os
import requests
from flask import Flask, request, render_template

app = Flask(__name__)

TRADIER_TOKEN = os.getenv("TRADIER_TOKEN")
TRADIER_BASE = "https://api.tradier.com/v1"


def tradier_headers():
    return {
        "Authorization": f"Bearer {TRADIER_TOKEN}",
        "Accept": "application/json"
    }


# -----------------------------
# Fetch stock price
# -----------------------------
def get_stock_price(symbol):
    url = f"{TRADIER_BASE}/markets/quotes"
    params = {"symbols": symbol}

    r = requests.get(url, headers=tradier_headers(), params=params)
    if r.status_code != 200:
        return None

    data = r.json()
    quote = data.get("quotes", {}).get("quote", {})
    return quote.get("last")


# -----------------------------
# Fetch expirations
# -----------------------------
def get_expirations(symbol):
    url = f"{TRADIER_BASE}/markets/options/expirations"
    params = {"symbol": symbol, "includeAllRoots": "true", "strikes": "false"}

    r = requests.get(url, headers=tradier_headers(), params=params)
    if r.status_code != 200:
        return []

    data = r.json()
    return data.get("expirations", {}).get("date", [])


# -----------------------------
# Fetch chain for one expiration
# -----------------------------
def get_chain(symbol, expiration):
    url = f"{TRADIER_BASE}/markets/options/chains"
    params = {"symbol": symbol, "expiration": expiration}

    r = requests.get(url, headers=tradier_headers(), params=params)
    return r.json() if r.status_code == 200 else {}


# -----------------------------
# Delta targets
# -----------------------------
DELTA_TARGETS = {
    "very_safe": 0.10,
    "safe": 0.15,
    "moderate": 0.20,
    "aggressive": 0.25,
    "very_aggressive": 0.30,
}


# -----------------------------
# MAIN ROUTE
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    expirations = None
    result = None
    error = None

    if request.method == "POST":
        action = request.form.get("action")
        ticker = request.form.get("ticker", "").upper().strip()

        if not ticker:
            error = "Ticker required."
            return render_template("index.html", error=error)

        # -------------------------
        # LOAD EXPIRATIONS
        # -------------------------
        if action == "load":
            expirations = get_expirations(ticker)
            if not expirations:
                error = "Could not load expirations."
            return render_template("index.html",
                                   expirations=expirations,
                                   error=error)

        # -------------------------
        # CALCULATE COVERED CALL
        # -------------------------
        if action == "calculate":
            expiration = request.form.get("expiration")
            risk = request.form.get("risk")

            expirations = get_expirations(ticker)

            if not expiration:
                error = "Select an expiration."
                return render_template("index.html",
                                       expirations=expirations,
                                       error=error)

            stock_price = get_stock_price(ticker)
            if not stock_price:
                error = "Could not fetch stock price."
                return render_template("index.html",
                                       expirations=expirations,
                                       error=error)

            chain_raw = get_chain(ticker, expiration)
            chain = chain_raw.get("options", {}).get("option", [])

            if not chain:
                error = "Could not load option chain."
                return render_template("index.html",
                                       expirations=expirations,
                                       error=error)

            # Filter calls only
            calls = [c for c in chain if c.get("option_type") == "call"]

            # Remove calls with missing greeks or missing delta
            calls = [
                c for c in calls
                if c.get("greeks") and c["greeks"].get("delta") is not None
            ]

            if not calls:
                error = "No valid call options found for this expiration."
                return render_template("index.html",
                                       expirations=expirations,
                                       error=error)

            # Pick strike closest to delta target
            target_delta = DELTA_TARGETS.get(risk, 0.20)

            best = min(
                calls,
                key=lambda c: abs(c["greeks"]["delta"] - target_delta)
            )

            strike = best.get("strike")
            delta = best["greeks"]["delta"]
            iv = best["greeks"].get("mid_iv")
            premium = best.get("bid")

            # Assignment probability (approx)
            assign_prob = round(abs(delta) * 100, 1)

            # Days out
            from datetime import datetime
            exp_clean = expiration.split(":")[0]
            d0 = datetime.now()
            d1 = datetime.strptime(exp_clean, "%Y-%m-%d")
            days_out = (d1 - d0).days

            result = {
                "ticker": ticker,
                "stock_price": round(stock_price, 2),
                "expiration": exp_clean,
                "days_out": days_out,
                "risk_label": risk.replace("_", " ").title(),
                "strike": strike,
                "iv": iv,
                "iv_estimated": iv is None,
                "assign_prob": assign_prob,
                "premium": premium,
            }

            return render_template("index.html",
                                   expirations=expirations,
                                   result=result)

    return render_template("index.html")


# -----------------------------
# DEBUG PAGE
# -----------------------------
@app.route("/debug")
def debug():
    symbol = request.args.get("symbol", "AAPL")
    expiration = request.args.get("expiration")

    debug_data = {}

    # Quote
    url_quote = f"{TRADIER_BASE}/markets/quotes"
    r_quote = requests.get(url_quote, headers=tradier_headers(), params={"symbols": symbol})
    debug_data["quote_status"] = r_quote.status_code
    debug_data["quote_raw"] = r_quote.text

    # Expirations
    url_exp = f"{TRADIER_BASE}/markets/options/expirations"
    r_exp = requests.get(url_exp, headers=tradier_headers(),
                         params={"symbol": symbol, "includeAllRoots": "true", "strikes": "false"})
    debug_data["expirations_status"] = r_exp.status_code
    debug_data["expirations_raw"] = r_exp.text

    # Chain
    if expiration:
        url_chain = f"{TRADIER_BASE}/markets/options/chains"
        r_chain = requests.get(url_chain, headers=tradier_headers(),
                               params={"symbol": symbol, "expiration": expiration})
        debug_data["chain_status"] = r_chain.status_code
        debug_data["chain_raw"] = r_chain.text
    else:
        debug_data["chain_status"] = "No expiration provided"
        debug_data["chain_raw"] = "Add ?expiration=YYYY-MM-DD"

    return debug_data


@app.route("/health")
def health():
    return {"status": "ok", "has_token": bool(TRADIER_TOKEN)}


if __name__ == "__main__":
    app.run(debug=True)
