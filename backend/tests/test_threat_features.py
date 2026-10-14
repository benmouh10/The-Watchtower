"""Hermetic tests for the shared_core feature vector and telemetry contract."""
import math

import numpy as np

from shared_core.features import (
    FEATURE_NAMES,
    extract_feature_vector,
    feature_vector_from_dict,
    shannon_entropy,
)
from shared_core.telemetry import TELEMETRY_VERSION, TelemetryEvent


class TestFeatureVectorContract:
    def test_vector_length_matches_names(self):
        assert len(FEATURE_NAMES) == 15
        vector = extract_feature_vector(TelemetryEvent())
        assert vector.shape == (15,)
        assert vector.dtype == np.float64

    def test_all_features_are_floats(self):
        assert all(isinstance(v, float) for v in extract_feature_vector(TelemetryEvent()))

    def test_order_and_values_are_deterministic(self):
        event = TelemetryEvent(
            request_rate=4.0, payload_bytes=5_000, header_count=11,
            header_entropy=3.8, user_agent_present=True, user_agent_length=90,
            content_type_is_json=True, has_authorization=True, has_forwarded=True,
            path_depth=3, is_api_path=True, time_of_day=0.25,
            endpoint_sensitivity=0.5, method="POST", status=200,
        )
        a = extract_feature_vector(event)
        b = extract_feature_vector(event)
        np.testing.assert_array_equal(a, b)
        assert a[0] == 4.0
        assert a[1] == math.log1p(5000)
        assert a[14] == 1.0  # POST → method_num

    def test_request_rate_is_capped_at_floor_zero(self):
        v = extract_feature_vector(TelemetryEvent(request_rate=-5.0))
        assert v[0] == 0.0


class TestClippingAndBounding:
    def test_bad_observation_never_raises(self):
        vector = extract_feature_vector(
            TelemetryEvent(
                payload_bytes=-1, header_count=-1, header_entropy=10**6,
                user_agent_length=-4, path_depth=-9, request_rate=float("inf"),
            )
        )
        assert np.isfinite(vector).all()

    def test_oversized_upload_is_capped(self):
        v = extract_feature_vector(TelemetryEvent(payload_bytes=10**9))
        assert v[1] <= math.log1p(10_000_000)

    def test_deep_path_is_capped(self):
        v = extract_feature_vector(TelemetryEvent(path_depth=10**6))
        assert v[9] == 12.0

    def test_header_entropy_is_capped(self):
        v = extract_feature_vector(TelemetryEvent(header_entropy=500.0))
        assert v[3] == 10.0


class TestTelemetryWireFormat:
    def test_from_dict_ignores_unknown_keys(self):
        event = TelemetryEvent.from_dict(
            {"ip": "1.2.3.4", "method": "POST", "future_field": "ignored", "nonsense": 42}
        )
        assert event.ip == "1.2.3.4"
        assert event.method == "POST"
        assert hasattr(event, "future_field") is False

    def test_missing_fields_use_defaults(self):
        assert TelemetryEvent.from_dict({}).ip == "unknown"

    def test_roundtrip_to_dict(self):
        payload = TelemetryEvent(ip="10.0.0.2", method="DELETE", path="/admin/x").to_dict()
        assert TelemetryEvent.from_dict(payload) == TelemetryEvent.from_dict(payload)

    def test_version_tag(self):
        assert TelemetryEvent().version == TELEMETRY_VERSION

    def test_feature_vector_from_raw_dict(self):
        vector = feature_vector_from_dict({"method": "PUT", "request_rate": 2})
        assert vector.shape == (15,)
        assert vector[14] == 2.0  # PUT → method_num


class TestShannonEntropy:
    def test_empty_string_is_zero(self):
        assert shannon_entropy("") == 0.0

    def test_repeated_char_is_zero(self):
        assert shannon_entropy("aaaa") == 0.0

    def test_two_symbols_uniform_is_one_bit(self):
        assert shannon_entropy("abababab") == 1.0

    def test_four_symbols_uniform_is_two_bits(self):
        assert shannon_entropy("abcdabcdabcdabcd") == 2.0