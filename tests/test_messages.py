"""Testy kształtu komunikatu (plugin/py_modules/sdsync/messages.py).

Sedno: `msg()` jest wołane ze ścieżek, na których właśnie coś padło — wyjątek
w konstruowaniu komunikatu o błędzie zabrałby informację o błędzie. Więc nie rzuca
NIGDY, a niezgodność parametrów musi być WIDOCZNA w zdaniu, nie cicha.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "py_modules"))

from sdsync.messages import CODES, fields, msg


def test_zwraca_kod_parametry_i_zlozone_zdanie():
    out = msg("restore_failed", title="Animal Well")
    assert out["code"] == "restore_failed"
    assert out["params"] == {"title": "Animal Well"}
    assert "Animal Well" in out["message"], out


def test_szablon_bez_dziur():
    """Każdy szablon w CODES musi dać się złożyć z pól, które sam wymienia —
    inaczej pierwszy prawdziwy wywołanie zostawi w zdaniu `{title}`."""
    for code, template in CODES.items():
        out = msg(code, **{name: "X" for name in fields(template)})
        assert "{" not in out["message"], (code, out["message"])
        assert "BAD PARAMS" not in out["message"], (code, out["message"])


def test_brakujacy_parametr_jest_widoczny_a_nie_rzuca():
    out = msg("restore_failed")  # szablon chce {title}
    assert out["code"] == "restore_failed"
    assert "BAD PARAMS" in out["message"], out
    assert "title" in out["message"], "trzeba powiedzieć, KTÓREGO pola brakuje"


def test_zly_parametr_nie_udaje_gotowego_zdania():
    out = msg("restore_failed", tytul="Animal Well")
    assert "BAD PARAMS" in out["message"], out
    assert out["params"] == {"tytul": "Animal Well"}, "parametry zostają nietknięte"


def test_nieznany_kod_nie_rzuca_i_jest_widoczny():
    out = msg("kod_ktorego_nie_ma", cokolwiek=1)
    assert out["code"] == "kod_ktorego_nie_ma"
    assert "unknown message code" in out["message"], out
    assert out["params"] == {"cokolwiek": 1}


def test_nadmiarowy_parametr_jest_dozwolony():
    """Frontend może dostać pole, którego angielski szablon nie używa (np. liczbę
    do wyboru formy liczby mnogiej) — to nie jest błąd."""
    out = msg("restore_failed", title="Hades", count=3)
    assert "BAD PARAMS" not in out["message"], out
    assert out["params"]["count"] == 3


def test_kody_sa_snake_case_bez_polskich_znakow():
    """Kod jedzie przez JSON, plik logu i klucz w katalogu tłumaczeń."""
    for code in CODES:
        assert code == code.lower() and code.replace("_", "").isalnum(), code


def test_format_raising_param_nie_rzuca():
    """Parametr, którego __format__() rzuca (np. TypeError), musi być obsłużony.
    Wyjątek tutaj zabrałby informację o awarii, która to wywołanie spowodowała."""
    class BadFormat:
        def __format__(self, spec):
            raise TypeError("boom")
    
    out = msg("restore_failed", title=BadFormat())
    assert out["code"] == "restore_failed"
    assert "BAD PARAMS" in out["message"], out
    assert "TypeError" in out["message"], "typ wyjątku musi być widoczny"
    assert out["params"]["title"].__class__.__name__ == "BadFormat"


def test_parametr_o_nazwie_code_nie_rozwala_wywolania():
    """`params` przychodzą z rejestru i z wyjątków, więc nazwa pola nie może
    kolidować z nazwą argumentu — kolizja rzucałaby PRZED ciałem funkcji,
    czyli poza zasięgiem `try`. Dlatego `code` musi być pozycyjnym parametrem
    tylko (`def msg(code: str, /, **params)`)."""
    out = msg("restore_failed", **{"code": "cudzy", "title": "Hades"})
    assert out["code"] == "restore_failed", "kod komunikatu bije parametr"
    assert out["params"]["code"] == "cudzy"
    assert out["params"]["title"] == "Hades"
