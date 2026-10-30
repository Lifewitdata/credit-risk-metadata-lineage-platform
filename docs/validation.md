# Validation record

Local Python 3.12 review on 2026-10-30: twelve regression tests passed; the MySQL
integration test was skipped because no local MySQL server was available.
These checks cover idempotent rule loads, changed formulas, retirement/reactivation,
conflicting input rejection, cycles, source verification, date boundaries, explicit
text clearing, calculation exceptions, configuration import and SQL script parsing.

The calculation reconciliation ran against the committed ten-row fixture:
seven mismatches, three blocked inputs, zero passes. This is a finding to investigate,
not a failed attempt to make the data look clean.

GitHub Actions supplies a disposable MySQL 8.0 service for a separate integration
check and uploads generated evidence. Refer to the actual run outcome before claiming
that MySQL execution passed. SQLite regression tests are not proof of MySQL behaviour.
