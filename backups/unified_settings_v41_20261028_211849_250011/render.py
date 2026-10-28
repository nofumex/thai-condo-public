"""Telegram HTML with robust omission of unknown fields and short inline keyboards."""
from html import escape
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from .filters import AMENITIES,FACILITIES,ROOM_TYPES,SORTS,concise,clean,deposit_terms
from .propertyhub import listing_url,price_of
from .geography import district_label, district_of, CITIES
from .ranking import age_days
from .pricing import public_text,retail_price,payment_amount,payment_display

def esc(x):return escape(str(x if x is not None else '—'),quote=False)
def money(n):
    try:return f'{int(float(n)):,}'.replace(',',' ')
    except (ValueError,TypeError):return '—'
def pretty_date(s):
    try:return datetime.fromisoformat(str(s).replace('Z','+00:00')).strftime('%d.%m.%Y')
    except (TypeError,ValueError):return None
def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t,callback_data=c) if c else InlineKeyboardButton(text=t,url=u) for t,c,u in row] for row in rows])
def b(text,callback):return (text,callback,None)
def link(text,url):return (text,None,url)

def main_kb(admin=False):
    rows=[[b('🆕 Новые','go:latest'),b('🔎 Поиск и фильтры','filter:search')],
          [b('🏆 Подборки','go:collections'),b('❤️ Избранное','go:favorites')],
          [b('📊 Рынок','go:market'),b('⚙️ Настройки','filter:latest')]]
    if admin:rows.append([b('🛠 Админка','go:admin')])
    return kb(rows)
MAIN='<b>🏙 Thai Condo</b>\nПоиск аренды по объявлениям PropertyHub\n\n<em>Выберите раздел</em>'

def filter_summary(kind,f):
    heading={'search':'🔎 Разовый поиск','latest':'⚙️ Постоянные настройки','channel':'📢 Фильтры публикаций в канал'}.get(kind,'⚙️ Фильтры')
    explanation=('Эти фильтры не влияют на «Новые» и «Подборки».' if kind=='search' else
                 'Эти фильтры применяются к «Новые» и «Подборки». Разовый поиск независим.' if kind=='latest' else
                 'Только для автоматических публикаций в канале; другие разделы не изменяются.')
    return f'<b>{heading}</b>\n\n🎯 {esc(concise(f))}\n↕️ {esc(SORTS[f["sort"]])}\n\n<i>{explanation}</i>'

def filter_kb(kind,f):
    prefix='f:'+('s' if kind=='search' else 'c' if kind=='channel' else 'l')+':'
    n=len(f['amenities']);m=len(f['facilities'])
    rows=[
        [b('💰 Цена',prefix+'price'),b('📐 Площадь',prefix+'area')],
        [b('🛏 '+ROOM_TYPES.get(f['room_type'],'Тип'),prefix+'room'),b('↕️ Сортировка',prefix+'sort')],
        [b('💳 Депозит / Предоплата',prefix+'payments'),b('📅 Даты',prefix+'dates')],
        [b(f'🧺 Квартира ({n})',prefix+'amen'),b(f'🏊 ЖК ({m})',prefix+'fac')],
        [b('🌏 Город',prefix+'location'),b('📍 Районы',prefix+'district')],
        [b('🔤 Название / адрес',prefix+'loccustom'),b('🆔 ID зоны / ЖК',prefix+'zone')],
        [b('🧹 Сбросить ВСЁ',prefix+'reset'),b('📋 Копировать профиль',prefix+'copy')],
        [b('🔎 Показать объявления',prefix+'run')] if kind!='channel' else [b('✅ Готово',prefix+'run')],
        [b('← Главное меню','go:home')] if kind!='channel' else [b('← Канал','admin:channel')]
    ]
    return kb(rows)

def payment_filter_text(f):
    def label(key):
        value=f.get(key)
        return 'без ограничения' if value is None else f'не более {value:g} мес.'
    return ('<b>💳 Депозит / Предоплата</b>\n\n'
            f'💳 Депозит: <b>{label("deposit_max_months")}</b>\n'
            f'🔑 Предоплата: <b>{label("advance_max_months")}</b>\n\n'
            '<i>Оба ограничения независимы. При отсутствии сведений '
            'об обязательном платеже объявление не проходит соответствующий активный фильтр.</i>')

