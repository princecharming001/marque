"""The standard tier's plan stage (``docs/standard-tier-plan.md`` §2, ``docs/standard-tier-interfaces.md``).

One structured-output model call replaces the Director's 50-150-call loop: code builds a text **digest** of the
Take Index (:mod:`studio.plan.digest`), the planner model answers with a :class:`~studio.plan.decision.PlanDecision`
that names IDs only (:mod:`studio.plan.decision` holds the models, the compact schema string and the guards that
reject an unusable answer), :mod:`studio.plan.planner` makes that one call (``plan()``, its cached prefix in
:mod:`studio.plan.prompt`), and :mod:`studio.plan.code_plan` turns the decision (or none: the pure-code plan) into
ops. Submodules import lazily; only ``planner.plan`` calls a model, so importing this package is cheap and keyless.
"""

__all__ = ["code_plan", "decision", "digest", "planner", "prompt"]
