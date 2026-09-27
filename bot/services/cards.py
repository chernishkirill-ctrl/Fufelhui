"""Тексты карточек. Публичные тексты формируются только из публичных полей объекта."""
from __future__ import annotations

from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

from bot.database.models import Deal, Lead, OfferType, Property, PropertyHistory, User
from bot.services.deal_service import DEAL_STATUS_LABELS, DEAL_TYPE_LABELS
from bot.services.lead_service import LEAD_STATUS_LABELS
from bot.services.property_service import EDITABLE_FIELDS, OFFER_LABELS, STATUS_LABELS, PropertySummary
from bot.utils import uk
from bot.utils.text import clean_public_text, esc, fmt_money, fmt_number, hashtag, truncate
from bot.utils.timeutils import fmt_date, fmt_dt

CAPTION_LIMIT = 1024


def rooms_label(prop: Property) -> str:
    ptype = (prop.property_type or "Квартира").lower()
    if prop.rooms:
        if ptype == "квартира":
            return f"{prop.rooms}-комнатная квартира"
        return f"{prop.property_type}, {prop.rooms} комн."
    return prop.property_type or "Объект"


def headline(prop: Property) -> str:
    if prop.rooms or prop.property_type:
        return rooms_label(prop)
    return prop.title or "Объект"


def price_label(prop: Property) -> str:
    if prop.price is None:
        return "цена по запросу"
    suffix = "/мес" if prop.offer_type == OfferType.RENT else ""
    return fmt_money(prop.price, prop.currency) + suffix


def floor_label(prop: Property) -> str | None:
    if prop.floor is None and prop.floors_total is None:
        return None
    if prop.floor is not None and prop.floors_total:
        return f"{prop.floor}/{prop.floors_total}"
    if prop.floor is not None:
        return str(prop.floor)
    return f"этажность {prop.floors_total}"


def short_line(prop: Property) -> str:
    parts = [prop.code, headline(prop)]
    if prop.district:
        parts.append(prop.district)
    parts.append(price_label(prop))
    return " · ".join(parts)


def location_line(prop: Property, public: bool) -> str:
    parts = []
    if prop.district:
        parts.append(f"{prop.district} район" if "район" not in prop.district.lower() else prop.district)
    if prop.city:
        parts.append(prop.city)
    if not public and prop.address:
        parts.insert(0, prop.address)
    return ", ".join(parts)


def user_label(user: User | None) -> str:
    return esc(user.display_name) if user else "не назначен"


# ---------------- внутренние карточки ----------------

def base_card(prop: Property, public_url: str | None = None) -> str:
    """Компактная карточка для темы «База» (без контактов собственника)."""
    lines = [f"🏠 <b>Объект {prop.code}</b>", "", esc(headline(prop))]
    loc = location_line(prop, public=True)
    if loc:
        lines.append(esc(loc))
    if prop.area:
        lines.append(f"Площадь: {fmt_number(prop.area)} м²")
    fl = floor_label(prop)
    if fl:
        lines.append(f"Этаж: {fl}")
    lines.append(f"Цена: {esc(price_label(prop))}")
    lines += ["", f"Ответственный: {user_label(prop.responsible)}", f"Статус: {STATUS_LABELS[prop.status]}"]
    if public_url:
        lines.append(f'<a href="{esc(public_url)}">Публикация в канале</a>')
    return "\n".join(lines)


def archive_card(prop: Property, tz: ZoneInfo) -> str:
    return "\n".join(
        [
            f"📦 <b>Архив: {prop.code}</b>",
            "",
            esc(headline(prop)) + (f", {esc(prop.district)}" if prop.district else ""),
            f"Цена: {esc(price_label(prop))}",
            f"Статус: {STATUS_LABELS[prop.status]}",
            f"Ответственный: {user_label(prop.responsible)}",
            f"В архиве с: {fmt_dt(prop.archived_at, tz)}",
        ]
    )


