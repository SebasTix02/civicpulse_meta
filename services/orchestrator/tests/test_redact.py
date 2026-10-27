from civicpulse.redact import redact


def test_masks_ecuador_personal_data():
    text, found = redact("Llamar a 0987815213 o +593 2 123 4567, cédula 1804567890, placa TBC-1234, mail a@b.ec")
    assert found
    for leaked in ("0987815213", "123 4567", "1804567890", "TBC-1234", "a@b.ec"):
        assert leaked not in text
    assert text.count("[phone]") == 2


def test_keeps_normal_text():
    text, found = redact("Bache enorme en la Av. Cevallos y Montalvo, línea 10, a las 7 am")
    assert not found and "línea 10" in text
