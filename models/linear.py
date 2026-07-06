"""Rung-1 linear model helpers."""

def train_linear_model(X=None, y=None, *, kind="logistic", **kwargs):
    if kind == "ridge":
        from sklearn.linear_model import Ridge
        model = Ridge(**kwargs)
    else:
        from sklearn.linear_model import LogisticRegression
        model = LogisticRegression(max_iter=1000, **kwargs)
    if X is not None and y is not None:
        model.fit(X, y)
    return model
