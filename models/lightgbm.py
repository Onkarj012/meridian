"""Rung-2 LightGBM training helper."""

def train_lightgbm_model(X=None, y=None, **kwargs):
    import lightgbm as lgb
    model = lgb.LGBMClassifier(**kwargs)
    if X is not None and y is not None:
        model.fit(X, y)
    return model
