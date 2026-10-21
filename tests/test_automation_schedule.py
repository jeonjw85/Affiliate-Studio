"""Service-specific UTC slots derived from one local schedule."""
from datetime import date, datetime
import pytest

from automation_fixtures import make_settings


def test_service_limits_and_preparation_window():
    from studio.automation_schedule import claimable, slots_for_day
    s = make_settings(daily_limits={'coupang': 3, 'toss': 2})
    rows = slots_for_day(s, date(2026, 10, 20), 'https://wp.example.test')
    assert [r['run_at'] for r in rows if r['provider'] == 'coupang'] == [
        '2026-10-20T00:00:00+00:00', '2026-10-20T04:00:00+00:00', '2026-10-20T08:00:00+00:00']
    assert [r['run_at'] for r in rows if r['provider'] == 'toss'] == [
        '2026-10-20T00:00:00+00:00', '2026-10-20T06:00:00+00:00']
    assert len(rows) == 5
    first = next(r for r in rows if r['provider'] == 'coupang')
    assert claimable(first, datetime.fromisoformat('2026-10-19T23:45:00+00:00'))
    assert claimable(first, datetime.fromisoformat('2026-10-19T23:55:00+00:00'))
    assert not claimable(first, datetime.fromisoformat('2026-10-19T23:55:01+00:00'))
    assert not claimable(first, datetime.fromisoformat('2026-10-19T23:44:59+00:00'))


def test_zero_invalid_counts_and_schedule():
    from studio.automation_schedule import slots_for_day, validate_settings
    zero = make_settings(daily_limits={'coupang': 0, 'toss': 0})
    assert slots_for_day(zero, date(2026, 10, 20), 'https://wp.example.test') == []
    for change in (
        {'daily_limits': {'coupang': True, 'toss': 1}},
        {'daily_limits': {'coupang': -1, 'toss': 1}},
        {'daily_limits': {'coupang': 500, 'toss': 1}},
        {'daily_limits': {'coupang': 1}},
        {'start': '21:00', 'end': '09:00'},
        {'start': '9:00'},
        {'timezone': 'Mars/Olympus'},
    ):
        with pytest.raises(ValueError):
            validate_settings({'enabled': False, 'writing': {}, 'schedule': {
                'timezone': 'Asia/Seoul', 'weekdays': [0], 'start': '09:00', 'end': '21:00',
                'daily_limits': {'coupang': 1, 'toss': 1}, **change}})
    with pytest.raises(ValueError):
        validate_settings({'enabled': True, 'writing': {}, 'schedule': {
            'timezone': 'Asia/Seoul', 'weekdays': [0], 'start': '09:00', 'end': '09:01',
            'daily_limits': {'coupang': 2, 'toss': 0}}})
    with pytest.raises(ValueError, match='편수'):
        validate_settings({'enabled': True, 'writing': {}, 'schedule': {
            'timezone': 'Asia/Seoul', 'weekdays': [0], 'start': '09:00', 'end': '21:00',
            'daily_limits': {'coupang': 0, 'toss': 0}}})


def test_dst_gap_and_fold_do_not_duplicate_slots():
    from studio.automation_schedule import slots_for_day
    spring = make_settings(timezone='America/New_York', start='01:00', end='05:00',
                           daily_limits={'coupang': 4, 'toss': 0})
    rows = slots_for_day(spring, date(2026, 3, 8), 'https://wp.example.test')
    assert [r['run_at'] for r in rows] == [
        '2026-03-08T06:00:00+00:00', '2026-03-08T07:00:00+00:00', '2026-03-08T08:00:00+00:00']
    fall = make_settings(timezone='America/New_York', start='00:00', end='03:00',
                         daily_limits={'coupang': 3, 'toss': 0})
    rows = slots_for_day(fall, date(2026, 11, 1), 'https://wp.example.test')
    assert [r['run_at'] for r in rows] == [
        '2026-11-01T04:00:00+00:00', '2026-11-01T05:00:00+00:00', '2026-11-01T07:00:00+00:00']
    assert len({r['id'] for r in rows}) == len(rows)


def test_weekday_selection_and_stable_slot_ids():
    from studio.automation_schedule import slots_for_day
    s = make_settings(weekdays=[0, 2], daily_limits={'coupang': 1, 'toss': 0})
    assert slots_for_day(s, date(2026, 10, 20), 'https://wp.example.test') == []  # Tuesday
    monday = slots_for_day(s, date(2026, 10, 19), 'https://wp.example.test')
    again = slots_for_day(make_settings(weekdays=[0, 2], daily_limits={'coupang': 1, 'toss': 0}),
                          date(2026, 10, 19), 'https://wp.example.test')
    assert monday == again
    other_site = slots_for_day(s, date(2026, 10, 19), 'https://other.example.test')
    assert monday[0]['id'] != other_site[0]['id']
