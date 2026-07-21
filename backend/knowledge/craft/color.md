# Craft — Color for Talking Heads (protect the skin)

Skin hue is fixed by blood, not melanin: face pixels sit ON the vectorscope
skin-tone line (melanin moves brightness/saturation, not hue). Exposure band
for faces: 40-70 IRE; saturation 20-50%; never clip facial highlights.

Order of operations (colorist workflow): balance (black/white points,
neutralize casts) -> skin (vectorscope) -> look LAST. A look never rides on
an uncorrected image.

Creator content wants the AUTHENTIC grade: restraint beats cinema. Teal-orange
belongs to backgrounds/negative space with skin protected via secondary —
pushed skin reads instantly fake and viewer connection "hinges on the realism
of characters' appearances." Our theme grades (finishing@0.55 default) encode
this; intensity above ~0.7 on people-first content is a violation of register.

```yaml
rules:
  - id: col.skin_protected
    principle: "Face region hue stays on the skin-tone line; looks apply to background, not faces"
    source: "Vectorscope I-line convention; DIY Photography/Raposo grading guides"
    enforce: critic
  - id: col.grade_restraint
    principle: "People-first content: look intensity moderate, no crushed blacks/bleached highlights on faces"
    source: "Creator-content grading consensus"
    enforce: knob
```