def payment_filter_kb(kind,f):
    prefix='f:'+('s' if kind=='search' else 'c' if kind=='channel' else 'l')+':'
    return kb([
        [b('💳 Изменить депозит',prefix+'deposit')],
        [b('🔑 Изменить предоплату',prefix+'advance')],
        [b('🧹 Сбросить оба',prefix+'payments_clear')],
        [b('← К фильтрам',prefix+'menu')],
    ])

def choices_kb(kind,category,f,offset=0):
    prefix='f:'+('s' if kind=='search' else 'c' if kind=='channel' else 'l')+':'
    if category=='amen':items=list(AMENITIES.items());selected=f['amenities']
    else:items=list(FACILITIES.items());selected=f['facilities']
    chunk=items[offset:offset+10];rows=[]
    for i in range(0,len(chunk),2):
        rows.append([b(('✓ ' if k in selected else '▫️ ')+name[:25],f'{prefix}toggle:{category}:{k}:{offset}') for k,name in chunk[i:i+2]])
    paging=[]
    if offset>0:paging.append(b('←',f'{prefix}{category}:{offset-10}'))
    paging.append(b(f'{offset+1}–{min(offset+10,len(items))} / {len(items)}',f'{prefix}noop'))
    if offset+10<len(items):paging.append(b('→',f'{prefix}{category}:{offset+10}'))
    rows.append(paging)
    rows.append([b('✓ Готово',prefix+'menu'),b('Снять все',prefix+'clear:'+category)])
    return kb(rows)

def payment(obj,price,markup=0):
    return payment_display(obj,price,markup)

def rental_terms(x):
    rate=((x.get('priceLandAndHouse') or {}).get('forRent') or
          (x.get('price') or {}).get('forRent') or {})
    return rate

def card_text(x,favorite=False,score=None,rate=None,markup=0,contact_label="Аренда, консультация и подбор квартиры - @nofumez"):
    p=x.get('project') or {};r=x.get('roomInformation') or {};price=price_of(x)
    name=public_text(p.get('nameEnglish') or p.get('name')) or 'Квартира'
    lines=[f'🏙 <b>{esc(name)}</b>']
    district=district_label(x)
    if district:lines.append(f'📍 <b>{esc(district)}</b>')
    if p.get('address'):lines.append('🗺 '+esc(public_text(p['address'])))
    # Include update date in both the preview and the channel post.
    refreshed=pretty_date(x.get('updatedAt') or x.get('refreshedAt'))
    if refreshed:lines.append('🔄 Обновлено: '+refreshed)
    lines.append('')
    if price is not None:
        public_price=retail_price(price,markup)
        rub=round(float(public_price)*rate) if rate else None
        lines.append(f'💰 <b>{money(public_price)} ฿/мес.</b>'+(f'  ≈{money(rub)} ₽' if rub is not None else ''))
    lines.append('')  # Keep the rent separated on BOTH sides.
    space=[]
    if r.get('roomArea') is not None:space.append(f'📐 {esc(r["roomArea"])} м²')
    if r.get('onFloor') not in (None,'','-'):space.append(f'🏢 {esc(r["onFloor"])} этаж')
    if space:lines.append('  ·  '.join(space))
    beds=[]
    if r.get('numberOfBed') is not None:beds.append(f'🛏 {esc(r["numberOfBed"])} сп.')
    if r.get('numberOfBath') is not None:beds.append(f'🛁 {esc(r["numberOfBath"])} ванн.')
    if beds:lines.append('  ·  '.join(beds))
    if price and r.get('roomArea'):
        try:lines.append(f'📊 {money(float(retail_price(price,markup))/float(r["roomArea"]))} ฿/м²')
        except (ValueError,TypeError,ZeroDivisionError):pass
    rent=rental_terms(x)
    dep=payment(rent.get('deposit'),price,markup)
    adv=payment(rent.get('advancePayment'),price,markup)
    if dep:lines.append('💳 Депозит: '+dep)
    if adv:lines.append('🔑 Предоплата: '+adv)
    pub=pretty_date(x.get('createdAt'))
    if pub:lines.append(f'🗓 Опубликовано: {pub} · {age_days(x):.0f} дн.')
    if x.get('userVerificationStatus') is True:lines.append('✅ Автор верифицирован')
    if score is not None:lines.append(f'✨ <b>Рейтинг: {score:.1f}/100</b> <i>(динамический)</i>')
    if favorite:lines.append('❤️ В избранном')
    lines.extend(['', '👤 <b>'+esc(contact_label)+'</b>', ''])
    out=[];used=0
    for line in lines:
        if used+len(line)+1>1020:continue
        out.append(line);used+=len(line)+1
    return '\n'.join(out)

