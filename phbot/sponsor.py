"""Sponsor-aware sorting and page classification shared by search, collections, channel.

A sponsor package is a ranking tier, NOT a distinct listing inventory. Never infer
age from position in the provider result set. The GraphQL API may show paid ads
before newly created FREE listings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from .propertyhub import price_of

SPONSORED = frozenset({'EXCLUSIVE', 'PREMIUM', 'STANDARD'})

def is_paid(item):
    return str(item.get('sponsorPackage') or '').upper() in SPONSORED

def is_regular(item):
    # Unknown tiers must not be mistaken for the chronological FREE stream.
    tier = str(item.get('sponsorPackage') or '').upper()
    return tier in ('FREE', '')  # missing field in legacy/test listings

def timestamp(value):
    if not value:return None
    try:
        x=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return x.astimezone(timezone.utc) if x.tzinfo else x.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError,OverflowError):return None

def sort_value(item, order):
    # Always deterministic, including malformed dates/prices and ties.
    ident=str(item.get('id') or '')
    created=timestamp(item.get('createdAt'))
    if order == 'PRICE_MONTHLY_LOW_TO_HIGH':
        p=price_of(item)
        try:p=float(p) if p is not None else float('inf')
        except (ValueError,TypeError):p=float('inf')
        return (p, -(created.timestamp() if created else 0))
    if order == 'REFRESHED_AT':
        updated=timestamp(item.get('refreshedAt') or item.get('updatedAt'))
        return (-(updated.timestamp() if updated else 0),
                -(created.timestamp() if created else 0))
    return (-(created.timestamp() if created else 0),)

def sort_items(rows, order='CREATED_AT'):
    return sorted(rows,key=lambda x:sort_value(x,order))


def free_page_is_old(rows, cutoff):
    """Early-stop predicate only for wholly FREE, fully dated pages."""
    return (bool(rows) and all(is_regular(x) and timestamp(x.get('createdAt')) is not None
        and timestamp(x['createdAt']) < cutoff for x in rows))
