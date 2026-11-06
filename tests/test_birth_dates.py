from datetime import UTC, date, datetime, timedelta

import pytest

from app.db.database import get_connection, init_db
from app.dependencies import get_people_service
from app.profiles import age_label, age_on, zodiac_for_birth_date
from app.services.people import PeopleService
from tests.test_horoscope import setup as horoscope_setup
from tests.test_people import DEVICE, KATYA, OTHER, PAPA, join, person, phone_sync


def setup(client):
    horoscope, _, now = horoscope_setup(client)
    people = PeopleService(clock=lambda: now[0])
    client.app.dependency_overrides[get_people_service] = lambda: people
    return people, horoscope, now


def save(client, value, person_id=KATYA["id"], **extra):
    return client.post(
        "/people/birth-date",
        data={
            "person": person_id,
            "birth_date": value,
            **extra,
        },
    )


def katya(people):
    return next(p for p in people.all() if p.id == KATYA["id"])


def test_date_is_optional_and_derives_age_and_horoscope_without_a_manual_sign(client):
    people, horoscope, _ = setup(client)
    assert katya(people).birth_date is None and katya(people).age is None
    response = save(client, "2018-04-03")
    assert response.status_code == 200
    assert 'value="2018-04-03"' in response.text
    assert "Возраст: 8 лет" in response.text
    assert "Знак по дате: Овен" in response.text
    profile = katya(people)
    assert profile.birth_date == date(2018, 4, 3) and profile.age == 8
    assert profile.zodiac_sign == ""  # Derived values are never stored as manual choices.
    result = horoscope.today(KATYA["id"])
    assert result.status == "ok" and result.sign == "aries"
    assert "birth_date" not in result.model_dump() and "age" not in result.model_dump()


def test_explicit_sign_overrides_birthday_and_can_be_switched_back_to_automatic(client):
    people, horoscope, _ = setup(client)
    horoscope.set_sign(KATYA["id"], "leo")
    page = save(client, "2018-04-03")
    assert "Для гороскопа: Лев · выбран вручную" in page.text
    assert horoscope.today(KATYA["id"]).sign == "leo"
    assert katya(people).derived_zodiac_sign == "aries"
    client.post("/people/zodiac", data={"person": KATYA["id"], "sign": ""})
    assert horoscope.today(KATYA["id"]).sign == "aries"
    assert "Для гороскопа: Овен · по дате рождения" in client.get("/people").text


@pytest.mark.parametrize(
    "bad",
    [
        "2027-01-01",
        "2025-02-29",
        "2018-02-30",
        "20180403",
        "03.04.2018",
        "2018-04-03T00:00:00",
        "0000-01-01",
        "not-a-date",
    ],
)
def test_invalid_or_future_date_does_not_replace_a_valid_date(client, bad):
    people, _, _ = setup(client)
    save(client, "2018-04-03")
    page = save(client, bad)
    assert 'role="alert"' in page.text
    assert katya(people).birth_date == date(2018, 4, 3)


def test_birthday_can_be_today_and_age_updates_at_midnight_in_home_timezone(client):
    people, _, now = setup(client)
    save(client, "2026-11-06")
    assert katya(people).age == 0
    save(client, "2018-11-07")
    now[0] = datetime(2026, 11, 6, 20, 59, tzinfo=UTC).timestamp()
    assert katya(people).age == 7
    now[0] += 60  # Midnight Moscow, still Nov 6 UTC.
    assert katya(people).age == 8


def test_removing_date_removes_age_and_automatic_sign_but_preserves_manual_sign(client):
    people, horoscope, _ = setup(client)
    save(client, "2018-04-03")
    page = save(client, "2018-04-03", clear="1")  # Explicit Delete button.
    assert katya(people).birth_date is None and katya(people).age is None
    assert "Возраст:" not in page.text
    assert horoscope.today(KATYA["id"]).status == "sign_missing"
    horoscope.set_sign(KATYA["id"], "leo")
    save(client, "2018-04-03")
    save(client, "")  # Clearing the date input is also supported.
    assert horoscope.today(KATYA["id"]).sign == "leo"
    assert katya(people).birth_date is None


