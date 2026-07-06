"""Model registry helpers."""

def register_model_version(registry=None, **model):
    item = dict(model)
    if registry is not None:
        registry.append(item)
    return item
