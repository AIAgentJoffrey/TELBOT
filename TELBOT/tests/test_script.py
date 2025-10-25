from bot import make_script


def test_make_script():
    script = make_script("Кафе", "млади професионалисти", "забавен", 20)
    assert "Кратка видео реклама (20s)" in script
    assert "Кафе" in script
    assert "млади професионалисти" in script
    assert "забавен" in script