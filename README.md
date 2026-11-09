# Umra monitoring — дашборд сравнения цен

Внутренний дашборд: пакеты ETIHAD (Safar Travel) против SAFO (Safo Marva Tour) и MANDARIN (Family Mandarin).

## Как это работает
Цены больше не лежат в страницах. Схема:

1. **Collector** (Python, `collector/`) читает сайты MANDARIN, SAFO и API ETIHAD и пишет цены в базу Supabase.
2. **Supabase** хранит данные (Postgres + вход по логину + права доступа RLS).
3. **Дашборд** (`index.html`) после входа читает данные из Supabase через `live.js` и сам обновляется раз в 5 минут.
4. **Ручной ввод** (`kiritish.html`) — таблица цен для editor/admin. Каждое изменение пишется в журнал.

Без входа сайт ничего не показывает.

## Как запустить
- Настройка Supabase и ключей: `supabase/` и `config.js` (только publishable-ключ, секретные ключи сюда нельзя).
- Collector: `cd collector`, потом `set -a; source .env; set +a; python3 -m collector.run --source all`. Подробно: `LIVE-DATA.md`.
- Локально сайт: `python3 -m http.server 8000` и открыть `http://localhost:8000`.
- Тесты: `python3 -m unittest discover` (в `collector/`) и `node --test tests-web/*.test.js`.
- Сервер (Docker, HTTPS, бэкапы): `DEPLOY.md`. Безопасность: `SECURITY-CHECKLIST.md`.

## Файлы
- `index.html` — дашборд. `live.js` — загрузка данных из Supabase. `auth.js` — вход и роли.
- `kiritish.html`, `kiritish.js` — ручной ввод цен (`MANUAL-ENTRY.md`).
- `collector/` — сборщик цен и тесты. `supabase/` — SQL-миграции. `deploy/` — Docker, Caddy, скрипты.
- `data/` — старые статические данные (снимок 4–5 ноября 2026), остались для услуг и запасного варианта.
- `images/` — фото пакетов (`<id>-cover.webp`, `<id>-hq.webp`).
- `support.js` — рантайм шаблонов Claude Design. Не править.
- `sw.js` — офлайн-режим (PWA). `tests-web/` — тесты страниц.

## Секреты (важно)
- `collector/.env` и `config.js` не коммитить. Секретный ключ Supabase только в `collector/.env`.
- В браузер идёт только publishable-ключ.

## Откуда данные
- ETIHAD — блок `window.ETIHAD_DATA` на внутреннем сайте showroom (адрес не публикуется) (пакеты, цены по датам и числу людей, отели, питание, трансфер, обложки).
- MANDARIN — JSON `https://showroom.familymandarin.uz/showroom/data`.
- SAFO — сайт safo-marva-tour-paketlari.uz (даты и карточки пакетов).

## Как подбираются пакеты конкурентов
Категория (Premium → PREMIUM, Lux → LUX, Comfort → KOMFORT/EKONOM/STANDART), разница ночей не больше 2, дата вылета в пределах ±3 дней. Это приблизительное соответствие, не идентичные пакеты.

## Ilova (PWA) sifatida o'rnatish
Sayt oddiy ilova kabi ishlaydi: telefon yoki kompyuterga o'rnatiladi, o'z ikonkasi bor, oflayn ham ochiladi.

**Boshqalar ishlatishi uchun (GitHub Pages, bepul):**
1. Papkani GitHub'ga push qiling.
2. Repo → Settings → Pages → Branch: `main`, papka `/ (root)` → Save.
3. 1–2 daqiqadan so'ng havola chiqadi: `https://<login>.github.io/<repo>/`
4. Shu havolani yuboring.

**O'rnatish:**
- iPhone: Safari → Share → «Add to Home Screen».
- Android: Chrome → ⋮ → «Install app».
- Kompyuter (Chrome/Edge): manzil qatoridagi «Install» tugmasi.

Yangi versiya chiqarganda `sw.js` ichidagi `VERSION` ni o'zgartiring (masalan `umra-v2`) — foydalanuvchilarda ilova yangilanadi.
Eslatma: `file://` orqali (ikki marta bosib) ochilganda ilova rejimi ishlamaydi — faqat http(s) havola orqali.
