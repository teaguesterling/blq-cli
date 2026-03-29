"""Tests for the annotator system: Annotation dataclass."""
from __future__ import annotations

import json

import pytest

from blq.ext.annotator import Annotation


class TestAnnotation:
    def test_to_dict_roundtrip(self):
        ann = Annotation(annotator="mypy", type="diagnostic", display="inline", data={"key": "val"})
        d = ann.to_dict()
        restored = Annotation.from_dict(d)
        assert restored == ann

    def test_display_inline_valid(self):
        Annotation(annotator="a", type="t", display="inline", data={})

    def test_display_detail_valid(self):
        Annotation(annotator="a", type="t", display="detail", data={})

    def test_display_hidden_valid(self):
        Annotation(annotator="a", type="t", display="hidden", data={})

    def test_display_invalid_raises(self):
        with pytest.raises(ValueError, match="display"):
            Annotation(annotator="a", type="t", display="invalid", data={})

    def test_from_dict(self):
        d = {"annotator": "src", "type": "source", "display": "detail", "data": {"line": 42}}
        ann = Annotation.from_dict(d)
        assert ann.annotator == "src"
        assert ann.data == {"line": 42}

    def test_json_roundtrip(self):
        ann = Annotation(annotator="p", type="provenance", display="hidden", data={"sha": "abc123"})
        s = json.dumps(ann.to_dict())
        restored = Annotation.from_dict(json.loads(s))
        assert restored == ann
