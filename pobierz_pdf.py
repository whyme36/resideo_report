"""Pobiera PDF-y z historii finansowej portalu Resideo."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import re
import unicodedata
from pathlib import Path
from urllib.parse import urljoin

from dotenv import load_dotenv
from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


LOGIN_URL = "https://portal.resideo-zarzadzanie.pl/content/InetObsKontr/login"
OUTPUT_DIR = Path(__file__).resolve().parent / "pobrane_pdf"
DIAGNOSTICS_DIR = Path(__file__).resolve().parent / "diagnostyka_resideo"
LOG_PATH = DIAGNOSTICS_DIR / "pobieranie.log"
MONTH_SELECTOR = "app-wiersz-miesiaca-finansowego"
LOGGER = logging.getLogger(__name__)
SESSION_LIMIT_TEXT = "Przekroczono dopuszczalną liczbę sesji użytkownika"

MONTH_NUMBERS = {
    "styczeń": "01",
    "stycznia": "01",
    "luty": "02",
    "lutego": "02",
    "marzec": "03",
    "marca": "03",
    "kwiecień": "04",
    "kwietnia": "04",
    "maj": "05",
    "maja": "05",
    "czerwiec": "06",
    "czerwca": "06",
    "lipiec": "07",
    "lipca": "07",
    "sierpień": "08",
    "sierpnia": "08",
    "wrzesień": "09",
    "września": "09",
    "październik": "10",
    "października": "10",
    "listopad": "11",
    "listopada": "11",
    "grudzień": "12",
    "grudnia": "12",
}


def safe_filename(name: str) -> str:
    """Zwraca pojedynczą, bezpieczną nazwę pliku PDF."""
    filename = Path(name.replace("\\", "/")).name
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
    if not filename:
        filename = "dokument.pdf"
    if Path(filename).suffix.lower() != ".pdf":
        filename = f"{Path(filename).stem}.pdf"
    return filename


def unique_path(directory: Path, filename: str) -> Path:
    """Wybiera wolną nazwę, nie nadpisując wcześniej pobranego pliku."""
    path = directory / safe_filename(filename)
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    number = 2
    while (directory / f"{stem} ({number}){suffix}").exists():
        number += 1
    return directory / f"{stem} ({number}){suffix}"


def normalize_document_title(title: str) -> str:
    """Zamienia tytuł dokumentu na stabilną nazwę bazową do porównań."""
    title = unicodedata.normalize("NFKC", Path(title).stem)
    title = re.sub(r"\s+\(\d+\)$", "", title)

    canonical = re.match(
        r"^(\d{4})_(\d{2})_(Naliczenie(?:_korekta)?|Rozliczenie(?:_mediów)?)$",
        title,
        re.I,
    )
    if canonical:
        return "_".join(part.casefold() for part in canonical.groups())

    normalized = re.sub(r"[\u2010-\u2015\u2212]", "-", title)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    kind_match = re.match(r"^(Naliczenie|Rozliczenie)\b", normalized, re.I)
    if not kind_match:
        return normalized.casefold()

    prefix = kind_match.group(1).casefold()
    if prefix == "naliczenie":
        kind = (
            "Naliczenie_korekta"
            if re.search(r"\bkorekta\b", normalized, re.I)
            else "Naliczenie"
        )
    elif re.match(r"^Rozliczenie\s+mediów\b", normalized, re.I):
        kind = "Rozliczenie_mediów"
    else:
        kind = "Rozliczenie"

    month_match = re.search(
        r"\b(" + "|".join(MONTH_NUMBERS) + r")\s+(\d{4})\b",
        normalized,
        re.I,
    )
    if not month_match:
        return normalized.casefold()
    month_name, year = month_match.groups()
    month_number = MONTH_NUMBERS[month_name.casefold()]
    return f"{year}_{month_number}_{kind}".casefold()


def document_filename(title: str, month_id: str | None = None) -> str:
    """Buduje nazwę YYYY_MM_Typ.pdf na podstawie tytułu dokumentu."""
    key = normalize_document_title(title)
    canonical = re.match(
        r"^\d{4}_\d{2}_(naliczenie(?:_korekta)?|rozliczenie(?:_mediów)?)$",
        key,
        re.I,
    )
    if canonical:
        year, month = key.split("_", maxsplit=2)[:2]
        kind_names = {
            "naliczenie": "Naliczenie",
            "naliczenie_korekta": "Naliczenie_korekta",
            "rozliczenie": "Rozliczenie",
            "rozliczenie_mediów": "Rozliczenie_mediów",
        }
        kind = kind_names[canonical.group(1).casefold()]
        return f"{year}_{month}_{kind}.pdf"

    normalized = unicodedata.normalize("NFKC", title).strip()
    if re.match(r"^Naliczenie\b", normalized, re.I):
        kind = (
            "Naliczenie_korekta"
            if re.search(r"\bkorekta\b", normalized, re.I)
            else "Naliczenie"
        )
    elif re.match(r"^Rozliczenie\s+mediów\b", normalized, re.I):
        kind = "Rozliczenie_mediów"
    elif re.match(r"^Rozliczenie\b", normalized, re.I):
        kind = "Rozliczenie"
    else:
        raise ValueError(f"Nieobsługiwany tytuł dokumentu: {title!r}")

    canonical_key = key
    date_match = re.match(r"^(\d{4})_(\d{2})_", canonical_key)
    if date_match:
        year, month = date_match.groups()
    elif month_id and re.fullmatch(r"\d{4}-\d{1,2}", month_id):
        year, month = month_id.split("-")
        month = month.zfill(2)
    else:
        raise ValueError(
            f"Nie można ustalić roku i miesiąca dla dokumentu {title!r}."
        )
    return f"{year}_{month}_{kind}.pdf"


def document_key(title: str, month_id: str) -> str:
    """Tworzy klucz porównania, także dla tytułów bez własnej daty."""
    return normalize_document_title(Path(document_filename(title, month_id)).stem)


def existing_document_titles(directory: Path) -> set[str]:
    """Zwraca klucze tytułów PDF-ów znajdujących się już w folderze."""
    return {
        normalize_document_title(path.stem)
        for path in directory.glob("*.pdf")
        if path.is_file()
    }



def save_dev_screenshot(page: Page, name: str, dev: bool) -> None:
    """Zapisuje zrzut ekranu wyłącznie w trybie developerskim."""
    if not dev:
        return
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    screenshot_path = DIAGNOSTICS_DIR / f"{name}.png"
    page.screenshot(path=str(screenshot_path), full_page=True)
    LOGGER.info("Zrzut developerski: %s", screenshot_path)


def is_session_limit_page(page: Page) -> bool:
    """Rozpoznaje stronę informującą o przekroczeniu limitu sesji urządzeń."""
    if "/usun-sesje" in page.url.casefold():
        return True
    return page.get_by_text(
        re.compile(re.escape(SESSION_LIMIT_TEXT), re.I)
    ).count() > 0


def raise_session_limit_error(page: Page, dev: bool) -> None:
    """Przerywa pracę z instrukcją usunięcia nadmiarowych sesji w profilu."""
    if is_session_limit_page(page):
        save_dev_screenshot(page, "limit_aktywnych_sesji", dev)
        message = (
            "Portal odrzucił dostęp, bo przekroczono limit aktywnych urządzeń/sesji. "
            "Zaloguj się do portalu w zwykłej przeglądarce, otwórz Profil i usuń "
            "nieużywane sesje w sekcji „Usuń sesję”, a następnie uruchom skrypt ponownie."
        )
        LOGGER.error(message)
        raise RuntimeError(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pobiera PDF-y z historii finansowej portalu Resideo."
    )
    parser.add_argument(
        "-dev",
        action="store_true",
        help="zapisuje zrzuty ekranu diagnostyczne",
    )
    return parser.parse_args()


def load_all_months(page: Page) -> None:
    """Rozwija listę historii, aż portal przestanie udostępniać starsze miesiące."""
    show_older = page.get_by_role("button", name=re.compile("Pokaż wcześniejsze", re.I))
    while show_older.count() and show_older.first.is_visible():
        previous_count = page.locator(MONTH_SELECTOR).count()
        LOGGER.info("Ładowanie wcześniejszych miesięcy (obecnie: %d).", previous_count)
        show_older.first.click()
        try:
            page.wait_for_function(
                "(count) => document.querySelectorAll"
                "('app-wiersz-miesiaca-finansowego').length > count",
                arg=previous_count,
                timeout=15_000,
            )
            LOGGER.info(
                "Załadowano starsze miesiące (łącznie: %d).",
                page.locator(MONTH_SELECTOR).count(),
            )
        except PlaywrightTimeoutError as error:
            raise RuntimeError(
                "Portal nie załadował starszych miesięcy po naciśnięciu "
                "„Pokaż wcześniejsze”."
            ) from error


def collect_month_documents(
    page: Page, account_id: str, month_id: str
) -> list[tuple[int, str]]:
    """Zbiera tytuły i indeksy wierszy płatności z rozwiniętej historii."""
    month_component = page.locator(
        f'{MONTH_SELECTOR}:has(.lista-item[id="{month_id}"])'
    )
    payment_rows = month_component.locator("a.list-item:has(span.header)")
    documents: list[tuple[int, str]] = []
    for row_index in range(payment_rows.count()):
        row = payment_rows.nth(row_index)
        if not row.is_visible():
            continue
        title = row.locator("span.header").inner_text().strip()
        if re.match(r"^(Naliczenie|Rozliczenie)\b", title, re.I):
            documents.append((row_index, title))

    LOGGER.info(
        "Miesiąc %s, konto %s: znaleziono %d wierszy dokumentów.",
        month_id,
        account_id,
        len(documents),
    )
    for row_index, title in documents:
        LOGGER.info("Wiersz %d: %s", row_index, title)
    return documents


def open_payment_document(
    page: Page,
    account_url: str,
    account_id: str,
    month_id: str,
    row_index: int,
    expected_title: str,
) -> str:
    """Ponownie otwiera historię i klika konkretny wiersz płatności."""
    LOGGER.info(
        "Wracam do historii konta %s, aby otworzyć wiersz „%s”.",
        account_id,
        expected_title,
    )
    page.goto(account_url, wait_until="domcontentloaded")
    page.get_by_role("heading", name="Historia finansowa").wait_for(
        state="visible", timeout=30_000
    )
    load_all_months(page)
    month = page.locator(f'.lista-item[id="{month_id}"]')
    month.wait_for(state="visible", timeout=30_000)
    month.click()

    month_component = page.locator(
        f'{MONTH_SELECTOR}:has(.lista-item[id="{month_id}"])'
    )
    payment_rows = month_component.locator("a.list-item:has(span.header)")
    row = payment_rows.nth(row_index)
    row.wait_for(state="visible", timeout=30_000)
    actual_title = row.locator("span.header").inner_text().strip()
    if actual_title != expected_title:
        raise RuntimeError(
            f"Wiersz płatności nr {row_index} zmienił się po ponownym otwarciu "
            f"miesiąca {month_id}: oczekiwano „{expected_title}”, "
            f"znaleziono „{actual_title}”."
        )

    LOGGER.info(
        "Klikam wiersz płatności %d „%s” na stronie %s.",
        row_index,
        actual_title,
        page.url,
    )
    row.click()
    try:
        page.wait_for_url(re.compile(r".*/dokument/[^/]+/.*"), timeout=30_000)
    except PlaywrightTimeoutError as error:
        raise RuntimeError(
            f"Kliknięcie wiersza „{expected_title}” nie otworzyło strony "
            f"dokumentu. Pozostały adres: {page.url}"
        ) from error
    LOGGER.info("Otworzono stronę dokumentu: %s", page.url)
    return page.url


def document_title(page: Page) -> str:
    """Odczytuje tytuł z bezpośredniego tekstu h1, bez adresu z elementu span."""
    title = page.locator("h1.header-title").evaluate(
        """element => Array.from(element.childNodes)
            .filter(node => node.nodeType === Node.TEXT_NODE)
            .map(node => node.textContent)
            .join(' ')
            .replace(/\\s+/g, ' ')
            .trim()"""
    )
    if not title:
        raise RuntimeError(
            f"Nagłówek h1.header-title na stronie {page.url} nie zawiera tytułu."
        )
    return title


def download_document(
    page: Page,
    document_url: str,
    account_id: str,
    month_id: str,
    output_dir: Path,
) -> str:
    """Otwiera stronę dokumentu i pobiera PDF pod nazwą z jej nagłówka."""
    LOGGER.info("Otwieram stronę dokumentu: %s", document_url)
    page.goto(document_url, wait_until="domcontentloaded")
    title_locator = page.locator("h1.header-title")
    title_locator.wait_for(state="visible", timeout=30_000)
    title = document_title(page)
    LOGGER.info("Tytuł dokumentu z h1.header-title: %s", title)
    filename = document_filename(title, month_id)
    output_path = output_dir / filename
    if output_path.exists():
        LOGGER.info("Pomijam istniejący plik: %s", output_path)
        return title

    icons = page.locator("i.ikona-pobierz")
    visible_icons = [
        icons.nth(index)
        for index in range(icons.count())
        if icons.nth(index).is_visible()
    ]
    LOGGER.info(
        "Na stronie dokumentu %s znaleziono %d widocznych ikon i.ikona-pobierz.",
        page.url,
        len(visible_icons),
    )
    if not visible_icons:
        raise RuntimeError(
            f"Nie znaleziono ikony pobierania i.ikona-pobierz na stronie "
            f"dokumentu {page.url}."
        )

    for icon in visible_icons:
        try:
            with page.expect_download(timeout=20_000) as download_info:
                icon.click()
        except PlaywrightTimeoutError as error:
            raise RuntimeError(
                f"Kliknięcie ikony pobierania nie rozpoczęło pobierania "
                f"dokumentu „{title}” ({page.url})."
            ) from error

        download = download_info.value
        path = unique_path(output_dir, filename)
        LOGGER.info(
            "Zapisuję dokument „%s” (konto %s) jako: %s",
            title,
            account_id,
            path,
        )
        download.save_as(path)
        with path.open("rb") as pdf_file:
            is_pdf = pdf_file.read(5) == b"%PDF-"
        if not is_pdf:
            path.unlink()
            raise RuntimeError(
                f"Pobrany plik dokumentu „{title}” nie jest poprawnym PDF-em."
            )
        LOGGER.info("Zapisano poprawny PDF: %s", path)
        return title

    raise RuntimeError(f"Nie pobrano żadnego pliku dla dokumentu „{title}”.")


def download_account_history(
    page: Page,
    account_href: str,
    output_dir: Path,
    downloaded_titles: set[str],
) -> int:
    account_url = urljoin(LOGIN_URL, account_href)
    account_id = account_href.rstrip("/").rsplit("/", 1)[-1]
    LOGGER.info("Przechodzę do historii konta %s: %s", account_id, account_url)
    page.goto(account_url, wait_until="domcontentloaded")
    page.get_by_role("heading", name="Historia finansowa").wait_for(
        state="visible", timeout=30_000
    )
    LOGGER.info("Historia finansowa konta %s została wyświetlona.", account_id)
    load_all_months(page)

    months = page.locator(MONTH_SELECTOR)
    month_ids = [
        months.nth(index).locator(".lista-item").get_attribute("id")
        for index in range(months.count())
    ]
    if not month_ids:
        print(f"Brak miesięcy historii dla konta {account_id}.")
        return 0

    documents: list[tuple[str, int, str]] = []
    for index, month_id in enumerate(month_ids):
        if not month_id:
            raise RuntimeError(
                f"Wpis historii nr {index + 1} nie ma identyfikatora miesiąca."
            )
        month = months.nth(index)
        LOGGER.info(
            "Otwieram miesiąc %d/%d: %s (konto %s).",
            index + 1,
            len(month_ids),
            month_id,
            account_id,
        )
        month.locator(".lista-item").click()
        page.wait_for_timeout(300)
        for row_index, title in collect_month_documents(page, account_id, month_id):
            documents.append((month_id, row_index, title))

    if not documents:
        raise RuntimeError(
            f"Nie znaleziono wierszy „Naliczenie za” ani "
            f"„Rozliczenie mediów za” w historii konta "
            f"{account_id} ({account_url})."
        )

    LOGGER.info(
        "Konto %s: zebrano %d wierszy dokumentów.",
        account_id,
        len(documents),
    )
    downloaded_count = 0
    for index, (month_id, row_index, title) in enumerate(documents, start=1):
        LOGGER.info(
            "Dokument %d/%d „%s” z miesiąca %s (konto %s).",
            index,
            len(documents),
            title,
            month_id,
            account_id,
        )
        title_key = document_key(title, month_id)
        if title_key in downloaded_titles:
            LOGGER.info(
                "Pomijam „%s” — PDF o takim tytule już istnieje w %s.",
                title,
                output_dir,
            )
            continue

        document_url = open_payment_document(
            page,
            account_url,
            account_id,
            month_id,
            row_index,
            title,
        )
        saved_title = download_document(
            page, document_url, account_id, month_id, output_dir
        )
        downloaded_titles.add(title_key)
        downloaded_titles.add(document_key(saved_title, month_id))
        downloaded_count += 1

    return downloaded_count


def main() -> None:
    dev = parse_args().dev
    load_dotenv(Path(__file__).resolve().parent / ".env")
    username = os.environ.get("RESIDEO_LOGIN") or input("Login do portalu: ")
    password = os.environ.get("RESIDEO_PASSWORD") or getpass.getpass( "Hasło do portalu: ")
    if not username or not password:
        raise ValueError("Login i hasło nie mogą być puste.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DIAGNOSTICS_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
        force=True,
    )
    LOGGER.info("Uruchamiam pobieranie PDF-ów z portalu Resideo.")
    LOGGER.info("Log działania: %s", LOG_PATH)
    if dev:
        LOGGER.info("Tryb developerski aktywny; zrzuty będą zapisywane.")
    downloaded_titles = existing_document_titles(OUTPUT_DIR)
    LOGGER.info(
        "Folder zawiera %d istniejących plików PDF do porównania.",
        len(downloaded_titles),
    )
    headless = os.environ.get("RESIDEO_HEADLESS", "true").lower() not in {
        "0",
        "false",
        "no",
    }
    try:
        login_delay_ms = int(os.environ.get("RESIDEO_LOGIN_DELAY_MS", "0"))
    except ValueError as error:
        raise ValueError("RESIDEO_LOGIN_DELAY_MS musi być liczbą całkowitą.") from error
    if login_delay_ms < 0:
        raise ValueError("RESIDEO_LOGIN_DELAY_MS nie może być ujemne.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        try:
            context = browser.new_context(accept_downloads=True)
            page = context.new_page()
            LOGGER.info("Otwieram stronę logowania: %s", LOGIN_URL)
            page.goto(LOGIN_URL, wait_until="domcontentloaded")
            LOGGER.info("Czekam 2 sekundy na załadowanie strony logowania.")
            page.wait_for_timeout(2_000)
            username_input = page.locator('input[name="nazwaUzytkownika"]')
            password_input = page.locator('input[type="password"]')
            login_button = page.get_by_role(
                "button", name=re.compile(r"^\s*Zaloguj\s*$", re.I)
            )
            LOGGER.info("Formularz logowania jest gotowy; wpisuję dane.")
            username_input.fill(username)
            password_input.fill(password)
            save_dev_screenshot(page, "po_wpisaniu_danych", dev)
            page.wait_for_timeout(login_delay_ms)
            LOGGER.info(
                "Klikam przycisk „Zaloguj” (opóźnienie: %d ms).",
                login_delay_ms,
            )
            login_button.click()

            finance_link = page.locator('a[href$="/finanse"]')
            try:
                finance_link.wait_for(state="visible", timeout=30_000)
            except PlaywrightTimeoutError as error:
                raise_session_limit_error(page, dev)
                save_dev_screenshot(page, "blad_logowania", dev)
                LOGGER.error("Logowanie nie powiodło się. Adres: %s", page.url)
                raise RuntimeError(
                    "Po kliknięciu przycisku „Zaloguj” nie pojawił się link "
                    "„Finanse”. Sprawdź dane logowania lub komunikat portalu."
                ) from error

            finance_href = finance_link.get_attribute("href")
            if not finance_href:
                raise RuntimeError(
                    "Po zalogowaniu link „Finanse” nie ma atrybutu href."
                )
            finance_url = urljoin(LOGIN_URL, finance_href)
            LOGGER.info(
                "Logowanie powiodło się; przechodzę bezpośrednio do Finanse: %s",
                finance_url,
            )
            response = page.goto(finance_url, wait_until="domcontentloaded")
            if response and response.status >= 400:
                raise RuntimeError(
                    f"Strona Finanse zwróciła HTTP {response.status}: {finance_url}"
                )
            raise_session_limit_error(page, dev)
            accounts = page.locator("app-konto-finansowe a")
            try:
                accounts.first.wait_for(state="visible", timeout=30_000)
            except PlaywrightTimeoutError as error:
                raise_session_limit_error(page, dev)
                save_dev_screenshot(page, "blad_strony_finanse", dev)
                LOGGER.error(
                    "Nie pojawiły się konta finansowe. Końcowy adres: %s",
                    page.url,
                )
                raise RuntimeError(
                    "Po przejściu do Finanse nie wyświetliła się lista kont. "
                    f"Portal otworzył: {page.url}."
                ) from error

            LOGGER.info("Strona Finanse jest otwarta: %s", page.url)
            account_hrefs = accounts.evaluate_all(
                "(links) => links.map((link) => link.getAttribute('href'))"
                ".filter(Boolean)"
            )
            if not account_hrefs:
                raise RuntimeError("Na stronie Finanse nie znaleziono żadnego konta.")
            LOGGER.info("Znaleziono %d kont.", len(account_hrefs))

            total = 0
            for account_href in account_hrefs:
                total += download_account_history(
                    page, account_href, OUTPUT_DIR, downloaded_titles
                )
            LOGGER.info("Pobieranie zakończone. Łącznie pobrano %d plików.", total)
        except Exception:
            current_url = page.url if "page" in locals() else "(strona nie została otwarta)"
            LOGGER.exception("Przerwano działanie skryptu na adresie %s.", current_url)
            raise
        finally:
            browser.close()

    if total == 0:
        LOGGER.warning(
            "Nie znaleziono widocznych ikon pobierania. "
            "Historia mogła nie zawierać dokumentów PDF."
        )
    else:
        LOGGER.info("Gotowe. Pobrano %d plików do: %s", total, OUTPUT_DIR)


if __name__ == "__main__":
    main()
