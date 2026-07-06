"""Sensitivity-suite helpers."""

def run_sensitivity_suite(base_result=None, scenarios=None, evaluator=None, **kwargs):
    output = {"base": base_result or {}, "scenarios": {}}
    for name, scenario in (scenarios or {}).items():
        output["scenarios"][name] = evaluator(scenario) if evaluator else scenario
    return output