def internal_card(prop: Property, tz: ZoneInfo, summary: PropertySummary | None = None, show_private: bool = True) -> str:
    lines = [f"🏠 <b>{prop.code}</b> — {STATUS_LABELS[prop.status]}", ""]
    if prop.title:
        lines.append(f"<b>{esc(prop.title)}</b>")
    lines.append(f"{esc(headline(prop))} · {OFFER_LABELS.get(prop.offer_type, '')}")
    loc = location_line(prop, public=False)
    if loc:
        lines.append(f"📍 {esc(loc)}")
    details = []
    if prop.area:
        details.append(f"📐 {fmt_number(prop.area)} м²")
    fl = floor_label(prop)
    if fl:
        details.append(f"🏢 этаж {fl}")
    if details:
        lines.append(" · ".join(details))
    lines.append(f"💰 {esc(price_label(prop))}")
    if prop.latitude is not None:
        lines.append(f"🗺 {prop.latitude:.5f}, {prop.longitude:.5f} ({'точно' if prop.show_exact_location else 'приблизительно в публикации'})")
    lines.append(f"🖼 Фото: {len(prop.photos or [])}")
    if prop.description:
        lines += ["", esc(truncate(prop.description, 700))]
    if prop.features:
        feats = "; ".join(f"{esc(k)}: {esc(v)}" for k, v in list(prop.features.items())[:12])
        lines += ["", f"<i>{truncate(feats, 600)}</i>"]
    lines += ["", f"👤 Ответственный: {user_label(prop.responsible)}", f"➕ Добавил: {user_label(prop.created_by)}"]
    if prop.source_url:
        lines.append(f'🔗 Источник: <a href="{esc(prop.source_url)}">{esc(prop.source_name or "ссылка")}</a>')
    if show_private:
        private = []
        if prop.owner_name:
            private.append(f"Собственник: {esc(prop.owner_name)}")
        if prop.owner_phone:
            private.append(f"Телефон: {esc(prop.owner_phone)}")
        if prop.internal_comment:
            private.append(f"Комментарий: {esc(prop.internal_comment)}")
        if private:
            lines += ["", "🔒 <b>Служебное</b>", *private]
    if prop.telegraph_url:
        lines.append(f'📸 <a href="{esc(prop.telegraph_url)}">Telegraph-страница</a>')
    if summary is not None:
        lines += [
            "",
            f"📋 Заявок: {summary.leads_total} (взято: {summary.leads_taken}) · 👀 просмотров: {summary.viewings}",
        ]
        for d in summary.deals:
            lines.append(f"🤝 {d.code}: {DEAL_TYPE_LABELS[d.deal_type]}, комиссия {fmt_money(d.commission, d.currency)}")
    lines.append(f"\n🕒 Создан: {fmt_dt(prop.created_at, tz)}")
    return "\n".join(lines)


def preview_card(data: dict) -> str:
    """Предпросмотр импортированного/введенного объекта до сохранения (data — словарь полей)."""
    tmp = Property(**{k: v for k, v in data.items() if hasattr(Property, k) and k not in {"id"}})
    tmp.id = 0
    lines = ["🔎 <b>Проверьте данные объекта</b>", ""]
    if tmp.title:
        lines.append(f"<b>{esc(tmp.title)}</b>")
    offer = OFFER_LABELS.get(OfferType(data["offer_type"])) if data.get("offer_type") else "тип не определен"
    lines.append(f"{esc(headline(tmp))} · {offer}")
    loc = location_line(tmp, public=False)
    lines.append(f"📍 {esc(loc) if loc else 'адрес/район не найдены'}")
    lines.append(f"📐 {fmt_number(tmp.area) + ' м²' if tmp.area else 'площадь —'} · 🏢 этаж {floor_label(tmp) or '—'}")
    lines.append(f"💰 {esc(price_label(tmp))}")
    lines.append(f"🖼 Фото: {len(data.get('photos') or [])}")
    if tmp.owner_phone or tmp.owner_name:
        lines.append(f"🔒 Собственник: {esc(tmp.owner_name or '')} {esc(tmp.owner_phone or '')}".rstrip())
    if tmp.description:
        lines += ["", esc(truncate(tmp.description, 600))]
    if data.get("source_url"):
        lines.append(f'\n🔗 <a href="{esc(data["source_url"])}">{esc(data.get("source_name") or "Источник")}</a>')
    return "\n".join(lines)


