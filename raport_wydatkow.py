"""Generate a self-contained, interactive HTML report from the expenses CSV."""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "zestawienie_wydatkow.csv"
DEFAULT_OUTPUT = BASE_DIR / "raport_wydatkow.html"
REQUIRED_FIELDS = {
    "typ_dokumentu",
    "rok_miesiac",
    "numer_dokumentu",
    "data_dokumentu",
    "termin_zaplaty",
    "numer_pozycji",
    "nazwa_uslugi",
    "jm",
    "ilosc",
    "cena_jednostkowa",
    "wartosc",
    "plik_pdf",
}


def parse_number(value: str, *, field: str, row_number: int) -> float:
    normalized = value.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(Decimal(normalized))
    except InvalidOperation as error:
        raise ValueError(
            f"Nieprawidłowa liczba w wierszu {row_number}, kolumnie {field}: {value!r}"
        ) from error


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def categorize(service: str) -> str:
    name = normalize_text(service)
    if any(word in name for word in ("fundusz", "zaliczka")):
        return "Fundusze i zaliczki"
    if any(
        word in name
        for word in (
            "woda",
            "zuzycia",
            "cw",
            "co -",
            "energia",
            "ogrzew",
            "mediow",
        )
    ):
        return "Media"
    if any(word in name for word in ("smieci", "nieczystosci", "odpadow")):
        return "Odpady"
    if "ubezpiec" in name:
        return "Ubezpieczenie"
    if any(word in name for word in ("zarzad", "techniczna")):
        return "Zarządzanie"
    if any(word in name for word in ("przeglad", "czystosci", "zielone")):
        return "Utrzymanie nieruchomości"
    return "Pozostałe koszty"


def document_group(document_type: str) -> str:
    normalized = normalize_text(document_type)
    return "Naliczenie" if "naliczenie" in normalized else "Rozliczenie"


def group_service_name(service: str) -> str:
    name = re.sub(r"\s+(?:PKWiU|Ustawa):.*$", "", service, flags=re.IGNORECASE)
    name = re.sub(r"\s+za\s+\d{4}-\d{2}\s*$", "", name, flags=re.IGNORECASE)
    return name.strip(" .-")


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source, delimiter=";")
        if reader.fieldnames is None:
            raise ValueError(f"Plik CSV nie zawiera nagłówka: {path}")
        missing_fields = REQUIRED_FIELDS.difference(reader.fieldnames)
        if missing_fields:
            raise ValueError(
                "Brak wymaganych kolumn w CSV: " + ", ".join(sorted(missing_fields))
            )

        rows: list[dict[str, Any]] = []
        for row_number, row in enumerate(reader, start=2):
            if not row.get("nazwa_uslugi", "").strip():
                continue
            month = row["rok_miesiac"].strip()
            if not re.fullmatch(r"\d{4}_\d{2}", month):
                raise ValueError(
                    f"Nieprawidłowy rok_miesiac w wierszu {row_number}: {month!r}"
                )
            service = row["nazwa_uslugi"].strip()
            rows.append(
                {
                    "typ": row["typ_dokumentu"].strip(),
                    "grupa": document_group(row["typ_dokumentu"]),
                    "miesiac": month,
                    "numer": row["numer_dokumentu"].strip(),
                    "data": row["data_dokumentu"].strip(),
                    "termin": row["termin_zaplaty"].strip(),
                    "pozycja": row["numer_pozycji"].strip(),
                    "usluga": service,
                    "usluga_grupowana": group_service_name(service),
                    "kategoria": categorize(service),
                    "jm": row["jm"].strip(),
                    "ilosc": parse_number(
                        row["ilosc"], field="ilosc", row_number=row_number
                    ),
                    "cena": parse_number(
                        row["cena_jednostkowa"],
                        field="cena_jednostkowa",
                        row_number=row_number,
                    ),
                    "wartosc": parse_number(
                        row["wartosc"], field="wartosc", row_number=row_number
                    ),
                    "plik": row["plik_pdf"].strip(),
                }
            )
    if not rows:
        raise ValueError(f"Nie znaleziono pozycji do raportu w pliku: {path}")
    return rows


