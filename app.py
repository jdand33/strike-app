from flask import Flask, render_template, request, jsonify
import yfinance as yf
import math

app = Flask(__name__)

# -----------------------------
# Black-Scholes Delta Functions
# -----------------------------
def norm_cdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))

def call_delta(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    return norm_cdf(d1)

def find_strike(S, T, r, sigma, target_delta):
    best_K = S
    best_diff = 1
    K = S
    while K <= S * 1.2:
        d = call_delta(S, K, T, r, sigma)
        diff = abs(d - target_delta)
        if diff < best_diff:
            best_diff = diff
            best_K = K
        K += 0.5
    return round(best_K, 2)

# -----------------------------
# API: Live Price
# -----------------------------
@app.route("/price")
def price():
    symbol = request.args.get("ticker", "MCD").upper()
    ticker = yf.Ticker(symbol)
    data = ticker.history(period="1d")
    current_price = round(float(data["Close"].iloc[-1]), 2)
    return jsonify({"price": current_price})

# -----------------------------
# API: Live IV
# -----------------------------
@app.route("/iv")
def iv():
    symbol = request.args.get("ticker", "MCD").upper()
    ticker = yf.Ticker(symbol)

    expirations = ticker.options
    if not expirations:
        return jsonify({"iv": None})

    nearest_exp = expirations[0]
    chain = ticker.option_chain(nearest_exp)
    calls = chain.calls

    hist = ticker.history(period="1d")
    spot = float(hist["Close"].iloc[-1])

    calls["diff"] = (calls["strike"] - spot).abs()
    atm = calls.sort_values("diff").iloc[0]

    iv_value = float(atm["impliedVolatility"])
    return jsonify({"iv": round(iv_value, 4)})

# -----------------------------
# Main Page
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def index():
    result = None

    if request.method == "POST":
        ticker = request.form["ticker"].upper()

        # LIVE PRICE
        t = yf.Ticker(ticker)
        data = t.history(period="1d")
        price = float(data["Close"].iloc[-1])

        # LIVE IV
        expirations = t.options
        nearest_exp = expirations[0]
        chain = t.option_chain(nearest_exp)
        calls = chain.calls
        calls["diff"] = (calls["strike"] - price).abs()
        atm = calls.sort_values("diff").iloc[0]
        iv = float(atm["impliedVolatility"])

        days = int(request.form["days"])
        risk = request.form["risk"]

        T = days / 365
        r = 0.02

        target = {"low": 0.10, "moderate": 0.20, "high": 0.30}[risk]
        strike = find_strike(price, T, r, iv, target)

        result = {
            "ticker": ticker,
            "target_delta": target,
            "strike": strike,
            "risk": risk.capitalize(),
            "iv": round(iv, 3)
        }

    return render_template("index.html", result=result)

if __name__ == "__main__":
    app.run(host="0.0.0.0", debug=True)