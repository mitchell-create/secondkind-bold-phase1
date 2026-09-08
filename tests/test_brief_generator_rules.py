"""Copy-rule inputs computed by strategy/brief_generator.py.

The first live run of the copy-rules change crashed with
`'Brand' object has no attribute 'social_proof'`: brand.yaml carries that key
as extra data but the Pydantic model has no such field. These tests pin the
helper so a Brand without the attribute is handled.
"""

from __future__ import annotations

from models.brand import Brand
from models.product import Product
from strategy.brief_generator import _competitor_names, _social_proof_available


def _product(**overrides) -> Product:
    fields = dict(
        name="Membership",
        description="One set, twice a week.",
        benefits=["measured strength"],
    )
    fields.update(overrides)
    return Product(**fields)


def test_social_proof_unavailable_when_brand_lacks_field_and_product_is_empty():
    brand = Brand(name="OnCore Longevity")
    assert not hasattr(brand, "social_proof")
    assert _social_proof_available(brand, _product()) is False
    assert _social_proof_available(brand, _product(social_proof=["", "  "])) is False


def test_social_proof_available_from_product():
    brand = Brand(name="OnCore Longevity")
    assert _social_proof_available(brand, _product(social_proof=["4.8 stars, 212 reviews"])) is True


def test_social_proof_available_from_brand_extra_data():
    class BrandWithProof(Brand):
        social_proof: list[str] = []

    brand = BrandWithProof(name="Zoka Coffee", social_proof=["Seattle's favourite roaster"])
    assert _social_proof_available(brand, _product()) is True


def test_competitor_names_empty_without_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _competitor_names("nobody") == []
