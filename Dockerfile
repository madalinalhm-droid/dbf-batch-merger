# Runs the headless batch (CLI) and the test suite – no GUI, no Flet needed.
#   docker build -t dbf-batch-merger .
#   docker run --rm dbf-batch-merger                       # demo on fictitious data
#   docker run --rm -v "$PWD/mydata:/data" dbf-batch-merger \
#       python -m dbf_merger.cli --config /data/config.json --unit U1 --month 09 --year 2026
FROM python:3.12-slim

WORKDIR /app
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY dbf_merger/ dbf_merger/
COPY tools/ tools/
COPY tests/ tests/

# Fail the build if the tests fail
RUN python -m pytest -q tests

CMD ["sh", "-c", "python tools/make_sample_data.py --out /tmp/demo --month 09 --year 2026 && python -m dbf_merger.cli --config /tmp/demo/config.json --work-folder /tmp/demo --unit U1 --month 09 --year 2026"]
