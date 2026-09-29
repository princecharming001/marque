"""Head top (hair included) above the landmark box: measurement and per-take ratio. Keyless, offline."""

from __future__ import annotations

import numpy as np
import pytest

from studio.perception.index import FaceBox, HeadTop, Visual
from studio.perception.visual import head_top_ratio, measure_head_top


def _selfie(hair_top: int, *, bg=(232, 230, 225), hair=(28, 22, 20), skin=(196, 150, 120), w=540, h=960):
    """A synthetic selfie: light wall, dark hair mass from ``hair_top`` over a skin-toned face ellipse."""
    import cv2

    img = np.zeros((h, w, 3), np.uint8)
    img[:] = bg
    rng = np.random.default_rng(3)
    img = np.clip(img.astype(np.int16) + rng.integers(-6, 7, img.shape), 0, 255).astype(np.uint8)
    cx = w // 2
    face_top, chin = 420, 760  # landmark box (upper forehead to chin)
    cv2.ellipse(img, (cx, (hair_top + 600) // 2), (175, (600 - hair_top) // 2), 0, 0, 360, hair, -1)  # top = hair_top
    cv2.ellipse(img, (cx, (face_top + chin) // 2), (140, (chin - face_top) // 2), 0, 0, 360, skin, -1)
    cv2.rectangle(img, (cx - 170, chin), (cx + 170, h), (90, 30, 40), -1)  # shirt
    box = ((cx - 140) / w, face_top / h, (cx + 140) / w, chin / h)
    return img, box


@pytest.mark.parametrize("hair_top", [250, 330])
def test_measure_head_top_finds_the_hair_not_the_forehead(hair_top: int):
    img, box = _selfie(hair_top)
    m = measure_head_top(img, box)
    assert m is not None
    y, touches = m
    assert not touches
    assert abs(y * 960 - hair_top) < 25, (y * 960, hair_top)  # the hair top, well above the landmark box (420)


def test_measure_head_top_reports_a_head_cut_by_the_frame_top():
    img, box = _selfie(-60)
    y, touches = measure_head_top(img, box)
    assert touches and y < 0.01


def test_measure_head_top_degenerate_box_returns_none():
    img, _ = _selfie(300)
    assert measure_head_top(img, (0.5, 0.5, 0.5, 0.5)) is None


def test_head_top_ratio_is_robust_and_leans_to_more_hair():
    ks = [0.44, 0.45, 0.43, 0.47, 0.5, 0.42, 0.46, 0.2, 0.21, 0.66]  # blur loses hair twice; one merge
    tops = [HeadTop(t_us=i * 500_000, y=0.2, k=k) for i, k in enumerate(ks)]
    r = head_top_ratio(tops)
    assert 0.46 <= r <= 0.55
    assert head_top_ratio(tops[:3]) is None  # too few to trust: callers use the prior
    # a head that keeps running off the frame top: its lower bounds win
    cut = [HeadTop(t_us=i, y=0.0, k=0.9, touches_top=True) for i in range(6)]
    assert head_top_ratio(tops + cut) >= 0.9


def test_visual_head_top_y_uses_the_ratio_or_the_prior():
    box = FaceBox(x=0.3, y=0.4, w=0.4, h=0.3)
    assert Visual(head_top_ratio=0.5).head_top_y(box) == pytest.approx(0.25)
    assert Visual().head_top_y(box, prior=0.55) == pytest.approx(0.4 - 0.165)
