# models/currency.py

class Currency(object):
    def __init__(self, code, precision=2):
        code = code.strip().upper()
        if not code or len(code) > 12:
            raise ValueError("Некорректный код валюты (пусто или слишком длинный)")
        # For custom currency
        if not code.isalnum():
            raise ValueError("Код валюты должен состоять только из букв/цифр (A-Z, 0-9)")
        if precision < 0 or precision > 8:
            raise ValueError("precision должен быть в диапазоне 0..8")

        self.code = code
        self.precision = precision

    def __repr__(self):
        return "Currency(code=%r precision=%r)" % (
            self.code, self.precision
        )
