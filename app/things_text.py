"""Семейная память («запомни», «вспомни»): разбор фраз и поиск записи по словам.

Распознанная речь и вопрос говорятся по-разному («батарейки» / «где лежит батарейка»), а смысловой
поиск сервера памяти на коротких словах путается (у него «пылесос» близок к «батарейкам»). Поэтому
записи ищутся сначала по словам с мягкой проверкой окончаний, а смысловая оценка сервера идёт вторым
шагом и только при высоком сходстве.
"""

import re
from dataclasses import dataclass

# Слова вопроса и просьбы, которые не называют предмет.
STOP = frozenset(
    [
        "где",
        "лежит",
        "лежат",
        "находится",
        "находятся",
        "хранится",
        "хранятся",
        "мои",
        "моя",
        "мой",
        "наши",
        "наш",
        "наша",
        "у",
        "нас",
        "пожалуйста",
        "запомни",
        "запомните",
        "вспомни",
        "вспомните",
        "знаешь",
        "про",
        "расскажи",
        "что",
        "это",
        "подскажи",
        "скажи",
        "найди",
        "ли",
    ]
)
# Здесь кончается название предмета и начинается место: «батарейки | в верхнем ящике».
PLACE_WORDS = frozenset(
    [
        "в",
        "во",
        "на",
        "под",
        "за",
        "над",
        "около",
        "возле",
        "между",
        "рядом",
        "у",
        "лежит",
        "лежат",
        "находится",
        "находятся",
        "хранится",
        "хранятся",
    ]
)
# Порог смыслового сходства, когда слов не хватает. Шкала сервера: настоящие совпадения 0,78–0,92,
# посторонние для вещей 0,65–0,72, но фразы схожей формы («три плюс три» к «два плюс два равно
# пять») получают 0,87, а настоящий синоним («когда у Полины праздник») всего 0,85. Лучше «не
# нашёл», чем уверенная ошибка, поэтому порог высокий.
SEMANTIC_ONLY = 0.88
_NUMBER_FORMS = {
    0: "ноль нуль",
    1: "один одна одно одну одного одной",
    2: "два две двух двум двумя",
    3: "три трех трем тремя",
    4: "четыре четырех четырем четырьмя",
    5: "пять пяти пятью",
    6: "шесть шести шестью",
    7: "семь семи семью",
    8: "восемь восьми восемью",
    9: "девять девяти девятью",
    10: "десять десяти десятью",
    20: "двадцать двадцати",
    30: "тридцать тридцати",
    100: "сто ста",
}
_NUMBERS = {form: value for value, forms in _NUMBER_FORMS.items() for form in forms.split()}
MAX_TEXT = 300


def words(text: str) -> list[str]:
    return re.sub(r"[^а-яa-z0-9]+", " ", text.lower().replace("ё", "е")).split()


def clean(text: str) -> str:
    """Фраза для хранения: без лишних пробелов, точки в конце и повтора «запомни»."""
    parts = " ".join(text.split()).rstrip(" .!?;,")
    lowered = words(parts)
    while lowered and lowered[0] in {"запомни", "запомните", "вспомни", "вспомните", "что"}:
        parts = parts.split(" ", 1)[1] if " " in parts else ""
        lowered = lowered[1:]
    return parts[:MAX_TEXT].strip()


def item_and_place(text: str) -> tuple[list[str], list[str]]:
    """«Ключи от дачи на крючке» → (['ключи', 'от', 'дачи'], ['на', 'крючке'])."""
    tokens = words(text)
    for i, token in enumerate(tokens):
        if token in PLACE_WORDS and i > 0:
            return tokens[:i], tokens[i:]
    return tokens, []


def _same_word(a: str, b: str) -> bool:
    """Одно слово в разных формах: общее начало, различие не больше последней буквы."""
    if a == b:
        return True
    common = 0
    for x, y in zip(a, b, strict=False):
        if x != y:
            break
        common += 1
    return common >= max(3, min(len(a), len(b)) - 1)


def _meaningful(tokens: list[str]) -> list[str]:
    long = [t for t in tokens if len(t) >= 3 and t not in STOP]
    return long or [t for t in tokens if t not in STOP]


def _found(token: str, among: list[str]) -> bool:
    return any(_same_word(token, other) for other in among)


def same_item(a: list[str], b: list[str]) -> bool:
    """Тот же предмет: каждое слово есть в другом и наоборот («батарейки» ≠ «батарейки AAA»)."""
    left, right = _meaningful(a), _meaningful(b)
    if not left or not right:
        return False
    return all(_found(t, right) for t in left) and all(_found(t, left) for t in right)


@dataclass
class Record:
    content: str
    content_hash: str
    item: list[str]
    created: float = 0.0
    score: float = 0.0  # смысловое сходство от сервера, 0 если сервер запись не называл


def lexical(query: list[str], record: Record) -> float:
    """Доля слов вопроса, найденных в записи; слова предмета весят вдвое больше слов места."""
    if not query:
        return 0.0
    in_content = words(record.content)
    total = 0.0
    for token in query:
        if _found(token, record.item):
            total += 1.0
        elif _found(token, in_content):
            total += 0.5
    return total / len(query)


def query_words(text: str) -> list[str]:
    return _meaningful(words(text))


def kind_and_item(text: str) -> tuple[str, list[str]]:
    """``location`` для «предмет + место» (замена старой записи о том же предмете), иначе ``fact``.

    У факта («у Полины день рождения 12 мая») предмет — вся фраза: все её слова ищутся наравне."""
    item, place = item_and_place(text)
    if place:
        return "location", item
    return "fact", words(text)


def numbers(tokens: list[str]) -> set[int]:
    """Числа в словах («два», «пяти») и цифрах: смысловая модель путает «два» и «три»."""
    return {int(t) if t.isdigit() else _NUMBERS[t] for t in tokens if t.isdigit() or t in _NUMBERS}


def pick(query: str, records: list[Record]) -> tuple[list[Record], int]:
    """До двух записей для чтения вслух (лучшая первой) и сколько равных осталось непрочитанными."""
    q = query_words(query)
    if not q:
        return [], 0
    # В записи должны быть все числа вопроса: «два плюс три» не отвечает про «два плюс два».
    asked = numbers(words(query))
    records = [r for r in records if asked <= numbers(words(r.content))]
    scored = [(lexical(q, r), r) for r in records]
    best = max((s for s, _ in scored), default=0.0)
    need = 0.5 if len(q) > 1 else 0.99
    if best >= need:
        top = [r for s, r in scored if s >= best - 1e-9]
        top.sort(key=lambda r: (r.score, r.created), reverse=True)
        return top[:2], max(0, len(top) - 2)
    semantic = sorted((r for r in records if r.score >= SEMANTIC_ONLY), key=lambda r: -r.score)
    if not semantic:
        return [], 0
    chosen = [semantic[0]]
    if len(semantic) > 1 and semantic[1].score >= semantic[0].score - 0.03:
        chosen.append(semantic[1])
    return chosen, max(0, len(semantic) - len(chosen))