def history_text(prop: Property, history: list[PropertyHistory], tz: ZoneInfo) -> str:
    action_labels = {
        "created": "➕ создан",
        "updated": "✏️ изменено",
        "status": "🔁 статус",
        "archived": "📦 в архив",
        "unarchived": "↩️ из архива",
        "assigned": "👤 ответственный",
        "published": "📢 опубликован",
        "unpublished": "🚫 снят с публикации",
        "lead_created": "📋 новая заявка",
        "lead_taken": "🙋 клиента взял",
        "lead_reassigned": "👤 заявка переназначена",
        "lead_status": "📋 статус заявки",
        "deal": "🤝 сделка",
        "deal_cancelled": "❌ сделка отменена",
    }
    lines = [f"📜 <b>История {prop.code}</b>", ""]
    if not history:
        lines.append("Пока пусто.")
    for h in history:
        who = f" — {esc(h.user.display_name)}" if h.user else ""
        label = action_labels.get(h.action, h.action)
        field = EDITABLE_FIELDS[h.field].label if h.field in EDITABLE_FIELDS else (h.field or "")
        change = ""
        if h.action in {"status", "archived", "unarchived"}:
            change = f": {esc(h.old_value)} → {esc(h.new_value)}"
        elif h.action == "updated":
            change = f" {esc(field)}: {esc(truncate(h.old_value, 40)) or '—'} → {esc(truncate(h.new_value, 40)) or '—'}"
        elif h.action in {"created"}:
            change = f" ({esc(h.new_value)})"
        elif h.action in {"lead_created", "lead_taken", "lead_reassigned", "deal", "published"}:
            change = f": {esc(h.new_value or h.old_value or '')}"
        elif h.action == "lead_status":
            change = f" {esc(h.field)}: {esc(h.old_value)} → {esc(h.new_value)}"
        elif h.action == "assigned":
            change = ""
        lines.append(f"{fmt_dt(h.created_at, tz)} {label}{change}{who}")
    return "\n".join(lines)[:4000]


# ---------------- публичные материалы (на украинском) ----------------
# Всё, что видит клиент, — на украинском языке (требование законодательства Украины).

def headline_uk(prop: Property) -> str:
    if prop.rooms or prop.property_type:
        return uk.rooms_title(prop.rooms, prop.property_type)
    return prop.title or "Об'єкт"


def price_label_uk(prop: Property) -> str:
    if prop.price is None:
        return "ціна за запитом"
    suffix = "/міс" if prop.offer_type == OfferType.RENT else ""
    return fmt_money(prop.price, prop.currency) + suffix


def floor_label_uk(prop: Property) -> str | None:
    if prop.floor is None and prop.floors_total is None:
        return None
    if prop.floor is not None and prop.floors_total:
        return f"{prop.floor}/{prop.floors_total}"
    if prop.floor is not None:
        return str(prop.floor)
    return f"поверховість {prop.floors_total}"


def location_line_uk(prop: Property) -> str:
    parts = []
    district = uk.place(prop.district)
    if district:
        parts.append(district if "район" in district.lower() else f"{district} район")
    city = uk.place(prop.city)
    if city:
        parts.append(city)
    return ", ".join(parts)


def offer_uk(prop: Property) -> str:
    return "продаж" if prop.offer_type == OfferType.SALE else "оренда"



def public_hashtags(prop: Property, agency_tag: str | None = None) -> str:
    tags = []
    district = uk.place(prop.district)
    if district:
        tags.append(hashtag(district.replace(" район", "")))
    city = uk.place(prop.city)
    if city:
        tags.append(hashtag(city))
    if prop.rooms:
        tags.append(f"#{prop.rooms}кімн")
    tags.append(f"#{offer_uk(prop)}")
    tags.append(f"#{prop.code.replace('-', '')}")
    if agency_tag:
        tags.append(hashtag(agency_tag))
    return " ".join(t for t in tags if t)


def public_caption(prop: Property, agency_name: str | None = None, limit: int = CAPTION_LIMIT) -> str:
    """Публикация с фото (запасной формат). ТОЛЬКО публичные данные: без собственника, телефонов,
    риелтора, комиссии, адреса."""
    head = [f"<b>{offer_uk(prop).capitalize()}: {esc(headline_uk(prop))}</b>"]
    loc = location_line_uk(prop)
    if loc:
        head.append(f"📍 {esc(loc)}")
    details = []
    if prop.area:
        details.append(f"📐 {fmt_number(prop.area)} м²")
    fl = floor_label_uk(prop)
    if fl:
        details.append(f"🏢 поверх {fl}")
    if details:
        head.append(" · ".join(details))
    head.append(f"💰 <b>{esc(price_label_uk(prop))}</b>")
    tail = ["", f"№ {prop.code}", public_hashtags(prop, agency_name)]
    base_len = len("\n".join(head + tail)) + 4
    desc = clean_public_text(prop.description, remove=[prop.owner_name, prop.owner_phone, prop.address])
    budget = max(0, min(450, limit - base_len - 20))
    body = []
    if desc and budget > 60:
        body = ["", esc(truncate(desc, budget))]
    text = "\n".join(head + body + tail)
    return text[:limit]


