#!/usr/bin/env python3
"""
update_data.py — Atualiza data.json com dados frescos do Fundamentus
Executado automaticamente pelo GitHub Actions duas vezes por dia.

Fontes:
  - B3 stocks (source=fundamentus): fundamentus.com.br
  - Demais (source=preserve): mantém dados existentes em data.json
"""

import json
import sys
from datetime import datetime, date, timedelta

import pytz
import requests
from bs4 import BeautifulSoup

BRASILIA = pytz.timezone('America/Sao_Paulo')
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (compatible; PainelAcoes/2.0; '
        '+https://edumvm.github.io/painel-acoes/)'
    )
}


# ── utils ────────────────────────────────────────────────────────────────────

def fetch_soup(url):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.encoding = 'iso-8859-1'
    return BeautifulSoup(r.text, 'html.parser')


def parse_float(s):
    # Convert Brazilian/percentage string to float. Returns None on failure.
    if not s or s.strip() in ('-', 'N/D', ''):
        return None
    s = s.strip().rstrip('%').replace('.', '').replace(',', '.')
    try:
        return float(s)
    except ValueError:
        return None


# ── fundamentus ──────────────────────────────────────────────────────────────

def fetch_fundamentus_data(ticker):
    # Returns dict with price, date, indicators, performances.
    url = f'https://www.fundamentus.com.br/detalhes.php?papel={ticker}'
    soup = fetch_soup(url)

    # Build flat label -> value dict from all table cells (paired)
    data = {}
    for row in soup.find_all('tr'):
        cells = row.find_all('td')
        for i in range(0, len(cells) - 1, 2):
            raw_label = cells[i].get_text(' ', strip=True)
            # Strip leading '?' (fundamentus tooltip icon text)
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
    pytd_raw  = get('2026')   # YTD current year

    # Format P/L: negative P/L = prejuízo
    pl_float = parse_float(pl_raw)
    if pl_float is None:
        pl_fmt = pl_raw
    elif pl_float < 0:
        pl_fmt = 'Prejuízo'
    else:
        pl_fmt = pl_raw + 'x'

    # Format P/VP
    pvp_float = parse_float(pvp_raw)
    pvp_fmt = (pvp_raw + 'x') if pvp_float is not None else pvp_raw

    p12m = parse_float(p12m_raw)
    pytd = parse_float(pytd_raw)

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


def fetch_fatos_relevantes(ticker, cutoff_days=10):
    # Returns (items_list, has_new_bool). items_list has at most 5 entries.
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

        # Date cell: "DD/MM/AAAA HH:MM" — take first 10 chars
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

        if len(items) >= 5:
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

    for cfg in config['stocks']:
        ticker   = cfg['ticker']
        source   = cfg.get('source', 'fundamentus')
        company  = cfg.get('company', ticker)
        currency = cfg.get('currency', 'BRL')

        if source == 'fundamentus':
            print(f'  -> {ticker}: buscando no Fundamentus...')
            try:
                fd = fetch_fundamentus_data(ticker)
                fr_items, has_new = fetch_fatos_relevantes(ticker)

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
                print(f'     OK: {fd["price"]} em {fd["priceDate"]}, hasNew={has_new}')

            except Exception as e:
                print(f'  ERRO ao buscar {ticker}: {e}', file=sys.stderr)
                # Preserve existing data on error to avoid losing info
                stock = existing_stocks.get(ticker, {
                    'ticker': ticker, 'company': company, 'currency': currency,
                    'price': 'N/D', 'priceDate': 'N/D',
                    'pl': 'N/D', 'pvp': 'N/D', 'dy': 'N/D', 'roe': 'N/D',
                    'p12m': None, 'pYtd': None, 'hasNew': False,
                })
                new_fatos[ticker] = existing_fatos.get(ticker, {'type': 'cvm', 'items': []})

        else:
            # source == 'preserve': keep existing data, only update timestamp
            print(f'  -> {ticker}: preservando dados existentes (source={source})')
            stock = existing_stocks.get(ticker, {
                'ticker': ticker, 'company': company, 'currency': currency,
                'price': 'N/D', 'priceDate': 'N/D',
                'pl': 'N/D', 'pvp': 'N/D', 'dy': 'N/D', 'roe': 'N/D',
                'p12m': None, 'pYtd': None, 'hasNew': False,
            })
            new_fatos[ticker] = existing_fatos.get(ticker, {
                'type': 'ir', 'irUrl': '#', 'irLabel': ticker
            })

        new_stocks.append(stock)

    output = {
        'lastUpdated': timestamp,
        'stocks':      new_stocks,
        'fatos':       new_fatos,
        'clipping':    existing.get('clipping', []),
    }

    with open('data.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print('[update_data] data.json atualizado com sucesso!')


if __name__ == '__main__':
    main()