def card_kb(x,index,total,favorite=False,busy=False,provider_total=None):
    label=f'{index+1}/{total}'+(' +' if busy else '')
    if provider_total and provider_total>total:label+=f' · ≈{provider_total:,}'.replace(',',' ')
    rows=[
        [b('←','card:prev'),b(label[:36],'card:noop'),b('→','card:next')],
        [b('❤️ В избранном' if favorite else '🤍 Сохранить','card:heart'),b('🙈 Скрыть','card:hide')],
        [b('📸 Подробнее','card:detail')],
        [b('← Меню','card:back')]
    ]
    return kb(rows)

def details_text(x,detail,rate=None,markup=0,contact_label="Аренда, консультация и подбор квартиры - @nofumez"):
    v=detail or x;p=v.get('project') or x.get('project') or {}
    room=v.get('roomInformation') or x.get('roomInformation') or {}
    price=price_of(v)
    if price is None:price=price_of(x)
    rent=rental_terms(v) or rental_terms(x)
    title=public_text(p.get('nameEnglish') or p.get('name')) or 'Квартира'
    lines=[f'🏙 <b>{esc(title)}</b>']
    district=district_label(v) or district_label(x)
    if district:lines.append('📍 '+esc(district))
    if p.get('address'):lines.append('🗺 '+esc(public_text(p['address'])))
    if price is not None:
        public_price=retail_price(price,markup)
        rub=f' · ≈{money(float(public_price)*rate)} ₽' if rate else ''
        lines.append(f'\n💰 <b>{money(public_price)} ฿/мес.</b>{rub}\n')
    for name,key,suffix in [('Площадь','roomArea','м²'),('Этаж','onFloor',''),('Спальни','numberOfBed',''),('Ванные','numberOfBath',''),('Направление окон','direction','')]:
        if room.get(key) not in (None,'','-'):lines.append(f'{name}: {esc(room[key])} {suffix}'.rstrip())
    for name,key in [('Депозит','deposit'),('Предоплата','advancePayment')]:
        val=payment(rent.get(key),price,markup)
        if val:lines.append(f'{name}: {val}')
    dep_amt,dep_m=payment_amount(rent.get('deposit'),price,markup)
    adv=rent.get('advancePayment') or {}
    adv_m=adv.get('month')
    if price and dep_amt is not None and adv_m is not None:
        try:lines.append(f'🔑 При заселении: {money(retail_price(price,markup)*float(adv_m)+float(dep_amt))} ฿')
        except (ValueError,TypeError):pass
    for label,key in [('1 месяц','monthly1'),('3 месяца','monthly3'),('6 месяцев','monthly6'),('Сутки','daily')]:
        obj=rent.get(key) or {}
        if obj.get('price') is not None:lines.append(f'{label}: {money(retail_price(obj["price"],markup))} ฿')
    for name,data,names in [('🧰 Оснащение',v.get('amenities'),AMENITIES),
                            ('🏢 Инфраструктура',p.get('facilities') or v.get('facilities'),FACILITIES)]:
        if isinstance(data,dict):
            marked=[names.get(k,k) for k,val in data.items() if val is True]
            if marked:lines.append('\n<b>'+name+'</b>\n✓ '+esc(', '.join(marked)))
    lines.append('\n👤 <b>'+esc(contact_label)+'</b>\n')
    point=v.get('location') or x.get('location') or {}
    if point.get('lat') is not None and point.get('lng') is not None:
        lines.append(f'\n🗺 {esc(point["lat"])}, {esc(point["lng"])}')
    when=pretty_date(v.get('createdAt') or x.get('createdAt'))
    refreshed=pretty_date(v.get('refreshedAt') or x.get('refreshedAt'))
    if when:lines.append('🗓 Опубликовано: '+when)
    if refreshed:lines.append('🔄 Обновлено: '+refreshed)
    return '\n'.join(lines)

def detail_kb(x,from_channel=False):
    return kb([[b('← Другие варианты','go:home')]] if from_channel else [[b('← Назад к объявлению','card:detailback')]])
