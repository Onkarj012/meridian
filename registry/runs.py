"""Run registry helpers."""

def register_run(registry=None, **run):
    item = dict(run)
    if registry is not None:
        registry.append(item)
    return item