def public_post_text(prop: Property, agency_tag: str | None = None, telegraph_url: str | None = None) -> str:
    """Короткий пост для канала: суть объекта + хэштеги; всё подробное — на Telegraph-странице.

    ТОЛЬКО публичные данные: без собственника, телефонов, точного адреса, риелтора и комиссии.
    """
    lines = [f"🏠 <b>{esc(headline_uk(prop))}</b> · {offer_uk(prop)}"]
    details = []
    if prop.area:
        details.append(f"📐 {fmt_number(prop.area)} м²")
    fl = floor_label_uk(prop)
    if fl:
        details.append(f"🏢 поверх {fl}")
    if details:
        lines.append(" · ".join(details))
    lines.append(f"💰 <b>{esc(price_label_uk(prop))}</b>")
    loc = location_line_uk(prop)
    if loc:
        lines.append(f"📍 {esc(loc)}")
    lines += ["", public_hashtags(prop, agency_tag)]
    if telegraph_url:
        lines.append(f'<a href="{esc(telegraph_url)}">📸 Фото та детальний опис</a>')
    return "\n".join(lines)


def telegraph_nodes(prop: Property, photo_urls: list[str], booking_url: str | None = None) -> list:
    """Контент Telegraph-страницы (без внутренних данных CRM)."""
    nodes: list = []
    for url in photo_urls[:20]:
        nodes.append({"tag": "figure", "children": [{"tag": "img", "attrs": {"src": url}}]})
    nodes.append({"tag": "h3", "children": [headline_uk(prop)]})
    info = [f"💰 Ціна: {price_label_uk(prop)}"]
    loc = location_line_uk(prop)
    if loc:
        info.append(f"📍 Розташування: {loc}")
    if prop.rooms:
        info.append(f"🚪 Кімнат: {prop.rooms}")
    if prop.area:
        info.append(f"📐 Площа: {fmt_number(prop.area)} м²")
    fl = floor_label_uk(prop)
    if fl:
        info.append(f"🏢 Поверх: {fl}")
    for line in info:
        nodes.append({"tag": "p", "children": [line]})
    desc = clean_public_text(prop.description, limit=3000, remove=[prop.owner_name, prop.owner_phone, prop.address])
    if desc:
        nodes.append({"tag": "h4", "children": ["Опис"]})
        for para in desc.split("\n"):
            if para.strip():
                nodes.append({"tag": "p", "children": [para.strip()]})
    if prop.features:
        nodes.append({"tag": "h4", "children": ["Характеристики"]})
        items = [
            {"tag": "li", "children": [f"{k}: {clean_public_text(str(v), 120, remove=[prop.owner_name])}"]}
            for k, v in list(prop.features.items())[:25]
            if clean_public_text(str(v), 120, remove=[prop.owner_name])
        ]
        if items:
            nodes.append({"tag": "ul", "children": items})
    if booking_url:
        nodes.append({"tag": "h4", "children": ["Записатися на перегляд"]})
        nodes.append({"tag": "p", "children": [{"tag": "a", "attrs": {"href": booking_url}, "children": ["📅 Залишити заявку"]}]})
    nodes.append({"tag": "p", "children": [{"tag": "i", "children": [f"№ {prop.code}"]}]})
    return nodes


def map_url(prop: Property) -> str | None:
    """Ссылка «На карте». Без разрешения точной точки — координаты округляются (~500 м) или только район."""
    if prop.latitude is not None and prop.longitude is not None:
        if prop.show_exact_location:
            lat, lon = prop.latitude, prop.longitude
        else:
            lat, lon = round(prop.latitude / 0.005) * 0.005, round(prop.longitude / 0.005) * 0.005
        return f"https://www.google.com/maps/search/?api=1&query={lat:.4f},{lon:.4f}"
    parts = []
    if prop.show_exact_location and prop.address:
        parts.append(prop.address)
    district = uk.place(prop.district)
    if district:
        parts.append(district if "район" in district.lower() else f"{district} район")
    city = uk.place(prop.city)
    if city:
        parts.append(city)
    if not parts or (len(parts) == 1 and not prop.city and not prop.district):
        return None
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(", ".join(parts))


# ---------------- заявки ----------------

def lead_group_card(lead: Lead) -> str:
    """Карточка заявки для рабочего чата: БЕЗ контактов клиента."""
    prop = lead.prop
    lines = [f"📋 <b>Новая заявка {lead.code}</b>", ""]
    if prop:
        lines.append(f"🏠 {esc(short_line(prop))}")
        lines.append(f"Ответственный за объект: {user_label(prop.responsible)}")
    if lead.preferred_time:
        lines.append(f"🕒 Желаемое время: {esc(clean_public_text(lead.preferred_time, 100))}")
    lines += ["", "Кто первый нажмет «Взять клиента» — получает заявку."]
    return "\n".join(lines)


