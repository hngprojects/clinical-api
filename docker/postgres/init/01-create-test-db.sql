-- pytest uses DATABASE_URL=.../test (see tests/conftest.py).
-- Runs once when the postgres volume is first created.
CREATE DATABASE test;
