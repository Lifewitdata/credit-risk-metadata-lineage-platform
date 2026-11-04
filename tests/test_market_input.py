"""Reject bad provider payloads before the replace-table write is reached."""
from datetime import date
import pytest
from etl import load_market_prices as market


def payload(rows):
    return 'observation_date,SP500\n' + rows


@pytest.mark.parametrize('value', ['0', '-1', 'NaN', 'nan', 'inf', '-inf', '1e309'])
def test_nonpositive_and_nonfinite_prices_are_rejected(value):
    with pytest.raises(ValueError, match='finite positive'):
        market.parse(payload(f'2024-01-03,{value}\n'))


@pytest.mark.parametrize('second', ['4750.45', '4800', '.'])
def test_duplicate_dates_are_rejected_even_if_values_agree(second):
    with pytest.raises(ValueError, match='duplicate trading date'):
        market.parse(payload(f'2024-01-03,4750.45\n2024-01-03,{second}\n'))


def test_missing_observations_are_preserved_and_rows_sorted():
    rows = market.parse(payload('2024-01-04,4800\n2024-01-02,.\n2024-01-03,4750.45\n'))
    assert rows == [(date(2024, 1, 2), None), (date(2024, 1, 3), 4750.45),
                    (date(2024, 1, 4), 4800.0)]


@pytest.mark.parametrize('body', [
    'observation_date,SP500,unexpected\n2024-01-03,4750.45,x\n',
    'observation_date,SP500\n2024-01-03,4750.45,x\n',
])
def test_extra_columns_are_not_silently_ignored(body):
    with pytest.raises(ValueError, match='changed shape'):
        market.parse(body)


def test_all_missing_download_cannot_replace_usable_history():
    with pytest.raises(ValueError, match='no priced observations'):
        market.parse(payload('2024-01-02,.\n2024-01-03,NA\n'))


@pytest.mark.parametrize('rows', [
    '2024-01-03,4750\n2024-01-03,4750\n',
    '2024-01-03,NaN\n',
    '2024-01-03,.\n',
])
def test_invalid_fetch_never_reaches_database_write(monkeypatch, rows):
    monkeypatch.setattr(market, 'get_engine', lambda: object())
    monkeypatch.setattr(market, 'fetch', lambda: payload(rows))
    def fail_write(*args, **kwargs):
        pytest.fail('Invalid input reached the replace-table write')
    monkeypatch.setattr(market.pd.DataFrame, 'to_sql', fail_write)
    with pytest.raises(ValueError):
        market.run()
