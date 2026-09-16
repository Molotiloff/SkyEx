from services.wallets.command_parser import WalletCommandParser


def test_archive_command_is_not_treated_as_currency_change() -> None:
    assert not WalletCommandParser.looks_like_currency_change(
        "/сообщение Предстоящие тест"
    )
    assert not WalletCommandParser.looks_like_currency_change(
        "/сообщение@skyex_bot Предстоящие тест"
    )
    assert not WalletCommandParser.looks_like_currency_change(
        "/сообщение SkyEx | Данил"
    )


def test_textual_bot_command_is_not_treated_as_currency_change() -> None:
    assert not WalletCommandParser.looks_like_currency_change("/неизвестная любой текст")


def test_real_currency_command_still_matches() -> None:
    assert WalletCommandParser.looks_like_currency_change("/USD 100")
    assert WalletCommandParser.looks_like_currency_change("/руб 1000 клиент")
    assert WalletCommandParser.looks_like_currency_change("/EUR -250")
    assert WalletCommandParser.looks_like_currency_change("/USDT (100+20)*2")
