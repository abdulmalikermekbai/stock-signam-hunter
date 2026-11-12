# Stock Signal Hunter

Личная система сигналов по акциям (LONG, spot, swing 2–8 недель) → Telegram + дашборд на GitHub Pages.
Работает бесплатно: вычисления в **GitHub Actions**, статический дашборд на **GitHub Pages**
(Pages сам ничего не исполняет — только показывает `docs/signals.json`).

## Запуск за 10 шагов
1. Создайте публичный репозиторий на GitHub, загрузите содержимое этой папки в ветку `main`.
2. Telegram: @BotFather → `/newbot` → получите токен. Напишите боту `/start`.
3. Chat id: откройте `https://api.telegram.org/bot<TOKEN>/getUpdates` после сообщения боту → `message.chat.id`.
4. Repo → Settings → Secrets and variables → Actions: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SEC_USER_AGENT` ("Имя email").
5. Settings → Pages → Source: **GitHub Actions**.
6. Settings → Actions → General → Workflow permissions: **Read and write**.
7. Actions → `scan` → Run workflow (первый запуск, смотрите логи).
8. Actions → `backtest` → Run workflow (честная статистика сетапа).
9. Отредактируйте `config.yaml`: капитал, риск, тарифы брокеров, `halal.confirmed`, universe.
10. Дашборд: `https://<user>.github.io/<repo>/`.

Локально: `pip install -r requirements.txt && pytest && python -m ssh.run analyze AAPL --dry`.

## Что реализовано
| Модуль | Файл | Статус |
|---|---|---|
| EMA20/50/200, RSI, ATR(14), RelVolume, rolling-VWAP(20), MACD, weekly | `ssh/indicators.py` | ✅ тесты |
| Уровни (pivot+кластеры, касания, зеркальные), пробой→ретест, не гнаться за свечой | `ssh/levels.py` | ✅ тесты |
| Ложный пробой (хвост / 2 свечи / combo), bear trap | `ssh/levels.py` | ✅ тесты |
| Гэпы Common/Breakaway/Runaway/Exhaustion + заполнение | `ssh/levels.py` | ✅ тесты |
| Accumulation / Distribution (score 0–100) | `ssh/levels.py` | ✅ тесты |
| Фундаментальный чек-лист из PDF (N/A ≠ пройдено) | `ssh/fundamentals.py` | ✅ |
| Халяль (запрещённые отрасли, долг/кэш к капитализации, ручное подтверждение, срок) | `ssh/fundamentals.py` | ⚠️ см. ниже |
| Новости: Google News/Yahoo RSS/SEC 8-K, уровни источников, кросс-проверка, задержка обнаружения | `ssh/news.py` | ⚠️ не проверено вживую |
| Риск: размер позиции, KZT, слиппедж, комиссии из config | `ssh/risk.py` | ✅ |
| Signal Engine, жёсткие гейты, BUY/WAIT/WATCHLIST/AVOID | `ssh/engine.py` | ✅ тесты |
| Telegram: сигналы, дайджест, дедупликация, команды | `ssh/telegram.py` | ⚠️ не проверено вживую |
| Бэктест (без look-ahead, in/out-of-sample) | `ssh/backtest.py` | ✅ дымовой тест |
| Дашборд | `docs/index.html` | ✅ статический |

## Күнделікті жұмыс (қысқаша)
1. `scan` күн сайын нарық жабылған соң жұмыс істейді → Telegram-ға **дайджест** (барлық компания, қиылмай) + BUY болса толық жоспар.
2. Дайжесте **🟡 «Сетап дайын — халяль күтуде»** бөлімі шығады: Entry/SL/TP1/көлем дайын, тек халялды сіз тексеруіңіз керек.
3. Musaffa / Zoya / 10-K-дан тексеріп, ботқа **`/halal TICKER`** деп жазыңыз (бот ~15 мин ішінде өңдейді) → келесі сканда BUY бере алады. Растау 90 күннен кейін ескіреді; алып тастау: `/unhalal TICKER`.
4. BUY алған соң SL/TP1-ге тисе — бот өзі хабарлайды (🎯/🛑). Барлық нәтиже сайттағы **«Нәтижелер»** бетінде (win rate, avg R).
5. Алты балл: Signal · Halal · Momentum · Entry · Catalyst · Risk (100 = қауіпсіз) + жалпы рейтинг.

## Честные ограничения
- **Живые API не проверены**: в среде разработки Yahoo/Telegram недоступны (403 прокси). Логика покрыта тестами
  на синтетических рядах; реальную работу проверьте первым запуском `scan` и пришлите логи.
- **Халяль**: полноценный AAOIFI требует долю процентного дохода (<5%) и 36-мес. капитализацию — бесплатно
  автоматизировать нельзя. Поэтому `HALAL` ставится только при `halal.confirmed` (ручная проверка) + пройденных порогах.
  Пока список пуст, максимум — `WAIT`. Это сделано намеренно.
- **Задержка новостей**: cron GitHub Actions ~15 мин + джиттер, Yahoo RSS не мгновенный. «Первым в мире» не гарантируется;
  система **измеряет** задержку (`latency_min`). Статусы MISLEADING/FALSE автоматически не ставятся.
- **Не реализовано**: QQE, Form 4/13F/13D парсинг (есть лишь счётчик покупок инсайдеров от Yahoo), 4H/1H (бесплатные
  интрадей-данные ненадёжны — вход ищется на дневках+недельке), ROI, KASE (в Yahoo почти нет бумаг; `KSPI` — это NASDAQ),
  Instagram (нельзя без обхода ограничений), SQLite (состояние в JSON/JSONL в `data/`), Next.js (статический HTML).
- Комиссии Freedom/Paydax **не заданы** — впишите из тарифов в `config.yaml`.
- Курс USD/KZT берётся из `KZT=X`; при сбое используется `fx_fallback_usdkzt` (в сообщении это отмечено).
- DST: cron в UTC; весной переведите `scan.yml`/`news.yml` на час раньше.

Не финансовая рекомендация. Сначала наблюдайте paper-статистику (`data/history.jsonl`, `/status`, бэктест) и только затем рискуйте деньгами.
