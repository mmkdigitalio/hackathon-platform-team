FROM python:3.13-alpine
COPY smoke_http.py /app/smoke_http.py
USER 65534:65534
ENTRYPOINT ["python3", "-u", "/app/smoke_http.py"]
