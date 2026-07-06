"""Reproducible training loop helpers."""

def run_training_loop(train_fn=None, *, seeds=(42,), **kwargs):
    results = []
    for seed in seeds:
        results.append(train_fn(seed=seed, **kwargs) if train_fn else {"seed": seed, "status": "no_train_fn"})
    return {"runs": results, "seeds": list(seeds)}
