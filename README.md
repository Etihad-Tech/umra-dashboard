# Umra monitoring — дашборд сравнения цен

Внутренний дашборд: пакеты ETIHAD (Safar Travel) против SAFO (Safo Marva Tour) и MANDARIN (Family Mandarin).

## Как открыть
Открой `index.html` в браузере (двойной клик). Нужен интернет только для шрифтов Google Fonts.

## Файлы
- `index.html` — страница (HTML + CSS + JavaScript, шаблон Claude Design с подстановками `{{...}}`).
- `data/` — все данные, по одному файлу на тему (снимок от 4–5 ноября 2026). Открывай и правь в любом редакторе:
  - `info.js` — дата снимка (`asOf`, `asOfText`) и список компаний.
  - `packages.js` — НАШИ пакеты ETIHAD: название, ночи, отели, цены `bands` (от, до, 4 чел, 3 чел, 2 чел, 1 чел), фото.
  - `services.js` — услуги (B = ETIHAD, S = SAFO, F = MANDARIN).
  - `competitors.js` — пакеты конкурентов; `d`: дата → [4, 3, 2, 1 чел, мест осталось].
  - `prices.js` — цены по датам вылета: компания → категория → дата.
  - `ours.js` — наши ближайшие вылеты: цена, места, квоты.
  - `events.js` — события (изменения цен).
- `images/` — фото пакетов (`<id>-cover.webp` — обложка, `<id>-hq.webp` — большое). Чтобы заменить фото, подставь файл с тем же именем.
- `support.js` — рантайм шаблонов Claude Design. Не править.
- `_backup/data.original.js` — старый единый `data.js` на случай отката. Можно удалить.

Совет: после правки данных просто обнови страницу в браузере (Cmd+R).

## Откуда данные
- ETIHAD — блок `window.ETIHAD_DATA` на сайте umrah-lyn8aqdgmc8e.18.185.13.51.sslip.io (пакеты, цены по датам и числу людей, отели, питание, трансфер, обложки).
- MANDARIN — JSON `https://showroom.familymandarin.uz/showroom/data`.
- SAFO — сайт safo-marva-tour-paketlari.uz (даты и карточки пакетов).

## Как подбираются пакеты конкурентов
Категория (Premium → PREMIUM, Lux → LUX, Comfort → KOMFORT/EKONOM/STANDART), разница ночей не больше 2, дата вылета в пределах ±3 дней. Это приблизительное соответствие, не идентичные пакеты.

## План: обновление в реальном времени
Сборщик на сервере (Python, раз в 15–30 мин) читает три сайта, пишет JSON и шлёт уведомления в Telegram при изменении цен. Дашборд читает этот JSON вместо папки `data/`. Пока не сделано.

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
