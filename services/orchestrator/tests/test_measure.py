from civicpulse.measure import estimate_depth, relative_size, size_class, text_depth
from civicpulse.sam.service import Detection


def det(box, score=0.9, mask=None):
    return Detection("x", score, [0, 0, 0, 0], box_px=box, mask_local=mask)


def test_width_share_is_measured_in_the_same_rows():
    out = relative_size(det([40, 50, 60, 70]), [det([0, 0, 100, 100])], 200, 100)
    # 20 px of a 100 px roadway; the road starts at the left edge only, so it is not clipped.
    assert out["width_share"] == 0.2 and out["size_class"] == "medium" and not out["clipped"]
    assert out["area_share"] == round(400 / 10000, 3)


def test_road_wider_than_the_photo_is_clipped():
    out = relative_size(det([40, 50, 60, 70]), [det([0, 0, 100, 100])], 100, 100)
    assert out["clipped"]


def test_road_mask_with_a_hole_where_the_pothole_is():
    # SAM's road mask leaves the pothole out: a 2x2 raster with the right column empty.
    road = det([0, 0, 100, 100], mask=(bytes([1, 0, 1, 0]), 2, 2))
    out = relative_size(det([50, 0, 100, 100]), [road], 200, 100)
    assert out["width_share"] == 0.5 and out["size_class"] == "large"


def test_no_road_and_weak_road():
    assert relative_size(det([1, 1, 5, 5]), [], 10, 10) == {"status": "no_road"}
    assert relative_size(det([1, 1, 5, 5]), [det([0, 0, 10, 10], score=0.2)], 10, 10) == {"status": "no_road"}


def test_size_classes():
    assert [size_class(x) for x in (0.05, 0.2, 0.5)] == ["small", "medium", "large"]


def test_text_depth():
    assert text_depth("Un bache muy PROFUNDO") == 1
    assert text_depth("es un hueco pequeño") == -1
    assert text_depth("deep but small") == 0
    assert text_depth("profundidad desconocida") == 0  # whole words only


def test_depth_levels():
    assert estimate_depth(None, "nada") == {"level": None, "cues": [], "text": 0}
    assert estimate_depth(None, "bache hondo")["level"] == "medium"
    assert estimate_depth({"surface_only": True}, "")["level"] == "low"
    assert estimate_depth({}, "")["level"] == "low"  # the model looked and saw no cue
    assert estimate_depth({"water_inside": True}, "")["level"] == "medium"
    assert estimate_depth({"base_layer_visible": True, "water_inside": True}, "")["level"] == "high"
