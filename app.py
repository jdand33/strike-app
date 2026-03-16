@app.route("/", methods=["GET", "POST"])
def index():
    result = None
    error = None
    expirations = []

    if request.method == "POST":
        action = request.form.get("action")
        ticker = request.form.get("ticker", "").upper().strip()
        expiration = request.form.get("expiration", "").strip()
        risk_key = request.form.get("risk", "").strip()

        if not validate_ticker(ticker):
            return render_template("index.html",
                                   error=f"'{ticker}' is not a valid ticker with options.",
                                   expirations=[])

        expirations = get_expirations(ticker)
        if not expirations:
            return render_template("index.html",
                                   error="No expirations available.",
                                   expirations=[])

        if action == "load":
            return render_template("index.html", expirations=expirations)

        if expiration not in expirations:
            return render_template("index.html",
                                   error=f"{expiration} is not a valid expiration.",
                                   expirations=expirations)

        if risk_key not in RISK_TO_DELTA:
            return render_template("index.html",
                                   error="Invalid risk level.",
                                   expirations=expirations)

        target_delta = RISK_TO_DELTA[risk_key]

        result = get_closest_delta_strike(ticker, expiration, target_delta)
        if result is None:
            return render_template("index.html",
                                   error="Unable to pull option data.",
                                   expirations=expirations)

    return render_template("index.html",
                           result=result,
                           error=error,
                           expirations=expirations)
