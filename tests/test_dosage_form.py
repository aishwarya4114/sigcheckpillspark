from app.checks import norm_form


def test_plural_and_abbreviated_forms_match_catalogue_spelling():
    assert norm_form("Capsules") == "capsule"
    assert norm_form("tabs") == "tablet"
    assert norm_form("tablet") == "tablet"
    assert norm_form("injection") == "injection"
    assert norm_form(None) == ""
