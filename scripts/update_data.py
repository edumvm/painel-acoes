#!/usr/bin/env python3
"""
update_data.py — Atualiza data.json com dados frescos do Fundamentus
Executado automaticamente pelo GitHub Actions duas vezes por dia.

IMPORTANTE: Este script NUNCA modifica index.html.
Só escreve em data.json.

Fontes:
  - B3 stocks (source=fundamentus): fundamentus.com.br
  - Demais (source=preserve): mantém dados existentes em data.json
"""

import json
import sys
import time
from datetime import datetime, date, timedelta

import pytz
import requests
from bs4 import BeautifulSoup

BRASILIA = pytz.timezone('America/Sao_Paulo')

# Headers realistas de browser para evitar bloqueios
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/125.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xhtml+xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
    'Upgrade-Insecure-Requests': '1',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Cache-Control': 'max-age=0',
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ── utils ────────────────────────────────────────────────────────────────────

def fetch_soup(url, retries=4):
    """Faz GET com headers de browser, retry com backoff, retorna BeautifulSoup."""
    last_exc = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, timeout=30)
            r.raise_for_status()
            r.encoding = 'iso-8859-1'
            return BeautifulSoup(r.text, 'html.parser')
        except Exception as e:
            last_exc = e
            if attempt < retries - 1:
                wait = 2 ** attempt   # 1s, 2s, 4s
                print(f'  [retry {attempt+1}/{retries-1}] {e} — aguardando {wait}s...')
                time.sleep(wait)
    raise last_exc


def parse_float(s):
    """Converte string brasileira/percentual para float. Retorna None se falhar."""
    if not s or s.strip() in ('-', 'N/D', ''):
        return None
    s = s.strip().rstrip('%').replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


# ── fundamentus ──────────────────────────────────────────────────────────────

def fetch_fundamentus_data(ticker):
    """Busca cotação e indicadores do Fundamentus. Retorna dict."""
    url = f'https://www.fundamentus.com.br/detalhes.php?papel={ticker}'
    soup = fetch_soup(url)

    # Monta dict label -> valor de todas as células pareadas das tabelas
    data = {}
    for row in soup.find_all('tr'):
        cells = row.find_all('td')
        for i in range(0, len(cells) - 1, 2):
            raw_label = cells[i].get_text(' ', strip=True)
            label = raw_label.lstrip('?').strip()
            value = cells[i + 1].get_text(strip=True)
            if label:
                data[label] = value

    def get(*keys):
        for k in keys:
            if k in data:
                return data[k]
        return 'N/D'

    price_str = get('Cotação', 'Cotacao')
    date_str  = get('Data últ cot', 'Data lt cot', 'Data ult cot')
    pl_raw    = get('P/L')
    pvp_raw   = get('P/VP')
    dy_raw    = get('Div. Yield')
    roe_raw   = get('ROE')
    p12m_raw  = get('12 meses')
    pytd_raw  = get('2026')   # YTD ano corrente

    # P/L negativo = prejuízo
    pl_float = parse_float(pl_raw)
    if pl_float is None:
        pl_fmt = pl_raw
    elif pl_float < 0:
        pl_fmt = 'Prejuízo'
    else:
        pl_fmt = pl_raw + 'x'

    pvp_float = parse_float(pvp_raw)
    pvp_fmt = (pvp_raw + 'x') if pvp_float is not None else pvp_raw

    p12m = parse_float(p12m_raw)
    pytd = parse_float(pytd_raw)

    # Validação: se cotação for "N/D" ou vazia, algo deu errado
    if not price_str or price_str == 'N/D':
        raise ValueError(f'Cotação vazia para {ticker} — possível bloqueio ou ticker inválido')

    return {
        'price':     price_str,
        'priceDate': date_str,
        'pl':        pl_fmt,
        'pvp':       pvp_fmt,
        'dy':        dy_raw,
        'roe':       roe_raw,
        'p12m':      round(p12m, 2) if p12m is not None else None,
        'pYtd':      round(pytd, 2) if pytd is not None else None,
    }


def fetch_fatos_relevantes(ticker, cutoff_days=10, max_items=10):
    """Busca fatos relevantes e comunicados do Fundamentus.
    Retorna (items_list, has_new_bool). Até max_items entradas.
    """
    url = f'https://www.fundamentus.com.br/fatos_relevantes.php?papel={ticker}'
    soup = fetch_soup(url)

    today  = datetime.now(BRASILIA).date()
    cutoff = today - timedelta(days=cutoff_days)

    items   = []
    has_new = False

    for row in soup.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) < 3:
            continue

        # Célula de data: "DD/MM/AAAA HH:MM" — pega só os 10 primeiros chars
        raw_date = cells[0].get_text(strip=True)[:10]
        tipo     = cells[1].get_text(strip=True).upper()
        link_el  = cells[2].find('a')

        if not raw_date or '/' not in raw_date:
            continue

        try:
            d, m, y = raw_date.split('/')
            item_date = date(int(y), int(m), int(d))
        except Exception:
            item_date = None

        badge = 'FR' if tipo.startswith('FR') else 'CO'
        label = link_el.get_text(strip=True) if link_el else cells[2].get_text(strip=True)
        href  = link_el['href'] if link_el and link_el.has_attr('href') else '#'
        if href.startswith('/'):
            href = 'https://www.fundamentus.com.br' + href

        if item_date and item_date >= cutoff:
            has_new = True

        if label:
            items.append({
                'date':  raw_date,
                'badge': badge,
                'label': label,
                'url':   href,
            })

        if len(items) >= max_items:
            break

    return items, has_new


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    now = datetime.now(BRASILIA)
    timestamp = now.strftime('%d/%m/%Y às %H:%M (Brasília)')
    print(f'[update_data] Iniciando: {timestamp}')

    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    with open('data.json', 'r', encoding='utf-8') as f:
        existing = json.load(f)

    existing_stocks = {s['ticker']: s for s in existing.get('stocks', [])}
    existing_fatos  = existing.get('fatos', {})

    new_stocks = []
    new_fatos  = {}

    for idx, cfg in enumerate(config['stocks']):
        ticker   = cfg['ticker']
        source   = cfg.get('source', 'fundamentus')
        company  = cfg.get('company', ticker)
        currency = cfg.get('currency', 'BRL')

        if source == 'fundamentus':
            # Pequeno delay entre requisições para não sobrecarregar o servidor
            if idx > 0:
                time.sleep(1.5)

            print(f'  -> {ticker}: buscando no Fundamentus...')
            try:
                fd = fetch_fundamentus_data(ticker)
                time.sleep(0.8)  # delay entre detalhes e fatos do mesmo ticker
                fr_items, has_new = fetch_fatos_relevantes(ticker, max_items=10)

                stock = {
                    'ticker':    ticker,
                    'company':   company,
                    'currency':  currency,
                    'price':     fd['price'],
                    'priceDate': fd['priceDate'],
                    'pl':        fd['pl'],
                    'pvp':       fd['pvp'],
                    'dy':        fd['dy'],
                    'roe':       fd['roe'],
                    'p12m':      fd['p12m'],
                    'pYtd':      fd['pYtd'],
                    'hasNew':    has_new,
                }
                new_fatos[ticker] = {'type': 'cvm', 'items': fr_items}
                print(f'     OK: {fd["price"]} em {fd["priceDate"]}, '
                      f'{len(fr_items)}