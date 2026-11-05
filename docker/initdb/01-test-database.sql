-- Runs once, when the database volume is first created. The test suite needs its own
-- database; it refuses to run against any name that does not end in _test.
CREATE DATABASE booking_test;
