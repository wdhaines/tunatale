def assert_json_roundtrip(obj) -> None:
    """Assert obj survives to_json → from_json preserving all fields.

    Relies on dataclass __eq__ for field-by-field comparison.
    Only suitable for dataclass models with to_json/from_json methods.
    """
    cls = type(obj)
    restored = cls.from_json(obj.to_json())
    assert restored == obj
