"""Config registry helpers."""

def register_config_grid(registry=None, configs=None, **metadata):
    item = {"configs": list(configs or []), "config_count": len(list(configs or [])), **metadata}
    if registry is not None:
        registry.append(item)
    return item
