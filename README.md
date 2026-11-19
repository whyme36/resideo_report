# Resideo Report

Narzędzie do pobierania i analizowania dokumentów finansowych z portalu Resideo.
Składa się z trzech etapów: pobrania PDF-ów, przetworzenia ich na jeden plik CSV
i wygenerowania interaktywnego raportu HTML.

## Czego potrzebujesz

- Windows,
- Python 3.10 lub nowszy,
- konto do portalu Resideo,
- dostęp do dokumentów finansowych na portalu.

## Instalacja

W katalogu projektu utwórz środowisko wirtualne i zainstaluj zależności:

```powershell
py -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

Zależności projektu:

- `playwright` - automatyzacja przeglądarki Chromium i pobieranie dokumentów,
- `pypdf` - odczyt tekstu z plików PDF.

## Dane logowania

Najwygodniejszym sposobem lokalnego przechowywania danych jest plik `.env`.
Skopiuj szablon i uzupełnij swoje dane:

```powershell
Copy-Item .env.example .env
notepad .env
```

Przykładowa zawartość:

```dotenv
RESIDEO_LOGIN=twoj_login
RESIDEO_PASSWORD=twoje_haslo
RESIDEO_HEADLESS=true
RESIDEO_LOGIN_DELAY_MS=0
```

Plik `.env` jest ignorowany przez Git. Skrypt automatycznie wczytuje go przed
logowaniem. Istniejące zmienne środowiskowe systemu mają pierwszeństwo przed
wartościami z `.env`.

Możesz też przekazać dane bezpośrednio przez zmienne środowiskowe:

```powershell
$env:RESIDEO_LOGIN = "twoj_login"
$env:RESIDEO_PASSWORD = "twoje_haslo"
```

Jeśli zmienna nie jest ustawiona, skrypt poprosi o wpisanie odpowiedniej wartości.
Jeśli preferujesz, aby okno przeglądarki było widoczne, ustaw:

```powershell
$env:RESIDEO_HEADLESS = "false"
```

Domyślnie pobieranie działa w trybie headless. Nie zapisuj danych logowania do
plików projektu ani do systemu kontroli wersji.

## Użycie

### 1. Pobierz dokumenty PDF

```powershell
python pobierz_pdf.py
```

Skrypt loguje się do portalu, przechodzi do sekcji `Finanse`, przegląda wszystkie
dostępne konta i pobiera dokumenty z historii finansowej. Pliki są zapisywane do:

```text
pobrane_pdf/
```

Nazwy pobieranych plików mają format `YYYY_MM_Typ.pdf`. Dokumenty, które już
istnieją w katalogu, są pomijane.

Log działania znajduje się w `diagnostyka_resideo/pobieranie.log`.

Opcjonalnie można uruchomić tryb diagnostyczny i zapisać zrzuty ekranu:

```powershell
python pobierz_pdf.py -dev
```

Zrzuty są przechowywane w `diagnostyka_resideo/`.

### 2. Przetwórz PDF-y do CSV

```powershell
python wyciagnij_dane_pdf.py
```

Domyślnie skrypt:

- czyta wszystkie PDF-y z `pobrane_pdf/`,
- wyodrębnia dane i pozycje wydatków,
- usuwa powtórzone dokumenty i wiersze,
- zapisuje wynik do `zestawienie_wydatkow.csv`.

Można wskazać własny folder wejściowy i plik wyjściowy:

```powershell
python wyciagnij_dane_pdf.py --input-dir "sciezka_do_pdf" --output "wynik.csv"
```

Plik CSV używa separatora `;` i zakodowania UTF-8 z BOM. Zawiera m.in. typ i numer
dokumentu, miesiąc, termin płatności, nazwę usługi, jednostkę, ilość, cenę i wartość.

### 3. Wygeneruj raport HTML

```powershell
python raport_wydatkow.py
```

Domyślnie skrypt czyta `zestawienie_wydatkow.csv` i zapisuje samodzielny raport:

```text
raport_wydatkow.html
```

Raport zawiera podsumowania naliczeń i rozliczeń, wykresy oraz filtrowaną tabelę
dokumentów i nie wymaga serwera ani zewnętrznych bibliotek po wygenerowaniu.

Można wskazać własne pliki wejściowy i wyjściowy:

```powershell
python raport_wydatkow.py --input "zestawienie_wydatkow.csv" --output "raport.html"
```

## Pełny przepływ

```powershell
python pobierz_pdf.py
python wyciagnij_dane_pdf.py
python raport_wydatkow.py
```

Po wykonaniu poleceń otwórz plik `raport_wydatkow.html` w przeglądarce.

## Struktura projektu

```text
pobierz_pdf.py                 pobieranie PDF-ów przez Playwright
wyciagnij_dane_pdf.py          odczyt PDF-ów i zapis danych do CSV
raport_wydatkow.py             generowanie raportu HTML
requirements.txt               lista zależności Python
pobrane_pdf/                   pobrane dokumenty PDF
diagnostyka_resideo/           logi i opcjonalne zrzuty ekranu
zestawienie_wydatkow.csv       wynik ekstrakcji
raport_wydatkow.html           wygenerowany raport
```