def lead_taken_group_card(lead: Lead) -> str:
    prop = lead.prop
    lines = [f"📋 <b>Заявка {lead.code}</b>"]
    if prop:
        lines.append(f"🏠 {esc(short_line(prop))}")
    lines.append(f"✅ Клиента взял(а): {user_label(lead.assigned_realtor)}")
    return "\n".join(lines)


def lead_private_card(lead: Lead, tz: ZoneInfo) -> str:
    """Полная карточка заявки — только для владельца и назначенного риелтора."""
    prop = lead.prop
    lines = [f"📋 <b>Заявка {lead.code}</b> — {LEAD_STATUS_LABELS[lead.status]}", ""]
    if prop:
        lines.append(f"🏠 {esc(short_line(prop))}")
    lines.append(f"👤 Клиент: {esc(lead.client_name or '—')}")
    if lead.client_username:
        lines.append(f"✈️ Telegram: @{esc(lead.client_username)}")
    if lead.client_telegram_id:
        lines.append(f'🆔 <a href="tg://user?id={lead.client_telegram_id}">написать клиенту</a> (ID {lead.client_telegram_id})')
    lines.append(f"📞 Телефон: {esc(lead.phone or '—')}")
    if lead.preferred_time:
        lines.append(f"🕒 Желаемое время: {esc(lead.preferred_time)}")
    if lead.comment:
        lines.append(f"💬 Комментарий: {esc(lead.comment)}")
    if lead.viewing_at:
        lines.append(f"📅 Просмотр: {esc(lead.viewing_at)}")
    if lead.internal_comment:
        lines.append(f"🔒 Заметка: {esc(lead.internal_comment)}")
    lines += ["", f"Риелтор: {user_label(lead.assigned_realtor)}", f"Создана: {fmt_dt(lead.created_at, tz)}"]
    if lead.assigned_at:
        lines.append(f"Взята: {fmt_dt(lead.assigned_at, tz)}")
    return "\n".join(lines)


def lead_line(lead: Lead, show_client: bool) -> str:
    who = f" · {lead.client_name}" if show_client and lead.client_name else ""
    prop = lead.prop.code if lead.prop else "—"
    return f"{lead.code} · {prop}{who} · {LEAD_STATUS_LABELS[lead.status]}"


# ---------------- сделки ----------------

def deal_group_card(deal: Deal) -> str:
    """Поздравление в теме «Сделки». Сумма сделки не публикуется, комиссия — по ТЗ."""
    realtor = user_label(deal.realtor)
    prop = deal.prop.code if deal.prop else "—"
    return "\n".join(
        [
            "🎉 <b>СДЕЛКА ЗАКРЫТА</b>",
            "",
            f"Риелтор: {realtor}",
            f"Объект: №{prop}",
            f"Тип: {DEAL_TYPE_LABELS[deal.deal_type]}",
            f"Комиссия: {fmt_money(deal.commission, deal.currency)}",
            f"Дата: {fmt_date(deal.deal_date)}",
            "",
            "Поздравляем с закрытием сделки! 🎉",
        ]
    )


def deal_card(deal: Deal, tz: ZoneInfo) -> str:
    prop = deal.prop
    lines = [
        f"🤝 <b>Сделка {deal.code}</b> — {DEAL_STATUS_LABELS[deal.status]}",
        "",
        f"🏠 Объект: {esc(short_line(prop)) if prop else '—'}",
        f"👤 Риелтор: {user_label(deal.realtor)}",
    ]
    if deal.lead:
        lines.append(f"🧑 Клиент: {esc(deal.lead.client_name or deal.lead.code)} ({deal.lead.code})")
    lines += [
        f"Тип: {DEAL_TYPE_LABELS[deal.deal_type]}",
        f"Сумма сделки: {fmt_money(deal.deal_amount, deal.currency)}",
        f"Комиссия: {fmt_money(deal.commission, deal.currency)}",
        f"Дата: {fmt_date(deal.deal_date)}",
    ]
    if deal.comment:
        lines.append(f"💬 {esc(deal.comment)}")
    lines.append(f"\nЗаписана: {fmt_dt(deal.created_at, tz)}")
    return "\n".join(lines)


def deal_line(deal: Deal) -> str:
    prop = deal.prop.code if deal.prop else "—"
    mark = "" if deal.status.value == "confirmed" else " ❌"
    return f"{deal.code} · {prop} · {DEAL_TYPE_LABELS[deal.deal_type]} · {fmt_money(deal.commission, deal.currency)} · {fmt_date(deal.deal_date)}{mark}"
