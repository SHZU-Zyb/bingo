# Pytest reference

- Run one test: `python -m pytest path/to/test.py::test_name -q`
- Run one file: `python -m pytest path/to/test.py -q`
- Stop on first failure while diagnosing: `python -m pytest -x -q`
- Run the complete suite before claiming repository-wide compatibility: `python -m pytest -q`

Do not infer success from collection alone. Check the process exit code and the final summary.
