# AGENTS.md — правила работы с субагентами

Полный контекст проекта — в CONTEXT.md (читать первым). Специализированные субагенты
описаны в `.kimi-code/agents/` (`test-engineer`, `tz-reviewer`, `content-designer`, `adapter-dev`).

## Карта владения файлами

| Зона | Кто правит |
|---|---|
| Ядро: `app/bus.py`, `app/db.py`, `app/config.py`, `app/models_game.py`, `alembic/versions/*`, `app/economy.py`, `app/expeditions.py`, `app/battles.py` | только главный агент |
| `tests/**` | test-engineer (главный — по мелочи) |
| `app/seeds.py`, значения `config_params` | content-designer |
| `adapters/telegram/**`, `adapters/payments/<новый>.py`, `adapters/http/*_page.py` | adapter-dev |
| `docs/**`, `CONTEXT.md` | любой агент по брифу |
| Прочее в `app/` | главный агент |

## Бриф-контракт (каждую делегацию)

Бриф обязан содержать: цель; разрешённые пути; запрещённые зоны; команды проверки
(`ruff check .`, `mypy app core worker adapters tests`, `pytest -q` с TEST_DATABASE_URL
для интеграционных); формат отчёта (что изменено / как проверено / что НЕ проверено).
Субагент не видит историю сессии — бриф самодостаточен.

## Ворота слияния (главный агент, обязательно)

1. `ruff check .` → 2. `mypy --strict` → 3. `pytest -q` (157+ тестов) → 4. смоук
   консольным адаптером при затронутых сценариях → 5. для изменений в `app/`:
   вердикт `tz-reviewer` (PASS / PASS_WITH_NOTES / FAIL; FAIL — не сливаем).

## Конфликты

- Один писатель на файл в момент времени; параллелим только непересекающиеся зоны
  (см. карту). Общее ядро в брифах субагентам — read-only.
- Субагентам запрещён рефакторинг существующего кода вне их зоны.
- Баг продукта, найденный субагентом, — в отчёт, не в правку.

## Запуск окружения (Windows)

`.venv` в корне; PostgreSQL 17 (пароль postgres), БД `postgres` (dev) и
`space_mmo_test` (тесты). Секреты в `.env` (не коммитить). Перед пересозданием
тестовой БД убить висячие python-процессы (блокируют dropdb).