def test_unknown_and_pending_person_cannot_receive_birth_date(client):
    people, _, _ = setup(client)
    incoming = person("ffffffffffff", "Другой человек", range(200, 210))
    join(client, OTHER, "Другой телефон", [incoming])
    for pid in ["missing", incoming["id"]]:
        assert "Выберите человека дома" in save(client, "2018-04-03", pid).text
    assert katya(people).birth_date is None
    assert all(p.birth_date is None for p in people.incoming())


def test_migration_preserves_manual_sign_and_faces_and_is_idempotent(client):
    people, horoscope, _ = setup(client)
    horoscope.set_sign(KATYA["id"], "leo")
    samples = katya(people).samples
    with get_connection() as conn:
        conn.execute("ALTER TABLE house_people DROP COLUMN birth_date")
    init_db()
    init_db()
    assert katya(people).birth_date is None
    assert katya(people).samples == samples
    assert horoscope.today(KATYA["id"]).sign == "leo"


def test_rename_merge_and_restart_keep_households_birthday_and_person_isolation(client):
    people, _, _ = setup(client)
    save(client, "2018-04-03")
    client.post("/people/rename", data={"person": KATYA["id"], "to": "Катюша"})
    incoming = person("ffffffffffff", "Катя", range(200, 210))
    join(client, OTHER, "Другой телефон", [incoming])
    client.post("/people/merge", data={"person": incoming["id"], "into": KATYA["id"]})
    init_db()
    assert katya(people).birth_date == date(2018, 4, 3)
    assert next(p for p in people.all() if p.id == PAPA["id"]).birth_date is None
    # Face synchronization does not send birthdays to phones.
    sync = phone_sync(client, DEVICE)
    assert "birth_date" not in str(sync) and "2018-04-03" not in str(sync)
    download = client.post("/api/device/people/download", json={"device": DEVICE}).json()
    assert "birth_date" not in str(download) and "2018-04-03" not in str(download)


@pytest.mark.parametrize(
    ("day", "sign", "next_sign"),
    [
        ("2000-01-20", "capricorn", "aquarius"),
        ("2000-02-19", "aquarius", "pisces"),
        ("2000-03-20", "pisces", "aries"),
        ("2000-04-20", "aries", "taurus"),
        ("2000-05-20", "taurus", "gemini"),
        ("2000-06-21", "gemini", "cancer"),
        ("2000-07-22", "cancer", "leo"),
        ("2000-08-23", "leo", "virgo"),
        ("2000-09-23", "virgo", "libra"),
        ("2000-10-23", "libra", "scorpio"),
        ("2000-11-22", "scorpio", "sagittarius"),
        ("2000-12-21", "sagittarius", "capricorn"),
    ],
)
def test_all_twelve_zodiac_boundaries(day, sign, next_sign):
    born = date.fromisoformat(day)
    assert zodiac_for_birth_date(born) == sign
    assert zodiac_for_birth_date(born + timedelta(days=1)) == next_sign
    assert zodiac_for_birth_date(date(2000, 2, 29)) == "pisces"
    assert zodiac_for_birth_date(date(2000, 12, 31)) == "capricorn"


def test_age_is_complete_years_and_february_29_anniversary_is_march_1():
    born = date(2000, 2, 29)
    assert age_on(born, date(2026, 2, 28)) == 25
    assert age_on(born, date(2026, 3, 1)) == 26
    assert age_on(born, date(2024, 2, 28)) == 23
    assert age_on(born, date(2024, 2, 29)) == 24
    assert age_on(born, date(1999, 1, 1)) is None
    assert [age_label(n) for n in [0, 1, 2, 5, 11, 21, 22, 111]] == [
        "0 лет",
        "1 год",
        "2 года",
        "5 лет",
        "11 лет",
        "21 год",
        "22 года",
        "111 лет",
    ]
