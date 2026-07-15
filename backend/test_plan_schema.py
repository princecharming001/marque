"""Regression guard for the structured-outputs schema-rejection class.

The native Structured Outputs API rejects array-length bounds other than 0/1
(minItems) and maxItems entirely with a hard 400. One such keyword buried in
EDIT_PLAN_JSON_SCHEMA silently 400'd the whole edit-plan authoring call, so EVERY
edit degraded to the untailored safe-default cut with no log trace. These tests
keep any structured-output schema from re-introducing an unsupported keyword.
"""
import main
import prompts


def _walk(node):
    """Yield every dict node in a JSON-schema tree."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for x in node:
            yield from _walk(x)


def _bad_keywords(schema):
    bad = []
    for n in _walk(schema):
        if "maxItems" in n:
            bad.append(("maxItems", n["maxItems"]))
        if "minItems" in n and n["minItems"] not in (0, 1):
            bad.append(("minItems", n["minItems"]))
    return bad


def test_edit_plan_schema_has_no_unsupported_array_bounds():
    # The raw schema must already be clean (the real fix), not merely rescued by
    # the sanitizer.
    assert _bad_keywords(prompts.EDIT_PLAN_JSON_SCHEMA) == []


def test_all_structured_output_schemas_are_clean():
    # Every module-level *_JSON_SCHEMA / *_SCHEMA that could be handed to
    # anthropic_json must be API-legal.
    for mod in (prompts, main):
        for name in dir(mod):
            if not (name.endswith("_JSON_SCHEMA") or name.endswith("_SCHEMA")):
                continue
            val = getattr(mod, name)
            if isinstance(val, dict):
                assert _bad_keywords(val) == [], f"{mod.__name__}.{name} has unsupported array bounds"


def test_sanitizer_strips_unsupported_bounds():
    dirty = {
        "type": "object",
        "properties": {
            "r": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
            "keep": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "empty_ok": {"type": "array", "items": {"type": "string"}, "minItems": 0},
        },
    }
    clean = main._sanitize_schema(dirty)
    r = clean["properties"]["r"]
    assert "maxItems" not in r and "minItems" not in r
    # minItems 0/1 are legal → preserved
    assert clean["properties"]["keep"]["minItems"] == 1
    assert clean["properties"]["empty_ok"]["minItems"] == 0
    # items survive the strip
    assert r["items"] == {"type": "integer"}