REPORT_HTML = r"""<!doctype html>
<html lang="pl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Analiza wydatków</title>
<style>
:root{color-scheme:light;--ink:#17212b;--muted:#667085;--line:#e4e9ef;--paper:#f4f7fa;--card:#fff;--blue:#2767a8;--green:#24916e;--red:#c64e4e;--navy:#172f4d}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 "Segoe UI",Arial,sans-serif}
header{background:linear-gradient(115deg,#153251,#28699e);color:#fff;padding:30px max(24px,calc((100vw - 1440px)/2));}
header h1{font-size:clamp(25px,3vw,36px);margin:0 0 4px}header p{margin:0;color:#dbe8f3}
main{max-width:1440px;margin:22px auto;padding:0 24px 40px}
.note{background:#edf5fb;border-left:4px solid #3884bd;border-radius:8px;padding:12px 16px;margin-bottom:18px;color:#294660}
.filters,.card,.metric{background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:0 2px 8px #18344d08}
.filters{padding:16px;display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:12px;align-items:end;margin-bottom:16px}
label{display:grid;gap:5px;font-weight:600;font-size:13px;color:#344054}select,input{font:inherit;color:var(--ink);border:1px solid #cbd5df;border-radius:7px;background:#fff;padding:9px 10px;min-width:0}
button{font:inherit;border:0;border-radius:7px;padding:9px 14px;cursor:pointer;background:var(--navy);color:#fff}button.secondary{background:#edf2f7;color:#23364b;border:1px solid var(--line)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:0 0 16px}.metric{padding:15px 17px}.metric span{display:block;color:var(--muted);font-size:13px}.metric strong{display:block;font-size:23px;margin-top:3px}
.charts{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}.card{padding:17px;min-width:0}.wide{grid-column:1/-1}.card h2{font-size:18px;margin:0 0 3px}.sub{color:var(--muted);font-size:13px;margin:0 0 12px}.chart{width:100%;height:auto;display:block;overflow:visible}.chart text{font-family:"Segoe UI",Arial,sans-serif;fill:#536273;font-size:11px}.legend{display:flex;gap:18px;font-size:13px;color:#475467;margin-top:8px}.swatch{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.chart-head{display:flex;justify-content:space-between;align-items:start;gap:14px}.chart-head select{max-width:340px}
.table-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:12px}.table-head h2{margin:0}.table-wrap{overflow:auto;max-height:540px;border:1px solid var(--line);border-radius:8px}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}th,td{text-align:left;padding:9px 11px;border-bottom:1px solid var(--line)}th{position:sticky;top:0;background:#f7f9fb;color:#475467;z-index:1}td.num{text-align:right;font-variant-numeric:tabular-nums}tbody tr:hover{background:#f5f9fc}.pagination{display:flex;align-items:center;justify-content:center;gap:12px;padding-top:12px;color:var(--muted);font-size:13px}
.empty{color:var(--muted);text-align:center;padding:35px}.foot{color:var(--muted);font-size:12px;margin-top:16px}
.chart-tooltip{position:fixed;z-index:10;pointer-events:none;display:none;background:#172f4d;color:#fff;padding:7px 10px;border-radius:6px;box-shadow:0 3px 12px #172f4d40;font-size:13px;white-space:nowrap}
@media(max-width:900px){.filters{grid-template-columns:repeat(3,1fr)}.metrics{grid-template-columns:repeat(2,1fr)}.charts{grid-template-columns:1fr}.wide{grid-column:auto}}
@media(max-width:560px){main{padding:0 12px 25px}header{padding:24px 16px}.filters{grid-template-columns:repeat(2,1fr);padding:12px}.metrics{gap:8px}.metric{padding:12px}.metric strong{font-size:19px}.chart-head{display:block}.chart-head select{margin-top:8px;width:100%}}
</style>
</head>
<body>
<header><h1>Analiza wydatków</h1><p>Interaktywny raport na podstawie pozycji z dokumentów finansowych</p></header>
<main>
<div class="note"><strong>Jak liczone są kwoty:</strong> sumowane są wartości pozycji z CSV (w tym pozycje ujemne). Korekty naliczeń są prezentowane jako naliczenia; w CSV dla miesiąca korekty znajdują się już dane po korekcie. Kategorie są przypisywane automatycznie na podstawie nazwy usługi. Raport działa lokalnie i nie ładuje zewnętrznych bibliotek.</div>
<section class="filters" aria-label="Filtry raportu">
<label>Od miesiąca<select id="fromMonth"></select></label>
<label>Do miesiąca<select id="toMonth"></select></label>
<label>Typ dokumentu<select id="typeFilter"></select></label>
<label>Kategoria<select id="categoryFilter"></select></label>
<label>Usługa<select id="serviceFilter"></select></label>
</section>
<section class="metrics" aria-label="Podsumowanie">
<div class="metric"><span>Suma wybranych pozycji</span><strong id="totalMetric">—</strong></div>
<div class="metric"><span>Naliczenia (w tym korekty)</span><strong id="chargesMetric">—</strong></div>
<div class="metric"><span>Rozliczenia</span><strong id="settlementsMetric">—</strong></div>
<div class="metric"><span>Dokumenty / miesiące</span><strong id="countMetric">—</strong></div>
</section>
<section class="charts">
<article class="card wide"><h2>Wydatki miesiąc po miesiącu</h2><p class="sub">Słupki skumulowane: naliczenia i rozliczenia sumują się w jednym słupku; wartości ujemne są poniżej zera.</p><div id="monthlyChart"></div><div class="legend"><span><i class="swatch" style="background:#2767a8"></i>Naliczenie</span><span><i class="swatch" style="background:#24916e"></i>Rozliczenie</span></div></article>
<article class="card"><div class="chart-head"><div><h2>Podział według kategorii</h2><p class="sub">Zakres czasu jest niezależny od filtrów u góry.</p></div><div><label for="categoryFromMonth">Od miesiąca</label><select id="categoryFromMonth"></select><label for="categoryToMonth">Do miesiąca</label><select id="categoryToMonth"></select></div></div><div id="categoryChart"></div></article>
<article class="card"><div class="chart-head"><div><h2>Zmiany cen jednostkowych</h2><p class="sub">Usługi o tej samej nazwie są łączone między miesiącami.</p></div><select id="priceService" aria-label="Usługa na wykresie cen"></select></div><div id="priceChart"></div></article>
</section>
<section class="card">
<div class="table-head"><h2>Pozycje dokumentów</h2></div>
<p class="sub" id="tableSummary"></p><div class="table-wrap"><table><thead><tr><th>Miesiąc</th><th>Typ</th><th>Kategoria</th><th>Usługa</th><th>Jm</th><th>Ilość</th><th>Cena jedn.</th><th>Wartość</th><th>Nr dokumentu</th><th>Termin płatności</th></tr></thead><tbody id="rowsTable"></tbody></table></div>
<div class="pagination"><button id="prevPage" class="secondary" type="button">Poprzednia</button><span id="pageInfo"></span><button id="nextPage" class="secondary" type="button">Następna</button></div>
</section>
<p class="foot">Dane źródłowe: __SOURCE_NAME__. Raport zawiera kopię danych z chwili wygenerowania. Uruchom ponownie skrypt po aktualizacji CSV.</p>
</main>
<div id="chartTooltip" class="chart-tooltip" role="status"></div>
<script id="report-data" type="application/json">__DATA_JSON__</script>
<script>
"use strict";
const DATA=JSON.parse(document.getElementById("report-data").textContent);
const COLORS={Naliczenie:"#2767a8",Rozliczenie:"#24916e"};
const CATEGORY_COLORS=["#2767a8","#24916e","#e2a72e","#8a62a8","#d36b4f","#4c9dad","#7a8795","#c25483"];
const PAGE_SIZE=100;
let currentPage=0;
const els=Object.fromEntries(["fromMonth","toMonth","typeFilter","categoryFilter","serviceFilter","categoryFromMonth","categoryToMonth","priceService","monthlyChart","categoryChart","priceChart","chartTooltip","rowsTable","tableSummary","pageInfo","totalMetric","chargesMetric","settlementsMetric","countMetric","prevPage","nextPage"].map(id=>[id,document.getElementById(id)]));
const months=[...new Set(DATA.map(r=>r.miesiac))].sort();
const types=[...new Set(DATA.map(r=>r.typ))].sort((a,b)=>a.localeCompare(b,"pl"));
const categories=[...new Set(DATA.map(r=>r.kategoria))].sort((a,b)=>a.localeCompare(b,"pl"));
const services=[...new Set(DATA.map(r=>r.usluga_grupowana))].sort((a,b)=>a.localeCompare(b,"pl"));
const money=value=>new Intl.NumberFormat("pl-PL",{style:"currency",currency:"PLN"}).format(value);
const number=value=>new Intl.NumberFormat("pl-PL",{maximumFractionDigits:2}).format(value);
const escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
function addOptions(select,values,allLabel,selected=""){
 select.innerHTML="";
 if(allLabel){const all=document.createElement("option");all.value="";all.textContent=allLabel;select.append(all)}
 for(const value of values){const option=document.createElement("option");option.value=value;option.textContent=value.replace(/^(\d{4})_(\d{2})$/,"$1-$2");select.append(option)}
 if(selected)select.value=selected;
}
addOptions(els.fromMonth,months,"Od początku");
addOptions(els.toMonth,months,"Do końca");
els.toMonth.value=months.at(-1);
addOptions(els.categoryFromMonth,months,"Od początku");
addOptions(els.categoryToMonth,months,"Do końca");
els.categoryToMonth.value=months.at(-1);
addOptions(els.typeFilter,types,"Wszystkie typy");
addOptions(els.categoryFilter,categories,"Wszystkie kategorie");
addOptions(els.serviceFilter,services,"Wszystkie usługi");

function filteredRows(){
 const from=els.fromMonth.value,to=els.toMonth.value,type=els.typeFilter.value,category=els.categoryFilter.value,service=els.serviceFilter.value;
 if(from&&to&&from>to)return [];
 return DATA.filter(row=>(!from||row.miesiac>=from)&&(!to||row.miesiac<=to)&&(!type||row.typ===type)&&(!category||row.kategoria===category)&&(!service||row.usluga_grupowana===service));
}
function svgStart(width,height){return `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img">`}
function tooltipAttribute(text){return `data-tip="${escapeHtml(text)}"`}
function moveTooltip(event){
 const target=event.target.closest("[data-tip]");
 if(!target){els.chartTooltip.style.display="none";return}
 els.chartTooltip.textContent=target.dataset.tip;
 els.chartTooltip.style.display="block";
 els.chartTooltip.style.left=`${Math.min(event.clientX+12,window.innerWidth-els.chartTooltip.offsetWidth-8)}px`;
 els.chartTooltip.style.top=`${Math.max(8,event.clientY-els.chartTooltip.offsetHeight-12)}px`;
}
document.addEventListener("pointerover",moveTooltip);
document.addEventListener("pointermove",moveTooltip);
document.addEventListener("pointerout",event=>{if(event.target.closest("[data-tip]")&&!event.relatedTarget?.closest("[data-tip]"))els.chartTooltip.style.display="none"});
function renderMonthly(rows){
 const width=1100,height=330,left=68,right=16,top=18,bottom=62,plotW=width-left-right,plotH=height-top-bottom;
 const byMonth=new Map();
 for(const row of rows){if(!byMonth.has(row.miesiac))byMonth.set(row.miesiac,{Naliczenie:0,Rozliczenie:0});byMonth.get(row.miesiac)[row.grupa]+=row.wartosc}
 const data=[...byMonth].sort((a,b)=>a[0].localeCompare(b[0]));
 if(!data.length){els.monthlyChart.innerHTML='<div class="empty">Brak danych dla wybranych filtrów.</div>';return}
 const stackTotals=values=>{let positive=0,negative=0;for(const group of ["Naliczenie","Rozliczenie"]){if(values[group]>=0)positive+=values[group];else negative+=values[group]}return {positive,negative}};
 let min=Math.min(0,...data.map(([,v])=>stackTotals(v).negative)),max=Math.max(0,...data.map(([,v])=>stackTotals(v).positive));
 if(min===max)max=1;
 const pad=(max-min)*.08;min-=pad;max+=pad;
 const y=value=>top+(max-value)/(max-min)*plotH,zero=y(0),slot=plotW/data.length,bar=Math.max(2,Math.min(30,slot*.62));
 let svg=svgStart(width,height);
 for(let i=0;i<=4;i++){const value=min+(max-min)*i/4,yy=y(value);svg+=`<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" stroke="#e7ecf1"/><text x="${left-8}" y="${yy+4}" text-anchor="end">${escapeHtml(number(value))}</text>`}
 svg+=`<line x1="${left}" y1="${zero}" x2="${width-right}" y2="${zero}" stroke="#788796" stroke-width="1.2"/>`;
 data.forEach(([month,values],i)=>{const cx=left+slot*(i+.5);let positive=0,negative=0;
  for(const group of ["Naliczenie","Rozliczenie"]){const value=values[group];if(!value)continue;const start=value>0?positive:negative,end=start+value;if(value>0)positive=end;else negative=end;const rectY=Math.min(y(start),y(end)),h=Math.max(1,Math.abs(y(start)-y(end)));svg+=`<rect x="${cx-bar/2}" y="${rectY}" width="${bar}" height="${h}" rx="2" fill="${COLORS[group]}" ${tooltipAttribute(`${month.replace("_","-")} · ${group}: ${money(value)}`)}><title>${escapeHtml(month)} · ${group}: ${escapeHtml(money(value))}</title></rect>`}
  const label=month.replace("_","-");if(data.length<=18||i%Math.ceil(data.length/18)===0)svg+=`<text x="${cx}" y="${height-29}" text-anchor="middle" transform="rotate(-35 ${cx} ${height-29})">${label}</text>`;
 });
 svg+="</svg>";els.monthlyChart.innerHTML=svg;
}
function renderCategories(){
 const from=els.categoryFromMonth.value,to=els.categoryToMonth.value;
 const rows=from&&to&&from>to?[]:DATA.filter(row=>(!from||row.miesiac>=from)&&(!to||row.miesiac<=to));
 const byMonth=new Map(),categorySet=new Set();
 for(const row of rows){categorySet.add(row.kategoria);if(!byMonth.has(row.miesiac))byMonth.set(row.miesiac,new Map());const totals=byMonth.get(row.miesiac);totals.set(row.kategoria,(totals.get(row.kategoria)||0)+row.wartosc)}
 const data=[...byMonth].sort((a,b)=>a[0].localeCompare(b[0])),names=[...categorySet].sort((a,b)=>a.localeCompare(b,"pl"));
 if(!data.length){els.categoryChart.innerHTML='<div class="empty">Brak danych dla wybranego zakresu czasu.</div>';return}
 const width=760,height=330,left=66,right=14,top=18,bottom=62,plotW=width-left-right,plotH=height-top-bottom;
 const stackTotals=totals=>{let positive=0,negative=0;for(const value of totals.values()){if(value>=0)positive+=value;else negative+=value}return {positive,negative}};
 let min=Math.min(0,...data.map(([,totals])=>stackTotals(totals).negative)),max=Math.max(0,...data.map(([,totals])=>stackTotals(totals).positive));
 if(min===max)max=1;const pad=(max-min)*.08;min-=pad;max+=pad;
 const y=value=>top+(max-value)/(max-min)*plotH,zero=y(0),slot=plotW/data.length,bar=Math.max(2,Math.min(25,slot*.68));
 let svg=svgStart(width,height);
 for(let i=0;i<=4;i++){const value=min+(max-min)*i/4,yy=y(value);svg+=`<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" stroke="#e7ecf1"/><text x="${left-8}" y="${yy+4}" text-anchor="end">${escapeHtml(number(value))}</text>`}
 svg+=`<line x1="${left}" y1="${zero}" x2="${width-right}" y2="${zero}" stroke="#aab5c0"/>`;
 data.forEach(([month,totals],i)=>{const cx=left+slot*(i+.5);let positive=0,negative=0;
  names.forEach((name,index)=>{const value=totals.get(name)||0;if(!value)return;const start=value>0?positive:negative,end=start+value;if(value>0)positive=end;else negative=end;const rectY=Math.min(y(start),y(end)),h=Math.max(1,Math.abs(y(start)-y(end))),fill=CATEGORY_COLORS[index%CATEGORY_COLORS.length];svg+=`<rect x="${cx-bar/2}" y="${rectY}" width="${bar}" height="${h}" fill="${fill}" ${tooltipAttribute(`${month.replace("_","-")} · ${name}: ${money(value)}`)}><title>${escapeHtml(month)} · ${escapeHtml(name)}: ${escapeHtml(money(value))}</title></rect>`});
  if(data.length<=18||i%Math.ceil(data.length/18)===0)svg+=`<text x="${cx}" y="${height-29}" text-anchor="middle" transform="rotate(-35 ${cx} ${height-29})">${month.replace("_","-")}</text>`;
 });
 svg+="</svg>";
 const legend=names.map((name,index)=>`<span><i class="swatch" style="background:${CATEGORY_COLORS[index%CATEGORY_COLORS.length]}"></i>${escapeHtml(name)}</span>`).join("");
 els.categoryChart.innerHTML=svg+`<div class="legend" style="flex-wrap:wrap">${legend}</div>`;
}
function renderPrices(rows){
 const choices=new Map();for(const row of rows){const key=row.usluga_grupowana+"|||"+row.jm;if(!choices.has(key))choices.set(key,{service:row.usluga_grupowana,unit:row.jm,months:new Set()});choices.get(key).months.add(row.miesiac)}
 for(const [key,value] of choices)if(value.months.size<2)choices.delete(key);
 const values=[...choices.entries()].map(([key,value])=>({key,...value})).sort((a,b)=>a.service.localeCompare(b.service,"pl")||a.unit.localeCompare(b.unit,"pl"));
 const old=els.priceService.value;els.priceService.innerHTML="";
 for(const value of values){const option=document.createElement("option");option.value=value.key;option.textContent=value.service+" · "+value.unit;els.priceService.append(option)}
 if(old&&values.some(value=>value.key===old))els.priceService.value=old;
 if(!values.length){els.priceChart.innerHTML='<div class="empty">Brak usług występujących w co najmniej dwóch różnych miesiącach dla wybranych filtrów.</div>';return}
 let selected=values.find(v=>v.key===els.priceService.value)||values[0];
 if(!els.priceService.value)els.priceService.value=selected.key;
 const chosen=rows.filter(r=>r.usluga_grupowana===selected.service&&r.jm===selected.unit);
 const aggregates=new Map();
 for(const row of chosen){if(!aggregates.has(row.miesiac))aggregates.set(row.miesiac,{weighted:0,weight:0});const entry=aggregates.get(row.miesiac),weight=Math.abs(row.ilosc);entry.weighted+=row.cena*weight;entry.weight+=weight}
 const points=[...aggregates].map(([month,a])=>({month,price:a.weight?a.weighted/a.weight:0})).sort((a,b)=>a.month.localeCompare(b.month));
 if(!points.length){els.priceChart.innerHTML='<div class="empty">Brak danych dla tej usługi.</div>';return}
 const width=620,height=280,left=66,right=20,top=18,bottom=52,plotW=width-left-right,plotH=height-top-bottom;
 let min=Math.min(...points.map(p=>p.price)),max=Math.max(...points.map(p=>p.price));if(min===max){const delta=Math.abs(min)*.08||1;min-=delta;max+=delta}else{const pad=(max-min)*.1;min-=pad;max+=pad}
 const x=i=>left+(points.length===1?plotW/2:i/(points.length-1)*plotW),y=value=>top+(max-value)/(max-min)*plotH;
 let svg=svgStart(width,height);
 for(let i=0;i<=4;i++){const value=min+(max-min)*i/4,yy=y(value);svg+=`<line x1="${left}" y1="${yy}" x2="${width-right}" y2="${yy}" stroke="#e7ecf1"/><text x="${left-8}" y="${yy+4}" text-anchor="end">${escapeHtml(number(value))}</text>`}
 const line=points.map((p,i)=>`${i?"L":"M"} ${x(i)} ${y(p.price)}`).join(" ");svg+=`<path d="${line}" fill="none" stroke="#2767a8" stroke-width="2.5"/>`;
 points.forEach((p,i)=>{const cx=x(i),cy=y(p.price);svg+=`<circle cx="${cx}" cy="${cy}" r="4" fill="#2767a8" ${tooltipAttribute(`${p.month.replace("_","-")} · ${money(p.price)}/${selected.unit}`)}><title>${escapeHtml(p.month)} · ${escapeHtml(money(p.price))}/${escapeHtml(selected.unit)}</title></circle>`;if(points.length<=12||i%Math.ceil(points.length/12)===0)svg+=`<text x="${cx}" y="${height-20}" text-anchor="middle">${p.month.replace("_","-")}</text>`});
 els.priceChart.innerHTML=svg+"</svg>";
}
function renderTable(rows){
 const sorted=[...rows].sort((a,b)=>b.miesiac.localeCompare(a.miesiac)||a.usluga.localeCompare(b.usluga,"pl"));
 const pages=Math.max(1,Math.ceil(sorted.length/PAGE_SIZE));currentPage=Math.min(currentPage,pages-1);
 const visible=sorted.slice(currentPage*PAGE_SIZE,(currentPage+1)*PAGE_SIZE);
 els.rowsTable.innerHTML=visible.map(r=>`<tr><td>${escapeHtml(r.miesiac.replace("_","-"))}</td><td>${escapeHtml(r.typ)}</td><td>${escapeHtml(r.kategoria)}</td><td>${escapeHtml(r.usluga)}</td><td>${escapeHtml(r.jm)}</td><td class="num">${escapeHtml(number(r.ilosc))}</td><td class="num">${escapeHtml(number(r.cena))}</td><td class="num">${escapeHtml(money(r.wartosc))}</td><td>${escapeHtml(r.numer)}</td><td>${escapeHtml(r.termin)}</td></tr>`).join("");
 els.tableSummary.textContent=`Pozycji: ${rows.length.toLocaleString("pl-PL")} · strona ${currentPage+1} z ${pages}`;
 els.pageInfo.textContent=`${currentPage+1} / ${pages}`;els.prevPage.disabled=currentPage===0;els.nextPage.disabled=currentPage>=pages-1;
}
function render(){
 const rows=filteredRows(),sum=key=>rows.filter(r=>r.grupa===key).reduce((total,r)=>total+r.wartosc,0);
 els.totalMetric.textContent=money(rows.reduce((total,r)=>total+r.wartosc,0));
 els.chargesMetric.textContent=money(sum("Naliczenie"));els.settlementsMetric.textContent=money(sum("Rozliczenie"));
 els.countMetric.textContent=`${new Set(rows.map(r=>r.numer)).size} / ${new Set(rows.map(r=>r.miesiac)).size}`;
 renderMonthly(rows);renderCategories();renderPrices(rows);renderTable(rows);
}
for(const select of [els.fromMonth,els.toMonth,els.typeFilter,els.categoryFilter,els.serviceFilter])select.addEventListener("change",()=>{currentPage=0;render()});
for(const select of [els.categoryFromMonth,els.categoryToMonth])select.addEventListener("change",renderCategories);
els.priceService.addEventListener("change",()=>renderPrices(filteredRows()));
els.prevPage.addEventListener("click",()=>{currentPage=Math.max(0,currentPage-1);renderTable(filteredRows())});
els.nextPage.addEventListener("click",()=>{currentPage++;renderTable(filteredRows())});
render();
</script>
</body>
</html>
"""


def generate_report(input_path: Path, output_path: Path) -> int:
    rows = load_rows(input_path)
    serialized = json.dumps(rows, ensure_ascii=True, separators=(",", ":"))
    serialized = (
        serialized.replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    html = REPORT_HTML.replace("__DATA_JSON__", serialized).replace(
        "__SOURCE_NAME__", input_path.name
    )
    output_path.write_text(html, encoding="utf-8")
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generuje samodzielny raport HTML z zestawienia wydatków."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Plik CSV")
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT, help="Wyjściowy plik HTML"
    )
    args = parser.parse_args()
    row_count = generate_report(args.input, args.output)
    print(f"Raport zapisany: {args.output.resolve()} ({row_count} pozycji)")


if __name__ == "__main__":
    main()
