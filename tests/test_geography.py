from jobfilter.geography import enrich_location


def test_resolves_german_state_and_distance_from_heilbronn():
    local = enrich_location("Heilbronn, Neckar")
    assert local.federal_state == "Baden-Württemberg"
    assert local.distance_from_heilbronn_km == 0

    munich = enrich_location("München")
    assert munich.federal_state == "Bayern"
    assert 200 < munich.distance_from_heilbronn_km < 300

    hamburg = enrich_location("Hamburg")
    assert hamburg.federal_state == "Hamburg"
    assert 450 < hamburg.distance_from_heilbronn_km < 550


def test_uses_nearest_resolved_city_for_multi_location_jobs():
    result = enrich_location("Berlin; Stuttgart")
    assert result.federal_state == "Berlin; Baden-Württemberg"
    assert 30 < result.distance_from_heilbronn_km < 70


def test_generic_location_is_left_unresolved():
    result = enrich_location("verschiedene Arbeitsorte")
    assert result.federal_state == ""
    assert result.distance_from_heilbronn_km is None
