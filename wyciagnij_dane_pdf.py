"""Wyciąga pozycje finansowe z PDF-ów i zapisuje je do pliku CSV."""

from __future__ import annotations

import argparse
import csv
import logging
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "pobrane_pdf"
DEFAULT_OUTPUT = BASE_DIR / "zestawienie_wydatkow.csv"
LOGGER = logging.getLogger(__name__)

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

AMOUNT = r"-?(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)[,.]\d+"
ROW_PATTERN = re.compile(
    rf"^\s*(?P<prefix>.*?)\s+(?P<quantity>{AMOUNT})"
    rf"\s+(?P<unit_price>{AMOUNT})\s+(?P<value>{AMOUNT})\s*$"
)
DATE_PATTERN = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SERVICE_PERIOD_PATTERN = re.compile(
    r"\bza\s+(\d{4}-\d{2}-\d{2}\s*[-–]\s*\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)

CSV_COLUMNS = [
    "typ_dokumentu",
    "rok_miesiac",
    "termin_zaplaty",
    "numer_dokumentu",
    "data_dokumentu",
    "numer_pozycji",
    "nazwa_uslugi",
    "okres_uslugi",
    "jm",
    "ilosc",
    "cena_jednostkowa",
    "wartosc",
    "plik_pdf",
]


@dataclass(frozen=True)
class DocumentInfo:
    path: Path
    year_month: str
    kind_key: str
    kind_label: str


def identify_document(path: Path) -> DocumentInfo:
    match = re.fullmatch(
        r"(?P<year>\d{4})_(?P<month>0[1-9]|1[0-2])_"
        r"(?P<kind>Naliczenie_korekta|Naliczenie|Rozliczenie_mediów|Rozliczenie)",
        path.stem,
        re.IGNORECASE,
    )
    if not match:
        raise ValueError(
            f"Nazwa PDF-u nie pasuje do formatu YYYY_MM_Typ: {path.name}"
        )

    kind_key = match.group("kind").casefold()
    kind_labels = {
        "naliczenie": "Naliczenie",
        "naliczenie_korekta": "Naliczenie - korekta",
        "rozliczenie_mediów": "Rozliczenie mediów",
        "rozliczenie": "Rozliczenie",
    }
    return DocumentInfo(
        path=path,
        year_month=f"{match.group('year')}_{match.group('month')}",
        kind_key=kind_key,
        kind_label=kind_labels[kind_key],
    )


def normalize_number(value: str) -> str:
    """Usuwa separatory tysięcy, pozostawiając oryginalny separator dziesiętny."""
    return re.sub(r"[ \u00a0]", "", value.strip())


def extract_payment_deadline(text: str) -> str:
    match = re.search(
        r"Termin\s+zapłaty\s*:\s*(\d{4}-\d{2}-\d{2})",
        text,
        re.IGNORECASE,
    )
    return match.group(1) if match else ""


def extract_document_metadata(text: str) -> tuple[str, str]:
    flattened = re.sub(r"\s+", " ", text)
    match = re.search(
        r"(?:Faktura|Nota\s+księgowa(?:\s+korygująca)?)\s+"
        r"([A-Z0-9-]+(?:/[A-Z0-9-]+)?)"
        r"(?:\s*\([^)]*\))?\s+z\s+dnia\s+"
        r"(\d{4}-\d{2}-\d{2})",
        flattened,
        re.IGNORECASE,
    )
    return (match.group(1), match.group(2)) if match else ("", "")


def extract_table_text(info: DocumentInfo, pages: list[str]) -> str:
    if info.kind_key in {"rozliczenie_mediów", "rozliczenie"}:
        for page_text in pages:
            attachment = re.search(r"Załącznik\s+1\b", page_text, re.IGNORECASE)
            if attachment:
                following_attachment = re.search(
                    r"Załącznik\s+2\b",
                    page_text[attachment.end() :],
                    re.IGNORECASE,
                )
                end = (
                    attachment.end() + following_attachment.start()
                    if following_attachment
                    else len(page_text)
                )
                return page_text[attachment.end() : end]
        raise ValueError(
            f"Nie znaleziono tabeli „Załącznik 1” w pliku {info.path.name}."
        )

    if info.kind_key == "naliczenie_korekta":
        for page_text in pages:
            correction = re.search(r"Po\s+korekcie\b", page_text, re.IGNORECASE)
            if correction:
                table_text = page_text[correction.end() :]
                end = re.search(
                    r"Razem\s*\(po\s+korekcie\)|Różnica\s+zobowiązania",
                    table_text,
                    re.IGNORECASE,
                )
                return table_text[: end.start()] if end else table_text
        raise ValueError(
            f"Nie znaleziono sekcji „Po korekcie” w pliku {info.path.name}."
        )

    if not pages:
        raise ValueError(f"PDF nie ma stron: {info.path.name}")
    return pages[0]


def parse_table_rows(table_text: str) -> list[dict[str, str]]:
    lines = table_text.splitlines()
    rows: list[dict[str, str]] = []
    current_row: dict[str, str] | None = None

    def save_current() -> None:
        if current_row is not None:
            rows.append(current_row)

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        row_match = ROW_PATTERN.match(line)
        if row_match:
            prefix = row_match.group("prefix").strip()
            item_match = re.match(
                r"(?P<number>\d{1,3})\s+(?P<service_and_unit>.+)",
                prefix,
            )
            if not item_match:
                continue
            service_unit = item_match.group("service_and_unit").rstrip()
            unit_match = re.match(
                r"(?P<service>.+?)\s+(?P<unit>[\w.²³]+)$",
                service_unit,
            )
            if not unit_match:
                continue

            save_current()
            current_row = {
                "numer_pozycji": item_match.group("number"),
                "nazwa_uslugi": re.sub(r"\s+", " ", unit_match.group("service")).strip(),
                "jm": unit_match.group("unit"),
                "ilosc": normalize_number(row_match.group("quantity")),
                "cena_jednostkowa": normalize_number(
                    row_match.group("unit_price")
                ),
                "wartosc": normalize_number(row_match.group("value")),
                "okres_uslugi": "",
            }
            continue

        if current_row is None:
            continue
        if re.match(r"^(Razem|Do zapłaty|Słownie|Strona|Załącznik)\b", stripped, re.I):
            save_current()
            current_row = None
            continue

        period = SERVICE_PERIOD_PATTERN.search(stripped)
        if period:
            current_row["okres_uslugi"] = re.sub(r"\s+", " ", period.group(1))
        elif not re.match(r"^(Po korekcie)", stripped, re.I):
            current_row["nazwa_uslugi"] = re.sub(
                r"\s+", " ", f"{current_row['nazwa_uslugi']} {stripped}"
            ).strip()

    save_current()
    return rows


def extract_document(info: DocumentInfo) -> list[dict[str, str]]:
    reader = PdfReader(info.path)
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    full_text = "\n".join(pages)
    deadline = extract_payment_deadline(full_text)
    document_number, document_date = extract_document_metadata(full_text)
    table_text = extract_table_text(info, pages)
    rows = parse_table_rows(table_text)
    if not rows:
        raise ValueError(
            f"Nie znaleziono pozycji tabeli w pliku {info.path.name}."
        )

    return [
        {
            "typ_dokumentu": info.kind_label,
            "rok_miesiac": info.year_month,
            "termin_zaplaty": deadline,
            "numer_dokumentu": document_number,
            "data_dokumentu": document_date,
            **row,
            "plik_pdf": info.path.name,
        }
        for row in rows
    ]


def deduplicate_documents(infos: list[DocumentInfo]) -> list[DocumentInfo]:
    correction_months = {
        info.year_month
        for info in infos
        if info.kind_key == "naliczenie_korekta"
    }
    selected = [
        info
        for info in infos
        if not (
            info.kind_key == "naliczenie"
            and info.year_month in correction_months
        )
    ]
    skipped = len(infos) - len(selected)
    if skipped:
        LOGGER.info(
            "Pominięto %d zwykłych naliczeń zastąpionych korektą za ten sam miesiąc.",
            skipped,
        )
    return selected


def row_key(row: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        row[column]
        for column in CSV_COLUMNS
        if column != "plik_pdf"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Zbiera pozycje z PDF-ów Resideo do jednego pliku CSV."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="folder z PDF-ami (domyślnie: pobrane_pdf)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="ścieżka pliku CSV (domyślnie: zestawienie_wydatkow.csv)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.input_dir.is_dir():
        raise FileNotFoundError(f"Nie znaleziono folderu PDF: {args.input_dir}")

    pdf_paths = sorted(args.input_dir.glob("*.pdf"))
    if not pdf_paths:
        raise FileNotFoundError(f"Folder nie zawiera plików PDF: {args.input_dir}")

    infos = deduplicate_documents(
        [identify_document(path) for path in pdf_paths]
    )
    rows: list[dict[str, str]] = []
    seen_rows: set[tuple[str, ...]] = set()
    for info in infos:
        LOGGER.info("Czytam %s", info.path.name)
        for row in extract_document(info):
            key = row_key(row)
            if key in seen_rows:
                LOGGER.info(
                    "Pomijam zduplikowany wiersz: %s / %s",
                    row["rok_miesiac"],
                    row["nazwa_uslugi"],
                )
                continue
            seen_rows.add(key)
            rows.append(row)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)

    LOGGER.info(
        "Zapisano %d unikalnych wierszy z %d PDF-ów do %s.",
        len(rows),
        len(infos),
        args.output,
    )


if __name__ == "__main__":
    main()
