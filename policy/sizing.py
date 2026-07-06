"""Fixed-fractional position sizing with conviction and ADV caps."""

def size_position(capital, price, *, risk_fraction=0.01, confidence=1.0, adv=None, max_adv_participation=0.05, **kwargs):
    budget = float(capital) * float(risk_fraction) * max(float(confidence), 0.0)
    quantity = int(budget / float(price)) if price else 0
    if adv is not None and price:
        quantity = min(quantity, int(float(adv) * float(max_adv_participation) / float(price)))
    return {"quantity": max(quantity, 0), "notional": max(quantity, 0) * float(price or 0)}
