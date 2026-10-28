"""Public-facing retail amounts and contact-safe snippets.

PropertyHub data is immutable upstream data: calculate retail values only at
presentation time so a change to markup affects existing saved listings too.
"""
from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation
from html import unescape

_URL = re.compile(r'(?:https?://|www\.)\S+', re.I)
_EMAIL = re.compile(r'(?<!\w)[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', re.I)
_HANDLE = re.compile(r'(?<!\w)@[a-zA-Z][a-zA-Z0-9_]{3,}', re.I)
_PRICE_LINE = re.compile(r'(?:฿\s*\d|\d[\d,. ]*\s*(?:฿|THB|baht|บาท)\b|(?:rent|price|ราคา|ค่าเช่า)\s*[:=]?\s*[\d,]+)',re.I)
_PHONE = re.compile(r'(?<!\d)(?:\+?66[\s.\-]?(?:0[\s.\-]?)?|0)[6892](?:[\s.\-]?\d){7,9}(?!\d)')
_CONTACT_LINE = re.compile(r'(?:\b(?:contact|call|tel|phone|whatsapp|line\s*id|wechat|agent|agency|inbox|dm|facebook|instagram|propertyhub)\b|ติดต่อ|โทร|ไลน์|контакт|телефон|напишите)', re.I)

def public_text(value: object, *, multiline: bool = False) -> str:
    """Best-effort protection for untrusted titles/description; never publish raw contact fields."""
    text=unescape(str(value or ''))
    text=re.sub(r'<[^>]+>', ' ', text)
    lines=[]
    for line in text.splitlines() if multiline else [text.replace('\n',' ')]:
        if _CONTACT_LINE.search(line) or _PRICE_LINE.search(line):
            continue
        line=_URL.sub('',line)
        line=_EMAIL.sub('',line)
        line=_HANDLE.sub('',line)
        line=_PHONE.sub('',line)
        line=re.sub(r'\s+', ' ', line).strip()
        if line:lines.append(line)
    return '\n'.join(lines)

def retail_price(amount, markup=0):
    if amount is None:return None
    try:return float(Decimal(str(amount))+Decimal(str(markup)))
    except (InvalidOperation,TypeError,ValueError):return None

def payment_amount(term:dict|None, base_rent, markup=0):
    """Return amount and months if determinable.

    AMOUNT can represent an arbitrary nominal deposit (e.g. 20700 / 11500=1.8
    months). Preserve that ratio and apply new monthly price to it.
    """
    if not isinstance(term,dict) or term.get('type') in ('CALL','NO_DEPOSIT','NO_ADVANCE_PAYMENT'):
        return None,None
    kind=term.get('type'); m=term.get('month'); amt=term.get('amount')
    try:
        base=Decimal(str(base_rent)) if base_rent is not None else None
        new=base+Decimal(str(markup)) if base is not None else None
        if kind=='MONTH' and m is not None:
            months=Decimal(str(m))
            if months < 0:return None,None
            return (float(new*months) if new is not None else None),float(months)
        if kind=='AMOUNT' and amt is not None:
            nominal=Decimal(str(amt))
            if nominal < 0:return None,None
            if base is not None and base>0:
                months=nominal/base
                return float(new*months),float(months)
            return float(nominal+Decimal(str(markup))),None
    except (InvalidOperation,ValueError,TypeError):pass
    return None,None

def payment_display(term,base_rent,markup=0):
    amount,months=payment_amount(term,base_rent,markup)
    if amount is None:return None
    from .render import money
    if months is None:return f'{money(amount)} ฿'
    return f'{months:g} мес. · {money(amount)} ฿'
